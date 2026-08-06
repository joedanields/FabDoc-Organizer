"""Compare two issues of the same drawing package.

Packages get re-issued constantly - "for Approval" then "for Re Approval" -
and the question every time is which drawings are new, which were dropped, and
which came back at a different revision. Doing that by eye across a hundred
drawings is exactly the error-prone chore this tool exists to remove.

This is register-against-register. Register-against-model lives in validate.py.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from .config import AppSettings
from .extract import DrawingRecord
from .register import Register
from .validate import normalise


@dataclass
class MemberDelta:
    """One member's fate between two issues."""

    member_name: str
    zone: str = ""
    old_revision: str = ""
    new_revision: str = ""
    old_count: int = 0
    new_count: int = 0

    @property
    def revision_changed(self) -> bool:
        return bool(self.old_revision) and bool(self.new_revision) \
            and self.old_revision != self.new_revision

    @property
    def quantity_changed(self) -> bool:
        return self.old_count != self.new_count


@dataclass
class IssueComparison:
    """Result of comparing an older issue against a newer one."""

    added: list[MemberDelta] = field(default_factory=list)
    removed: list[MemberDelta] = field(default_factory=list)
    revision_changed: list[MemberDelta] = field(default_factory=list)
    unchanged: list[MemberDelta] = field(default_factory=list)
    quantity_changed: list[MemberDelta] = field(default_factory=list)

    old_label: str = ""
    new_label: str = ""
    old_total: int = 0
    new_total: int = 0

    @property
    def is_identical(self) -> bool:
        return not self.added and not self.removed and not self.revision_changed

    @property
    def verdict(self) -> str:
        if self.is_identical:
            return "IDENTICAL - both issues contain the same members at the same revisions."
        bits = []
        if self.added:
            bits.append(f"{len(self.added)} added")
        if self.removed:
            bits.append(f"{len(self.removed)} removed")
        if self.revision_changed:
            bits.append(f"{len(self.revision_changed)} revised")
        return "CHANGED - " + ", ".join(bits) + "."

    def summary_lines(self) -> list[str]:
        return [
            f"Old issue ({self.old_label}): {self.old_total} drawing(s)",
            f"New issue ({self.new_label}): {self.new_total} drawing(s)",
            "",
            f"Added in new issue    : {len(self.added)}",
            f"Removed in new issue  : {len(self.removed)}",
            f"Revision changed      : {len(self.revision_changed)}",
            f"Quantity changed      : {len(self.quantity_changed)}",
            f"Unchanged             : {len(self.unchanged)}",
            "",
            self.verdict,
        ]


def _index(register: Register, settings: AppSettings,
           categories: list[str] | None) -> "OrderedDict[str, list[DrawingRecord]]":
    """Members of a register keyed by comparison key, preserving order."""
    wanted = {c.lower() for c in categories} if categories else None
    out: "OrderedDict[str, list[DrawingRecord]]" = OrderedDict()
    for cat in register.categories:
        if wanted is not None and cat.name.lower() not in wanted:
            continue
        for rec in cat.records:
            if not rec.member_name:
                continue
            key = normalise(rec.member_name, settings)
            if key:
                out.setdefault(key, []).append(rec)
    return out


def compare_issues(
    old: Register,
    new: Register,
    settings: AppSettings | None = None,
    categories: list[str] | None = None,
    old_label: str = "",
    new_label: str = "",
) -> IssueComparison:
    """Compare an older register against a newer one.

    Both are treated as registers of the same package at different issues, so
    "added" means present in ``new`` but not ``old``.
    """
    cfg = settings or AppSettings()
    result = IssueComparison(
        old_label=old_label or old.meta.title or "old",
        new_label=new_label or new.meta.title or "new",
    )

    old_idx = _index(old, cfg, categories)
    new_idx = _index(new, cfg, categories)
    result.old_total = sum(len(v) for v in old_idx.values())
    result.new_total = sum(len(v) for v in new_idx.values())

    def delta(key: str) -> MemberDelta:
        o = old_idx.get(key, [])
        n = new_idx.get(key, [])
        source = n[0] if n else o[0]
        return MemberDelta(
            member_name=source.member_name,
            zone=source.zone,
            old_revision=o[0].revision if o else "",
            new_revision=n[0].revision if n else "",
            old_count=len(o),
            new_count=len(n),
        )

    for key in new_idx:
        d = delta(key)
        if key not in old_idx:
            result.added.append(d)
        elif d.revision_changed:
            result.revision_changed.append(d)
        else:
            result.unchanged.append(d)
        if key in old_idx and d.quantity_changed:
            result.quantity_changed.append(d)

    for key in old_idx:
        if key not in new_idx:
            result.removed.append(delta(key))

    return result
