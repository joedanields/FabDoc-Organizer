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

# Session ids are minted by new_session() and are nothing but hex. The client
# hands one back on every later request, so it is untrusted text that ends up
# joined onto a filesystem path - and clear_session() deletes what it points at.
# Anything not of this exact shape is refused rather than sanitised.
_SESSION_RE = re.compile(r"^[0-9a-f]{8,32}$")


class InvalidSession(ValueError):
    """Raised when a session id could not have come from new_session()."""


def _checked_session(session_id: str) -> str:
    if not _SESSION_RE.match(session_id or ""):
        raise InvalidSession(f"Invalid session id: {session_id!r}")
    return session_id


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
    """The upload directory for a session. Raises on anything malformed."""
    return UPLOAD_ROOT / _checked_session(session_id)


def package_root(session_id: str) -> Path | None:
    """The uploaded package folder inside a workspace.

    A directory upload arrives as ``<package>/<category>/<drawing>.pdf``, so the
    single child of the workspace is the package the engineer chose.
    """
    try:
        root = workspace_for(session_id)
    except InvalidSession:
        return None
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

    This deletes a whole directory tree from a client-supplied id, so it refuses
    anything it did not mint and re-checks containment after resolving. A bad id
    is a silent no-op: the caller is finishing a successful run, and there is
    nothing useful it could do with the failure.
    """
    try:
        target = workspace_for(session_id).resolve()
    except InvalidSession:
        return
    # Belt and braces: never recurse outside the upload root, whatever the id.
    if target.parent != UPLOAD_ROOT.resolve() or not target.is_dir():
        return
    shutil.rmtree(target, ignore_errors=True)
