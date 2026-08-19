"""The web API, exercised the way the page drives it.

These cover what the browser front end adds over a plain function call: reading a
package in place instead of uploading it, writing the workbook to a folder the
engineer chose, progress and cancellation on a long run, and the settings tab
saving the same profile file the desktop app reads.

Everything runs against a locally-connected client, because that is what the
packaged executable is. The one test that does not is the point of the
distinction: a non-loopback client must not be able to browse the server's disk.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from fastapi.testclient import TestClient          # noqa: E402

from app import local_disk as disk                 # noqa: E402
from app import workspace as ws                    # noqa: E402
from app.main import app                           # noqa: E402

from conftest import make_drawing                  # noqa: E402
from fabdoc.tracking import load_state, state_path_for   # noqa: E402


class _Client:
    """Rewrites the ASGI scope so the test client presents as a real address.

    Starlette's test client reports its host as "testclient", which is neither
    loopback nor remote. Local-disk access hangs off that host, so the tests
    have to say which one they are pretending to be.
    """

    def __init__(self, inner, host: str):
        self.inner, self.host = inner, host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(self.host, 51000))
        await self.inner(scope, receive, send)


@pytest.fixture
def local() -> TestClient:
    return TestClient(_Client(app, "127.0.0.1"))


@pytest.fixture
def remote() -> TestClient:
    return TestClient(_Client(app, "10.0.0.9"))


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch) -> Path:
    """Keep every test's uploads, trackers and settings out of the real ones."""
    root = tmp_path / "data"
    for name, attr in (("uploads", "UPLOAD_ROOT"), ("trackers", "TRACKER_ROOT"),
                       ("output", "OUTPUT_ROOT")):
        monkeypatch.setattr(ws, attr, root / name)
    ws.ensure_roots()
    monkeypatch.setattr("app.main.ws", ws)

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("fabdoc.config.default_settings_path", lambda: settings_file)
    return root


def _finish(client: TestClient, job: str, limit: float = 120.0) -> dict:
    """Poll a job to completion the way the page does."""
    deadline = time.time() + limit
    while time.time() < deadline:
        state = client.get(f"/api/job/{job}").json()
        if state["state"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("job never finished")


# --------------------------------------------------------------- local scope


def test_a_remote_client_cannot_browse_the_server_disk(remote: TestClient):
    """Path browsing is a feature of the local executable, not of a shared server.

    Left ungated, anyone who could reach the port could enumerate the machine's
    filesystem and name any folder as an output destination.
    """
    assert remote.get("/api/browse", params={"path": "C:\\"}).status_code == 403
    assert remote.post("/api/scan/local", json={"folder": "C:\\"}).status_code == 403
    assert remote.post("/api/open", json={"path": "C:\\Windows\\notepad.exe"}
                       ).status_code == 403


def test_a_remote_client_still_gets_the_sandbox(remote: TestClient, project_folder: Path,
                                                sandbox: Path):
    """A shared deployment keeps working: uploads in, downloads out."""
    files, paths = [], []
    for pdf in sorted(project_folder.rglob("*.pdf")):
        files.append(("files", (pdf.name, pdf.read_bytes(), "application/pdf")))
        paths.append(str(pdf.relative_to(project_folder.parent)).replace("\\", "/"))
    scanned = remote.post("/api/scan", files=files, data={"paths": paths})
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert body["pdfs"] == 7
    # No local paths are offered to a client that cannot use them.
    assert body["output"]["folder"] == ""

    started = remote.post("/api/generate", data={
        "session": body["session"],
        "categories": [c["name"] for c in body["categories"]],
        "output_name": "Register.xlsx", "track": "false",
    })
    assert started.status_code == 200, started.text
    result = _finish(remote, started.json()["job"])
    assert result["state"] == "done", result.get("error")
    assert result["result"]["sandboxed"] is True
    assert (ws.OUTPUT_ROOT / "Register.xlsx").is_file()


def test_a_remote_client_cannot_write_outside_the_sandbox(remote: TestClient,
                                                          project_folder: Path,
                                                          tmp_path: Path):
    """An output folder named by a remote client is ignored, not honoured."""
    escape = tmp_path / "escape"
    started = remote.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(escape), "output_name": "Register.xlsx", "track": "false",
    })
    # No session and no usable local path: the upload is what it falls back to.
    assert started.status_code == 400
    assert not escape.exists()


# ------------------------------------------------------- reading in place


def test_scanning_a_local_folder_reads_it_where_it_sits(local: TestClient,
                                                        project_folder: Path):
    body = local.post("/api/scan/local", json={"folder": str(project_folder)}).json()
    assert body["pdfs"] == 7
    assert body["session"] is None or body["session"] == ""
    assert Path(body["local_path"]) == project_folder.resolve()
    assert {c["name"] for c in body["categories"]} == {"Structural", "Erection", "Part"}
    assert body["meta"]["zone"] == "B"
    assert body["output"]["name"].endswith(".xlsx")
    # Nothing was copied into the workspace.
    assert not any(ws.UPLOAD_ROOT.iterdir())


def test_scanning_a_folder_with_no_drawings_says_so(local: TestClient, tmp_path: Path):
    empty = tmp_path / "Nothing Here"
    empty.mkdir()
    r = local.post("/api/scan/local", json={"folder": str(empty)})
    assert r.status_code == 400
    assert "No drawing PDFs" in r.json()["detail"]


# ------------------------------------------------------- chosen destinations


def test_the_register_lands_in_the_folder_the_engineer_chose(local: TestClient,
                                                             project_folder: Path,
                                                             tmp_path: Path):
    """The whole point of the path fields: no download, no fishing in Downloads."""
    chosen = tmp_path / "Job Folder" / "Registers"
    started = local.post("/api/generate", data={
        "local_path": str(project_folder),
        "categories": ["Structural", "Erection", "Part"],
        "output_folder": str(chosen), "output_name": "Drawing Register",
        "track": "false",
    })
    result = _finish(local, started.json()["job"])
    assert result["state"] == "done", result.get("error")
    payload = result["result"]

    written = chosen / "Drawing Register.xlsx"     # the extension is supplied
    assert written.is_file()
    assert Path(payload["register_path"]) == written
    assert payload["sandboxed"] is False
    assert payload["total"] == 7


def test_the_tracker_lands_where_asked_and_is_appended_to(local: TestClient,
                                                          project_folder: Path,
                                                          single_category_folder: Path,
                                                          tmp_path: Path):
    """A tracker outlives the issue, so it has its own remembered folder.

    The second issue must find the first one's tracker and chain onto it rather
    than starting a new one - that is what the whole tracking feature is for.
    Both issues name the same project, which is what keys the chain.
    """
    chosen = tmp_path / "Trackers"
    for round_no, folder in (("1", project_folder), ("2", single_category_folder)):
        started = local.post("/api/generate", data={
            "local_path": str(folder), "categories": ["Structural", "Erection"],
            "output_folder": str(tmp_path / "out"), "output_name": f"R{round_no}.xlsx",
            "track": "true", "stage": "IFA", "round_no": round_no,
            "project": "Skyline Tower",
            "tracker_folder": str(chosen), "tracker_name": "Skyline - Tracker.xlsx",
        })
        result = _finish(local, started.json()["job"])
        assert result["state"] == "done", result.get("error")

    tracker = chosen / "Skyline - Tracker.xlsx"
    assert tracker.is_file()
    chain = result["result"]["chain"]
    assert len(chain["issues"]) == 2, "the second issue started a new tracker"
    assert Path(chain["tracker_path"]) == tracker


def test_the_tracker_is_downloadable_from_wherever_it_went(local: TestClient,
                                                           project_folder: Path,
                                                           tmp_path: Path):
    """The tracker can live somewhere the register does not.

    Its download link was derived from the register's location, so a tracker in
    a folder of its own pointed at the sandbox route and 404'd.
    """
    started = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(tmp_path / "registers"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Skyline",
        "tracker_folder": str(tmp_path / "trackers"),
        "tracker_name": "Skyline - Tracker.xlsx",
    })
    payload = _finish(local, started.json()["job"])["result"]
    chain = payload["chain"]

    assert chain["tracker_sandboxed"] is False
    assert payload["sandboxed"] is False
    assert local.get("/download/file",
                     params={"path": chain["tracker_path"]}).status_code == 200


def test_a_sandboxed_tracker_is_flagged_as_such(local: TestClient, project_folder: Path,
                                                tmp_path: Path):
    """No tracker folder given: it goes to the server's own folder and is listed."""
    started = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(tmp_path / "registers"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Skyline",
    })
    chain = _finish(local, started.json()["job"])["result"]["chain"]
    assert chain["tracker_sandboxed"] is True
    assert local.get("/download/tracker/" + chain["tracker"]).status_code == 200
    assert [t["file"] for t in local.get("/api/trackers").json()["trackers"]] \
        == [chain["tracker"]]


def test_re_running_the_same_issue_folder_replaces_its_entry(local: TestClient,
                                                             project_folder: Path,
                                                             tmp_path: Path):
    """A corrected re-run must not appear as a second issue of the package."""
    chosen = tmp_path / "Trackers"
    for _ in range(2):
        started = local.post("/api/generate", data={
            "local_path": str(project_folder), "categories": ["Structural"],
            "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
            "track": "true", "stage": "IFA", "round_no": "1",
            "project": "Skyline Tower",
            "tracker_folder": str(chosen), "tracker_name": "Skyline - Tracker.xlsx",
        })
        result = _finish(local, started.json()["job"])
        assert result["state"] == "done", result.get("error")
    assert len(result["result"]["chain"]["issues"]) == 1


def test_a_bad_output_folder_is_refused_before_the_run(local: TestClient,
                                                       project_folder: Path,
                                                       tmp_path: Path):
    """A typo must not surface after five minutes of reading PDFs."""
    a_file = tmp_path / "not-a-folder.txt"
    a_file.write_text("x", encoding="utf-8")
    r = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(a_file), "track": "false",
    })
    assert r.status_code == 400
    assert "not a folder" in r.json()["detail"].lower()

    r = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": "Registers", "track": "false",     # relative
    })
    assert r.status_code == 400
    assert "full path" in r.json()["detail"]


def test_remembering_the_output_folder_survives_to_the_next_scan(local: TestClient,
                                                                 project_folder: Path,
                                                                 tmp_path: Path):
    """Ticking the box is what stops the path being retyped every issue."""
    chosen = tmp_path / "Remembered"
    started = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(chosen), "output_name": "R.xlsx",
        "save_default_output": "true", "track": "false",
    })
    assert _finish(local, started.json()["job"])["state"] == "done"

    again = local.post("/api/scan/local", json={"folder": str(project_folder)}).json()
    assert Path(again["output"]["folder"]) == chosen


# ------------------------------------------------------- progress and cancel


def test_a_run_reports_progress_and_can_be_cancelled(local: TestClient,
                                                     project_folder: Path,
                                                     tmp_path: Path):
    """The desktop app's progress bar and Cancel button, over HTTP."""
    started = local.post("/api/generate", data={
        "local_path": str(project_folder),
        "categories": ["Structural", "Erection", "Part"],
        "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
        "track": "false",
    })
    job = started.json()["job"]
    assert local.get(f"/api/job/{job}").json()["state"] in {"running", "done"}

    local.post(f"/api/job/{job}/cancel")
    state = _finish(local, job)

    # Whichever way the race falls, the state must match what is on disk. A run
    # reported as cancelled that quietly wrote a register - over the previous
    # one - is the failure worth guarding against.
    written = (tmp_path / "out" / "R.xlsx").exists()
    if state["state"] == "cancelled":
        assert not written, "a cancelled run still wrote a workbook"
    else:
        assert state["state"] == "done" and written


def test_an_unknown_job_is_not_a_500(local: TestClient):
    assert local.get("/api/job/deadbeef").status_code == 404


# --------------------------------------------------------------- validation


def _register(local: TestClient, project_folder: Path, out: Path) -> dict:
    started = local.post("/api/generate", data={
        "local_path": str(project_folder),
        "categories": ["Structural", "Erection", "Part"],
        "output_folder": str(out), "output_name": "Register.xlsx", "track": "false",
    })
    state = _finish(local, started.json()["job"])
    assert state["state"] == "done", state.get("error")
    return state["result"]


def test_validation_reports_every_row_not_just_a_count(local: TestClient,
                                                       project_folder: Path,
                                                       tmp_path: Path):
    """Which member is missing is the only question this screen answers."""
    reg = _register(local, project_folder, tmp_path / "out")
    members = tmp_path / "model.csv"
    members.write_text("Assembly Mark\nC-101\nC-102\nGHOST-1\n", encoding="utf-8")

    body = local.post("/api/validate", data={
        "register_path": reg["register_path"], "members_path": str(members),
        "column": "Assembly Mark", "category": "(all)",
        "output_folder": str(tmp_path / "out"), "output_name": "Validation.xlsx",
    }).json()

    assert body["counts"]["matched"] == 2
    assert [r["member"] for r in body["missing_rows"]] == ["GHOST-1"]
    extra = {r["member"] for r in body["extra_rows"]}
    assert "C-110" in extra and "E-201" in extra
    # The detail columns the desktop tree shows, present per row. Source File is
    # blank unless the register was written with that column switched on.
    row = body["extra_rows"][0]
    assert set(row) == {"member", "category", "seq", "rev", "file"}
    assert row["category"] in {"Structural", "Erection", "Part"}
    assert row["seq"] and row["rev"]
    assert Path(body["report_path"]).is_file()


def test_validating_one_category_leaves_the_others_alone(local: TestClient,
                                                         project_folder: Path,
                                                         tmp_path: Path):
    """Part drawings and erection drawings validate against different exports."""
    reg = _register(local, project_folder, tmp_path / "out")
    members = tmp_path / "model.csv"
    members.write_text("Assembly Mark\nP-301\nP-302\n", encoding="utf-8")

    body = local.post("/api/validate", data={
        "register_path": reg["register_path"], "members_path": str(members),
        "column": "Assembly Mark", "category": "Part",
        "output_folder": str(tmp_path / "out"),
    }).json()
    assert body["clean"] is True, body["verdict"]
    assert body["counts"]["extra"] == 0


def test_comparison_options_reach_the_engine(local: TestClient, project_folder: Path,
                                             tmp_path: Path):
    """Ignore case / spaces / leading zeros were on the desktop and nowhere here."""
    reg = _register(local, project_folder, tmp_path / "out")
    members = tmp_path / "model.csv"
    members.write_text("Assembly Mark\nc-101\nc-102\nc-110\n", encoding="utf-8")

    strict = local.post("/api/validate", data={
        "register_path": reg["register_path"], "members_path": str(members),
        "column": "Assembly Mark", "category": "Structural",
        "case_insensitive": "false", "output_folder": str(tmp_path / "out"),
    }).json()
    assert strict["counts"]["matched"] == 0

    relaxed = local.post("/api/validate", data={
        "register_path": reg["register_path"], "members_path": str(members),
        "column": "Assembly Mark", "category": "Structural",
        "case_insensitive": "true", "output_folder": str(tmp_path / "out"),
    }).json()
    assert relaxed["counts"]["matched"] == 3


def test_the_register_from_tab_one_can_be_reused(local: TestClient, project_folder: Path,
                                                 tmp_path: Path):
    """"Use the register I just generated" - no re-upload, no re-read."""
    _register(local, project_folder, tmp_path / "out")
    last = local.get("/api/last-register").json()
    assert last["available"] is True
    assert last["total"] == 7

    members = tmp_path / "model.csv"
    members.write_text("Assembly Mark\nC-101\n", encoding="utf-8")
    body = local.post("/api/validate", data={
        "use_last": "true", "members_path": str(members),
        "column": "Assembly Mark", "output_folder": str(tmp_path / "out"),
    }).json()
    assert body["counts"]["matched"] == 1


def test_the_member_list_preview_lists_columns(local: TestClient, tmp_path: Path):
    """Typing a column name blind is how you validate against Quantity."""
    members = tmp_path / "model.csv"
    members.write_text("Assembly Mark,Quantity\nC-101,2\nC-102,1\n", encoding="utf-8")
    body = local.post("/api/members/preview", data={"path": str(members)}).json()
    assert "Assembly Mark" in body["columns"]
    assert body["count"] == 2


# --------------------------------------------------------------- comparison


def test_two_package_folders_can_be_compared_directly(local: TestClient,
                                                      project_folder: Path,
                                                      single_category_folder: Path,
                                                      tmp_path: Path):
    """The desktop app takes a folder on either side; the web took workbooks only."""
    started = local.post("/api/diff", data={
        "old_path": str(project_folder), "new_path": str(single_category_folder),
        "output_folder": str(tmp_path / "out"), "output_name": "Diff.xlsx",
    })
    state = _finish(local, started.json()["job"])
    assert state["state"] == "done", state.get("error")
    body = state["result"]

    assert body["old_total"] == 7 and body["new_total"] == 2
    assert body["counts"]["removed"] == 7 and body["counts"]["added"] == 2
    row = body["added_rows"][0]
    assert set(row) == {"member", "zone", "old", "new", "qty"}
    assert Path(body["report_path"]).is_file()


# ----------------------------------------------------------------- settings


def test_settings_round_trip_to_the_file_the_desktop_app_reads(local: TestClient,
                                                               tmp_path: Path):
    from fabdoc.config import default_settings_path, load_settings

    current = local.get("/api/settings").json()
    profile = dict(current["profile"])
    profile["member_patterns"] = [r"MARK\s*:\s*([A-Z0-9\-]+)"]
    profile["title_block_rect"] = [0.4, 0.5, 1.0, 1.0]

    saved = local.post("/api/settings", json={
        "profile": profile, "group_by_zone": False,
        "default_output_folder": str(tmp_path / "Registers"),
    })
    assert saved.status_code == 200, saved.text
    assert default_settings_path().is_file()

    reloaded = load_settings()
    assert reloaded.profile.member_patterns == [r"MARK\s*:\s*([A-Z0-9\-]+)"]
    assert reloaded.profile.title_block_rect == (0.4, 0.5, 1.0, 1.0)
    assert reloaded.group_by_zone is False
    assert reloaded.default_output_folder == str(tmp_path / "Registers")


@pytest.mark.parametrize("rect,why", [
    ([0.9, 0.1, 0.2, 0.9], "left past right"),
    ([0.1, 0.9, 0.9, 0.2], "top past bottom"),
    ([0.1, 0.1, 1.4, 0.9], "outside the page"),
])
def test_an_impossible_title_block_region_is_refused(local: TestClient, rect, why):
    """An inverted rect matches nothing, silently, for a thousand drawings."""
    profile = local.get("/api/settings").json()["profile"]
    profile["title_block_rect"] = rect
    r = local.post("/api/settings", json={"profile": profile})
    assert r.status_code == 400, why


def test_restoring_defaults_does_not_save_them(local: TestClient):
    """The desktop app restores into the form and waits for Save."""
    before = local.get("/api/settings").json()["profile"]["member_patterns"]
    local.post("/api/settings", json={
        "profile": dict(local.get("/api/settings").json()["profile"],
                        member_patterns=["ZZZ"])})
    assert local.get("/api/settings").json()["profile"]["member_patterns"] == ["ZZZ"]

    defaults = local.get("/api/settings/defaults").json()
    assert defaults["profile"]["member_patterns"] == before
    # Fetching defaults changed nothing on disk.
    assert local.get("/api/settings").json()["profile"]["member_patterns"] == ["ZZZ"]


def test_a_drawing_can_be_tested_against_the_patterns_on_the_page(local: TestClient,
                                                                  project_folder: Path):
    """The Test button: what the parser sees, then what it extracted."""
    pdf = next(project_folder.rglob("*.pdf"))
    profile = local.get("/api/settings").json()["profile"]
    body = local.post("/api/settings/test", data={
        "path": str(pdf), "profile": json.dumps(profile),
    }).json()

    assert body["ok"] is True
    assert "Largest text spans" in body["text"]
    assert "Member Name:" in body["text"]

    # A profile that cannot match anything gives an honest empty answer, not a crash.
    profile["member_patterns"] = [r"NOTHING_MATCHES_THIS\s*:\s*(\w+)"]
    profile["use_largest_text_fallback"] = False
    body = local.post("/api/settings/test", data={
        "path": str(pdf), "profile": json.dumps(profile),
    }).json()
    assert "Member Name:" in body["text"]


# ----------------------------------------------------------------- download


def _spec_folder(root: Path, name: str, parts: dict) -> Path:
    folder = root / name
    for idx, (mark, (qty, length)) in enumerate(parts.items(), start=1):
        make_drawing(folder / "Single Part Drawings" / f"{idx}_{mark}.pdf", mark,
                     revision=0, seq=idx, qty=qty, length=length,
                     weight="9.5 lbs")
    return folder


def test_part_specs_is_a_page_of_its_own(local: TestClient):
    """Not a panel over the register screen: it shares nothing with a run."""
    page = local.get("/specs")
    assert page.status_code == 200
    assert 'id="panel-specs"' in page.text
    assert 'id="s-first"' in page.text and 'id="s-open"' in page.text
    # Its own scripts, and none of the register screen's machinery.
    assert "/static/common.js" in page.text and "/static/specs.js" in page.text
    assert 'id="sheet-host"' not in page.text

    register = local.get("/")
    assert 'id="panel-specs"' not in register.text
    assert 'href="/specs"' in register.text, "and a way to reach it"


def test_a_bare_folder_of_part_drawings_is_read(local: TestClient, tmp_path: Path):
    """What somebody hands this screen: two revisions of one set, pulled out.

    There is no "Single Part Drawings" category in that shape - the folder
    itself is the category, under whatever name - and insisting on one refuses
    the very case the screen is for.
    """
    for side, qty in (("OLD", "2"), ("NEW", "5")):
        make_drawing(tmp_path / side / side / f"17a25 - Rev 0.pdf", "17a25",
                     revision=0, qty=qty, length="2'-0\"", weight="9 lbs")

    started = local.post("/api/specs", data={
        "first": str(tmp_path / "OLD"), "second": str(tmp_path / "NEW"),
        "output_folder": str(tmp_path / "out"), "output_name": "S.xlsx",
    })
    done = _finish(local, started.json()["job"])
    assert done["state"] == "done", done.get("error")

    qty = next(f for f in done["result"]["fields"] if f["name"] == "Qty")
    assert qty["rows"] == [{"member": "17a25", "old": "2", "new": "5",
                            "change": "Increased"}]


def test_the_spec_report_reads_the_folders_the_names_say(local: TestClient,
                                                         tmp_path: Path):
    """OLD and NEW come from the folder names, not from which box they went in."""
    _spec_folder(tmp_path, "12. Zone 1 OLD", {"17a25": ("2", "2'-0\"")})
    _spec_folder(tmp_path, "18. Zone 1 NEW", {"17a25": ("5", "2'-0\"")})

    started = local.post("/api/specs", data={
        # deliberately the wrong way round
        "first": str(tmp_path / "18. Zone 1 NEW"),
        "second": str(tmp_path / "12. Zone 1 OLD"),
        "output_folder": str(tmp_path / "out"), "output_name": "S.xlsx",
    })
    done = _finish(local, started.json()["job"])
    assert done["state"] == "done", done.get("error")
    payload = done["result"]

    assert payload["old_label"] == "12. Zone 1 OLD"
    assert payload["new_label"] == "18. Zone 1 NEW"
    qty = next(f for f in payload["fields"] if f["name"] == "Qty")
    assert qty["rows"] == [{"member": "17a25", "old": "2", "new": "5",
                            "change": "Increased"}]
    assert (tmp_path / "out" / "S.xlsx").is_file()


def test_a_folder_that_says_neither_is_refused_before_the_run(local: TestClient,
                                                              tmp_path: Path):
    _spec_folder(tmp_path, "12. Zone 1", {"17a25": ("2", "2'-0\"")})
    _spec_folder(tmp_path, "18. Zone 1 NEW", {"17a25": ("5", "2'-0\"")})

    refused = local.post("/api/specs", data={
        "first": str(tmp_path / "12. Zone 1"),
        "second": str(tmp_path / "18. Zone 1 NEW"),
        "output_folder": str(tmp_path / "out"), "output_name": "S.xlsx",
    })
    assert refused.status_code == 400
    assert "OLD" in refused.json()["detail"]


def test_a_remote_client_cannot_read_two_folders_off_the_server(remote: TestClient,
                                                                tmp_path: Path):
    """Both sides are folders on disk, so the screen is local-only."""
    assert remote.post("/api/specs", data={
        "first": str(tmp_path / "12. Zone 1 OLD"),
        "second": str(tmp_path / "18. Zone 1 NEW"),
    }).status_code == 403


def test_only_files_this_app_wrote_can_be_opened_or_downloaded(local: TestClient,
                                                               project_folder: Path,
                                                               tmp_path: Path):
    """A real path in a request must not turn the app into a file server."""
    reg = _register(local, project_folder, tmp_path / "out")
    assert local.get("/download/file",
                     params={"path": reg["register_path"]}).status_code == 200

    secret = tmp_path / "payroll.xlsx"
    secret.write_bytes(b"PK\x03\x04not really")
    assert local.get("/download/file", params={"path": str(secret)}).status_code == 404
    assert local.post("/api/open", json={"path": str(secret)}).status_code == 400
    assert not disk.is_remembered(secret)


# ------------------------------------------------------- existing trackers


def test_picking_an_existing_tracker_adds_to_it(local: TestClient, project_folder: Path,
                                                single_category_folder: Path,
                                                tmp_path: Path):
    """Naming the same tracker twice must chain, not start a second history.

    This is what went wrong on the real job: each issue titled itself
    differently, the suggested tracker name followed the title, and the
    fabrication release opened its own empty chain - so nothing was ever
    reported on hold.
    """
    tracker_dir = tmp_path / "trackers"
    for folder, stage, rnd in ((project_folder, "IFA", "1"),
                               (single_category_folder, "IFF", "2")):
        started = local.post("/api/generate", data={
            "local_path": str(folder), "categories": ["Structural", "Erection"],
            "output_folder": str(tmp_path / "out"), "output_name": f"R{rnd}.xlsx",
            "track": "true", "stage": stage, "round_no": rnd, "project": "Zone 1",
            "tracker_folder": str(tracker_dir), "tracker_name": "Zone 1 - Tracker.xlsx",
        })
        result = _finish(local, started.json()["job"])
        assert result["state"] == "done", result.get("error")

    assert [p.name for p in sorted(tracker_dir.glob("*.xlsx"))] == ["Zone 1 - Tracker.xlsx"]
    chain = result["result"]["chain"]
    assert len(chain["issues"]) == 2
    # The release found the approved scope, so the balance is on hold.
    assert chain["outstanding"] > 0


def test_tracker_info_reports_what_a_chosen_file_already_holds(local: TestClient,
                                                               project_folder: Path,
                                                               tmp_path: Path):
    """The page states the chain before the run, not after."""
    tracker = tmp_path / "trackers" / "Zone 1 - Tracker.xlsx"

    unknown = local.post("/api/tracker/info", json={"path": str(tracker)}).json()
    assert unknown["exists"] is False

    started = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Zone 1",
        "tracker_folder": str(tracker.parent), "tracker_name": tracker.name,
    })
    assert _finish(local, started.json()["job"])["state"] == "done"

    known = local.post("/api/tracker/info", json={"path": str(tracker)}).json()
    assert known["tracked"] is True
    assert known["project"] == "Zone 1"
    assert known["issues"] == 1
    assert known["stages"] == ["IFA-1"]


def test_a_round_already_tracked_is_reported_before_the_run(local: TestClient,
                                                           project_folder: Path,
                                                           single_category_folder: Path,
                                                           tmp_path: Path):
    """The page asks about a clash before a single PDF is read.

    Asked afterwards, it would be asking about a chain it had already changed.
    """
    tracker = tmp_path / "trackers" / "Zone 1 - Tracker.xlsx"
    started = local.post("/api/generate", data={
        "local_path": str(project_folder),
        "categories": ["Structural", "Erection"],
        "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "2", "project": "Zone 1",
        "tracker_folder": str(tracker.parent), "tracker_name": tracker.name,
    })
    assert _finish(local, started.json()["job"])["state"] == "done"

    body = {"path": str(tracker), "stage": "IFA", "round": "2"}
    clash = local.post("/api/tracker/info", json={**body,
                                                  "label": single_category_folder.name}).json()
    assert clash["clash"]["code"] == "IFA-2"
    assert clash["clash"]["label"] == project_folder.name
    assert clash["next_round"] == "3"

    # The same folder again is a re-run, not a clash.
    same = local.post("/api/tracker/info", json={**body,
                                                 "label": project_folder.name}).json()
    assert "clash" not in same
    # A free round is not one either.
    free = local.post("/api/tracker/info",
                      json={**body, "round": "3",
                            "label": single_category_folder.name}).json()
    assert "clash" not in free


@pytest.mark.parametrize("answer, labels", [
    ("next", ["IFA-2", "IFA-3"]),
    ("overwrite", ["IFA-2"]),
])
def test_the_answer_to_a_clash_decides_what_happens_to_the_round(
        local: TestClient, project_folder: Path, single_category_folder: Path,
        tmp_path: Path, answer: str, labels: list):
    """Overwrite replaces the tracked round; anything else keeps both issues."""
    tracker = tmp_path / "trackers" / "Zone 1 - Tracker.xlsx"
    common = {
        "categories": ["Structural", "Erection"],
        "output_folder": str(tmp_path / "out"),
        "track": "true", "stage": "IFA", "round_no": "2", "project": "Zone 1",
        "tracker_folder": str(tracker.parent), "tracker_name": tracker.name,
    }
    started = local.post("/api/generate", data={
        **common, "local_path": str(project_folder), "output_name": "R1.xlsx"})
    assert _finish(local, started.json()["job"])["state"] == "done"

    started = local.post("/api/generate", data={
        **common, "local_path": str(single_category_folder),
        "output_name": "R2.xlsx", "on_clash": answer})
    done = _finish(local, started.json()["job"])
    assert done["state"] == "done", done.get("error")
    payload = done["result"]

    assert [i["code"] for i in payload["chain"]["issues"]] == labels
    assert payload["round"]["taken"] == "IFA-2"
    assert payload["round"]["overwritten"] is (answer == "overwrite")
    # Whichever was chosen, the issue that is there is this folder.
    assert payload["chain"]["issues"][-1]["label"] == single_category_folder.name


def test_a_clash_nobody_answered_is_refused_rather_than_renumbered(
        local: TestClient, project_folder: Path, single_category_folder: Path,
        tmp_path: Path):
    """A page too old to ask must not get a silent answer.

    A tab left open across a restart posts no answer at all. Renumbering on its
    behalf is the one outcome nothing reports: the engineer typed round 1 and
    the workbook comes back saying round 2.
    """
    tracker = tmp_path / "trackers" / "Zone 1 - Tracker.xlsx"
    common = {
        "categories": ["Structural", "Erection"],
        "output_folder": str(tmp_path / "out"),
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Zone 1",
        "tracker_folder": str(tracker.parent), "tracker_name": tracker.name,
    }
    started = local.post("/api/generate", data={
        **common, "local_path": str(project_folder), "output_name": "R1.xlsx"})
    assert _finish(local, started.json()["job"])["state"] == "done"

    refused = local.post("/api/generate", data={
        **common, "local_path": str(single_category_folder),
        "output_name": "R2.xlsx"})
    assert refused.status_code == 409
    assert "IFA-1 is already tracked" in refused.json()["detail"]

    state = load_state(state_path_for(tracker))
    assert [e.code for e in state.issues] == ["IFA-1"], "the chain was written anyway"


def test_the_page_is_told_where_the_run_will_write(local: TestClient,
                                                   project_folder: Path,
                                                   tmp_path: Path):
    """The boxes decide the tracker, not a path the browser composed.

    Composed there, the two disagree the moment the name box is left empty or
    typed without an extension - and the page then reports on a chain the run is
    never going to touch, which is how a clash goes unasked.
    """
    chosen = tmp_path / "trackers"
    started = local.post("/api/generate", data={
        "local_path": str(project_folder), "categories": ["Structural"],
        "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Zone 1",
        "tracker_folder": str(chosen), "tracker_name": "Zone 1 - Tracker",
    })
    assert _finish(local, started.json()["job"])["state"] == "done"
    assert (chosen / "Zone 1 - Tracker.xlsx").is_file()

    # The name as it was typed - no extension - still finds that chain.
    d = local.post("/api/tracker/info", json={
        "folder": str(chosen), "name": "Zone 1 - Tracker", "project": "Zone 1",
        "stage": "IFA", "round": "1", "label": "somebody else",
    }).json()
    assert d["tracked"] is True
    assert d["clash"]["code"] == "IFA-1"
    assert d["clash"]["label"] == project_folder.name

    # An empty name box falls back to the derived one, exactly as a run does.
    empty = local.post("/api/tracker/info", json={
        "folder": str(chosen), "name": "", "project": "Zone 1",
    }).json()
    assert Path(empty["path"]).name == "Zone 1 - Tracker.xlsx"


def test_parts_and_assemblies_share_one_tracker_workbook(local: TestClient,
                                                         project_folder: Path,
                                                         tmp_path: Path):
    """Every category lands in the same file, on its own sheet."""
    from openpyxl import load_workbook

    tracker = tmp_path / "trackers" / "Zone 1 - Tracker.xlsx"
    started = local.post("/api/generate", data={
        "local_path": str(project_folder),
        "categories": ["Structural", "Erection", "Part"],
        "output_folder": str(tmp_path / "out"), "output_name": "R.xlsx",
        "track": "true", "stage": "IFA", "round_no": "1", "project": "Zone 1",
        "tracker_folder": str(tracker.parent), "tracker_name": tracker.name,
    })
    assert _finish(local, started.json()["job"])["state"] == "done"

    assert list(tracker.parent.glob("*.xlsx")) == [tracker]
    wb = load_workbook(tracker)
    try:
        histories = [n for n in wb.sheetnames if n.startswith("History")]
        # Erection is not tracked: it shows where assemblies go on site, so it
        # has no approved scope to release and nothing to hold.
        assert histories == ["History - Structural", "History - Part"]
    finally:
        wb.close()
