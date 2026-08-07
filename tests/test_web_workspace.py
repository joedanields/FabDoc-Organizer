"""Path-safety tests for the web workspace.

The web layer takes two pieces of client-controlled text and joins them onto
filesystem paths: the per-file relative path from ``webkitRelativePath``, and
the session id echoed back on every request after upload. The session id is the
dangerous one, because ``clear_session`` deletes the tree it points at.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from app import workspace as ws  # noqa: E402


# --------------------------------------------------------------- session ids


@pytest.mark.parametrize("session", [
    "..",
    "../..",
    "../../..",
    "..\\..\\..",
    "/etc",
    "\\windows",
    "C:",
    "abc/../../..",
    "abc123/../..",
    "",
    "not-hex!",
    "../" * 12,
])
def test_traversal_session_ids_are_refused(session: str):
    """A crafted session id must never resolve outside the upload root.

    ``session=../../..`` resolved to the repository root. package_root() then
    returned it (any *.pdf below it was enough), the register built, and
    clear_session() recursively deleted the lot.
    """
    with pytest.raises(ws.InvalidSession):
        ws.workspace_for(session)
    assert ws.package_root(session) is None


def test_clear_session_ignores_a_traversal_id(tmp_path: Path, monkeypatch):
    """clear_session must delete nothing when handed an id it did not mint."""
    root = tmp_path / "uploads"
    (root / "real").mkdir(parents=True)
    (root / "real" / "drawing.pdf").write_bytes(b"%PDF-")
    sibling = tmp_path / "trackers"
    sibling.mkdir()
    (sibling / "Package - Tracker.xlsx").write_bytes(b"x")

    monkeypatch.setattr(ws, "UPLOAD_ROOT", root)

    for hostile in ("..", "../..", "../../.."):
        ws.clear_session(hostile)

    assert sibling.exists(), "clear_session escaped the upload root"
    assert (sibling / "Package - Tracker.xlsx").exists()
    assert (root / "real").exists()


def test_clear_session_removes_its_own_workspace(tmp_path: Path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setattr(ws, "UPLOAD_ROOT", root)
    session = ws.new_session()
    workspace = root / session
    (workspace / "Assembly").mkdir(parents=True)
    (workspace / "Assembly" / "d.pdf").write_bytes(b"%PDF-")

    ws.clear_session(session)
    assert not workspace.exists()


def test_minted_sessions_are_accepted():
    session = ws.new_session()
    assert ws.workspace_for(session).name == session


# ------------------------------------------------------- uploaded file paths


@pytest.mark.parametrize("raw", [
    "../secrets.pdf",
    "../../etc/passwd.pdf",
    "/absolute/x.pdf",
    "\\absolute\\x.pdf",
    "C:/Windows/x.pdf",
    "pkg/../../x.pdf",
    "",
])
def test_hostile_relative_paths_are_refused(raw: str):
    assert ws.safe_relative(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("pkg/Assembly/17172C172.pdf", ("pkg", "Assembly", "17172C172.pdf")),
    ("pkg\\Assembly\\a.pdf", ("pkg", "Assembly", "a.pdf")),
    ("./pkg/a.pdf", ("pkg", "a.pdf")),
])
def test_ordinary_relative_paths_survive_intact(raw: str, expected: tuple[str, ...]):
    """Subfolders must be preserved - category detection depends on them."""
    result = ws.safe_relative(raw)
    assert result is not None and result.parts == expected


# ------------------------------------------------------------------ slugify


@pytest.mark.parametrize("name", ["../../etc", "a/b", "a\\b", "..", "  ..  "])
def test_slugify_never_yields_a_path_separator(name: str):
    slug = ws.slugify(name)
    assert "/" not in slug and "\\" not in slug
    assert slug not in ("", ".", "..")
