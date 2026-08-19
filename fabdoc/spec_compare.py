"""Compare what the shop is told to make, between one issue and the next.

This is the second tracker, and it is deliberately not a chain. The package
tracker in ``tracking.py`` follows a package across every issue it ever has,
because the questions it answers - what is approved, what has shipped, what is
still on hold - are questions about the whole history. These five numbers are
not. A detailer sends a revised set and one question is asked of it: against the
set before it, what moved?

So this takes exactly two inputs, an **old** issue and a **new** one, and says
for every part whether each of its five title-block values went up, went down,
changed, or stayed as it was. Two inputs and one answer needs no state on disk,
nothing to keep in step, and no way to get the order wrong - the folder names
carry OLD and NEW, so which is which is not a matter of which box it was typed
into.

Single parts only, for the same reason the extraction reads them off single
parts only: a part is one piece cut to one length from one profile in one
material at one weight, so that row of five means something. An assembly's
title block carries the same headings meaning different things.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppSettings
from .extract import (DrawingRecord, band_for_mark, parse_length_inches,
                      parse_weight, reads_spec)
from .register import Register
from .validate import normalise

# --- the five values, in the order they are drawn across the title block -----

FIELD_QTY = "Qty"
FIELD_PROFILE = "Profile"
FIELD_MATERIAL = "Material"
FIELD_LENGTH = "Length"
FIELD_WEIGHT = "Weight"

# --- what happened to one of them -------------------------------------------

INCREASED = "Increased"
DECREASED = "Decreased"
CHANGED = "Changed"          # profile and material: neither goes up or down
UNCHANGED = "No change"
# Neither of these claims anything happened to the part. A revised set is often
# a partial re-issue - eight drawings answering a comment - and calling the 289
# it did not carry "removed" would be the same false alarm the package tracker
# refuses to raise about a fabrication release.
ADDED = "Only in NEW"
REMOVED = "Only in OLD"

# A value that actually moved between the two issues. These are what the report
# is for, and on the sheets they are all it carries.
MOVED = (INCREASED, DECREASED, CHANGED)

# A part that is in one issue and not the other says nothing about any one
# value - it is the same fact five times over, so it is reported once, beside
# the totals, rather than on all five sheets.
ONE_SIDED = (ADDED, REMOVED)


@dataclass(frozen=True)
class SpecField:
    """One column of the title block table, and how to compare it."""

    name: str
    attr: str
    # None for a value that is text: a profile does not go up or down, it is
    # either what it was or it is something else.
    to_number: "callable | None" = None

    def value(self, record: DrawingRecord) -> str:
        return (getattr(record, self.attr, "") or "").strip()

    def verdict(self, old: str, new: str) -> str:
        """What happened between the two, as one word."""
        if old == new:
            return UNCHANGED
        if self.to_number is None:
            return CHANGED
        before, after = self.to_number(old), self.to_number(new)
        if before is None or after is None:
            # One side unreadable: it differs, and saying which way it went
            # would be an invention.
            return CHANGED
        if after > before:
            return INCREASED
        if after < before:
            return DECREASED
        # Same measurement written two ways - 4'-0" and 48" - is not a change
        # the shop has to act on.
        return UNCHANGED


def _to_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() else None


FIELDS: tuple[SpecField, ...] = (
    SpecField(FIELD_QTY, "quantity", _to_int),
    SpecField(FIELD_PROFILE, "profile"),
    SpecField(FIELD_MATERIAL, "material"),
    SpecField(FIELD_LENGTH, "length", parse_length_inches),
    SpecField(FIELD_WEIGHT, "weight", parse_weight),
)


# ---------------------------------------------------------------------------
# Which folder is which
# ---------------------------------------------------------------------------

# Whole words, so "RENEWAL" is not a new issue and "GOLDEN" is not an old one.
_OLD_IN_NAME = re.compile(r"(?<![A-Za-z])OLD(?![A-Za-z])", re.IGNORECASE)
_NEW_IN_NAME = re.compile(r"(?<![A-Za-z])NEW(?![A-Za-z])", re.IGNORECASE)

OLD = "old"
NEW = "new"


def side_of(folder: str | Path) -> str:
    """``"old"``, ``"new"``, or ``""`` when the folder name says neither."""
    name = Path(folder).name
    is_old, is_new = bool(_OLD_IN_NAME.search(name)), bool(_NEW_IN_NAME.search(name))
    if is_old and not is_new:
        return OLD
    if is_new and not is_old:
        return NEW
    return ""


def order_folders(first: str | Path, second: str | Path) -> tuple[Path, Path]:
    """``(old, new)``, decided by the folder names rather than the boxes.

    Two folders and five columns of numbers, and reading them backwards turns
    every increase into a decrease - a silent, total inversion of the one thing
    the report is for. The names have to carry it: one folder says OLD and the
    other says NEW, and then it does not matter which box each was typed into.
    """
    a, b = Path(first), Path(second)
    sides = {side_of(a): a, side_of(b): b}
    if OLD in sides and NEW in sides:
        return sides[OLD], sides[NEW]

    unmarked = [p.name for p in (a, b) if not side_of(p)]
    if unmarked:
        raise ValueError(
            "Name the folders so it is clear which issue is which: one must "
            "have OLD in its name and the other NEW. "
            + " and ".join(f'"{n}" has neither' for n in unmarked) + ".")
    both = side_of(a)
    raise ValueError(
        f'Both folders are marked {both.upper()} - "{a.name}" and "{b.name}". '
        f"One of them is the other issue.")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class SpecRow:
    """One part's value for one field, on both sides."""

    member_name: str = ""
    member_key: str = ""
    category: str = ""
    zone: str = ""
    band: tuple[str, str] = ("", "")
    old: str = ""
    new: str = ""
    verdict: str = UNCHANGED

    @property
    def moved(self) -> bool:
        return self.verdict in MOVED


@dataclass
class SpecSheet:
    """One field, over every part the two issues have in common."""

    field_name: str = ""
    rows: list[SpecRow] = field(default_factory=list)

    def count(self, verdict: str) -> int:
        return sum(1 for r in self.rows if r.verdict == verdict)

    @property
    def changes(self) -> list[SpecRow]:
        """The rows worth writing down: the ones where this value moved.

        The rest are parts that came back identical, which is most of a
        re-issue. Six hundred rows saying "No change" is not a report of what
        changed, it is a haystack with the answer somewhere in it.
        """
        return [r for r in self.rows if r.moved]

    @property
    def moved(self) -> int:
        return len(self.changes)

    @property
    def verdict(self) -> str:
        """The sheet's headline: "3 increased, 1 decreased", or no changes."""
        bits = [f"{self.count(v)} {v.lower()}" for v in MOVED if self.count(v)]
        return ", ".join(bits) if bits else "no changes"


@dataclass
class SpecComparison:
    """The whole report: five fields over the same set of parts."""

    old_label: str = ""
    new_label: str = ""
    old_folder: str = ""
    new_folder: str = ""
    sheets: "OrderedDict[str, SpecSheet]" = field(default_factory=OrderedDict)
    old_total: int = 0
    new_total: int = 0

    # Parts that are in one issue and not the other, worked out once: the same
    # fact holds for every one of the five values.
    only_in_old: list[SpecRow] = field(default_factory=list)
    only_in_new: list[SpecRow] = field(default_factory=list)

    @property
    def parts(self) -> int:
        """How many parts the two issues have in common - what is compared."""
        return len(self.sheets[FIELD_QTY].rows) if self.sheets else 0

    @property
    def moved(self) -> int:
        return sum(sheet.moved for sheet in self.sheets.values())

    @property
    def is_identical(self) -> bool:
        return (self.moved == 0 and not self.only_in_old
                and not self.only_in_new)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _parts(register: Register, cfg: AppSettings) -> "OrderedDict[str, DrawingRecord]":
    """The single-part drawings of one issue, keyed for comparison.

    Two folder shapes reach here and both are right: a whole issue folder, where
    the parts sit in a category beside the assemblies, and a folder of part
    drawings on their own - which is what somebody hands this screen when they
    have pulled two revisions of the same set out to compare. Everything that
    is not an assembly or an erection drawing counts, which covers both.

    First drawing of a mark wins, the same rule the rest of the app compares
    by: a duplicate file is a duplicate, not a second part.
    """
    out: "OrderedDict[str, DrawingRecord]" = OrderedDict()
    for rec in register.all_records():
        if not rec.member_name or not reads_spec(rec.category, cfg.profile):
            continue
        key = normalise(rec.member_name, cfg)
        if key and key not in out:
            out[key] = rec
    return out


def compare_specs(old: Register, new: Register,
                  settings: AppSettings | None = None,
                  old_label: str = "", new_label: str = "") -> SpecComparison:
    """Compare the five title-block values of two issues, part by part."""
    cfg = settings or AppSettings()
    old_parts, new_parts = _parts(old, cfg), _parts(new, cfg)

    result = SpecComparison(
        old_label=old_label or old.meta.title or "old",
        new_label=new_label or new.meta.title or "new",
        old_folder=str(old.project_folder or ""),
        new_folder=str(new.project_folder or ""),
        old_total=len(old_parts), new_total=len(new_parts),
    )

    # The parts the two issues have in common are what can be compared at all.
    # A part in only one of them is recorded once, below, rather than as a row
    # on all five sheets saying the same thing.
    keys = [k for k in old_parts if k in new_parts]

    def sort_key(key: str) -> tuple:
        rec = new_parts.get(key) or old_parts[key]  # noqa: B023
        kind, band = band_for_mark(rec.member_name, cfg.profile)
        return ((0, int(band), "") if kind == "seq" and band.isdigit()
                else (1, 0, band.upper()), rec.member_name.upper())

    def row(key: str, rec: DrawingRecord, old_value: str, new_value: str,
            verdict: str) -> SpecRow:
        return SpecRow(
            member_name=rec.member_name, member_key=key, category=rec.category,
            zone=rec.zone, band=band_for_mark(rec.member_name, cfg.profile),
            old=old_value, new=new_value, verdict=verdict,
        )

    for spec in FIELDS:
        sheet = SpecSheet(field_name=spec.name)
        for key in sorted(keys, key=sort_key):
            before, after = old_parts[key], new_parts[key]
            old_value, new_value = spec.value(before), spec.value(after)
            # The newer drawing is the one that says how the mark is spelled.
            sheet.rows.append(row(key, after, old_value, new_value,
                                  spec.verdict(old_value, new_value)))
        result.sheets[spec.name] = sheet

    for key in sorted((k for k in old_parts if k not in new_parts), key=sort_key):
        rec = old_parts[key]
        result.only_in_old.append(row(key, rec, "", "", REMOVED))
    for key in sorted((k for k in new_parts if k not in old_parts), key=sort_key):
        rec = new_parts[key]
        result.only_in_new.append(row(key, rec, "", "", ADDED))

    return result
