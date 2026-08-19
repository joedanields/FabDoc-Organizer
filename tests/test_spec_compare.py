"""The second tracker: two issues, OLD and NEW, and what moved between them.

Not a chain. Everything here is about exactly two inputs, which is why the
folder names have to say which is which - reading them backwards turns every
increase in the report into a decrease.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from conftest import make_drawing

from fabdoc.register import build_register
from fabdoc.spec_compare import (ADDED, CHANGED, DECREASED, FIELDS, INCREASED,
                                 REMOVED, UNCHANGED, compare_specs,
                                 order_folders, side_of)
from fabdoc.spec_out import write_spec_report

# qty, profile, material, length, weight
SPEC = tuple[str, str, str, str, str]

BASE: SPEC = ("2", "L3X3X3/16", "A36", "3'-11 5/8\"", "14.72 lbs")


def issue(root: Path, name: str, parts: dict[str, SPEC]) -> Path:
    """One issue folder of single part drawings."""
    folder = root / name
    for idx, (mark, spec) in enumerate(parts.items(), start=1):
        qty, profile, material, length, weight = spec
        make_drawing(folder / "Single Part Drawings" / f"{idx}_{mark}.pdf", mark,
                     revision=0, seq=idx, qty=qty, length=length,
                     profile=profile, material=material, weight=weight)
    return folder


def compare(root: Path, old: dict[str, SPEC], new: dict[str, SPEC]):
    old_folder = issue(root, "12. Zone 1 for Fabrication OLD", old)
    new_folder = issue(root, "18. Zone 1 for Fabrication NEW", new)
    a, b = order_folders(old_folder, new_folder)
    return compare_specs(build_register(a), build_register(b),
                         old_label=a.name, new_label=b.name)


def verdicts(result, field: str) -> dict[str, str]:
    return {r.member_name: r.verdict for r in result.sheets[field].rows}


# ------------------------------------------------------------ which is which


@pytest.mark.parametrize("name, expected", [
    ("12. Zone 1 for Fabrication OLD", "old"),
    ("18. Zone 1 NEW", "new"),
    ("old zone 1", "old"),
    ("Renewal Works", ""),          # NEW inside a word is not a marker
    ("Golden Gate", ""),
    ("Zone 1", ""),
])
def test_the_folder_name_says_which_issue_it_is(name: str, expected: str):
    assert side_of(name) == expected


def test_the_names_decide_the_order_not_the_boxes():
    """Read backwards, every increase in the report is printed as a decrease."""
    old, new = order_folders("18. Zone 1 NEW", "12. Zone 1 OLD")
    assert (old.name, new.name) == ("12. Zone 1 OLD", "18. Zone 1 NEW")


def test_an_unmarked_folder_is_refused_by_name():
    with pytest.raises(ValueError, match="Zone 1 for Fabrication"):
        order_folders("12. Zone 1 for Fabrication", "18. Zone 1 NEW")


def test_two_folders_marked_the_same_way_are_refused():
    with pytest.raises(ValueError, match="Both folders are marked OLD"):
        order_folders("12. Zone 1 OLD", "18. Zone 1 OLD")


# ------------------------------------------------------------- the five values


def test_a_quantity_that_went_up_and_one_that_went_down(tmp_path: Path):
    result = compare(
        tmp_path,
        {"17a25": ("2", *BASE[1:]), "17a26": ("6", *BASE[1:])},
        {"17a25": ("5", *BASE[1:]), "17a26": ("5", *BASE[1:])},
    )
    assert verdicts(result, "Qty") == {"17a25": INCREASED, "17a26": DECREASED}


def test_a_part_that_got_longer_and_heavier(tmp_path: Path):
    result = compare(
        tmp_path,
        {"17a25": ("2", "L3X3X3/16", "A36", "0'-8 5/16\"", "1.77 lbs")},
        {"17a25": ("2", "L3X3X3/16", "A36", "0'-10 1/8\"", "2.15 lbs")},
    )
    assert verdicts(result, "Length") == {"17a25": INCREASED}
    assert verdicts(result, "Weight") == {"17a25": INCREASED}
    assert verdicts(result, "Qty") == {"17a25": UNCHANGED}


def test_profile_and_material_change_rather_than_move(tmp_path: Path):
    """Neither goes up or down: a section is what it is, or it is another one."""
    result = compare(
        tmp_path,
        {"17a25": ("2", "L3X3X3/16", "A36", *BASE[3:])},
        {"17a25": ("2", "L4X4X1/4", "A572-GR.50", *BASE[3:])},
    )
    assert verdicts(result, "Profile") == {"17a25": CHANGED}
    assert verdicts(result, "Material") == {"17a25": CHANGED}


def test_a_part_in_only_one_issue_is_listed_once_not_on_every_sheet(tmp_path: Path):
    """The same fact five times over is four times too many.

    It is not a change to any of the five values either: a revised set is
    usually a partial re-issue, and what it did not carry was not touched.
    """
    result = compare(tmp_path, {"17a25": BASE}, {"17a26": BASE})
    assert [r.member_name for r in result.only_in_old] == ["17a25"]
    assert [r.member_name for r in result.only_in_new] == ["17a26"]
    assert REMOVED == "Only in OLD" and ADDED == "Only in NEW"
    # Neither appears on a value sheet - there is nothing to compare them to.
    for spec in FIELDS:
        assert result.sheets[spec.name].rows == []


def test_nothing_moved_says_so(tmp_path: Path):
    result = compare(tmp_path, {"17a25": BASE, "17a26": BASE},
                     {"17a25": BASE, "17a26": BASE})
    assert result.is_identical
    assert result.moved == 0
    assert all(sheet.verdict == "no changes" for sheet in result.sheets.values())
    assert all(sheet.changes == [] for sheet in result.sheets.values())


def test_only_the_parts_that_moved_are_written_down(tmp_path: Path):
    """Every part is compared; only the ones that moved are reported.

    Six hundred rows saying "No change" is not a report of what changed, it is
    a haystack with the answer somewhere in it.
    """
    result = compare(tmp_path, {"17a25": BASE, "17a26": BASE},
                     {"17a25": ("9", *BASE[1:]), "17a26": BASE})
    qty = result.sheets["Qty"]
    assert len(qty.rows) == 2, "both parts were compared"
    assert [r.member_name for r in qty.changes] == ["17a25"]
    assert qty.count(UNCHANGED) == 1
    assert result.sheets["Length"].changes == []


def test_assemblies_are_not_read_at_all(tmp_path: Path):
    """A part is one piece cut to one length; an assembly's numbers are not that."""
    for name in ("12. Zone 1 OLD", "18. Zone 1 NEW"):
        make_drawing(tmp_path / name / "Assembly Drawings" / "1_17172C1.pdf",
                     "17172C1", revision=0, seq=1, qty="4", length="9'-0\"",
                     profile="W24X55", material="A992", weight="500 lbs")
    make_drawing(tmp_path / "12. Zone 1 OLD" / "Single Part Drawings" / "1_17a25.pdf",
                 "17a25", revision=0, seq=1, qty="2", length="2'-0\"",
                 profile="L3X3X3/16", material="A36", weight="9 lbs")
    make_drawing(tmp_path / "18. Zone 1 NEW" / "Single Part Drawings" / "1_17a25.pdf",
                 "17a25", revision=0, seq=1, qty="3", length="2'-0\"",
                 profile="L3X3X3/16", material="A36", weight="9 lbs")

    old, new = order_folders(tmp_path / "12. Zone 1 OLD", tmp_path / "18. Zone 1 NEW")
    result = compare_specs(build_register(old), build_register(new))
    assert list(verdicts(result, "Qty")) == ["17a25"]


# ------------------------------------------------------------------- workbook


def test_the_report_has_a_sheet_per_value(tmp_path: Path):
    result = compare(tmp_path, {"17a25": ("2", *BASE[1:])},
                     {"17a25": ("5", *BASE[1:])})
    out = write_spec_report(result, tmp_path / "specs.xlsx")

    wb = load_workbook(out)
    try:
        assert wb.sheetnames == ["Summary", "Qty", "Profile", "Material",
                                 "Length", "Weight"]
        ws = wb["Qty"]
        assert [c.value for c in ws[3]] == [
            "Member Name", "Zone", "Sequence", "OLD", "NEW", "Change"]
        row = [c.value for c in ws[4]]
        assert row[0] == "17a25"
        assert row[3] == "2" and row[4] == "5"
        assert row[5] == INCREASED, "one word, and no sentence rebuilding it"

        # The summary counts what each sheet found.
        summary = [list(r) for r in wb["Summary"].iter_rows(values_only=True)]
        head = next(r for r in summary if r[0] == "Value")
        qty = next(r for r in summary if r[0] == "Qty")
        assert qty[head.index(INCREASED)] == 1
        assert qty[head.index(DECREASED)] == 0
    finally:
        wb.close()


def test_a_value_that_did_not_move_gets_a_sheet_that_says_so(tmp_path: Path):
    """No table at all, and a line saying why - not six hundred blank verdicts."""
    result = compare(tmp_path, {"17a25": ("2", *BASE[1:]), "17a26": BASE},
                     {"17a25": ("5", *BASE[1:]), "17a26": BASE})
    out = write_spec_report(result, tmp_path / "specs.xlsx")

    wb = load_workbook(out)
    try:
        length = wb["Length"]
        assert "NO CHANGES" in length.cell(row=2, column=1).value
        assert length.max_row == 2, "nothing under the line"

        # And the sheet that did move carries only the part that moved.
        qty = wb["Qty"]
        names = [r[0] for r in qty.iter_rows(min_row=4, values_only=True)]
        assert names == ["17a25"]
    finally:
        wb.close()


def test_parts_in_only_one_issue_are_listed_once_on_the_summary(tmp_path: Path):
    result = compare(tmp_path, {"17a25": BASE, "17a26": BASE}, {"17a25": BASE})
    out = write_spec_report(result, tmp_path / "specs.xlsx")

    wb = load_workbook(out)
    try:
        rows = [list(r) for r in wb["Summary"].iter_rows(values_only=True)]
        heading = next(i for i, r in enumerate(rows)
                       if r[0] == "PARTS IN ONLY ONE ISSUE")
        listed = [r[0] for r in rows[heading + 2:] if r[0] and r[3]]
        assert listed == ["17a26"]
        # And not on any of the five.
        for name in ("Qty", "Profile", "Material", "Length", "Weight"):
            sheet = [c.value for c in wb[name]["A"]]
            assert "17a26" not in sheet, name
    finally:
        wb.close()


def test_the_change_column_carries_nothing_but_the_verdict(tmp_path: Path):
    result = compare(tmp_path, {"17a25": ("6", *BASE[1:])},
                     {"17a25": ("5", *BASE[1:])})
    out = write_spec_report(result, tmp_path / "specs.xlsx")

    wb = load_workbook(out)
    try:
        change = wb["Qty"].cell(row=4, column=6)
        assert change.value == DECREASED
        assert change.comment is None
    finally:
        wb.close()
