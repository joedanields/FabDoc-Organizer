"""FastAPI front end for FabDoc Organizer.

A browser port of the desktop app. The extraction, register, validation and
tracking logic is imported from ``fabdoc`` unchanged - this module is transport
and nothing else. Any rule that matters (the four-tier extraction cascade, "absent
is not removed once fabrication starts", the accumulating IFF baseline) lives in
the package and is shared by the CLI, the tkinter app and this.

The five tabs mirror the desktop app one for one, plus a trackers tab the desktop
app has no room for. Long runs go through ``jobs`` so the page gets the same
progress bar and Cancel button; workbook destinations go through ``local_disk``
so a locally-run copy writes to the job folder instead of a download sandbox.

Run it with::

    python -m uvicorn app.main:app --reload      # from the web/ folder
"""

from __future__ import annotations

import shutil
import sys
import traceback
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

# fabdoc lives one level up. Import it from the checkout rather than requiring an
# install, so the web app runs from a fresh clone the same way the desktop one does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fabdoc import __app_name__, __version__
from fabdoc.categories import discover_categories
from fabdoc.compare import compare_issues
from fabdoc.config import (AppSettings, ExtractionProfile, load_settings,
                           save_settings)
from fabdoc.excel_out import (suggest_register_name, write_comparison_report,
                              write_register, write_validation_report)
from fabdoc.extract import dump_text, extract_drawing
from fabdoc.folder_meta import parse_folder
from fabdoc.memberlist import (preview_columns, read_member_list, sheet_names)
from fabdoc.register import Register, build_register
from fabdoc.register_io import read_register
from fabdoc.tracking import (STAGE_IFF, STAGES, IssueEntry, apply_reasons,
                             build_chain, load_state, project_name_from,
                             save_state, snapshot_register, state_path_for)
from fabdoc.tracking_out import write_tracker
from fabdoc.validate import validate

from . import jobs
from . import local_disk as disk
from . import workspace as ws

# Templates and static files. Frozen into an executable they are unpacked to a
# temporary directory that ``__file__`` does not point at, so ask the bundle.
BASE = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parents[1]

# The register generated on tab 1, so tabs 3 and 4 can offer "use the one I just
# made" the way the desktop app does. One engineer, one machine, one in-flight
# register - the same assumption the rest of the app is built on.
_LAST: dict[str, object] = {"register": None, "path": "", "label": ""}


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


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {"app_name": __app_name__, "version": __version__, "stages": list(STAGES),
         "local": disk.is_local_client(_client(request))},
    )


# ---------------------------------------------------------------------------
# Local disk: folder picker, opening what was written
# ---------------------------------------------------------------------------


@app.get("/api/browse")
def api_browse(request: Request, path: str = "", suffixes: str = ""):
    """List folders, for the in-page equivalent of the desktop Browse... button."""
    _require_local(request)
    return disk.list_dir(path, suffixes)


@app.post("/api/browse/mkdir")
async def api_mkdir(request: Request):
    _require_local(request)
    body = await request.json()
    try:
        return {"path": disk.make_dir(str(body.get("parent", "")),
                                      str(body.get("name", "")))}
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/check-folder")
async def api_check_folder(request: Request):
    """Tell the engineer a typed path is wrong before a long run, not after."""
    _require_local(request)
    body = await request.json()
    path, problem = disk.check_folder(str(body.get("folder", "")))
    return {"ok": path is not None, "message": problem,
            "path": str(path) if path else ""}


@app.post("/api/open")
async def api_open(request: Request):
    """Open a written workbook, or show it in its folder.

    The desktop app's "Open Workbook" button. Restricted to files this process
    wrote: the browser can name a path, but not an arbitrary one.
    """
    _require_local(request)
    body = await request.json()
    target = str(body.get("path", ""))
    if not disk.is_remembered(target):
        raise HTTPException(400, "That file was not written by this app.")
    if not Path(target).exists():
        raise HTTPException(404, "That file is no longer there.")
    try:
        if body.get("reveal"):
            disk.reveal_path(target)
        else:
            disk.open_path(target)
    except OSError as exc:
        raise HTTPException(500, f"Could not open it: {exc}")
    return {"opened": target}


def _require_local(request: Request) -> None:
    try:
        disk.require_local(_client(request))
    except disk.NotLocal as exc:
        raise HTTPException(403, str(exc))


# ---------------------------------------------------------------------------
# Settings (desktop tab 4)
# ---------------------------------------------------------------------------


def _settings_payload(settings: AppSettings) -> dict:
    data = settings.to_dict()
    data["settings_path"] = str(_settings_file())
    return data


def _settings_file() -> Path:
    from fabdoc.config import default_settings_path
    return default_settings_path()


@app.get("/api/settings")
def api_settings():
    return _settings_payload(load_settings())


@app.get("/api/settings/defaults")
def api_settings_defaults():
    return _settings_payload(AppSettings())


def _profile_from(data: dict) -> ExtractionProfile:
    """Build a profile from the page, refusing a title block region that cannot exist.

    A rect with left past right silently matches nothing, and the engineer would
    see it as "the parser stopped finding marks" a thousand drawings later.
    """
    profile = ExtractionProfile.from_dict(data)
    rect = tuple(float(v) for v in data.get("title_block_rect", profile.title_block_rect))
    if len(rect) != 4:
        raise ValueError("The title block region needs four numbers.")
    if not all(0.0 <= v <= 1.0 for v in rect):
        raise ValueError("The title block region must be fractions between 0 and 1.")
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise ValueError("The title block region needs left < right and top < bottom.")
    profile.title_block_rect = rect          # type: ignore[assignment]
    return profile


def _sequence_groups_from(raw: object) -> list[list]:
    """Validate the steel-type priority table coming back from the page.

    Order is the erection order, so a duplicated decade is a real mistake: two
    rows claiming the 70s would silently make the second unreachable and put a
    whole sequence under the wrong heading.
    """
    if not isinstance(raw, list):
        raise ValueError("The steel type table must be a list of rows.")
    table: list[list] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError("Each steel type needs a sequence digit and a name.")
        try:
            decade = int(entry[0])
        except (TypeError, ValueError):
            raise ValueError(f"'{entry[0]}' is not a sequence digit (0-9).")
        if not 0 <= decade <= 9:
            raise ValueError(f"Sequence digit {decade} must be between 0 and 9.")
        if decade in seen:
            raise ValueError(f"Sequence digit {decade} is listed twice.")
        seen.add(decade)
        name = str(entry[1]).strip()
        if not name:
            raise ValueError(f"Sequence digit {decade} has no name.")
        table.append([decade, name])
    return table


@app.post("/api/settings")
async def api_save_settings(request: Request):
    """Persist the whole profile to ~/.fabdoc/settings.json - the file the
    desktop app and the CLI read, so calibration done here applies everywhere."""
    body = await request.json()
    settings = load_settings()
    try:
        if "profile" in body:
            settings.profile = _profile_from(body["profile"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    for key in ("day_first_dates", "compare_case_insensitive",
                "compare_ignore_whitespace", "compare_strip_leading_zeros",
                "include_source_column", "group_by_zone"):
        if key in body:
            setattr(settings, key, bool(body[key]))
    for key in ("default_output_folder", "default_tracker_folder"):
        if key in body:
            setattr(settings, key, str(body[key] or "").strip())
    if "sequence_groups" in body:
        try:
            settings.sequence_groups = _sequence_groups_from(body["sequence_groups"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
    if isinstance(body.get("category_aliases"), dict):
        settings.category_aliases = {
            str(k): [str(x) for x in v] for k, v in body["category_aliases"].items()
        }
    if isinstance(body.get("category_order"), list):
        settings.category_order = [str(x) for x in body["category_order"]]

    try:
        path = save_settings(settings)
    except OSError as exc:
        raise HTTPException(500, f"Could not save settings: {exc}")
    payload = _settings_payload(settings)
    payload["saved_to"] = str(path)
    return payload


@app.post("/api/settings/test")
async def api_test_pattern(request: Request,
                           pdf: UploadFile | None = File(None),
                           path: str = Form(""),
                           profile: str = Form("")):
    """Run one drawing through the patterns on the page.

    The desktop app's Test button: what the parser can see on the sheet, then
    what the current patterns pulled out of it. Tuning patterns blind against a
    thousand drawings is how a whole register comes out wrong.
    """
    import json

    try:
        prof = _profile_from(json.loads(profile)) if profile else load_settings().profile
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_test-{ws.new_session()}"
    try:
        if pdf is not None and pdf.filename:
            tmp.mkdir(parents=True, exist_ok=True)
            target = tmp / disk.safe_component(Path(pdf.filename).name, "drawing.pdf")
            target.write_bytes(await pdf.read())
        elif path.strip() and disk.is_local_client(_client(request)):
            target = Path(path.strip().strip('"'))
            if not target.is_file():
                raise HTTPException(400, f"No such drawing: {target}")
        else:
            raise HTTPException(400, "Choose a drawing PDF to test against.")

        lines: list[str] = []
        try:
            data = dump_text(target)
            lines.append(f"File: {data['file']}    pages: {data['pages']}")
            if data.get("page_size"):
                w, h = data["page_size"]                      # type: ignore[misc]
                lines.append(f"Page size: {w:.0f} x {h:.0f} pt")
            lines.append("")
            lines.append("Largest text spans   size    x       y     text")
            spans = data["spans"]                             # type: ignore[assignment]
            if not spans:
                lines.append("  (no text at all - this is a scanned image, and OCR "
                             "would be needed to read it)")
            for size, text, (x, y) in spans[:25]:             # type: ignore[misc]
                lines.append(f"  {size:>18.1f}  {x:<6.3f}  {y:<6.3f}  {text}")

            record = extract_drawing(target, profile=prof)
            lines.append("")
            lines.append("--- Extracted with the patterns above ---")
            lines.append(f"  S.No:        {record.seq_no or '(none)':<22} [{record.seq_source}]")
            lines.append(f"  Member Name: {record.member_name or '(none)':<22} "
                         f"[{record.member_source}]")
            lines.append(f"  Revision No: {record.revision or '(none)':<22} "
                         f"[{record.revision_source}]")
            if record.notes:
                lines.append(f"  Notes:       {record.note_text}")
            if record.error:
                lines.append(f"  Error:       {record.error}")
            lines.append("")
            lines.append("Tip: the x/y columns are page fractions. If the mark you want "
                         "sits outside the title block region, widen it above.")
            ok = bool(record.member_name)
        except Exception as exc:
            lines.append(f"Failed to read the PDF: {exc}")
            ok = False
        return {"text": "\n".join(lines), "ok": ok}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tab 1: scanning a package
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


def _scan_payload(root: Path, settings: AppSettings, pdfs: int,
                  session: str, local_path: str, host: str) -> dict:
    """Everything tab 1 needs to fill itself in after a scan."""
    meta = parse_folder(root, day_first=settings.day_first_dates)
    cats = discover_categories(root, aliases=settings.category_aliases,
                               order=settings.category_order)

    # Where the workbook goes by default: the remembered folder if there is one,
    # otherwise beside the drawings, exactly as the desktop app decides it.
    temp = Register(meta=meta, project_folder=root)
    suggested = suggest_register_name(temp)
    out_dir = ""
    if disk.is_local_client(host):
        remembered = settings.default_output_folder
        out_dir = remembered if remembered and Path(remembered).is_dir() else (
            local_path or "")
    tracker_dir = settings.default_tracker_folder if disk.is_local_client(host) else ""
    project = project_name_from(meta.title)

    return {
        "session": session,
        "local_path": local_path,
        "folder": root.name,
        "pdfs": pdfs,
        "meta": {
            "title": meta.title, "date": meta.date_display, "zone": meta.zone,
            "package": meta.package, "issue_no": meta.issue_no, "project": project,
        },
        "categories": [
            {"name": c.name, "folder": c.folder.name, "count": c.count,
             "recognised": c.is_recognised, "matched": c.matched_alias}
            for c in cats
        ],
        "output": {
            "folder": out_dir,
            "name": suggested,
            "tracker_folder": tracker_dir or out_dir,
            "tracker_name": f"{ws.slugify(project, 'Package')} - Tracker.xlsx",
        },
    }


@app.post("/api/scan")
async def api_scan(request: Request, files: list[UploadFile],
                   paths: list[str] = Form(default=[])):
    """Dry run on an uploaded folder: metadata and categories before anything is written."""
    session = ws.new_session()
    saved = await _receive(session, files, paths)
    if not saved:
        ws.clear_session(session)
        raise HTTPException(400, "No PDFs in that folder.")

    root = ws.package_root(session)
    if root is None:
        ws.clear_session(session)
        raise HTTPException(400, "Could not find the package folder in that upload.")
    return _scan_payload(root, load_settings(), saved, session, "", _client(request))


@app.post("/api/scan/local")
async def api_scan_local(request: Request):
    """Dry run on a folder already on this machine - no upload at all.

    A package is thousands of PDFs. Uploading them to a server running on the
    same disk they already live on is minutes of copying for nothing, so a local
    run reads the folder in place, exactly as the desktop app does.
    """
    _require_local(request)
    body = await request.json()
    raw = str(body.get("folder", "")).strip().strip('"')
    if not raw:
        raise HTTPException(400, "Choose the project (issue) folder first.")
    root = Path(raw)
    if not root.is_dir():
        raise HTTPException(400, f"Not a folder:\n{raw}")
    pdfs = sum(1 for _ in root.rglob("*.pdf"))
    if not pdfs:
        raise HTTPException(400, "No drawing PDFs found in that folder.")
    return _scan_payload(root, load_settings(), pdfs, "", str(root.resolve()),
                         _client(request))


# ---------------------------------------------------------------------------
# Tab 1: generating the register
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
        "tracker_path": str(tracker),
        # The tracker can live somewhere the register does not - a job folder
        # per issue, one tracker folder for the package - so it carries its own
        # flag rather than borrowing the register's download route.
        "tracker_sandboxed": tracker.parent.resolve() == ws.TRACKER_ROOT.resolve(),
    }


@app.post("/api/generate")
async def api_generate(
    request: Request,
    session: str = Form(""),
    local_path: str = Form(""),
    title: str = Form(""),
    date: str = Form(""),
    zone: str = Form(""),
    package: str = Form(""),
    categories: list[str] = Form(default=[]),
    output_folder: str = Form(""),
    output_name: str = Form(""),
    save_default_output: bool = Form(False),
    track: bool = Form(False),
    stage: str = Form(""),
    round_no: str = Form(""),
    project: str = Form(""),
    tracker_folder: str = Form(""),
    tracker_name: str = Form(""),
    save_default_tracker: bool = Form(False),
):
    """Start the register build. Returns a job id; the page polls it for progress."""
    host = _client(request)
    if local_path.strip() and disk.is_local_client(host):
        root = Path(local_path.strip())
        if not root.is_dir():
            raise HTTPException(400, "That folder is no longer there.")
        uploaded = ""
    else:
        root = ws.package_root(session)                      # type: ignore[assignment]
        if root is None:
            raise HTTPException(400, "That upload has expired. Upload the folder again.")
        uploaded = session

    if not categories:
        raise HTTPException(400, "Select at least one drawing category.")

    settings = load_settings()
    if track and stage not in STAGES:
        # Never inferred: an approval issue mistaken for a fabrication release
        # rewrites the approved baseline and reports the rest as unshipped.
        raise HTTPException(400, "Choose IFA or IFF before tracking this issue.")

    meta = parse_folder(root, day_first=settings.day_first_dates)
    if title:
        meta.title = title
    if zone:
        meta.zone = zone
    if package:
        meta.package = package
    if date and date != meta.date_display:
        # A typed date replaces the parsed one; keeping the parsed date object
        # would print the old value back into the register header.
        meta = replace(meta, date_text=date, issue_date=None)

    try:
        out_path = disk.resolve_target(output_folder, output_name, ws.OUTPUT_ROOT,
                                       suggest_register_name(
                                           Register(meta=meta, project_folder=root)),
                                       host)
        tracker = None
        if track:
            project_name = project.strip() or project_name_from(meta.title)
            tracker = disk.resolve_target(
                tracker_folder, tracker_name, ws.TRACKER_ROOT,
                f"{ws.slugify(project_name, 'Package')} - Tracker.xlsx", host)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    # Remembering the folder is what stops the engineer retyping it every issue.
    if disk.is_local_client(host):
        changed = False
        if save_default_output and output_folder.strip():
            settings.default_output_folder = str(out_path.parent)
            changed = True
        if save_default_tracker and tracker_folder.strip() and tracker is not None:
            settings.default_tracker_folder = str(tracker.parent)
            changed = True
        if changed:
            try:
                save_settings(settings)
            except OSError:
                pass          # the register still gets written; this is a nicety

    def work(job: jobs.Job) -> dict:
        register = build_register(
            root, settings=settings, meta_override=meta,
            selected_categories=categories or None,
            progress=job.progress, should_cancel=job.cancelled,
        )
        if job.cancelled():
            return {}
        if not register.categories:
            raise ValueError("No drawings found in the selected categories.")

        job.progress(job.total, job.total, "Writing the workbook...")
        write_register(register, out_path,
                       include_source=settings.include_source_column,
                       sequence_groups=settings.sequence_groups)
        disk.remember(out_path)
        _LAST["register"] = register
        _LAST["path"] = str(out_path)
        _LAST["label"] = root.name

        payload: dict = {
            "register": out_path.name,
            "register_path": str(out_path),
            "sandboxed": out_path.parent.resolve() == ws.OUTPUT_ROOT.resolve(),
            "total": register.total,
            "review": register.review_count,
            "categories": [
                {"name": c.name, "total": c.total, "review": c.review_count,
                 "errors": c.error_count}
                for c in register.categories
            ],
        }
        if track and tracker is not None:
            job.progress(job.total, job.total, "Updating the tracker...")
            payload.update(_track(register, root, stage, round_no, settings,
                                  project, tracker))
        if uploaded:
            ws.clear_session(uploaded)
        return payload

    return {"job": jobs.start(work).id}


def _track(register, root: Path, stage: str, round_no: str, settings,
           project: str, tracker: Path) -> dict:
    # The project keys the tracker, so issues sharing it chain together. It is
    # derived from the folder name but stays editable: an unanticipated naming
    # convention should be a correction, not a silent split into two trackers.
    project = project.strip() or project_name_from(register.meta.title)
    state = load_state(state_path_for(tracker))
    if not state.project:
        state.project = project

    state.add_issue(IssueEntry(
        label=root.name, stage=stage,
        round_no=round_no or register.meta.issue_no,
        date_text=register.meta.date_display, folder=str(root),
        members=snapshot_register(register),
    ))
    chain = build_chain(state, settings)
    save_state(state, state_path_for(tracker))
    write_tracker(chain, tracker)
    disk.remember(tracker)

    result = {"chain": _chain_payload(chain, tracker)}
    step = chain.steps[-1] if chain.steps else None

    # A release that left approved members behind is the moment to ask why.
    if stage == STAGE_IFF and step and step.on_hold:
        result["holds"] = {
            "release": root.name,
            "released": len(step.released),
            "tracker_path": str(tracker),
            "members": [
                {"member": h.member_name, "zone": h.zone, "revision": h.revision,
                 "since": h.held_since, "reason": h.reason}
                for h in step.on_hold
            ],
        }
    return result


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "That run is no longer being tracked.")
    return job.payload()


@app.post("/api/job/{job_id}/cancel")
def api_job_cancel(job_id: str):
    return {"cancelled": jobs.cancel(job_id)}


# ---------------------------------------------------------------------------
# Trackers
# ---------------------------------------------------------------------------


def _tracker_for(project: str, path: str) -> Path:
    """The tracker a request means: an explicit path we wrote, else the project's."""
    if path and disk.is_remembered(path):
        return Path(path)
    return ws.tracker_path_for(project)


@app.post("/api/tracker/reasons")
async def api_reasons(request: Request):
    """Record why a release left approved members behind."""
    body = await request.json()
    project = (body.get("project") or "").strip()
    reasons = body.get("reasons") or {}
    tracker = _tracker_for(project, str(body.get("tracker_path") or ""))
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
# Tab 3: validation
# ---------------------------------------------------------------------------


def _stash(upload: UploadFile | None, local: str, tmp: Path, fallback: str,
           host: str) -> Path | None:
    """Resolve one file input: an upload, or a path on this machine."""
    if upload is not None and upload.filename:
        tmp.mkdir(parents=True, exist_ok=True)
        target = tmp / Path(upload.filename).name
        target.write_bytes(upload.file.read())
        return target
    text = (local or "").strip().strip('"')
    if text and disk.is_local_client(host):
        path = Path(text)
        if not path.exists():
            raise HTTPException(400, f"No such file: {text}")
        return path
    return None


@app.post("/api/members/preview")
async def api_members_preview(request: Request,
                              members: UploadFile | None = File(None),
                              path: str = Form(""),
                              sheet: str = Form("")):
    """Worksheets and column headers in a member list.

    The desktop app fills two dropdowns from these. Typing a column name blind
    is how you end up validating against the Quantity column and getting a
    hundred per cent mismatch.
    """
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_members-{ws.new_session()}"
    try:
        source = _stash(members, path, tmp, "members.xlsx", _client(request))
        if source is None:
            raise HTTPException(400, "Choose the model member list.")
        try:
            sheets = sheet_names(source)
            chosen = sheet or (sheets[0] if sheets else None)
            headers = preview_columns(source, sheet=chosen)
            loaded = read_member_list(source, sheet=chosen)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return {
            "sheets": sheets,
            "sheet": chosen or "",
            "columns": headers or [loaded.column_name],
            "column": loaded.column_name,
            "count": loaded.count,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/register/preview")
async def api_register_preview(request: Request,
                               register_file: UploadFile | None = File(None),
                               path: str = Form("")):
    """Categories in a register workbook, for the category filter on tab 3."""
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_reg-{ws.new_session()}"
    try:
        source = _stash(register_file, path, tmp, "register.xlsx", _client(request))
        if source is None:
            raise HTTPException(400, "Choose a register workbook.")
        try:
            loaded = read_register(source)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Could not read the register: {exc}")
        if not loaded.categories:
            raise HTTPException(400, "That workbook has no recognisable register sheets. "
                                     "A sheet needs a 'Member Name' column header.")
        return {
            "total": loaded.total,
            "categories": [c.name for c in loaded.categories],
            "label": Path(source).name,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/last-register")
def api_last_register():
    """What tab 1 generated, so tabs 3 and 4 can offer to reuse it."""
    register = _LAST.get("register")
    if register is None:
        return {"available": False}
    return {
        "available": True,
        "label": _LAST.get("label", ""),
        "path": _LAST.get("path", ""),
        "total": register.total,                              # type: ignore[union-attr]
        "categories": [c.name for c in register.categories],  # type: ignore[union-attr]
    }


@app.post("/api/validate")
async def api_validate(
    request: Request,
    register_file: UploadFile | None = File(None),
    members: UploadFile | None = File(None),
    register_path: str = Form(""),
    members_path: str = Form(""),
    use_last: bool = Form(False),
    sheet: str = Form(""),
    column: str = Form(""),
    category: str = Form(""),
    case_insensitive: bool = Form(True),
    ignore_whitespace: bool = Form(True),
    strip_leading_zeros: bool = Form(False),
    output_folder: str = Form(""),
    output_name: str = Form(""),
):
    """Compare a register against a model member list.

    The upload is named ``register_file`` rather than ``register`` because
    pydantic reserves ``register`` on its base model and warns about the clash.
    """
    host = _client(request)
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_validate-{ws.new_session()}"
    try:
        if use_last and _LAST.get("register") is not None:
            loaded = _LAST["register"]                         # type: ignore[assignment]
            source_label = str(_LAST.get("label") or "the generated register")
        else:
            reg_path = _stash(register_file, register_path, tmp, "register.xlsx", host)
            if reg_path is None:
                raise HTTPException(400, "Choose a register workbook, or generate one "
                                         "on tab 1 first.")
            try:
                loaded = read_register(reg_path)
            except (OSError, ValueError) as exc:
                raise HTTPException(400, f"Could not read the register: {exc}")
            if not loaded.categories:                          # type: ignore[union-attr]
                raise HTTPException(
                    400, "That workbook has no register sheets - a sheet needs a "
                         "'Member Name' column header.")
            source_label = reg_path.name

        mem_path = _stash(members, members_path, tmp, "members.csv", host)
        if mem_path is None:
            raise HTTPException(400, "Choose the model member list.")

        settings = load_settings()
        settings.compare_case_insensitive = case_insensitive
        settings.compare_ignore_whitespace = ignore_whitespace
        settings.compare_strip_leading_zeros = strip_leading_zeros

        try:
            model = read_member_list(mem_path, column=column or None,
                                     sheet=sheet or None)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Could not read the member list: {exc}")
        if not model.members:
            raise HTTPException(400, f"No members found in column "
                                     f"'{model.column_name}'. Pick a different column.")

        cats = None if category in ("", "(all)") else [category]
        result = validate(loaded, model, settings=settings, categories=cats)

        try:
            out = disk.resolve_target(output_folder, output_name, ws.OUTPUT_ROOT,
                                      "Member Validation Report.xlsx", host)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        write_validation_report(result, out)
        disk.remember(out)

        def rows(records):
            return [{"member": r.member_name, "category": r.category,
                     "seq": r.seq_no, "rev": r.revision, "file": r.source_file}
                    for r in records[:1000]]

        return {
            "report": out.name,
            "report_path": str(out),
            "sandboxed": out.parent.resolve() == ws.OUTPUT_ROOT.resolve(),
            "register": source_label,
            "column": model.column_name,
            "model_count": result.model_count,
            "drawing_count": result.drawing_count,
            "matched": len(result.matched),
            "unnamed": result.unnamed_drawings,
            "missing_rows": [{"member": m} for m in result.missing_in_drawings[:1000]],
            "extra_rows": rows(result.extra_records),
            "matched_rows": rows(result.matched_records),
            "duplicate_rows": [
                {"member": name, "count": len(files), "file": ", ".join(files)}
                for name, files in list(result.duplicates_in_drawings.items())[:1000]
            ],
            "counts": {
                "missing": len(result.missing_in_drawings),
                "extra": len(result.extra_in_drawings),
                "matched": len(result.matched),
                "duplicates": len(result.duplicates_in_drawings),
            },
            "verdict": result.verdict,
            "clean": result.is_clean,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tab 4: comparing two issues
# ---------------------------------------------------------------------------


@app.post("/api/diff")
async def api_diff(
    request: Request,
    old: UploadFile | None = File(None),
    new: UploadFile | None = File(None),
    old_path: str = Form(""),
    new_path: str = Form(""),
    case_insensitive: bool = Form(True),
    ignore_whitespace: bool = Form(True),
    strip_leading_zeros: bool = Form(False),
    output_folder: str = Form(""),
    output_name: str = Form(""),
):
    """Compare two issues. Each side may be a register workbook or a package folder.

    A folder side is read drawing by drawing, so this runs as a job with a
    progress bar - the desktop app does the same, and for the same reason.
    """
    host = _client(request)
    ws.ensure_roots()
    tmp = ws.OUTPUT_ROOT / f"_diff-{ws.new_session()}"
    tmp.mkdir(parents=True, exist_ok=True)

    sides: dict[str, Path] = {}
    for key, upload, raw in (("old", old, old_path), ("new", new, new_path)):
        path = _stash(upload, raw, tmp, f"{key}.xlsx", host)
        if path is None:
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(400, "Choose both the older and the newer issue.")
        sides[key] = path

    settings = load_settings()
    settings.compare_case_insensitive = case_insensitive
    settings.compare_ignore_whitespace = ignore_whitespace
    settings.compare_strip_leading_zeros = strip_leading_zeros

    try:
        out = disk.resolve_target(output_folder, output_name, ws.OUTPUT_ROOT,
                                  "Issue Comparison Report.xlsx", host)
    except ValueError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, str(exc))

    def work(job: jobs.Job) -> dict:
        try:
            def load(path: Path) -> Register:
                if path.is_dir():
                    return build_register(
                        path, settings=settings,
                        progress=lambda d, t, label: job.progress(
                            d, t, f"{path.name[:28]}: {label}"),
                        should_cancel=job.cancelled,
                    )
                return read_register(path)

            result = compare_issues(
                load(sides["old"]), load(sides["new"]), settings=settings,
                old_label=sides["old"].stem, new_label=sides["new"].stem,
            )
            if job.cancelled():
                return {}
            job.progress(job.total, job.total, "Writing the report...")
            write_comparison_report(result, out)
            disk.remember(out)

            def rows(deltas):
                return [{"member": d.member_name, "zone": d.zone or "",
                         "old": d.old_revision or "", "new": d.new_revision or "",
                         "qty": f"{d.old_count} / {d.new_count}"}
                        for d in deltas[:1000]]

            return {
                "report": out.name,
                "report_path": str(out),
                "sandboxed": out.parent.resolve() == ws.OUTPUT_ROOT.resolve(),
                "old_total": result.old_total, "new_total": result.new_total,
                "added_rows": rows(result.added),
                "removed_rows": rows(result.removed),
                "revised_rows": rows(result.revision_changed),
                "unchanged_rows": rows(result.unchanged),
                "counts": {
                    "added": len(result.added), "removed": len(result.removed),
                    "revised": len(result.revision_changed),
                    "unchanged": len(result.unchanged),
                },
                "verdict": result.verdict,
                "identical": result.is_identical,
            }
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return {"job": jobs.start(work).id}


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _serve(folder: Path, name: str) -> FileResponse:
    """Serve a generated file by name, refusing anything outside its folder."""
    target = (folder / name).resolve()
    if folder.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(target, filename=target.name, media_type=XLSX)


@app.get("/download/register/{name}")
def download_register(name: str):
    return _serve(ws.OUTPUT_ROOT, name)


@app.get("/download/tracker/{name}")
def download_tracker(name: str):
    return _serve(ws.TRACKER_ROOT, name)


@app.get("/download/file")
def download_file(path: str):
    """Download a workbook written to a folder outside the sandbox.

    Restricted to files this process wrote, so a chosen output folder is still
    reachable from the browser without turning the app into a file server.
    """
    if not disk.is_remembered(path) or not Path(path).is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(path, filename=Path(path).name, media_type=XLSX)
