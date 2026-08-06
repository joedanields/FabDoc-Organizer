"""Tunable extraction profile.

Everything in this module is data, not logic: which regexes locate a field, where
the title block sits on the sheet, which folder names map to which drawing
category. Drawing templates differ between detailers, so these defaults are a
starting point meant to be calibrated against real drawings (see
``fabdoc.calibrate``) and then saved to disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Drawing categories
# ---------------------------------------------------------------------------

# Canonical category name -> substrings that identify it in a folder name.
# Matching is case-insensitive and checked against the folder name with
# non-alphanumeric characters stripped, so "Erection-Drawings" and
# "erection drawings" both hit "erection".
# "Assembly" is its own category, not an alias of Erection. An assembly drawing
# details one fabricated assembly for the shop; an erection drawing shows where
# assemblies go on site. They are different deliverables and folders named
# "Assembly" must not be relabelled "Erection" in the register.
DEFAULT_CATEGORY_ALIASES: dict[str, list[str]] = {
    "Structural": ["structural", "struct", "gad", "generalarrangement"],
    "Assembly": ["assembly", "assy", "shopassembly", "shopdrawing", "shop"],
    "Erection": ["erection", "erect", "layout", "anchorbolt"],
    "Part": ["part", "partdrawing", "piece", "singlepart", "component"],
}

# Order categories appear as worksheets, regardless of folder order on disk.
DEFAULT_CATEGORY_ORDER: list[str] = ["Structural", "Assembly", "Erection", "Part"]


# ---------------------------------------------------------------------------
# Extraction profile
# ---------------------------------------------------------------------------


@dataclass
class ExtractionProfile:
    """Rules describing how to pull fields out of a drawing PDF.

    Each ``*_patterns`` entry is a regular expression applied case-insensitively
    to the page text. The first capturing group is taken as the value. Patterns
    are tried in order and the first that matches wins, so put the most specific
    (most reliably labelled) pattern first.
    """

    name: str = "Default"

    # --- Member name / mark -------------------------------------------------
    member_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?:ASSEMBLY|ASSY)\s*(?:MARK|MK|No\.?|NUMBER|ID|REF|POS(?:ITION)?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:MEMBER)\s*(?:MARK|MK|NAME|No\.?|NUMBER|ID|REF|POS(?:ITION)?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:PIECE|PART)\s*(?:MARK|MK|No\.?|NUMBER|ID|REF|POS(?:ITION)?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"\bMARK\s*[:\-]\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:DRAWING|DRG|DWG)\s*(?:No\.?|NUMBER|NAME)\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
        ]
    )

    # --- Revision -----------------------------------------------------------
    revision_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?:REVISION|REV)\s*(?:No\.?|NUMBER)?\s*[:\-]?\s*([0-9]{1,3}|[A-Z]{1,2})\b",
            r"\bR\s*E\s*V\s*[:\-]?\s*([0-9]{1,3}|[A-Z]{1,2})\b",
        ]
    )

    # --- Sequence number ----------------------------------------------------
    sequence_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?:S\.?\s*No\.?|SL\.?\s*No\.?|SERIAL\s*No\.?)\s*[:\-]?\s*([0-9]{1,6})",
            r"(?:SHEET|SHT)\s*[:\-]?\s*([0-9]{1,6})\s*(?:OF|/)\s*[0-9]{1,6}",
            r"(?:SEQ(?:UENCE)?)\s*(?:No\.?)?\s*[:\-]?\s*([0-9]{1,6})",
        ]
    )

    # --- Filename fallbacks -------------------------------------------------
    # Applied to the file stem when page text yields nothing. Named groups
    # ``seq``, ``member`` and ``rev`` are picked up if present.
    # A revision suffix is a number or a single letter with optional digits.
    # Allowing any 1-3 characters lets "no_rev" backtrack into R + "EV".
    filename_patterns: list[str] = field(
        default_factory=lambda: [
            r"^(?P<seq>\d{1,6})[\s_\-]+(?P<member>[A-Za-z0-9._/\-]+?)[\s_\-]+(?:REV|R)[\s_\-]?(?P<rev>\d{1,3}|[A-Z]\d{0,2})$",
            r"^(?P<seq>\d{1,6})[\s_\-]+(?P<member>[A-Za-z0-9._/\-]+)$",
            r"^(?P<member>[A-Za-z0-9._/\-]+?)[\s_\-]+(?:REV|R)[\s_\-]?(?P<rev>\d{1,3}|[A-Z]\d{0,2})$",
            r"^(?P<member>[A-Za-z0-9._/\-]+)$",
        ]
    )

    # --- Title block geometry ----------------------------------------------
    # Fraction of the page (x0, y0, x1, y1) in 0..1 coordinates, origin top-left.
    # Steel detailing title blocks sit bottom-right on nearly every template, so
    # that region is searched first and only then the whole page.
    title_block_rect: tuple[float, float, float, float] = (0.55, 0.60, 1.0, 1.0)

    # When no labelled field matches, treat the largest text in the title block
    # that looks like a mark as the member name. This is how most templates
    # present the assembly mark - big and unlabelled.
    use_largest_text_fallback: bool = True

    # A token must look like this to be accepted as a member mark by the
    # largest-text fallback. Deliberately strict: it must contain a digit, so
    # words like "DETAIL" or "SECTION" are rejected.
    member_shape_pattern: str = r"^(?=.*\d)[A-Z0-9][A-Z0-9._/\-]{1,19}$"

    # Words that must never be accepted as a member name, whatever matches.
    # The second row is label vocabulary: an unrecognised qualifier after a
    # label ("MEMBER ID : B-101") would otherwise be captured as the mark.
    member_stopwords: list[str] = field(
        default_factory=lambda: [
            "SCALE", "SHEET", "DATE", "REV", "REVISION", "DRAWN", "CHECKED",
            "APPROVED", "TITLE", "PROJECT", "CLIENT", "ZONE", "NONE", "NTS",
            "DETAIL", "SECTION", "NOTES", "TOTAL", "WEIGHT", "GRADE", "QTY",
            "ID", "NO", "NO.", "NUMBER", "NAME", "MARK", "MK", "POS", "POSITION",
            "REF", "TYPE", "ITEM", "DESC", "DESCRIPTION", "OF", "SIZE", "UNIT",
        ]
    )

    # --- Page selection -----------------------------------------------------
    # Which page carries the title block. 0 = first page, -1 = last page.
    title_block_page: int = 0

    # --- Revision normalisation --------------------------------------------
    # Treat a drawing with no revision found as this value rather than blank.
    default_revision: str = "0"

    # --- Sequence and zone encoded in the member mark -----------------------
    # Many detailers encode the erection sequence into the mark itself:
    # "17172C172" is job 17, sequence 172, member C172. The named groups "job"
    # and "seq" are what matter; "seq" drives both the S.No grouping and the
    # zone. Set member_seq_pattern to "" to switch this off entirely.
    member_seq_pattern: str = r"^(?P<job>\d{2})(?P<seq>\d{3})(?P<rest>[A-Za-z].*)$"

    # Zone is the leading digit(s) of the sequence: seq 172 -> zone 1,
    # seq 270 -> zone 2, seq 471 -> zone 4.
    derive_zone_from_seq: bool = True
    zone_seq_digits: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["title_block_rect"] = list(self.title_block_rect)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractionProfile":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        if "title_block_rect" in clean:
            clean["title_block_rect"] = tuple(clean["title_block_rect"])  # type: ignore[assignment]
        return cls(**clean)


@dataclass
class AppSettings:
    """User-level settings persisted between runs."""

    profile: ExtractionProfile = field(default_factory=ExtractionProfile)
    category_aliases: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_CATEGORY_ALIASES.items()}
    )
    category_order: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORY_ORDER))

    # Folder-name date parsing. Indian/European packages are overwhelmingly
    # day-first; flip this if your issue folders use mm-dd-yyyy.
    day_first_dates: bool = True

    # Member comparison normalisation (see fabdoc.validate).
    compare_case_insensitive: bool = True
    compare_ignore_whitespace: bool = True
    compare_strip_leading_zeros: bool = False

    # Include Source File and Notes columns in the register. Off by default:
    # the register is a deliverable, and project title and date already appear
    # in the header band above the table rather than repeating on every row.
    include_source_column: bool = False

    # Group register rows under a banded zone header, restarting S.No per zone.
    group_by_zone: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "category_aliases": self.category_aliases,
            "category_order": self.category_order,
            "day_first_dates": self.day_first_dates,
            "compare_case_insensitive": self.compare_case_insensitive,
            "compare_ignore_whitespace": self.compare_ignore_whitespace,
            "compare_strip_leading_zeros": self.compare_strip_leading_zeros,
            "include_source_column": self.include_source_column,
            "group_by_zone": self.group_by_zone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        s = cls()
        if "profile" in data:
            s.profile = ExtractionProfile.from_dict(data["profile"])
        for key in (
            "category_aliases", "category_order", "day_first_dates",
            "compare_case_insensitive", "compare_ignore_whitespace",
            "compare_strip_leading_zeros", "include_source_column",
            "group_by_zone",
        ):
            if key in data:
                setattr(s, key, data[key])
        return s


def default_settings_path() -> Path:
    """Per-user settings location. Falls back to the home directory."""
    base = Path.home() / ".fabdoc"
    return base / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings, returning defaults if the file is absent or unreadable."""
    p = path or default_settings_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return AppSettings.from_dict(json.load(fh))
    except (OSError, ValueError, TypeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Persist settings, creating the parent directory as needed."""
    p = path or default_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(settings.to_dict(), fh, indent=2)
    return p
