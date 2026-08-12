"""Segregating a register by erection sequence.

The sequence inside a member mark is two fields: the leading digit is the zone,
the last two say what kind of steel it is. Those two digits group by their tens
digit - the 10s are perimeter, the 30s roof, the 70s miscellaneous and stair
steel - and the order those groups appear in is the order the steel is erected,
which is not the order the numbers sort in.
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


# --------------------------------------------------------------- the code


@pytest.mark.parametrize("seq,code", [
    ("172", "72"),      # zone 1, misc/stair
    ("270", "70"),      # zone 2, interior misc
    ("110", "10"),      # zone 1, anchor bolts
    ("341", "41"),
    # A two-digit sequence has no zone digit to strip. One package mixes both
    # widths, and "10" is perimeter steel in the same register as "130".
    ("10", "10"),
    ("11", "11"),
    ("", ""),
    ("abc", ""),
])
def test_the_code_is_the_last_two_digits(seq: str, code: str):
    assert sq.sequence_code(seq) == code


@pytest.mark.parametrize("seq,name", [
    ("110", "Perimeter Steel"),
    ("210", "Perimeter Steel"),     # same steel, different zone
    ("111", "Perimeter Steel"),
    ("130", "Roof Steel"),
    ("121", "Mezzanine Steel"),
    ("150", "Elevator Steel"),
    ("170", "Misc. / Stair Steel"),
    ("171", "Misc. / Stair Steel"),
])
def test_listed_codes_land_in_their_group(seq: str, name: str):
    assert sq.group_for(seq)[1] == name


@pytest.mark.parametrize("seq", ["172", "173", "179"])
def test_an_unlisted_code_follows_its_decade(seq: str):
    """72 and 73 are written down nowhere but are plainly 70s work.

    They are 56 of the sample package's 107 drawings. Falling back to the
    decade is what keeps them beside 70 and 71 instead of in a nameless heap.
    """
    rank, name = sq.group_for(seq)
    assert name == "Misc. / Stair Steel"
    assert rank == sq.group_for("170")[0]


def test_a_decade_nobody_listed_sorts_last_and_is_not_named():
    """Cooling tower steel (341) fits no group. Guessing a name would be worse."""
    rank, name = sq.group_for("341")
    assert rank == sq.UNKNOWN_RANK
    assert name == ""
    assert sq.label_for("341") == "SEQ 341"


def test_the_group_order_is_erection_order_not_numeric():
    """Mezzanine (20s) is erected after roof (30s).

    Sorting the sequence numbers would put mezzanine before roof and print the
    register in an order the shop cannot work to.
    """
    seqs = ["171", "170", "121", "130", "111", "150", "110", "131", "122"]
    assert sorted(seqs, key=sq.sort_key) == [
        "110", "111",          # 1. Perimeter
        "130", "131",          # 2. Roof
        "121", "122",          # 3. Mezzanine
        "150",                 # 4. Elevator
        "170", "171",          # 5. Misc. / Stair
    ]


def test_unlisted_decades_sort_after_every_named_group():
    assert sorted(["341", "170", "110"], key=sq.sort_key) == ["110", "170", "341"]


def test_the_table_is_editable_because_the_numbering_is_a_convention():
    """A different project can renumber its steel without a code change."""
    groups = [[7, "Stairs First"], [1, "Perimeter Last"]]
    assert sq.group_for("172", groups)[1] == "Stairs First"
    assert sorted(["110", "172"], key=lambda s: sq.sort_key(s, groups)) == ["172", "110"]


def test_a_malformed_table_row_does_not_raise():
    """Settings are hand-editable JSON, so half a row must not kill a run."""
    for broken in ([["oops", "Bad"]], [[]], [[None, None]], []):
        assert sq.group_for("172", broken) == (sq.UNKNOWN_RANK, "")


# ------------------------------------------------------------- the register


def test_records_sort_into_erection_order_within_a_zone():
    records = [
        DrawingRecord(member_name="17170A1", zone="1", seq_group="170"),
        DrawingRecord(member_name="17110A1", zone="1", seq_group="110"),
        DrawingRecord(member_name="17121A1", zone="1", seq_group="121"),
        DrawingRecord(member_name="17130A1", zone="1", seq_group="130"),
        DrawingRecord(member_name="17210A1", zone="2", seq_group="210"),
    ]
    assert [r.member_name for r in sort_records(records)] == [
        "17110A1", "17130A1", "17121A1", "17170A1",   # zone 1, erection order
        "17210A1",                                     # zone 2
    ]


def test_a_zone_is_split_into_its_sequences(tmp_path: Path):
    root = tmp_path / "Stairs at Zone 1 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17173R1", "17130B1"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    cat = build_register(root).categories[0]

    clusters = cat.sequence_groups("1")
    # Roof steel first, then the two misc./stair sequences in code order.
    assert [seq for seq, _ in clusters] == ["130", "172", "173"]
    assert [len(recs) for _, recs in clusters] == [1, 2, 1]
    assert sum(len(r) for _, r in clusters) == cat.total


def test_serial_numbers_restart_in_every_sequence(tmp_path: Path):
    root = tmp_path / "Stairs at Zone 1 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17172C3", "17173R1", "17173R2"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    cat = build_register(root).categories[0]
    numbers = {seq: [r.seq_no for r in recs] for seq, recs in cat.sequence_groups("1")}
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
    settings = AppSettings()
    return write_register(build_register(root, settings=settings),
                          tmp_path / "banded.xlsx",
                          sequence_groups=settings.sequence_groups)


def test_the_sheet_bands_each_sequence_inside_its_zone(banded: Path):
    first = [str(r[0]) for r in _sheet_rows(banded, "Assembly") if r and r[0]]
    bands = [t for t in first if t.startswith(("ZONE", "    SEQ", "TOTAL"))]
    assert bands == [
        "ZONE 1   (Seq 172, 173)   -   3 drawing(s)",
        "    SEQ 172   -   Misc. / Stair Steel   -   2 drawing(s)",
        "    SEQ 173   -   Misc. / Stair Steel   -   1 drawing(s)",
        "ZONE 2   (Seq 270, 271)   -   3 drawing(s)",
        "    SEQ 270   -   Misc. / Stair Steel   -   1 drawing(s)",
        "    SEQ 271   -   Misc. / Stair Steel   -   2 drawing(s)",
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
    start = next(i for i, r in enumerate(rows) if r and r[0] == "DRAWINGS BY SEQUENCE")

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
        ["1", "172", "Misc. / Stair Steel", 2],
        ["1", "173", "Misc. / Stair Steel", 1],
        ["2", "270", "Misc. / Stair Steel", 1],
        ["2", "271", "Misc. / Stair Steel", 2],
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
