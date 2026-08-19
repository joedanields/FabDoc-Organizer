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
            # A qualifier word or a separator is required. With both optional
            # the bare noun matched - "In Assembly" is a column heading on a
            # single-part title block, and the capture fell onto the material
            # cell beside it.
            r"(?:ASSEMBLY|ASSY)\s*(?:(?:MARK|MK|No\.?|NUMBER|#|ID|REF|POS(?:ITION)?)\s*[:\-#]?|[:\-])\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:MEMBER)\s*(?:(?:MARK|MK|NAME|No\.?|NUMBER|#|ID|REF|POS(?:ITION)?)\s*[:\-#]?|[:\-])\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
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

    # --- Quantity and cut length -------------------------------------------
    # A fabrication drawing carries the numbers the shop works to: how many of
    # this piece to make, and how long to cut it. They are written as a table in
    # the title block - the heading in one row, the value in the row below it -
    # so they are read by column position rather than by a same-line pattern.
    # These are the headings to look under, matched whole and case-insensitively.
    #
    # Which drawings do NOT carry a part's numbers. An assembly's title block
    # states a count too, but it counts assemblies rather than anything cut, and
    # read into the same column the two mean different things.
    #
    # Named as an exclusion rather than "read these categories" on purpose. The
    # part specs screen is routinely pointed straight at a folder of part
    # drawings - two revisions of the same set, pulled out to be compared - and
    # that folder is a category called "OLD", or "NEW", or nothing at all. A
    # list of category names to read refuses exactly the case the screen is for;
    # a list of what to skip lets anything unrecognised be what it plainly is.
    non_spec_categories: list[str] = field(
        default_factory=lambda: ["Assembly", "Erection", "Structural"]
    )
    quantity_labels: list[str] = field(
        default_factory=lambda: ["Qty", "Qty.", "Quantity", "No. Off", "No Off"]
    )
    length_labels: list[str] = field(
        default_factory=lambda: ["Length", "Cut Length", "Len"]
    )
    # The rest of the same row. Profile and material are what the piece is cut
    # from, weight is what it comes to - read together they are the part's
    # whole specification, and the second tracker compares all five.
    profile_labels: list[str] = field(
        default_factory=lambda: ["Profile", "Section", "Shape", "Size"]
    )
    material_labels: list[str] = field(
        default_factory=lambda: ["Material", "Grade", "Mat", "Mat."]
    )
    weight_labels: list[str] = field(
        default_factory=lambda: ["Weight", "Wt", "Wt.", "Mass"]
    )

    # Templates that write them inline ("QTY: 3") instead of as a table. Tried
    # after the table lookup, which is the arrangement on every drawing seen so
    # far and the only one that cannot pick up a neighbouring cell by accident.
    quantity_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?:QTY|QUANTITY|No\.?\s*OFF)\s*[:\-]\s*([0-9]{1,5})\b",
        ]
    )
    length_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?:CUT\s*)?LENGTH\s*[:\-]\s*([0-9]+'[\s\-]*[0-9]+(?:\s+[0-9]+/[0-9]+)?\"?)",
            r"(?:CUT\s*)?LENGTH\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?)\b",
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

    # Shapes that are never a member mark, whatever matched them. Applied to
    # labelled captures as well as to the largest-text fallback.
    #
    # Single-part drawings are what made this necessary. Their title block is a
    # column table - "Part #", "Qty", "In Assembly", "Material" as headings with
    # the values on the row below - so a same-line label pattern matches the
    # heading "In Assembly" and captures the material beside it. 121 of 125 part
    # drawings came out named after a steel section or a length: HSS4X4X1/2,
    # PIPE1-1/2SCH40, 29.00, 134/. None of those is a mark, and rejecting them
    # lets the cascade fall through to the largest text in the title block,
    # which is the mark on every one of those drawings.
    member_reject_patterns: list[str] = field(
        default_factory=lambda: [
            r"[/.]$",                       # a cut fraction: "12/", "134/"
            r"\d\s*[Xx]\s*\d",              # a section size: C12X25, HSS4X4X1/2
            r"^\d+\.\d+$",                  # a length: 29.00, 58.19
            r"SCH\d",                       # a pipe schedule: PIPE1-1/2SCH40
            r"^(?:PIPE|PL|HSS|TS|WT|MC)\d",  # a profile designation
        ]
    )

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
            # Column headings on a single-part title block, which a same-line
            # label pattern will otherwise capture as the mark.
            "PART", "PARTS", "ASSEMBLY", "ASSY", "MATERIAL", "LENGTH", "JOB",
            "QUANTITY", "PIECE", "MEMBER", "DRAWN", "SHEETS",
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
    #
    # The sequence is 2 OR 3 digits, and one package routinely mixes both:
    # "17120B163" is seq 120 while "1710B84" is seq 10. Pinned at 3 digits, the
    # 2-digit marks matched nothing, got no zone, and were clustered into a
    # second unlabelled table below the real one - 245 rows of a 968-row
    # register. The quantifier is greedy so a 3-digit sequence still wins, and
    # "rest" must start with a letter, which is what stops "10B" being read as
    # three digits.
    member_seq_pattern: str = r"^(?P<job>\d{2})(?P<seq>\d{2,3})(?P<rest>[A-Za-z].*)$"

    # Single parts are not erected in a sequence - they are cut for an assembly
    # - so they are marked differently: "17a24", "17ch104", "17hsp1" are job 17,
    # type A / CH / HSP, piece 24 / 104 / 1. There is no sequence in them at all,
    # and 92 of 124 single-part drawings in a real package are named this way.
    # The type is what groups them, the way the sequence groups an assembly.
    part_type_pattern: str = r"^(?P<job>\d{2})(?P<type>[A-Za-z]+)(?P<piece>\d+)$"

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

    # Group register rows under a banded zone header, and inside each zone
    # under a banded sequence header, restarting S.No per sequence.
    group_by_zone: bool = True

    # Categories the package tracker ignores. An erection drawing shows where
    # assemblies go on site - it is not a fabricated item, so it has no approved
    # scope to release and nothing to hold. Tracking it put site drawings in the
    # released and on-hold counts the shop reads.
    untracked_categories: list[str] = field(default_factory=lambda: ["Erection"])

    # Default output folder for generated registers (empty = project folder)
    default_output_folder: str = ""

    # Default folder for package trackers (empty = alongside the register).
    # The tracker is the one file that outlives the issue, so teams keep it in a
    # fixed place rather than in whichever issue folder happened to be processed.
    default_tracker_folder: str = ""

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
            "untracked_categories": self.untracked_categories,
            "default_output_folder": self.default_output_folder,
            "default_tracker_folder": self.default_tracker_folder,
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
            "group_by_zone", "default_output_folder", "default_tracker_folder",
        ):
            if key in data:
                setattr(s, key, data[key])
        return s


def default_settings_path() -> Path:
    """Per-user settings location. Falls back to the home directory."""
    base = Path.home() / ".fabdoc"
    return base / "settings.json"


# Defaults that shipped in an earlier version and were later corrected. A saved
# profile holds a full copy of every pattern, so a value the engineer never
# chose - it was simply the default the day they first pressed Save - would
# otherwise shadow the fix for the life of the install.
#
# The 3-digit sequence pattern is the one that made this worth doing: it matched
# nothing on a 2-digit sequence, so a package numbered "Seq 10-12, 120-139" lost
# every drawing in sequences 10, 11 and 12 to an unbanded table at the foot of
# the sheet - 245 rows of a 968-row register, on a fix that had already shipped.
SUPERSEDED_DEFAULTS: dict[str, list[Any]] = {
    "member_seq_pattern": [
        r"^(?P<job>\d{2})(?P<seq>\d{3})(?P<rest>[A-Za-z].*)$",
    ],
    # The label patterns whose qualifier and separator were both optional, so
    # the bare noun in a column heading ("In Assembly") read as a label.
    "member_patterns": [
        [
            r"(?:ASSEMBLY|ASSY)\s*(?:MARK|MK|No\.?|NUMBER|ID|REF|POS(?:ITION)?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:MEMBER)\s*(?:MARK|MK|NAME|No\.?|NUMBER|ID|REF|POS(?:ITION)?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:PIECE|PART)\s*(?:MARK|MK|No\.?|NUMBER|ID|REF|POS(?:ITION)?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"\bMARK\s*[:\-]\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
            r"(?:DRAWING|DRG|DWG)\s*(?:No\.?|NUMBER|NAME)\s*[:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{1,19})",
        ],
    ],
    # The stopword list before the single-part column headings were added.
    "member_stopwords": [
        [
            "SCALE", "SHEET", "DATE", "REV", "REVISION", "DRAWN", "CHECKED",
            "APPROVED", "TITLE", "PROJECT", "CLIENT", "ZONE", "NONE", "NTS",
            "DETAIL", "SECTION", "NOTES", "TOTAL", "WEIGHT", "GRADE", "QTY",
            "ID", "NO", "NO.", "NUMBER", "NAME", "MARK", "MK", "POS", "POSITION",
            "REF", "TYPE", "ITEM", "DESC", "DESCRIPTION", "OF", "SIZE", "UNIT",
        ],
    ],
}


def migrate_profile(data: dict[str, Any]) -> list[str]:
    """Replace superseded defaults in a saved profile. Returns what changed.

    Only values identical to a previous default are touched. Anything the
    engineer actually tuned is left exactly as they wrote it.
    """
    fresh = ExtractionProfile()
    changed: list[str] = []
    for key, old_defaults in SUPERSEDED_DEFAULTS.items():
        if data.get(key) in old_defaults and data.get(key) != getattr(fresh, key):
            data[key] = getattr(fresh, key)
            changed.append(key)
    return changed


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings, returning defaults if the file is absent or unreadable."""
    p = path or default_settings_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data.get("profile"), dict):
            migrate_profile(data["profile"])
        return AppSettings.from_dict(data)
    except (OSError, ValueError, TypeError, AttributeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Persist settings, creating the parent directory as needed."""
    p = path or default_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(settings.to_dict(), fh, indent=2)
    return p
