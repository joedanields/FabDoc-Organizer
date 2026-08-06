"""End-to-end and unit tests for the register pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from conftest import make_drawing

from fabdoc.categories import classify_folder_name, discover_categories, safe_sheet_name
from fabdoc.config import AppSettings, ExtractionProfile
from fabdoc.excel_out import write_register, write_validation_report
from fabdoc.extract import SOURCE_FILENAME, SOURCE_LARGEST, extract_drawing
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
    ("Assembly Drawings", "Erection"),
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


def test_non_numeric_sequences_sort_last_without_error():
    from fabdoc.extract import DrawingRecord
    records = [
        DrawingRecord(seq_no="A1", member_name="X"),
        DrawingRecord(seq_no="3", member_name="Y"),
    ]
    assert [r.seq_no for r in sort_records(records)] == ["3", "A1"]


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
    assert headers == ["Title", "Date", "S.No", "Member Name", "Revision No",
                       "Source File", "Notes"]
    wb.close()


def test_single_category_workbook_has_one_register_sheet(single_category_folder: Path,
                                                         tmp_path: Path):
    reg = build_register(single_category_folder)
    out = write_register(reg, tmp_path / "one.xlsx")
    wb = load_workbook(out)
    assert [s for s in wb.sheetnames if s != "Summary"] == ["Erection"]
    wb.close()


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
