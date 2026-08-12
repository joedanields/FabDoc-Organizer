"""Write the cumulative package tracker to an Excel workbook.

The per-issue register answers "what is in this delivery". This answers "what has
happened to this package", which is the question asked in a progress meeting:
which revision is each member on, what shipped when, and what is still held up.

Kept out of ``excel_out`` because it renders a different object (a chain, not a
register) and that module is already the larger of the two.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .categories import safe_sheet_name
from .excel_out import (_BORDER, _CENTER, _HEADER_FILL, _HEADER_FONT, _LABEL_FONT,
                        _LEFT, _OK_FILL, _REVIEW_FILL, _TITLE_FILL, _TITLE_FONT,
                        _VALUE_FONT, _save_workbook)
from .tracking import (STAGE_IFA, STAGE_IFF, PackageChain,
                       split_member_id)

_IFA_FILL = PatternFill("solid", fgColor="DDEBF7")    # blue: still in approval
_IFF_FILL = PatternFill("solid", fgColor="E2EFDA")    # green: released to shop
_HOLD_FILL = PatternFill("solid", fgColor="F8CBAD")   # orange: not shipped
_NEW_FILL = PatternFill("solid", fgColor="FFF2CC")    # amber: new this issue


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


def _write_summary(ws: Worksheet, chain: PackageChain) -> None:
    """The tracking table: one row per issue, in the order they were delivered."""
    columns = ["#", "Code", "Stage", "Round", "Issue Folder", "Date", "Drawings",
               "Added", "Revised", "Removed", "Released", "On Hold", "Status"]
    _title(ws, "PACKAGE TRACKER", len(columns))

    row = 3
    for label, value in (
        ("Project", chain.project),
        ("Approved baseline", chain.baseline_label),
        ("Issues tracked", len(chain.issues)),
        ("Still on hold", len(chain.outstanding)),
        ("Generated", datetime.now().strftime("%d-%b-%Y %H:%M")),
    ):
        if value == "" or value is None:
            continue
        ws.cell(row=row, column=1, value=f"{label}:").font = _LABEL_FONT
        ws.cell(row=row, column=2, value=value).font = _VALUE_FONT
        row += 1

    row += 1
    _headers(ws, columns, row)
    row += 1

    # steps[i] describes the move into issues[i + 1]; the first issue has none.
    steps = [None] + list(chain.steps)
    for idx, entry in enumerate(chain.issues, start=1):
        step = steps[idx - 1] if idx - 1 < len(steps) else None
        is_iff = entry.stage == STAGE_IFF
        values = [
            idx, entry.code, entry.stage, entry.round_no, entry.label,
            entry.date_text, entry.total,
            len(step.added) if step else entry.total,
            len(step.revised) if step else 0,
            len(step.removed) if step else 0,
            len(step.released) if step and is_iff else "",
            len(step.on_hold) if step and is_iff else "",
            step.verdict if step else "first issue",
        ]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c_idx, value=value)
            cell.border = _BORDER
            cell.alignment = _LEFT if c_idx in (5, 13) else _CENTER
            cell.fill = _IFF_FILL if is_iff else _IFA_FILL
        row += 1

    row += 1
    note = ws.cell(
        row=row, column=1,
        value="Members absent from an IFF release are on hold, not removed - "
              "fabrication ships the approved scope in slices. See the On Hold sheet "
              "for the reason recorded against each one.",
    )
    note.font = Font(italic=True, size=9, color="808080")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(columns))

    ws.freeze_panes = ws.cell(row=row - len(chain.issues) - 1, column=1)
    for idx, width in enumerate(
        [5, 10, 8, 8, 46, 14, 10, 8, 9, 10, 10, 9, 30], start=1
    ):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_history(ws: Worksheet, chain: PackageChain, category: str = "",
                   members: list[str] | None = None) -> None:
    """Member down the side, issue across the top, revision in the cell.

    This is the rev-by-rev view: one glance shows a member's whole life, when it
    entered the package, every revision it went through, and which release it
    shipped in.

    One sheet per drawing category, because an assembly and a single part are
    different deliverables that happen to share a mark - reading them in one
    list invites treating a cut part as a fabricated assembly.
    """
    labels = [e.label for e in chain.issues]
    columns = ["Member Name", "Zone"] + [e.code for e in chain.issues]
    heading = "MEMBER HISTORY - REVISION BY ISSUE"
    if category:
        heading += f"   ({category})"
    _title(ws, heading, max(len(columns), 3))

    _headers(ws, columns, 3)
    held = {h.ident for h in chain.outstanding}

    row = 4
    for member in (members if members is not None else list(chain.history)):
        per_issue = chain.history.get(member, {})
        zone = ""
        name = split_member_id(member)[1]
        for entry in chain.issues:
            info = entry.members.get(member)
            if info:
                name = info.get("name") or name
                if info.get("zone"):
                    zone = info["zone"]
                    break
        cells: list[object] = [name, zone]
        for label in labels:
            cells.append(per_issue.get(label, ""))
        for c_idx, value in enumerate(cells, start=1):
            cell = ws.cell(row=row, column=c_idx, value=value)
            cell.border = _BORDER
            cell.alignment = _LEFT if c_idx == 1 else _CENTER
            if c_idx > 2:
                entry = chain.issues[c_idx - 3]
                if value:
                    cell.fill = _IFF_FILL if entry.stage == STAGE_IFF else _IFA_FILL
                elif entry.stage == STAGE_IFF and member in held:
                    cell.fill = _HOLD_FILL
        row += 1

    ws.freeze_panes = ws.cell(row=4, column=3)
    if row > 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(columns))}{row - 1}"
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 8
    for idx in range(3, len(columns) + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 11


def _write_holds(ws: Worksheet, chain: PackageChain) -> None:
    """Approved members that have not shipped, and why."""
    columns = ["Member Name", "Zone", "Approved Rev", "On Hold Since",
               "Released In", "Reason"]
    _title(ws, "ON HOLD - APPROVED BUT NOT YET RELEASED", len(columns))
    _headers(ws, columns, 3)

    row = 4
    for hold in chain.holds.values():
        shipped = bool(hold.released_in)
        values = [hold.member_name, hold.zone, hold.revision, hold.held_since,
                  hold.released_in or "", hold.reason or ""]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c_idx, value=value)
            cell.border = _BORDER
            cell.alignment = _LEFT if c_idx in (1, 6) else _CENTER
            if shipped:
                cell.fill = _OK_FILL
            elif c_idx == 6 and not hold.reason:
                cell.fill = _REVIEW_FILL   # amber: nobody has said why yet
            else:
                cell.fill = _HOLD_FILL
        row += 1

    ws.freeze_panes = "A4"
    if row > 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(columns))}{row - 1}"
    for idx, width in enumerate([24, 8, 14, 30, 30, 52], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_changes(ws: Worksheet, chain: PackageChain) -> None:
    """Every change, issue by issue - the comparison log, flattened."""
    columns = ["Issue", "Code", "Stage", "Change", "Category", "Member Name",
               "From Rev", "To Rev"]
    _title(ws, "CHANGE LOG", len(columns))
    _headers(ws, columns, 3)

    row = 4
    for step in chain.steps:
        groups: list[tuple[str, list, PatternFill]] = [
            ("Added", [(m, "", "") for m in step.added], _NEW_FILL),
            ("Revised", list(step.revised), _REVIEW_FILL),
            ("Removed", [(m, "", "") for m in step.removed], _HOLD_FILL),
            ("Released", [(m, "", "") for m in step.released], _IFF_FILL),
            ("On Hold", [(h.ident, h.revision, "") for h in step.on_hold], _HOLD_FILL),
        ]
        for change, items, fill in groups:
            for item in items:
                ident, from_rev, to_rev = item
                # The lists carry identities, so the same mark issued as both an
                # assembly and a part reads as the two separate rows it is.
                category, name = split_member_id(ident)
                values = [step.new_label, step.code, step.stage, change,
                          category, name, from_rev, to_rev]
                for c_idx, value in enumerate(values, start=1):
                    cell = ws.cell(row=row, column=c_idx, value=value)
                    cell.border = _BORDER
                    cell.alignment = _LEFT if c_idx in (1, 6) else _CENTER
                    cell.fill = fill
                row += 1

    ws.freeze_panes = "A4"
    if row > 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(columns))}{row - 1}"
    for idx, width in enumerate([46, 10, 8, 12, 14, 24, 11, 11], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _by_category(chain: PackageChain) -> list[tuple[str, list[str]]]:
    """Members grouped by drawing category, in the order they were first seen."""
    grouped: "OrderedDict[str, list[str]]" = OrderedDict()
    for ident in chain.history:
        grouped.setdefault(split_member_id(ident)[0], []).append(ident)
    if not grouped:
        return [("", [])]
    return list(grouped.items())


def write_tracker(chain: PackageChain, output_path: str | Path) -> Path:
    """Write the cumulative tracking workbook for one package."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    wb.remove(wb.active)

    used: set[str] = set()
    _write_summary(wb.create_sheet(safe_sheet_name("Tracker", used)), chain)

    # One history sheet per drawing category, in the same workbook. A package
    # that issues assembly and single-part drawings is one package with one
    # chain - splitting it across two tracker files would break the chaining
    # that the tracker exists for.
    for category, members in _by_category(chain):
        title = f"History - {category}" if category else "Member History"
        _write_history(wb.create_sheet(safe_sheet_name(title, used)), chain,
                       category, members)

    _write_changes(wb.create_sheet(safe_sheet_name("Change Log", used)), chain)
    if chain.holds:
        _write_holds(wb.create_sheet(safe_sheet_name("On Hold", used)), chain)

    _save_workbook(wb, out)
    return out


def suggest_tracker_name(chain: PackageChain) -> str:
    """Filename for the tracker, named after the package it follows."""
    stem = (chain.project or "Package").strip()
    safe = "".join(ch for ch in stem if ch not in '<>:"/\\|?*')
    safe = safe[:110].strip(" .")
    return f"{safe or 'Package'} - Tracker.xlsx"
