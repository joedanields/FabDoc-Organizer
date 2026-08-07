"""FastAPI front end for FabDoc Organizer.

A browser port of the desktop app. The extraction, register, validation and
tracking logic is imported from ``fabdoc`` unchanged - this module is transport
and nothing else. Any rule that matters (the four-tier extraction cascade, "absent
is not removed once fabrication starts", the accumulating IFF baseline) lives in
the package and is shared by the CLI, the tkinter app and this.

Run it with::

    python -m uvicorn app.main:app --reload      # from the web/ folder
"""

from __future__ import annotations

import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

# fabdoc lives one level up. Import it from the checkout rather than requiring an
# install, so the web app runs from a fresh clone the same way the desktop one does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fabdoc import __app_name__, __version__
from fabdoc.categories import discover_categories
from fabdoc.compare import compare_issues
from fabdoc.config import load_settings
from fabdoc.excel_out import (suggest_register_name, write_comparison_report,
                              write_register, write_validation_report)
from fabdoc.folder_meta import parse_folder
from fabdoc.memberlist import read_member_list
from fabdoc.register import build_register
from fabdoc.register_io import read_register
from fabdoc.tracking import (STAGE_IFF, STAGES, IssueEntry, apply_reasons,
                             build_chain, load_state, project_name_from,
                             save_state, snapshot_register, state_path_for)
from fabdoc.tracking_out import write_tracker
from fabdoc.validate import validate

from . import workspace as ws

BASE = Path(__file__).resolve().parents[1]

@asynccontextmanager
async def lifespan(_: FastAPI):
    ws.ensure_roots()
    yield


app = FastAPI(title=f"{__app_name__} Web", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Return the failure as JSON so the page can show it.

    A corrupt drawing or a locked workbook must surface in the browser, not as an
    opaque 500 with the detail only in the server console.
    """
    return JSONResponse(
        status_code=500,
        content={"error": f"{type(exc).__name__}: {exc}",
                 "trace": traceback.format_exc()[-2000:]},
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {"app_name": __app_name__, "version": __version__, "stages": list(STAGES)},
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def _receive(session: str, files: list[UploadFile], paths: list[str]) -> int:
    """Rebuild an uploaded folder under a workspace, preserving subfolders."""
    root = ws.workspace_for(session)
    root.mkdir(parents=True, exist_ok=True)
    saved = 0
    for index, upload in enumerate(files):
        raw = paths[index] if index < len(paths) else (upload.filename or "")
        relative = ws.safe_relative(raw)
        if relative is None or relative.suffix.lower() != ".pdf":
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as fh:
            while chunk := await upload.read(1 << 20):
                fh.write(chunk)
        saved += 1
    return saved


@app.post("/api/scan")
async def api_scan(files: list[UploadFile], paths: list[str] = Form(default=[])):
    """Dry run: show the metadata and categories before anything is written."""
    session = ws.new_session()
    saved = await _receive(session, files, paths)
    if not saved:
        ws.clear_session(session)
        raise HTTPException(400, "No PDFs in that folder.")

    root = ws.package_root(session)
    if root is None:
        ws.clear_session(session)
        raise HTTPException(400, "Could not find the package folder in that upload.")

    settings = load_settings()
    meta = parse_folder(root, day_first=settings.day_first_dates)
    cats = discover_categories(root, aliases=settings.category_aliases,
                               order=settings.category_order)
    return {
        "session": session,
        "folder": root.name,
        "pdfs": saved,
        "meta": {
            "title": meta.title, "date": meta.date_display, "zone": meta.zone,
            "package": meta.package, "issue_no": meta.issue_no,
            "project": project_name_from(meta.title),
        },
        "categories": [
            {"name": c.name, "folder": c.folder.name, "count": c.count,
             "recognised": c.is_recognised, "matched": c.matched_alias}
            for c in cats
        ],
    }


# ---------------------------------------------------------------------------
# Register + tracking
# ---------------------------------------------------------------------------


def _chain_payload(chain, tracker: Path) -> dict:
    steps = [None] + list(chain.steps)
    issues = []
    for idx, entry in enumerate(chain.issues, start=1):
        step = steps[idx - 1] if idx - 1 < len(steps) else None
        issues.append({
            "n": idx, "code": entry.code, "stage": entry.stage,
            "round": entry.round_no, "label": entry.label,
            "date": entry.date_text, "total": entry.total,
            "added": len(step.added) if step else entry.total,
            "revised": len(step.revised) if step else 0,
            "removed": len(step.removed) if step else 0,
            "released": len(step.released) if step else 0,
            "on_hold": len(step.on_hold) if step else 0,
            "verdict": step.verdict if step else "first issue",
        })
    return {
        "project": chain.project,
        "baseline": chain.baseline_label,
        "issues": issues,
        "outstanding": len(chain.outstanding),
        "tracker": tracker.name,
    }


@app.post("/api/generate")
def api_generate(
    session: str = Form(...),
    title: str = Form(""),
    zone: str = Form(""),
    package: str = Form(""),
    categories: list[str] = Form(default=[]),
    track: bool = Form(False),
    stage: str = Form(""),
    round_no: str = Form(""),
    project: str = Form(""),
):
    """Build the register, and optionally append the issue to its package chain."""
    root = ws.package_root(session)
    if root is None:
        raise HTTPException(400, "That upload has expired. Upload the folder again.")

    settings = load_settings()
    meta = parse_folder(root, day_first=settings.day_first_dates)
    if title:
        meta.title = title
    if zone:
        meta.zone = zone
    if package:
        meta.package = package

    register = build_register(root, settings=settings, meta_override=meta,
                              selected_categories=categories or None)
    if not register.categories:
        raise HTTPException(400, "No drawings found in the selected categories.")

    ws.ensure_roots()
    out = ws.OUTPUT_ROOT / suggest_register_name(register)
    write_register(register, out, include_source=settings.include_source_column)

    payload: dict = {
        "register": out.name,
        "total": register.total,
        "review": register.review_count,
        "categories": [
            {"name": c.name, "total": c.total, "review": c.review_count,
             "errors": c.error_count}
            for c in register.categories
        ],
    }

    if track:
        # Never inferred: an approval issue mistaken for a fabrication release
        # rewrites the approved baseline and reports the rest as unshipped.
        if stage not in STAGES:
            raise HTTPException(400, "Choose IFA or IFF before tracking this issue.")
        payload.update(_track(register, root, stage, round_no, settings, project))

    ws.clear_session(session)
    return payload


def _track(register, root: Path, stage: str, round_no: str, settings,
           project: str = "") -> dict:
    # The project keys the tracker, so issues sharing it chain together. It is
    # derived from the folder name but stays editable: an unanticipated naming
    # convention should be a correction, not a silent split into two trackers.
    project = project.strip() or project_name_from(register.meta.title)
    tracker = ws.tracker_path_for(project)
    state = load_state(state_path_for(tracker))
    if not state.project:
        state.project = project

    state.add_issue(IssueEntry(
        label=root.name, stage=stage,
        round_no=round_no or register.meta.issue_no,
        date_text=register.meta.date_display, folder=root.name,
        members=snapshot_register(register),
    ))
    chain = build_chain(state, settings)
    save_state(state, state_path_for(tracker))
    write_tracker(chain, tracker)

    result = {"chain": _chain_payload(chain, tracker)}
    step = chain.steps[-1] if chain.steps else None

    # A release that left approved members behind is the moment to ask why.
    if stage == STAGE_IFF and step and step.on_hold:
        result["holds"] = {
            "release": root.name,
            "released": len(step.released),
            "members": [
                {"member": h.member_name, "zone": h.zone, "revision": h.revision,
                 "since": h.held_since, "reason": h.reason}
                for h in step.on_hold
            ],
        }
    return result


@app.post("/api/tracker/reasons")
async def api_reasons(request: Request):
    """Record why a release left approved members behind."""
    body = await request.json()
    project = (body.get("project") or "").strip()
    reasons = body.get("reasons") or {}
    tracker = ws.tracker_path_for(project)
    state_file = state_path_for(tracker)
    if not state_file.exists():
        raise HTTPException(404, f"No tracker for '{project}'.")

    settings = load_settings()
    state = load_state(state_file)
    apply_reasons(state, {str(k): str(v) for k, v in reasons.items()}, settings)
    save_state(state, state_file)
    chain = build_chain(state, settings)
    write_tracker(chain, tracker)
    return {"saved": len(reasons), "chain": _chain_payload(chain, tracker),
            "without_reason": len(chain.missing_reasons())}


@app.get("/api/trackers")
def api_trackers():
    return {"trackers": ws.list_trackers()}


@app.get("/api/tracker/{name}")
def api_tracker(name: str):
    tracker = ws.TRACKER_ROOT / f"{ws.slugify(name)}.xlsx"
    state_file = state_path_for(tracker)
    if not state_file.exists():
        raise HTTPException(404, "No such tracker.")
    chain = build_chain(load_state(state_file), load_settings())
    payload = _chain_payload(chain, tracker)
    payload["holds"] = [
        {"member": h.member_name, "zone": h.zone, "revision": h.revision,
         "since": h.held_since, "released": h.released_in, "reason": h.reason}
        for h in chain.holds.values()
    ]
    return payload


# ---------------------------------------------------------------------------
# Validation and comparison
# ---------------------------------------------------------------------------


@app.post("/api/validate")
async def api_validate(register_file: UploadFile, members: UploadFile,
                       column: str = Form("")):
    """Compare a generated register workbook against a model member list.

    The upload is named ``register_file`` rather than ``register`` because
    pydantic reserves ``register`` on its base model and warns about the clash.
    """
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_validate-{ws.new_session()}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        reg_path = tmp / (register_file.filename or "register.xlsx")
        reg_path.write_bytes(await register_file.read())
        mem_path = tmp / (members.filename or "members.csv")
        mem_path.write_bytes(await members.read())

        loaded = read_register(reg_path)
        if not loaded.categories:
            raise HTTPException(
                400, "That workbook has no register sheets - a sheet needs a "
                     "'Member Name' column header.")
        model = read_member_list(mem_path, column=column or None)
        if not model.members:
            raise HTTPException(400, f"No members found in column '{model.column_name}'.")

        result = validate(loaded, model, settings=load_settings())
        out = ws.OUTPUT_ROOT / "Member Validation Report.xlsx"
        write_validation_report(result, out)
        return {
            "report": out.name,
            "column": model.column_name,
            "model_count": result.model_count,
            "drawing_count": result.drawing_count,
            "matched": len(result.matched),
            "missing": result.missing_in_drawings[:500],
            "extra": result.extra_in_drawings[:500],
            "duplicates": len(result.duplicates_in_drawings),
            "verdict": result.verdict,
            "clean": result.is_clean,
        }
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/diff")
async def api_diff(old: UploadFile, new: UploadFile):
    """Compare two already-generated registers, issue against issue."""
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_diff-{ws.new_session()}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        old_path = tmp / (old.filename or "old.xlsx")
        old_path.write_bytes(await old.read())
        new_path = tmp / (new.filename or "new.xlsx")
        new_path.write_bytes(await new.read())

        result = compare_issues(
            read_register(old_path), read_register(new_path),
            settings=load_settings(),
            old_label=old_path.stem, new_label=new_path.stem,
        )
        out = ws.OUTPUT_ROOT / "Issue Comparison Report.xlsx"
        write_comparison_report(result, out)
        return {
            "report": out.name,
            "old_total": result.old_total, "new_total": result.new_total,
            "added": [d.member_name for d in result.added][:500],
            "removed": [d.member_name for d in result.removed][:500],
            "revised": [{"member": d.member_name, "old": d.old_revision,
                         "new": d.new_revision} for d in result.revision_changed][:500],
            "unchanged": len(result.unchanged),
            "verdict": result.verdict,
            "identical": result.is_identical,
        }
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _serve(folder: Path, name: str) -> FileResponse:
    """Serve a generated file by name, refusing anything outside its folder."""
    target = (folder / name).resolve()
    if folder.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(
        target, filename=target.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/download/register/{name}")
def download_register(name: str):
    return _serve(ws.OUTPUT_ROOT, name)


@app.get("/download/tracker/{name}")
def download_tracker(name: str):
    return _serve(ws.TRACKER_ROOT, name)
