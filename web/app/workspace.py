"""Server-side storage for uploaded packages and package trackers.

The desktop app is handed a folder path and reads it in place. A browser cannot
do that, so an uploaded package is rebuilt under a workspace directory with its
subfolder structure intact - category detection depends on those subfolders, so
flattening the upload would collapse every worksheet into one.

Trackers live outside the per-upload workspaces and are keyed by project, because
chaining is the whole point: issue 20 uploaded on Tuesday has to find the tracker
that issue 10 created on Monday.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

# Everything the server writes lives under here, so a deployment has exactly one
# directory to point at a real disk and one directory to clean up.
DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
UPLOAD_ROOT = DATA_ROOT / "uploads"
TRACKER_ROOT = DATA_ROOT / "trackers"
OUTPUT_ROOT = DATA_ROOT / "output"

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()\-]+")


def ensure_roots() -> None:
    for path in (UPLOAD_ROOT, TRACKER_ROOT, OUTPUT_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def slugify(text: str, fallback: str = "package") -> str:
    """A filesystem-safe stem that still reads like the original name."""
    clean = _UNSAFE.sub("-", (text or "").strip()).strip(" .-")
    return (clean[:80] or fallback)


def new_session() -> str:
    return uuid.uuid4().hex[:12]


def safe_relative(raw: str) -> Path | None:
    """Turn a browser-supplied relative path into one that cannot escape the workspace.

    ``webkitRelativePath`` is client-controlled text. Anything absolute, or
    containing a parent reference, is refused outright rather than sanitised: a
    silently rewritten path would land drawings under the wrong category, which
    is harder to notice than a rejected upload.
    """
    if not raw or raw.startswith(("/", "\\")):
        return None
    parts = [p for p in re.split(r"[\\/]+", raw) if p not in ("", ".")]
    if not parts or ".." in parts or re.match(r"^[A-Za-z]:$", parts[0]):
        return None
    return Path(*parts)


def workspace_for(session_id: str) -> Path:
    return UPLOAD_ROOT / session_id


def package_root(session_id: str) -> Path | None:
    """The uploaded package folder inside a workspace.

    A directory upload arrives as ``<package>/<category>/<drawing>.pdf``, so the
    single child of the workspace is the package the engineer chose.
    """
    root = workspace_for(session_id)
    if not root.is_dir():
        return None
    children = [p for p in root.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return root if any(root.rglob("*.pdf")) else None


def tracker_path_for(project: str) -> Path:
    """Where a project's tracker lives. Same project name, same tracker."""
    return TRACKER_ROOT / f"{slugify(project, 'Package')} - Tracker.xlsx"


def list_trackers() -> list[dict[str, object]]:
    ensure_roots()
    out: list[dict[str, object]] = []
    for path in sorted(TRACKER_ROOT.glob("*.xlsx")):
        out.append({
            "name": path.stem,
            "file": path.name,
            "size": path.stat().st_size,
        })
    return out


def clear_session(session_id: str) -> None:
    """Drop an upload's PDFs once its register is written.

    A package is thousands of files and the server has no reason to keep them:
    everything the tracker needs is already in the chain state.
    """
    shutil.rmtree(workspace_for(session_id), ignore_errors=True)
