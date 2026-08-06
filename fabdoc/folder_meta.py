"""Extract project metadata from the master (issue) folder name.

Issue folders carry their metadata in the name, but the convention varies between
projects. Rather than demand one layout, this parser pulls out the tokens it can
recognise unambiguously - date, zone, package - and treats whatever is left as
the project title. Every field it produces is editable in the GUI before the
register is written, so a miss is a correction, not a failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Delimiters that separate metadata fields in a folder name.
_SPLIT_RE = re.compile(r"\s*(?:\s-\s|_{1,2}|\|)\s*")

# Underscore is a word character, so \b does not fire between "_" and "2024".
# Folder names are full of underscores, so boundaries are spelled out instead.
_LB = r"(?<![0-9A-Za-z])"
_RB = r"(?![0-9A-Za-z])"


@dataclass
class ProjectMeta:
    """Metadata recovered from a folder name."""

    title: str = ""
    issue_date: date | None = None
    date_text: str = ""          # the raw token the date came from
    zones: list[str] = field(default_factory=list)
    package: str = ""
    revision: str = ""
    issue_no: str = ""           # leading "25." in "25. 2026-07-06 Stairs..."
    sequences: list[str] = field(default_factory=list)
    folder_name: str = ""
    extras: list[str] = field(default_factory=list)

    @property
    def zone(self) -> str:
        """Zones as one display string: "1, 2"."""
        return ", ".join(self.zones)

    @zone.setter
    def zone(self, value: str) -> None:
        self.zones = [z.strip() for z in str(value).split(",") if z.strip()]

    @property
    def date_display(self) -> str:
        """Date formatted for the register header, or the raw token if unparsed."""
        if self.issue_date:
            return self.issue_date.strftime("%d-%b-%Y")
        return self.date_text

    def as_row(self) -> dict[str, str]:
        return {
            "Title": self.title,
            "Date": self.date_display,
            "Zone": self.zone,
            "Package": self.package,
            "Revision": self.revision,
        }


# ---------------------------------------------------------------------------
# Date detection
# ---------------------------------------------------------------------------

# Ordered most-specific first so an ISO date is never mistaken for d-m-y.
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_LB + r"(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})" + _RB), "ymd"),
    (re.compile(_LB + r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{4})" + _RB), "dmy_or_mdy"),
    (re.compile(_LB + r"(\d{1,2})[-_. ]?([A-Za-z]{3,4})[-_. ]?(\d{2,4})" + _RB), "dMy"),
    (re.compile(_LB + r"([A-Za-z]{3,4})[-_. ]?(\d{1,2})[-_. ]?(\d{2,4})" + _RB), "Mdy"),
    (re.compile(_LB + r"(\d{4})(\d{2})(\d{2})" + _RB), "ymd"),
    (re.compile(_LB + r"(\d{2})(\d{2})(\d{4})" + _RB), "dmy_or_mdy"),
]


def _build_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date_token(text: str, day_first: bool = True) -> tuple[date | None, str]:
    """Find the first parseable date in ``text``.

    Returns ``(date, matched_text)``; ``(None, "")`` when nothing parses.
    """
    for pattern, kind in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            a, b, c = match.group(1), match.group(2), match.group(3)
            result: date | None = None
            if kind == "ymd":
                result = _build_date(int(a), int(b), int(c))
            elif kind == "dmy_or_mdy":
                first, second = int(a), int(b)
                if day_first:
                    # Fall back to the other reading when day-first is impossible.
                    result = _build_date(int(c), second, first) or _build_date(int(c), first, second)
                else:
                    result = _build_date(int(c), first, second) or _build_date(int(c), second, first)
            elif kind == "dMy":
                month = _MONTHS.get(b.upper())
                if month:
                    result = _build_date(int(c), month, int(a))
            elif kind == "Mdy":
                month = _MONTHS.get(a.upper())
                if month:
                    result = _build_date(int(c), month, int(b))
            if result:
                return result, match.group(0)
    return None, ""


# ---------------------------------------------------------------------------
# Field detection
# ---------------------------------------------------------------------------

_ZONE_RE = re.compile(_LB + r"ZONE\s*[-_:# ]?\s*([A-Za-z0-9][A-Za-z0-9\-]{0,11})" + _RB, re.I)
_ISSUE_NO_RE = re.compile(r"^(\d{1,4})\s*[.)]\s*")
_SEQS_RE = re.compile(r"\(\s*SEQ(?:UENCE)?S?\.?\s*[:\-]?\s*([\d,\s]+?)\s*\)", re.I)
_ZONE_BARE_RE = re.compile(r"^Z[-_ ]?([A-Za-z0-9]{1,6})$", re.I)
_PACKAGE_RE = re.compile(
    _LB + r"(?:PACKAGE|PKG|PCKG|PK)\s*[-_:# ]?\s*([A-Za-z0-9][A-Za-z0-9\-]{0,11})" + _RB, re.I
)
_REV_RE = re.compile(_LB + r"(?:REVISION|REV|ISSUE)\s*[-_:# ]?\s*([A-Za-z0-9]{1,4})" + _RB, re.I)


def parse_folder_name(name: str, day_first: bool = True) -> ProjectMeta:
    """Parse a master folder name into project metadata."""
    meta = ProjectMeta(folder_name=name)
    working = name.strip()

    # A leading "25." is the issue number, not part of the title.
    issue = _ISSUE_NO_RE.match(working)
    if issue:
        meta.issue_no = issue.group(1)
        working = working[issue.end():]

    # Date first: it is the most reliably shaped token, and removing it stops
    # its digits from polluting the title.
    parsed_date, date_text = parse_date_token(working, day_first=day_first)
    meta.issue_date = parsed_date
    meta.date_text = date_text
    if date_text:
        working = working.replace(date_text, " \x00 ", 1)

    # "(Seqs 172,173,270,271)" - the sequences covered by this issue.
    seqs = _SEQS_RE.search(working)
    if seqs:
        meta.sequences = [s.strip() for s in re.split(r"[,\s]+", seqs.group(1)) if s.strip()]
        working = working[: seqs.start()] + " \x00 " + working[seqs.end():]

    # Every zone mentioned, in order, de-duplicated: "Zone 1 and Zone 2" -> 1, 2.
    # Zones are deliberately NOT excised from the working string - the title
    # usually reads "Stairs at Zone 1 and Zone 2", and cutting the zones out
    # leaves a mangled "Stairs at and Zone 2".
    seen: set[str] = set()
    for match in _ZONE_RE.finditer(working):
        zone = match.group(1).upper()
        if zone not in seen:
            seen.add(zone)
            meta.zones.append(zone)

    for regex, attr in ((_PACKAGE_RE, "package"), (_REV_RE, "revision")):
        match = regex.search(working)
        if match:
            setattr(meta, attr, match.group(1).upper())
            working = working[: match.start()] + " \x00 " + working[match.end():]

    # Whatever survives is the title. Split on field delimiters, drop the
    # placeholders left by removed tokens, and keep meaningful chunks.
    parts = [p.strip(" -_") for p in _SPLIT_RE.split(working)]
    parts = [p.replace("\x00", "").strip(" -_") for p in parts]
    parts = [p for p in parts if p]

    if not meta.zones:
        # A standalone "Z2"-style token counts as a zone once the labelled
        # forms have been ruled out.
        for part in list(parts):
            bare = _ZONE_BARE_RE.match(part)
            if bare:
                meta.zones = [bare.group(1).upper()]
                parts.remove(part)
                break

    if parts:
        meta.title = parts[0]
        meta.extras = parts[1:]
    else:
        meta.title = name.strip()

    return meta


def parse_folder(path: str | Path, day_first: bool = True) -> ProjectMeta:
    """Parse metadata from a folder path's final component."""
    return parse_folder_name(Path(path).name, day_first=day_first)
