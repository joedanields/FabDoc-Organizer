"""Command-line interface.

Useful for batch runs and for calibrating extraction patterns against a real
drawing before committing them to a profile.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __app_name__, __version__
from .categories import discover_categories
from .config import load_settings, save_settings
from .excel_out import suggest_register_name, write_register, write_validation_report
from .extract import dump_text, extract_drawing
from .folder_meta import parse_folder
from .memberlist import read_member_list
from .register import build_register
from .register_io import read_register
from .validate import validate


def _progress(done: int, total: int, label: str) -> None:
    pct = (done / total * 100) if total else 0.0
    sys.stderr.write(f"\r  [{done}/{total}] {pct:5.1f}%  {label[:60]:<60}")
    sys.stderr.flush()
    if done == total:
        sys.stderr.write("\n")


def cmd_scan(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    folder = Path(args.folder)
    meta = parse_folder(folder, day_first=settings.day_first_dates)

    print(f"Folder:  {folder}")
    print(f"Title:   {meta.title}")
    print(f"Date:    {meta.date_display or '(not found)'}")
    print(f"Zone:    {meta.zone or '(not found)'}")
    print(f"Package: {meta.package or '(not found)'}")
    print()

    cats = discover_categories(folder, aliases=settings.category_aliases,
                               order=settings.category_order)
    if not cats:
        print("No drawing categories with PDFs found.")
        return 1
    print(f"{len(cats)} drawing categor{'y' if len(cats) == 1 else 'ies'} found:")
    for cat in cats:
        tag = "" if cat.is_recognised else "  (unrecognised name - used as-is)"
        print(f"  {cat.name:<20} {cat.count:>6} PDF(s)   <- {cat.folder.name}{tag}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    folder = Path(args.folder)

    print(f"Scanning {folder} ...")
    register = build_register(
        folder, settings=settings,
        selected_categories=args.categories,
        progress=None if args.quiet else _progress,
    )
    if not register.categories:
        print("No drawings found.", file=sys.stderr)
        return 1

    out = Path(args.output) if args.output else folder / suggest_register_name(register)
    write_register(register, out, include_source=settings.include_source_column)

    print()
    for cat in register.categories:
        flag = f"  ({cat.review_count} need review)" if cat.review_count else ""
        print(f"  {cat.name:<20} {cat.total:>6} row(s){flag}")
    print(f"\nTotal: {register.total} drawing(s)")
    if register.review_count:
        print(f"{register.review_count} row(s) flagged for review (highlighted in the workbook).")
    print(f"Register written to: {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)

    source = Path(args.register)
    if source.is_dir():
        print(f"Scanning {source} ...")
        register = build_register(source, settings=settings,
                                  progress=None if args.quiet else _progress)
    else:
        register = read_register(source)

    members = read_member_list(args.members, column=args.column, sheet=args.sheet)
    print(f"\nModel list: {members.count} member(s) from column "
          f"'{members.column_name}' of {Path(members.source).name}")

    result = validate(register, members, settings=settings, categories=args.categories)

    print()
    for line in result.summary_lines():
        print(line)

    if args.output:
        out = Path(args.output)
    else:
        base = source if source.is_dir() else source.parent
        out = base / "Member Validation Report.xlsx"
    write_validation_report(result, out)
    print(f"\nValidation report written to: {out}")

    return 0 if result.is_clean or not args.strict else 2


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Show what the parser sees in a drawing, then what it extracts."""
    settings = load_settings(args.settings)
    pdf = Path(args.pdf)

    data = dump_text(pdf, page_index=args.page)
    print(f"File: {data['file']}   pages: {data['pages']}")
    if data.get("page_size"):
        w, h = data["page_size"]  # type: ignore[misc]
        print(f"Page size: {w:.0f} x {h:.0f} pt")
    print()

    print("--- Largest text spans (size, text, x/y as page fraction) ---")
    spans = data["spans"]  # type: ignore[assignment]
    if not spans:
        print("  (no text - this looks like a scanned image; OCR would be needed)")
    for size, text, (x, y) in spans[: args.limit]:  # type: ignore[misc]
        print(f"  {size:>6.1f}  x={x:<6.3f} y={y:<6.3f}  {text}")

    if args.full_text:
        print("\n--- Full page text ---")
        print(data["text"])

    print("\n--- Extracted with the current profile ---")
    rec = extract_drawing(pdf, profile=settings.profile)
    print(f"  S.No:        {rec.seq_no or '(none)':<24} [{rec.seq_source}]")
    print(f"  Member Name: {rec.member_name or '(none)':<24} [{rec.member_source}]")
    print(f"  Revision No: {rec.revision or '(none)':<24} [{rec.revision_source}]")
    if rec.notes:
        print(f"  Notes:       {rec.note_text}")
    if rec.error:
        print(f"  Error:       {rec.error}")
    return 0


def cmd_settings(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    if args.reset:
        from .config import AppSettings
        path = save_settings(AppSettings(), args.settings)
        print(f"Settings reset to defaults: {path}")
        return 0
    if args.show:
        print(json.dumps(settings.to_dict(), indent=2))
        return 0
    path = save_settings(settings, args.settings)
    print(f"Settings written to: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabdoc",
        description=f"{__app_name__} - drawing register generation and member validation.",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--settings", type=Path, default=None,
                        help="Path to a settings JSON file (default: ~/.fabdoc/settings.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="Show folder metadata and detected categories")
    p.add_argument("folder", help="Project (issue) folder")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("generate", help="Generate the drawing register workbook")
    p.add_argument("folder", help="Project (issue) folder")
    p.add_argument("-o", "--output", help="Output .xlsx path")
    p.add_argument("-c", "--categories", nargs="*", default=None,
                   help="Limit to these categories (default: all found)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("validate", help="Compare a register against the model member list")
    p.add_argument("register", help="Project folder to scan, or an existing register .xlsx")
    p.add_argument("members", help="Model member list (.xlsx/.csv/.txt)")
    p.add_argument("-o", "--output", help="Output report .xlsx path")
    p.add_argument("--column", default=None, help="Member column name or 0-based index")
    p.add_argument("--sheet", default=None, help="Worksheet name in the member list")
    p.add_argument("-c", "--categories", nargs="*", default=None,
                   help="Limit comparison to these drawing categories")
    p.add_argument("--strict", action="store_true",
                   help="Exit with status 2 when discrepancies are found")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("calibrate", help="Inspect one PDF to tune extraction patterns")
    p.add_argument("pdf", help="A representative drawing PDF")
    p.add_argument("--page", type=int, default=0, help="Page index (default 0, -1 = last)")
    p.add_argument("--limit", type=int, default=30, help="Spans to show")
    p.add_argument("--full-text", action="store_true", help="Also print the full page text")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("settings", help="Show, write or reset settings")
    p.add_argument("--show", action="store_true", help="Print current settings")
    p.add_argument("--reset", action="store_true", help="Restore defaults")
    p.set_defaults(func=cmd_settings)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
