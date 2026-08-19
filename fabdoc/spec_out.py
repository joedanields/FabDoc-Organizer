"""Write the OLD-vs-NEW part specification comparison to an Excel workbook.

One sheet per title-block value - Qty, Profile, Material, Length, Weight - and
a summary in front of them. Each sheet is the same shape: every part, what the
old issue said, what the new one says, and one word for what happened.

One word is the whole design. The reader is a detailer checking a revised set
against the one before it, and what they need from a row is whether to look at
it. "Increased" says that; a sentence reconstructing the two values does not say
it any better than the two columns already sitting beside it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import sequencing
from .excel_out import (_BORDER, _CENTER, _HEADER_FILL, _HEADER_FONT, _LABEL_FONT,
                        _LEFT, _OK_FILL, _TITLE_FILL, _TITLE_FONT, _VALUE_FONT,
                        _save_workbook)
from .spec_compare import (ADDED, CHANGED, DECREASED, FIELDS, INCREASED,
                           REMOVED, UNCHANGED, SpecComparison, SpecSheet)

# One colour per verdict, so a sheet can be read down the Change column without
# reading the words: up is green, down is orange, and a swapped profile or
# material is amber - a thing to check rather than a direction.
_VERDICT_FILL = {
    INCREASED: PatternFill("solid", fgColor="C6EFCE"),
    DECREASED: PatternFill("solid", fgColor="F8CBAD"),
    CHANGED: PatternFill("solid", fgColor="FFF2CC"),
    ADDED: PatternFill("solid", fgColor="DDEBF7"),
    REMOVED: PatternFill("solid", fgColor="D9D9D9"),
    UNCHANGED: _OK_FILL,
}
_VERDICT_FONT = {
    INCREASED: Font(bold=True, size=10, color="006100"),
    DECREASED: Font(bold=True, size=10, color="843C0C"),
    CHANGED: Font(bold=True, size=10, color="843C0C"),
    ADDED: Font(bold=True, size=10, color="1F4E79"),
    REMOVED: Font(bold=True, size=10, color="595959"),
    UNCHANGED: _VALUE_FONT,
}
_TOTAL_FONT = Font(bold=True, size=10)


def _title(ws: Worksheet, text: str, width: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    cell = ws.cell(row=1, column=1, value=text)
    cell.font = _TITLE_FONT
    cell.fill = _TITLE_FILL
    cell.alignment = _CENTER
    ws.row_dimensions[1].height = 22


def _headers(ws: Worksheet, names: list[str], row: int) -> None:
    for idx, name in enumerate(names, start=1):
        cell = ws.cell(row=row, column=idx, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER
    ws.row_dimensions[row].height = 18


def _write_summary(ws: Worksheet, result: SpecComparison) -> None:
    """Which of the five moved, and how much of each. The sheet to open first."""
    # The count headings are the verdict words themselves, so the colour under
    # each is looked up by the heading it sits below.
    columns = ["Value", "Changed", INCREASED, DECREASED, CHANGED, UNCHANGED]
    _title(ws, "PART SPECIFICATION - OLD vs NEW", len(columns))

    row = 3
    for label, value in (
        ("OLD issue", f"{result.old_label}   ({result.old_total} part drawings)"),
        ("NEW issue", f"{result.new_label}   ({result.new_total} part drawings)"),
        ("Parts in both", result.parts),
        ("Only in OLD", len(result.only_in_old) or ""),
        ("Only in NEW", len(result.only_in_new) or ""),
        ("Generated", datetime.now().strftime("%d-%b-%Y %H:%M")),
    ):
        if value == "":
            continue
        ws.cell(row=row, column=1, value=f"{label}:").font = _LABEL_FONT
        ws.cell(row=row, column=2, value=value).font = _VALUE_FONT
        row += 1

    row += 1
    verdict = ws.cell(
        row=row, column=1,
        value="Nothing moved: every part is the same in both issues."
        if result.is_identical else
        f"{result.moved} value(s) moved across the five sheets.",
    )
    verdict.font = Font(bold=True, size=12,
                        color="006100" if result.is_identical else "843C0C")
    row += 2

    _headers(ws, columns, row)
    row += 1
    for spec in FIELDS:
        sheet = result.sheets[spec.name]
        values = [spec.name, sheet.moved or "no changes",
                  sheet.count(INCREASED), sheet.count(DECREASED),
                  sheet.count(CHANGED), sheet.count(UNCHANGED)]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c_idx, value=value)
            cell.border = _BORDER
            cell.alignment = _LEFT if c_idx == 1 else _CENTER
            if c_idx == 1:
                cell.font = _TOTAL_FONT
            elif c_idx == 2:
                cell.fill = _VERDICT_FILL[CHANGED] if sheet.moved else _OK_FILL
                cell.font = _VERDICT_FONT[CHANGED] if sheet.moved else _VALUE_FONT
            elif c_idx >= 3 and value:
                # Only a count that is not zero is worth colouring; a sheet of
                # zeros should read as quiet, not as a wall of green.
                verdict_name = columns[c_idx - 1]
                cell.fill = _VERDICT_FILL[verdict_name]
                cell.font = _VERDICT_FONT[verdict_name]
        row += 1

    row = _write_one_sided(ws, result, row + 1, len(columns))

    row += 1
    note = ws.cell(
        row=row, column=1,
        value="Single part drawings only - a part is one piece cut to one "
              "length from one profile, and that row of five means something. "
              "The five sheets carry the parts whose value moved and nothing "
              "else. Which folder is OLD and which is NEW comes from the "
              "folder names, not from the order they were chosen.",
    )
    note.font = Font(italic=True, size=9, color="808080")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(columns))

    for idx, width in enumerate([14, 44, 11, 11, 11, 12], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_one_sided(ws: Worksheet, result: SpecComparison, row: int,
                     width: int) -> int:
    """The parts that are in one issue and not the other. Returns the next row.

    Once, here, rather than as a row on all five sheets: a part missing from the
    new issue is missing from its quantity, its profile, its material, its
    length and its weight, and saying so five times is saying it four times too
    many. It is not a change to any of them either - a revised set is usually a
    partial re-issue, and the parts it did not carry were not touched.
    """
    both = [(REMOVED, result.only_in_old), (ADDED, result.only_in_new)]
    if not any(rows for _verdict, rows in both):
        return row

    heading = ws.cell(row=row, column=1, value="PARTS IN ONLY ONE ISSUE")
    heading.font = _LABEL_FONT
    row += 1

    columns = ["Member Name", "Zone", "Sequence", "In"]
    _headers(ws, columns, row)
    row += 1
    for verdict, rows in both:
        for entry in rows:
            values = [entry.member_name, entry.zone or "-",
                      sequencing.band_label(entry.band) if entry.band[1] else "-",
                      verdict]
            for c_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=row, column=c_idx, value=value)
                cell.border = _BORDER
                cell.alignment = _LEFT if c_idx == 1 else _CENTER
                if c_idx == len(columns):
                    cell.fill = _VERDICT_FILL[verdict]
                    cell.font = _VERDICT_FONT[verdict]
            row += 1
    return row


def _write_field(ws: Worksheet, sheet: SpecSheet, result: SpecComparison) -> None:
    """One value, and only the parts whose value moved.

    Not every part: most of a re-issue comes back identical, and six hundred
    rows saying "No change" is not a report of what changed. A sheet with
    nothing on it says so in a line rather than leaving the reader to wonder
    whether it failed.
    """
    columns = ["Member Name", "Zone", "Sequence", "OLD", "NEW", "Change"]
    _title(ws, f"{sheet.field_name.upper()} - OLD vs NEW", len(columns))

    changes = sheet.changes
    note = ws.cell(
        row=2, column=1,
        value=f"{len(changes)} of {len(sheet.rows)} part(s) changed.  "
              f"{sheet.verdict.capitalize()}."
        if changes else
        f"NO CHANGES.  All {len(sheet.rows)} part(s) carry the same "
        f"{sheet.field_name.lower()} in both issues.")
    note.font = Font(bold=not changes, italic=bool(changes), size=10 if not changes else 9,
                     color="843C0C" if changes else "375623")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))

    if not changes:
        # Nothing to tabulate. The line above is the whole answer.
        for idx, width in enumerate([26, 8, 14, 20, 20, 13], start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width
        return

    _headers(ws, columns, 3)

    row = 4
    for entry in changes:
        values = [entry.member_name, entry.zone or "-",
                  sequencing.band_label(entry.band) if entry.band[1] else "-",
                  entry.old or "-", entry.new or "-", entry.verdict]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c_idx, value=value)
            cell.border = _BORDER
            cell.alignment = _LEFT if c_idx == 1 else _CENTER
            if c_idx == len(columns):
                cell.fill = _VERDICT_FILL[entry.verdict]
                cell.font = _VERDICT_FONT[entry.verdict]
            elif c_idx in (4, 5):
                # The two values are what the verdict is about, so they carry
                # its colour too - the eye lands on the pair, not on the word.
                cell.fill = _VERDICT_FILL[entry.verdict]
        row += 1

    ws.freeze_panes = "A4"
    if row > 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(columns))}{row - 1}"
    for idx, width in enumerate([26, 8, 14, 20, 20, 13], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def write_spec_report(result: SpecComparison, output_path: str | Path) -> Path:
    """Write the comparison workbook: a summary, then a sheet per value."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    wb.remove(wb.active)
    _write_summary(wb.create_sheet("Summary"), result)
    for spec in FIELDS:
        _write_field(wb.create_sheet(spec.name), result.sheets[spec.name], result)

    _save_workbook(wb, out)
    return out


def suggest_spec_name(result: SpecComparison) -> str:
    """Filename for the report, named after the two issues it compares."""
    stem = Path(result.new_folder).name or result.new_label or "Package"
    safe = "".join(ch for ch in stem if ch not in '<>:"/\\|?*')[:100].strip(" .")
    return f"{safe or 'Package'} - Part Specs (OLD vs NEW).xlsx"
