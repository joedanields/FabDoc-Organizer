"""End-to-end and unit tests for the register pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import fitz
import pytest
from openpyxl import load_workbook

from conftest import make_drawing

from fabdoc.categories import classify_folder_name, discover_categories, safe_sheet_name
from fabdoc.config import AppSettings, ExtractionProfile
from fabdoc.compare import compare_issues
from fabdoc.excel_out import (suggest_register_name, write_comparison_report,
                              write_register, write_validation_report)
from fabdoc.extract import (SOURCE_FILENAME, SOURCE_LARGEST, extract_drawing,
                            parse_member_mark)
from fabdoc.folder_meta import parse_folder_name
from fabdoc.memberlist import read_member_list
from fabdoc.register import build_register, sort_records
from fabdoc.register_io import read_register
from fabdoc.validate import normalise, validate


# --------------------------------------------------------------- folder names


def test_parses_title_zone_package_and_date():
    meta = parse_folder_name("Skyline Tower - Zone B - PKG-03 - 12-05-2024")
    assert meta.title == "Skyline Tower"
    assert meta.zone == "B"
    assert meta.package == "03"
    assert meta.issue_date == date(2024, 5, 12)


def test_iso_date_is_not_read_as_day_first():
    meta = parse_folder_name("Bridge Deck_Zone 4_2024-11-02")
    assert meta.issue_date == date(2024, 11, 2)
    assert meta.zone == "4"


def test_day_first_flag_switches_ambiguous_dates():
    # 05-03-2024 is 5 March day-first, 3 May month-first.
    assert parse_folder_name("Job - 05-03-2024", day_first=True).issue_date == date(2024, 3, 5)
    assert parse_folder_name("Job - 05-03-2024", day_first=False).issue_date == date(2024, 5, 3)


def test_impossible_day_first_date_falls_back():
    # 25 cannot be a month, so this must resolve as 25 December.
    assert parse_folder_name("Job - 12-25-2024").issue_date == date(2024, 12, 25)


def test_month_name_dates():
    assert parse_folder_name("Tower A - 09-Jan-2025").issue_date == date(2025, 1, 9)


def test_folder_without_metadata_becomes_the_title():
    meta = parse_folder_name("Random Package")
    assert meta.title == "Random Package"
    assert meta.issue_date is None


# ---------------------------------------------------------------- categories


@pytest.mark.parametrize("folder,expected", [
    ("Structural Drawings", "Structural"),
    ("erection", "Erection"),
    ("Part Drawings", "Part"),
    ("Single Part Drawings", "Part"),
    # An assembly drawing details one fabricated assembly for the shop; an
    # erection drawing shows where assemblies go on site. Not the same thing.
    ("Assembly Drawings", "Assembly"),
    ("Assembly", "Assembly"),
    ("Shop Drawings", "Assembly"),
    ("Random Folder", None),
])
def test_category_classification(folder, expected):
    assert classify_folder_name(folder)[0] == expected


def test_discovers_three_categories_in_order(project_folder: Path):
    cats = discover_categories(project_folder)
    assert [c.name for c in cats] == ["Structural", "Erection", "Part"]
    assert [c.count for c in cats] == [3, 2, 2]


def test_single_category_yields_one_sheet(single_category_folder: Path):
    cats = discover_categories(single_category_folder)
    assert len(cats) == 1 and cats[0].name == "Erection"


def test_unrecognised_folder_is_kept_under_its_own_name(tmp_path: Path):
    root = tmp_path / "Job X"
    make_drawing(root / "Misc Sketches" / "1_M1.pdf", "M1", seq=1)
    cats = discover_categories(root)
    assert len(cats) == 1
    assert cats[0].name == "Misc Sketches" and cats[0].is_recognised is False


def test_loose_pdfs_become_a_drawings_category(tmp_path: Path):
    root = tmp_path / "Flat Package"
    make_drawing(root / "1_A1.pdf", "A1", seq=1)
    cats = discover_categories(root)
    assert [c.name for c in cats] == ["Drawings"]


def test_sheet_names_are_deduplicated_and_clipped():
    used: set[str] = set()
    assert safe_sheet_name("Structural", used) == "Structural"
    assert safe_sheet_name("Structural", used) == "Structural (2)"
    assert len(safe_sheet_name("x" * 60)) == 31
    assert "/" not in safe_sheet_name("Part/Piece")


# ---------------------------------------------------------------- extraction


def test_extracts_labelled_fields(tmp_path: Path):
    pdf = make_drawing(tmp_path / "001_C-101.pdf", "C-101", revision=2, seq=7)
    rec = extract_drawing(pdf)
    assert rec.member_name == "C-101"
    assert rec.revision == "2"
    assert rec.seq_no == "7"
    assert rec.ok and not rec.needs_review


def test_unlabelled_mark_falls_back_to_largest_text(tmp_path: Path):
    pdf = make_drawing(tmp_path / "drawing.pdf", "B-77", labelled=False, big_mark=True)
    rec = extract_drawing(pdf)
    assert rec.member_name == "B-77"
    assert rec.member_source == SOURCE_LARGEST


def test_falls_back_to_filename_when_page_has_nothing(tmp_path: Path):
    pdf = make_drawing(tmp_path / "012_G-5_REV3.pdf", "G-5", blank=True)
    rec = extract_drawing(pdf)
    assert rec.member_name == "G-5"
    assert rec.revision == "3"
    assert rec.seq_no == "12"
    assert rec.member_source == SOURCE_FILENAME
    assert rec.needs_review  # filename-sourced rows must be flagged


def test_missing_revision_defaults_and_is_noted(tmp_path: Path):
    pdf = make_drawing(tmp_path / "no_rev.pdf", "K9", labelled=False, big_mark=True)
    rec = extract_drawing(pdf)
    assert rec.revision == "0"
    assert "revision defaulted" in rec.note_text


def test_corrupt_pdf_is_reported_not_raised(tmp_path: Path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    rec = extract_drawing(bad)
    assert rec.error and rec.needs_review


@pytest.mark.parametrize("label", [
    "ASSEMBLY MARK", "MEMBER NAME", "PIECE MARK", "MARK", "ASSEMBLY No",
    "SHIPPING MARK", "MEMBER ID", "PART POSITION", "ASSEMBLY REF",
])
def test_common_title_block_label_wordings(tmp_path: Path, label: str):
    """An unrecognised qualifier must not be captured as the mark.

    "MEMBER ID : B-101" previously yielded "ID", because the qualifier group
    failed to match and the capture fell onto the next word.
    """
    pdf = tmp_path / f"{label.replace(' ', '_')}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((520, 430), f"{label} : B-101", fontsize=11)
    doc.save(pdf)
    doc.close()
    assert extract_drawing(pdf).member_name == "B-101"


@pytest.mark.parametrize("mark", [
    "B-101", "1001", "ASSY-12A", "B/101", "C101A", "EB-1", "P.301",
    "BM_204", "12", "SC-1001-A", "COL-1",
])
def test_real_world_mark_shapes(tmp_path: Path, mark: str):
    pdf = make_drawing(tmp_path / f"{mark.replace('/', '~')}.pdf", mark, revision=1, seq=1)
    assert extract_drawing(pdf).member_name == mark.upper()


def test_stopwords_are_never_taken_as_member_names(tmp_path: Path):
    pdf = make_drawing(tmp_path / "SCALE.pdf", "X1", blank=True)
    rec = extract_drawing(pdf)
    assert rec.member_name != "SCALE"


def test_bad_user_regex_does_not_crash_extraction(tmp_path: Path):
    pdf = make_drawing(tmp_path / "001_C-101.pdf", "C-101", revision=1, seq=1)
    profile = ExtractionProfile(member_patterns=["([unclosed"] + ExtractionProfile().member_patterns)
    rec = extract_drawing(pdf, profile=profile)
    assert rec.member_name == "C-101"


# ------------------------------------------------------------------ register


def test_build_register_covers_every_category(project_folder: Path):
    reg = build_register(project_folder)
    assert [c.name for c in reg.categories] == ["Structural", "Erection", "Part"]
    assert reg.total == 7
    assert reg.review_count == 0
    assert reg.meta.title == "Skyline Tower"
    assert reg.meta.zone == "B"


def test_rows_are_sorted_by_sequence_number():
    from fabdoc.extract import DrawingRecord
    records = [DrawingRecord(seq_no=s, member_name=f"M{s}") for s in ["10", "2", "1"]]
    assert [r.seq_no for r in sort_records(records)] == ["1", "2", "10"]


def test_records_sort_by_zone_then_sequence_then_mark():
    from fabdoc.extract import DrawingRecord
    records = [
        DrawingRecord(member_name="17271X9", zone="2", seq_group="271"),
        DrawingRecord(member_name="17172C10", zone="1", seq_group="172"),
        DrawingRecord(member_name="17172C2", zone="1", seq_group="172"),
        DrawingRecord(member_name="17173R1", zone="1", seq_group="173"),
    ]
    assert [r.member_name for r in sort_records(records)] == [
        "17172C2", "17172C10", "17173R1", "17271X9",
    ]


def test_records_without_a_zone_sort_last_without_error():
    from fabdoc.extract import DrawingRecord
    records = [
        DrawingRecord(member_name="LOOSE"),
        DrawingRecord(member_name="17172C1", zone="1", seq_group="172"),
    ]
    assert [r.member_name for r in sort_records(records)] == ["17172C1", "LOOSE"]


def test_category_selection_limits_the_run(project_folder: Path):
    reg = build_register(project_folder, selected_categories=["Erection"])
    assert len(reg.categories) == 1 and reg.total == 2


def test_progress_reports_every_file(project_folder: Path):
    seen: list[tuple[int, int]] = []
    build_register(project_folder, progress=lambda d, t, _l: seen.append((d, t)))
    assert seen[0] == (1, 7) and seen[-1] == (7, 7)


def test_cancellation_stops_the_run(project_folder: Path):
    reg = build_register(project_folder, should_cancel=lambda: True)
    assert reg.total == 0


# --------------------------------------------------------------------- excel


def test_workbook_has_a_sheet_per_category(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    out = write_register(reg, tmp_path / "register.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Structural", "Erection", "Part"]

    ws = wb["Structural"]
    headers = None
    for row in ws.iter_rows(values_only=True):
        if row and "Member Name" in [str(c) for c in row if c]:
            headers = [c for c in row if c]
            break
    # Title and date belong in the header band above, stated once, not
    # repeated on every row of the deliverable.
    assert headers == ["S.No", "Member Name", "Revision No"]
    wb.close()


def test_source_columns_can_be_switched_back_on(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    out = write_register(reg, tmp_path / "traced.xlsx", include_source=True)
    wb = load_workbook(out)
    ws = wb["Structural"]
    headers = next(([c for c in row if c] for row in ws.iter_rows(values_only=True)
                    if row and "Member Name" in [str(c) for c in row if c]), None)
    assert headers == ["S.No", "Member Name", "Revision No", "Source File", "Notes"]
    wb.close()


def test_single_category_workbook_has_one_register_sheet(single_category_folder: Path,
                                                         tmp_path: Path):
    reg = build_register(single_category_folder)
    out = write_register(reg, tmp_path / "one.xlsx")
    wb = load_workbook(out)
    assert [s for s in wb.sheetnames if s != "Summary"] == ["Erection"]
    wb.close()


def test_suggested_name_matches_the_source_folder(project_folder: Path):
    """The register is filed next to its issue, so the names must agree."""
    reg = build_register(project_folder)
    assert suggest_register_name(reg) == f"{project_folder.name}.xlsx"


def test_suggested_name_falls_back_when_the_folder_has_no_name(project_folder: Path):
    reg = build_register(project_folder)
    reg.project_folder = Path("D:\\")  # a drive root has no name of its own
    assert suggest_register_name(reg).endswith(".xlsx")
    assert "/" not in suggest_register_name(reg)


def test_register_round_trips_through_excel(project_folder: Path, tmp_path: Path):
    original = build_register(project_folder)
    out = write_register(original, tmp_path / "register.xlsx")
    reloaded = read_register(out)
    assert reloaded.total == original.total
    assert set(reloaded.member_names()) == set(original.member_names())
    assert reloaded.meta.title == "Skyline Tower"


# ---------------------------------------------------------------- validation


def _member_file(tmp_path: Path, names: list[str], name: str = "members.csv") -> Path:
    path = tmp_path / name
    path.write_text("Assembly Mark\n" + "\n".join(names), encoding="utf-8")
    return path


def test_clean_comparison_passes(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    members = _member_file(tmp_path, ["C-101", "C-102", "C-110", "E-201", "E-202",
                                      "P-301", "P-302"])
    result = validate(reg, read_member_list(members))
    assert result.is_clean
    assert len(result.matched) == 7
    assert result.verdict.startswith("PASS")


def test_detects_members_missing_from_drawings(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    members = _member_file(tmp_path, ["C-101", "C-102", "C-110", "E-201", "E-202",
                                      "P-301", "P-302", "C-999"])
    result = validate(reg, read_member_list(members))
    assert result.missing_in_drawings == ["C-999"]
    assert not result.is_clean


def test_detects_drawings_absent_from_the_model(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    members = _member_file(tmp_path, ["C-101", "C-102"])
    result = validate(reg, read_member_list(members))
    assert set(result.extra_in_drawings) == {"C-110", "E-201", "E-202", "P-301", "P-302"}


def test_category_filter_narrows_the_comparison(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    members = _member_file(tmp_path, ["E-201", "E-202"])
    result = validate(reg, read_member_list(members), categories=["Erection"])
    assert result.is_clean


def test_duplicate_drawings_are_flagged(tmp_path: Path):
    root = tmp_path / "Dupes - 2024-01-01"
    make_drawing(root / "Part" / "1_D1.pdf", "D1", seq=1)
    make_drawing(root / "Part" / "2_D1.pdf", "D1", seq=2)
    reg = build_register(root)
    result = validate(reg, read_member_list(_member_file(tmp_path, ["D1"])))
    assert "D1" in result.duplicates_in_drawings
    assert len(result.duplicates_in_drawings["D1"]) == 2
    assert result.is_clean  # a duplicate is a warning, not a mismatch


def test_normalisation_options():
    settings = AppSettings()
    assert normalise(" c-101 ", settings) == "C-101"
    settings.compare_strip_leading_zeros = True
    assert normalise("B007", settings) == normalise("B7", settings)


def test_case_sensitive_comparison_can_be_enforced(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    settings = AppSettings()
    settings.compare_case_insensitive = False
    members = _member_file(tmp_path, ["c-101"])
    result = validate(reg, read_member_list(members), settings=settings)
    assert "c-101" in result.missing_in_drawings


def test_validation_report_sheets(project_folder: Path, tmp_path: Path):
    reg = build_register(project_folder)
    members = _member_file(tmp_path, ["C-101", "ZZ-1"])
    result = validate(reg, read_member_list(members))
    out = write_validation_report(result, tmp_path / "report.xlsx")
    wb = load_workbook(out)
    assert "Summary" in wb.sheetnames
    assert "Missing in Drawings" in wb.sheetnames
    assert "Not in Model" in wb.sheetnames
    assert wb["Missing in Drawings"]["A2"].value == "ZZ-1"
    wb.close()


# --------------------------------------------------------------- member lists


def test_reads_csv_and_picks_the_member_column(tmp_path: Path):
    path = tmp_path / "model.csv"
    path.write_text("Qty,Assembly Mark,Weight\n1,C-101,250\n1,C-102,260\n", encoding="utf-8")
    result = read_member_list(path)
    assert result.column_name == "Assembly Mark"
    assert result.members == ["C-101", "C-102"]


def test_reads_plain_text_one_per_line(tmp_path: Path):
    path = tmp_path / "model.txt"
    path.write_text("C-101\nC-102\n\nC-103\n", encoding="utf-8")
    assert read_member_list(path).members == ["C-101", "C-102", "C-103"]


def test_reads_xlsx_export(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Phase", "Assembly Position", "Weight"])
    ws.append([1, "E-201", 120])
    ws.append([1, "E-202", 130])
    path = tmp_path / "model.xlsx"
    wb.save(path)
    result = read_member_list(path)
    assert result.column_name == "Assembly Position"
    assert result.members == ["E-201", "E-202"]


def test_explicit_column_override(tmp_path: Path):
    path = tmp_path / "model.csv"
    path.write_text("Mark,Alt Mark\nA1,B1\n", encoding="utf-8")
    assert read_member_list(path, column="Alt Mark").members == ["B1"]


def test_numeric_marks_are_not_lost_to_float_formatting(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Mark"])
    ws.append([101])
    path = tmp_path / "numeric.xlsx"
    wb.save(path)
    assert read_member_list(path).members == ["101"]


def test_legacy_xls_is_rejected_with_guidance(tmp_path: Path):
    path = tmp_path / "old.xls"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="xlsx"):
        read_member_list(path)


# ------------------------------------------------------- zones and sequences


def test_multiple_zones_are_all_captured():
    meta = parse_folder_name(
        "25. 2026-07-06  Stairs at Zone 1 and Zone 2 for Re Approval (Seqs 172,173,270,271)"
    )
    assert meta.zones == ["1", "2"]
    assert meta.zone == "1, 2"
    assert meta.issue_no == "25"
    assert meta.sequences == ["172", "173", "270", "271"]
    assert meta.issue_date == date(2026, 7, 6)


def test_zone_tokens_are_not_cut_out_of_the_title():
    """Excising the zones left a mangled "Stairs at  and Zone 2"."""
    meta = parse_folder_name(
        "25. 2026-07-06  Stairs at Zone 1 and Zone 2 for Re Approval (Seqs 172,173,270,271)"
    )
    assert meta.title == "Stairs at Zone 1 and Zone 2 for Re Approval"


@pytest.mark.parametrize("mark,job,seq,zone", [
    ("17172C172", "17", "172", "1"),
    ("17173R44", "17", "173", "1"),
    ("17270S12", "17", "270", "2"),
    ("17271X211", "17", "271", "2"),
    ("17471C10", "17", "471", "4"),
    # A package mixes sequence widths: "17120B163" is seq 120 and "1710B84" is
    # seq 10. Pinned at 3 digits the 2-digit marks got no zone at all and were
    # clustered into a second unlabelled table - 245 rows of a 968-row register.
    ("17120B163", "17", "120", "1"),
    ("1710B84", "17", "10", "1"),
    ("1710B100", "17", "10", "1"),
    ("1720S5", "17", "20", "2"),
])
def test_sequence_and_zone_derived_from_member_mark(mark, job, seq, zone):
    assert parse_member_mark(mark) == (job, seq, zone)


def test_a_three_digit_sequence_still_wins_over_a_two_digit_reading():
    """The quantifier is greedy, so 17172C172 must not read as seq 17."""
    assert parse_member_mark("17172C172")[1] == "172"
    assert parse_member_mark("17120B163")[1] == "120"


def test_marks_that_do_not_encode_a_sequence_are_not_forced():
    assert parse_member_mark("C-101") == ("", "", "")
    assert parse_member_mark("") == ("", "", "")


def test_serial_number_restarts_within_each_sequence(tmp_path: Path):
    """S.No counts within its own band, not across the zone.

    Each band is a separate slice of work, so "the third drawing of sequence
    172" is the number worth printing. The band headings carry the counts.
    """
    root = tmp_path / "Stairs at Zone 1 and Zone 2 - 2026-07-06"
    for mark in ["17172C1", "17172C2", "17173R1", "17270S1", "17271X1"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev B.pdf", mark, revision="B")
    reg = build_register(root)
    cat = reg.categories[0]
    groups = cat.zone_groups()
    assert [z for z, _ in groups] == ["1", "2"]
    #                        seq 172   seq 173  seq 270  seq 271
    assert [r.seq_no for _, recs in groups for r in recs] == ["1", "2", "1", "1", "1"]
    assert cat.sequences_for("1") == ["172", "173"]
    assert reg.meta.zones == ["1", "2"]


def test_workbook_writes_a_banded_header_per_zone(tmp_path: Path):
    root = tmp_path / "Stairs at Zone 1 and Zone 2 - 2026-07-06"
    for mark in ["17172C1", "17271X1"]:
        make_drawing(root / "Assembly" / f"{mark}  - Rev B.pdf", mark, revision="B")
    out = write_register(build_register(root), tmp_path / "zoned.xlsx")
    wb = load_workbook(out)
    ws = wb["Assembly"]
    bands = [str(r[0]) for r in ws.iter_rows(values_only=True)
             if r and r[0] and str(r[0]).startswith("ZONE")]
    assert len(bands) == 2
    assert bands[0].startswith("ZONE 1") and "Seq 172" in bands[0]
    assert bands[1].startswith("ZONE 2") and "Seq 271" in bands[1]
    wb.close()


# ------------------------------------------------------- issue vs issue diff


def _issue(tmp_path: Path, name: str, marks: dict[str, str]) -> Path:
    root = tmp_path / name
    for mark, rev in marks.items():
        make_drawing(root / "Assembly" / f"{mark}  - Rev {rev}.pdf", mark, revision=rev)
    return root


def test_compare_issues_reports_added_and_removed(tmp_path: Path):
    old = _issue(tmp_path, "15. 2026-06-26 Stairs at Zone 1 - Approval",
                 {"17172C1": "A", "17172C2": "A", "17172C3": "A"})
    new = _issue(tmp_path, "25. 2026-07-06 Stairs at Zone 1 - Re Approval",
                 {"17172C1": "A", "17172C2": "B", "17172C9": "A"})
    result = compare_issues(build_register(old), build_register(new))
    assert [d.member_name for d in result.added] == ["17172C9"]
    assert [d.member_name for d in result.removed] == ["17172C3"]
    assert [d.member_name for d in result.revision_changed] == ["17172C2"]
    assert [d.member_name for d in result.unchanged] == ["17172C1"]
    assert not result.is_identical


def test_compare_issues_carries_old_and_new_revisions(tmp_path: Path):
    old = _issue(tmp_path, "old pkg 2026-06-26", {"17172C1": "A"})
    new = _issue(tmp_path, "new pkg 2026-07-06", {"17172C1": "B"})
    d = compare_issues(build_register(old), build_register(new)).revision_changed[0]
    assert (d.old_revision, d.new_revision) == ("A", "B")
    assert d.zone == "1"


def test_identical_issues_compare_clean(tmp_path: Path):
    old = _issue(tmp_path, "old pkg 2026-06-26", {"17172C1": "A", "17271X1": "A"})
    new = _issue(tmp_path, "new pkg 2026-07-06", {"17172C1": "A", "17271X1": "A"})
    result = compare_issues(build_register(old), build_register(new))
    assert result.is_identical
    assert result.verdict.startswith("IDENTICAL")
    assert len(result.unchanged) == 2


def test_comparison_report_sheets(tmp_path: Path):
    old = _issue(tmp_path, "old pkg 2026-06-26", {"17172C1": "A", "17172C3": "A"})
    new = _issue(tmp_path, "new pkg 2026-07-06", {"17172C1": "B", "17172C9": "A"})
    result = compare_issues(build_register(old), build_register(new))
    out = write_comparison_report(result, tmp_path / "diff.xlsx")
    wb = load_workbook(out)
    assert "Summary" in wb.sheetnames
    assert "Added" in wb.sheetnames and "Removed" in wb.sheetnames
    assert wb["Added"]["A2"].value == "17172C9"
    assert wb["Removed"]["A2"].value == "17172C3"
    wb.close()
