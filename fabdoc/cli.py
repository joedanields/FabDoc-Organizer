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
from .compare import compare_issues
from .excel_out import (suggest_register_name, write_comparison_report,
                        write_register, write_validation_report)
from .extract import dump_text, extract_drawing
from .folder_meta import parse_folder
from .memberlist import read_member_list
from .register import build_register
from .register_io import read_register
from .spec_compare import compare_specs, order_folders
from .spec_out import suggest_spec_name, write_spec_report
from .tracking import (STAGE_IFF, STAGES, ChainState, IssueEntry, apply_reasons,
                       CLASH_NEXT_ROUND, CLASH_OVERWRITE, build_chain,
                       load_state, project_name_from, save_state,
                       snapshot_register, state_path_for)
from .tracking_out import write_tracker
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

    if args.track:
        # The stage is never guessed: an IFA folder misread as IFF would report
        # the unshipped balance as on hold and quietly rewrite the baseline.
        if not args.stage:
            print("\n--track needs --stage IFA or --stage IFF.", file=sys.stderr)
            return 1
        _track_issue(args, settings, folder, register)
    return 0


def _default_tracker(settings, folder: Path) -> Path:
    """Where the tracker lives when the user has not named one."""
    base = Path(settings.default_output_folder or folder)
    return base / "Package Tracker.xlsx"


def _print_tracker(chain) -> None:
    print(f"\n{'#':<3}{'Code':<9}{'Issue':<42}{'Drawings':>9}   Status")
    print("-" * 100)
    steps = [None] + list(chain.steps)
    for idx, entry in enumerate(chain.issues, start=1):
        step = steps[idx - 1] if idx - 1 < len(steps) else None
        status = step.verdict if step else "first issue"
        print(f"{idx:<3}{entry.code:<9}{entry.label[:40]:<42}{entry.total:>9}   {status}")


def _report_holds(chain, tracker: Path) -> None:
    """Tell the user what is outstanding and what still needs a reason."""
    outstanding = chain.outstanding
    if not outstanding:
        return
    missing = chain.missing_reasons()
    print(f"\n{len(outstanding)} approved member(s) have not been released yet "
          f"(on hold, not removed).")
    for hold in outstanding[:15]:
        why = hold.reason or "** no reason recorded **"
        print(f"  {hold.member_name:<14} rev {hold.revision or '-':<3} "
              f"held since {hold.held_since[:26]:<28} {why}")
    if len(outstanding) > 15:
        print(f"  ... and {len(outstanding) - 15} more")
    if missing:
        print(f"\n{len(missing)} of them have no reason recorded. Supply one with:")
        print(f'  python -m fabdoc track "{tracker}" --reason "why they are held"')


def _resolve_clash(state, entry, clash, choice: str) -> str | None:
    """Decide what to do about a round that is already tracked. None cancels.

    The engineer either pointed at the wrong tracker or is re-issuing that
    round, and the two want opposite things done, so an unattended run takes
    the answer that destroys nothing and says loudly what it did.
    """
    nxt = state.next_round(entry.stage)
    print()
    print(f"{clash.code} is already tracked in this chain:")
    print(f"  existing: {clash.label}   ({clash.total} drawing(s))")
    print(f"  adding:   {entry.label}   ({entry.total} drawing(s))")

    if choice != "ask":
        return choice
    if not sys.stdin.isatty():
        print(f"Nobody at the terminal: adding it as {entry.stage}-{nxt} rather than "
              f"overwriting. Pass --on-clash overwrite to replace {clash.code}.")
        return CLASH_NEXT_ROUND

    prompt = (f"[o]verwrite {clash.code}, add as [{entry.stage}-{nxt}], or [c]ancel? ")
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("o", "overwrite"):
            return CLASH_OVERWRITE
        if answer in ("", "n", "next", nxt.lower()):
            return CLASH_NEXT_ROUND
        if answer in ("c", "cancel", "q"):
            return None


def _track_issue(args: argparse.Namespace, settings, folder: Path, register) -> Path:
    """Append one issue to its package chain and rewrite the tracker."""
    tracker = Path(args.tracker) if args.tracker else _default_tracker(settings, folder)
    state = load_state(state_path_for(tracker))

    if not state.project:
        state.project = project_name_from(register.meta.title)

    entry = IssueEntry(
        label=folder.name,
        stage=args.stage.upper(),
        round_no=args.round or register.meta.issue_no,
        date_text=register.meta.date_display,
        folder=str(folder),
        members=snapshot_register(register, settings),
    )

    clash = state.clash_for(entry)
    if clash is not None:
        on_clash = _resolve_clash(state, entry, clash, getattr(args, "on_clash", "ask"))
        if on_clash is None:
            print("Tracker left alone.")
            return tracker
        state.add_issue(entry, on_clash)
        print(f"Tracked as {entry.code}.")
    else:
        state.add_issue(entry)

    chain = build_chain(state, settings)
    if args.hold_reason:
        apply_reasons(state, {h.member_name: args.hold_reason
                              for h in chain.outstanding if h.needs_reason}, settings)
        chain = build_chain(state, settings)

    save_state(state, state_path_for(tracker))
    write_tracker(chain, tracker)

    _print_tracker(chain)
    _report_holds(chain, tracker)
    print(f"\nTracker updated: {tracker}")
    return tracker


def cmd_track(args: argparse.Namespace) -> int:
    """Rebuild a tracker from its saved chain, optionally recording reasons.

    No PDF is rescanned: the chain state holds each issue's members, so the
    workbook can be redrawn after reasons are supplied.
    """
    settings = load_settings(args.settings)
    tracker = Path(args.tracker)
    state_file = state_path_for(tracker)
    if not state_file.exists():
        print(f"No chain state beside {tracker.name}.\n"
              f"Track an issue first:  python -m fabdoc generate <folder> "
              f"--stage IFA --round 1 --track", file=sys.stderr)
        return 1

    state = load_state(state_file)
    chain = build_chain(state, settings)

    if args.reason:
        targets = {h.member_name: args.reason for h in chain.outstanding
                   if h.needs_reason or args.overwrite_reasons}
        if not targets:
            print("Every outstanding member already has a reason. "
                  "Pass --overwrite-reasons to replace them.")
        apply_reasons(state, targets, settings)
        save_state(state, state_file)
        chain = build_chain(state, settings)
        print(f"Reason recorded against {len(targets)} member(s).")

    write_tracker(chain, tracker)
    print(f"Project: {chain.project}")
    print(f"Approved baseline: {chain.baseline_label or '(none yet)'}")
    _print_tracker(chain)
    _report_holds(chain, tracker)
    print(f"\nTracker written to: {tracker}")
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


def _load_side(path_text: str, settings, quiet: bool):
    """A register from either a package folder or an existing workbook."""
    p = Path(path_text)
    if p.is_dir():
        return build_register(p, settings=settings,
                              progress=None if quiet else _progress)
    return read_register(p)


def cmd_diff(args: argparse.Namespace) -> int:
    """Compare two issues of the same package."""
    settings = load_settings(args.settings)

    print(f"Old issue: {args.old}")
    old = _load_side(args.old, settings, args.quiet)
    print(f"New issue: {args.new}")
    new = _load_side(args.new, settings, args.quiet)

    result = compare_issues(
        old, new, settings=settings, categories=args.categories,
        old_label=Path(args.old).name, new_label=Path(args.new).name,
    )

    print()
    for line in result.summary_lines():
        print(line)

    if result.added:
        print("\nAdded in new issue:")
        for d in result.added[:40]:
            print(f"  + {d.member_name:<16} zone {d.zone or '-':<4} rev {d.new_revision}")
        if len(result.added) > 40:
            print(f"  ... and {len(result.added) - 40} more")
    if result.removed:
        print("\nRemoved in new issue:")
        for d in result.removed[:40]:
            print(f"  - {d.member_name:<16} zone {d.zone or '-':<4} rev {d.old_revision}")
        if len(result.removed) > 40:
            print(f"  ... and {len(result.removed) - 40} more")

    out = Path(args.output) if args.output else Path(args.new).parent / "Issue Comparison Report.xlsx"
    write_comparison_report(result, out)
    print(f"\nComparison report written to: {out}")
    return 0 if result.is_identical or not args.strict else 2


def cmd_specs(args: argparse.Namespace) -> int:
    """Compare the five title-block values of two issues, OLD against NEW."""
    settings = load_settings(args.settings)
    try:
        old_folder, new_folder = order_folders(args.first, args.second)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"OLD: {old_folder}")
    print(f"NEW: {new_folder}")
    old = build_register(old_folder, settings=settings,
                         progress=None if args.quiet else _progress)
    new = build_register(new_folder, settings=settings,
                         progress=None if args.quiet else _progress)
    result = compare_specs(old, new, settings,
                           old_label=old_folder.name, new_label=new_folder.name)
    if not result.parts:
        print("No single part drawings found in either folder.", file=sys.stderr)
        return 1

    out = Path(args.output) if args.output else new_folder / suggest_spec_name(result)
    write_spec_report(result, out)

    from .spec_compare import CHANGED, DECREASED, FIELDS, INCREASED

    print()
    print(f"{result.parts} part(s) in both issues.", end="")
    for count, side in ((len(result.only_in_old), "OLD"),
                        (len(result.only_in_new), "NEW")):
        if count:
            print(f"  {count} only in {side}.", end="")
    print()
    print()
    print(f"{'Value':<10}{'Changed':>9}{'Increased':>11}{'Decreased':>11}"
          f"{'Changed':>9}")
    print("-" * 50)
    for spec in FIELDS:
        sheet = result.sheets[spec.name]
        print(f"{spec.name:<10}{sheet.moved or '-':>9}{sheet.count(INCREASED):>11}"
              f"{sheet.count(DECREASED):>11}{sheet.count(CHANGED):>9}")
    print()
    print("Nothing moved - every part is the same in both issues."
          if result.is_identical else
          f"{result.moved} value(s) moved.")
    print(f"Report written to: {out}")
    return 0


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
    p.add_argument("--track", action="store_true",
                   help="Also append this issue to the package tracker")
    p.add_argument("--stage", choices=[s.lower() for s in STAGES] + list(STAGES),
                   default=None,
                   help="IFA (issued for approval) or IFF (issued for fabrication). "
                        "Required with --track; never guessed from the folder name.")
    p.add_argument("--round", default=None,
                   help="Issue round, e.g. 25 for an IFA or 2 for the second IFF "
                        "release (default: the leading number of the folder name)")
    p.add_argument("--tracker", default=None,
                   help="Tracker workbook path (default: 'Package Tracker.xlsx' "
                        "in the output folder)")
    p.add_argument("--hold-reason", default=None,
                   help="Reason to record against members this IFF release left behind")
    p.add_argument("--on-clash", choices=["ask", CLASH_OVERWRITE, CLASH_NEXT_ROUND],
                   default="ask",
                   help="What to do when that stage and round are already tracked "
                        "against another folder: overwrite that issue, add this one "
                        "as the next round, or ask (default; falls back to 'next' "
                        "when nobody is at the terminal)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("track", help="Rebuild the package tracker and show what is on hold")
    p.add_argument("tracker", help="Tracker workbook written by 'generate --track'")
    p.add_argument("--reason", default=None,
                   help="Record this reason against outstanding members")
    p.add_argument("--overwrite-reasons", action="store_true",
                   help="Replace reasons already recorded, not just the blank ones")
    p.set_defaults(func=cmd_track)

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

    p = sub.add_parser("diff", help="Compare two issues of the same package")
    p.add_argument("old", help="Older issue: package folder or register .xlsx")
    p.add_argument("new", help="Newer issue: package folder or register .xlsx")
    p.add_argument("-o", "--output", help="Output report .xlsx path")
    p.add_argument("-c", "--categories", nargs="*", default=None,
                   help="Limit comparison to these drawing categories")
    p.add_argument("--strict", action="store_true",
                   help="Exit with status 2 when the issues differ")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("specs", help="Compare part quantities, profiles, "
                                     "materials, lengths and weights: OLD vs NEW")
    p.add_argument("first", help="One issue folder - its name must contain OLD or NEW")
    p.add_argument("second", help="The other issue folder")
    p.add_argument("-o", "--output", help="Output report .xlsx path")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_specs)

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
