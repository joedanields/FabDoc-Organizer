"""Segregating a register by erection sequence.

The sequence inside a member mark carries the zone in its leading digit, and the
register is split by the whole sequence number inside each zone. Sequences run
in plain ascending order, and a band says only which sequence it is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import make_drawing                              # noqa: E402
from fabdoc import sequencing as sq                            # noqa: E402
from fabdoc.config import AppSettings                          # noqa: E402
from fabdoc.excel_out import write_register                    # noqa: E402
from fabdoc.extract import DrawingRecord                       # noqa: E402
from fabdoc.register import build_register, sort_records       # noqa: E402
from fabdoc.register_io import read_register                   # noqa: E402


# --------------------------------------------------------------- ordering


def test_sequences_sort_numerically_not_as_text():
    """A package mixes two- and three-digit sequences in one register.

    Sorted as text "10" lands after "120", which puts the first sequence
    erected at the bottom of the sheet.
    """
    seqs = ["130", "10", "12", "120", "11", "139", "129", "172"]
    assert sorted(seqs, key=sq.sort_key) == [
        "10", "11", "12", "120", "129", "130", "139", "172",
    ]


def test_a_missing_sequence_sorts_last_without_raising():
    assert sorted(["172", "", "10"], key=sq.sort_key) == ["10", "172", ""]
    assert sorted(["172", "n/a"], key=sq.sort_key) == ["172", "n/a"]


def test_the_band_says_only_the_sequence():
    assert sq.label_for("130") == "SEQ 130"
    assert sq.label_for("10") == "SEQ 10"
    assert sq.label_for("") == "NO SEQUENCE"


# ------------------------------------------------------------- the register


def test_records_sort_by_zone_then_sequence():
    records = [
        DrawingRecord(member_name="17170A1", zone="1", seq_group="170"),
        DrawingRecord(member_name="17110A1", zone="1", seq_group="110"),
        DrawingRecord(member_name="17121A1", zone="1", seq_group="121"),
        DrawingRecord(member_name="17130A1", zone="1", seq_group="130"),
        DrawingRecord(member_name="17210A1", zone="2", seq_group="210"),
    ]
    assert [r.member_name for r in sort_records(records)] == [
        "17110A1", "17121A1", "17130A1", "17170A1",   # zone 1, ascending
        "17210A1",                                     # zone 2
    ]


def test_a_zone_is_split_into_its_sequences(tmp_path: Path):
    root = tmp_path / "Stairs at Zone 1 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17173R1", "17130B1"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    cat = build_register(root).categories[0]

    clusters = cat.band_groups("1")
    assert [key for key, _ in clusters] == [("seq", "130"), ("seq", "172"), ("seq", "173")]
    assert [len(recs) for _, recs in clusters] == [1, 2, 1]
    assert sum(len(r) for _, r in clusters) == cat.total


def test_serial_numbers_restart_in_every_sequence(tmp_path: Path):
    root = tmp_path / "Stairs at Zone 1 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17172C3", "17173R1", "17173R2"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    cat = build_register(root).categories[0]
    numbers = {key[1]: [r.seq_no for r in recs] for key, recs in cat.band_groups("1")}
    assert numbers == {"172": ["1", "2", "3"], "173": ["1", "2"]}


def test_a_package_with_no_sequences_still_builds(tmp_path: Path):
    """Plenty of projects do not encode a sequence. Nothing here may assume one."""
    root = tmp_path / "Bridge Deck - 2026-07-06"
    for mark in ["C-101", "C-102"]:
        make_drawing(root / "Assembly" / f"{mark}.pdf", mark, revision="A")
    cat = build_register(root).categories[0]
    assert cat.total == 2
    assert cat.zone_groups() == [("", cat.records)]


# ------------------------------------------------------------- the workbook


def _sheet_rows(path: Path, sheet: str) -> list[list]:
    wb = load_workbook(path)
    try:
        return [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


@pytest.fixture
def banded(tmp_path: Path) -> Path:
    root = tmp_path / "Stairs at Zone 1 and Zone 2 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17173R1", "17270S1", "17271X1", "17271X2"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    return write_register(build_register(root, settings=AppSettings()),
                          tmp_path / "banded.xlsx")


def test_the_sheet_bands_each_sequence_inside_its_zone(banded: Path):
    first = [str(r[0]) for r in _sheet_rows(banded, "Assembly") if r and r[0]]
    bands = [t for t in first if t.startswith(("ZONE", "    SEQ", "TOTAL"))]
    assert bands == [
        "ZONE 1   (Seq 172, 173)   -   3 drawing(s)",
        "    SEQ 172   -   2 drawing(s)",
        "    SEQ 173   -   1 drawing(s)",
        "ZONE 2   (Seq 270, 271)   -   3 drawing(s)",
        "    SEQ 270   -   1 drawing(s)",
        "    SEQ 271   -   2 drawing(s)",
        "TOTAL   -   6 drawing(s)",
    ]


def test_the_counts_on_the_bands_add_up(banded: Path):
    """S.No restarts at every band, so the counts are the only running total."""
    import re
    first = [str(r[0]) for r in _sheet_rows(banded, "Assembly") if r and r[0]]
    seq_counts = [int(re.search(r"(\d+) drawing", t).group(1))
                  for t in first if t.startswith("    SEQ")]
    total = [int(re.search(r"(\d+) drawing", t).group(1))
             for t in first if t.startswith("TOTAL")][0]
    assert sum(seq_counts) == total == 6


def test_the_summary_breaks_the_total_down_by_sequence(banded: Path):
    rows = _sheet_rows(banded, "Summary")
    start = next(i for i, r in enumerate(rows) if r and r[0] == "DRAWINGS BY BAND")

    # The table runs from below its heading to its own TOTAL row; the footnote
    # about amber rows sits further down the sheet.
    table = []
    for r in rows[start + 2:]:
        if not r or not r[0]:
            continue
        table.append(r)
        if r[0] == "TOTAL":
            break

    body = [r[:4] for r in table if r[0] != "TOTAL"]
    assert body == [
        ["1", "SEQ 172", "Assembly", 2],
        ["1", "SEQ 173", "Assembly", 1],
        ["2", "SEQ 270", "Assembly", 1],
        ["2", "SEQ 271", "Assembly", 2],
    ]
    total = next(r for r in table if r[0] == "TOTAL")
    assert total[3] == sum(r[3] for r in body) == 6


def test_the_totals_row_is_not_read_back_as_a_drawing(banded: Path):
    """A count sitting in the member column becomes a phantom member.

    It would be reported as a drawing with no model member on every validation
    and as added or removed on every issue comparison - the same way the
    repeated header row used to be.
    """
    loaded = read_register(banded)
    names = loaded.member_names()
    assert loaded.total == 6
    assert all("drawing(s)" not in n for n in names)
    assert "TOTAL" not in names
    assert sorted(names) == ["17172C1", "17172C2", "17173R1",
                             "17270S1", "17271X1", "17271X2"]


# ------------------------------------------------------ single-part types


@pytest.mark.parametrize("mark,type_code", [
    ("17a24", "A"),
    ("17ch104", "CH"),
    ("17hsp1", "HSP"),
    ("17HSP280", "HSP"),
])
def test_a_single_part_mark_yields_its_type(mark: str, type_code: str):
    from fabdoc.extract import parse_part_type
    assert parse_part_type(mark) == type_code


@pytest.mark.parametrize("mark", ["17172C172", "1710B84", "17130B103"])
def test_a_sequenced_mark_has_no_type(mark: str):
    """The two schemes are mutually exclusive, so a band is never ambiguous."""
    from fabdoc.extract import parse_part_type
    assert parse_part_type(mark) == ""


def test_bands_order_sequences_before_types():
    assert sorted([("type", "CH"), ("seq", "172"), ("type", "A"), ("seq", "10")],
                  key=sq.band_sort_key) == [
        ("seq", "10"), ("seq", "172"), ("type", "A"), ("type", "CH")]
    assert sq.band_label(("seq", "172")) == "SEQ 172"
    assert sq.band_label(("type", "ch")) == "TYPE CH"


def test_single_parts_band_by_type_not_by_sequence(tmp_path: Path):
    """A part is cut for an assembly, not erected in a sequence.

    Its mark carries no sequence at all, so without a type band three quarters
    of a real part sheet sat in one unnamed block below the table.
    """
    root = tmp_path / "Zone 1 Parts - 2026-07-22"
    for mark in ["17a24", "17a25", "17ch104", "17hsp1", "17hsp2", "17172C172"]:
        make_drawing(root / "Single Part Drawings" / f"{mark}  - Rev 0.pdf",
                     mark, revision=0)
    cat = build_register(root).categories[0]
    assert cat.name == "Part"

    # The sequenced one keeps its zone; the typed ones have none to keep.
    assert [b for b, _ in cat.band_groups("1")] == [("seq", "172")]
    assert [b for b, _ in cat.band_groups("")] == [
        ("type", "A"), ("type", "CH"), ("type", "HSP")]

    counts = {b: len(r) for b, r in cat.band_groups("")}
    assert counts == {("type", "A"): 2, ("type", "CH"): 1, ("type", "HSP"): 2}


def test_every_part_lands_in_exactly_one_band(tmp_path: Path):
    """The band counts are the only running total, so they must sum to the sheet."""
    root = tmp_path / "Zone 1 Parts - 2026-07-22"
    marks = ["17a24", "17ch104", "17ch11", "17hsp1", "17172C172", "17172R8"]
    for mark in marks:
        make_drawing(root / "Single Part Drawings" / f"{mark}  - Rev 0.pdf",
                     mark, revision=0)
    cat = build_register(root).categories[0]
    banded = sum(len(r) for z, _ in cat.zone_groups() for _, r in cat.band_groups(z))
    assert banded == cat.total == len(marks)
