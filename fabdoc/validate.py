"""Compare a drawing register against the structural model member list.

The comparison is set-based but reports against the original spellings, so an
engineer sees the mark as it appears in their model and in their drawing rather
than a normalised form they would have to translate back.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from .config import AppSettings
from .extract import DrawingRecord
from .memberlist import MemberList
from .register import Register

_WS = re.compile(r"\s+")


def normalise(name: str, settings: AppSettings | None = None) -> str:
    """Reduce a member mark to its comparison key."""
    cfg = settings or AppSettings()
    key = name.strip()
    if cfg.compare_ignore_whitespace:
        key = _WS.sub("", key)
    if cfg.compare_case_insensitive:
        key = key.upper()
    if cfg.compare_strip_leading_zeros:
        # Strip leading zeros inside each numeric run: B007 -> B7.
        key = re.sub(r"(?<!\d)0+(\d)", r"\1", key)
    return key


@dataclass
class ValidationResult:
    """Outcome of comparing drawings against the model."""

    matched: list[str] = field(default_factory=list)
    missing_in_drawings: list[str] = field(default_factory=list)
    extra_in_drawings: list[str] = field(default_factory=list)
    duplicates_in_drawings: "OrderedDict[str, list[str]]" = field(default_factory=OrderedDict)

    matched_records: list[DrawingRecord] = field(default_factory=list)
    extra_records: list[DrawingRecord] = field(default_factory=list)

    model_count: int = 0
    drawing_count: int = 0
    unnamed_drawings: int = 0

    project_title: str = ""
    project_date: str = ""
    register_source: str = ""
    model_source: str = ""

    @property
    def is_clean(self) -> bool:
        return not self.missing_in_drawings and not self.extra_in_drawings

    @property
    def match_rate(self) -> float:
        """Share of model members that have a drawing, 0.0-1.0."""
        return len(self.matched) / self.model_count if self.model_count else 0.0

    @property
    def verdict(self) -> str:
        if self.is_clean:
            return "PASS - every model member has a drawing and every drawing has a model member."
        problems = []
        if self.missing_in_drawings:
            problems.append(f"{len(self.missing_in_drawings)} model member(s) have no drawing")
        if self.extra_in_drawings:
            problems.append(f"{len(self.extra_in_drawings)} drawing(s) have no model member")
        return "REVIEW REQUIRED - " + "; ".join(problems) + "."

    def summary_lines(self) -> list[str]:
        lines = [
            f"Members in model:      {self.model_count}",
            f"Members in drawings:   {self.drawing_count}",
            f"Matched:               {len(self.matched)}",
            f"Missing in drawings:   {len(self.missing_in_drawings)}",
            f"Not in model:          {len(self.extra_in_drawings)}",
        ]
        if self.duplicates_in_drawings:
            lines.append(f"Duplicated in drawings:{len(self.duplicates_in_drawings):>4}")
        if self.unnamed_drawings:
            lines.append(f"Drawings with no member name: {self.unnamed_drawings}")
        lines.append("")
        lines.append(self.verdict)
        return lines


def validate(
    register: Register,
    model_list: MemberList,
    settings: AppSettings | None = None,
    categories: list[str] | None = None,
) -> ValidationResult:
    """Compare a register against a model member list.

    ``categories`` limits the comparison to named drawing categories - part
    drawings and erection drawings are usually validated against different
    model exports, so comparing everything at once would be misleading.
    """
    cfg = settings or AppSettings()
    result = ValidationResult(
        project_title=register.meta.title,
        project_date=register.meta.date_display,
        register_source=str(register.project_folder),
        model_source=model_list.source,
    )

    records: list[DrawingRecord] = []
    for cat in register.categories:
        if categories is not None and cat.name.lower() not in {c.lower() for c in categories}:
            continue
        records.extend(cat.records)

    # Model side: keep first spelling seen for each key, drop duplicates.
    model_keys: "OrderedDict[str, str]" = OrderedDict()
    for raw in model_list.members:
        key = normalise(raw, cfg)
        if key and key not in model_keys:
            model_keys[key] = raw.strip()
    result.model_count = len(model_keys)

    # Drawing side: a key can legitimately map to several files, which is
    # exactly the duplicate case the engineer needs flagged.
    drawing_keys: "OrderedDict[str, list[DrawingRecord]]" = OrderedDict()
    for rec in records:
        if not rec.member_name:
            result.unnamed_drawings += 1
            continue
        key = normalise(rec.member_name, cfg)
        if not key:
            result.unnamed_drawings += 1
            continue
        drawing_keys.setdefault(key, []).append(rec)
    result.drawing_count = len(drawing_keys)

    for key, recs in drawing_keys.items():
        if len(recs) > 1:
            result.duplicates_in_drawings[recs[0].member_name] = [r.source_file for r in recs]

    for key, original in model_keys.items():
        if key in drawing_keys:
            result.matched.append(original)
            result.matched_records.append(drawing_keys[key][0])
        else:
            result.missing_in_drawings.append(original)

    for key, recs in drawing_keys.items():
        if key not in model_keys:
            result.extra_in_drawings.append(recs[0].member_name)
            result.extra_records.append(recs[0])

    return result
