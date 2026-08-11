"""Real filesystem access for the locally-run app.

The desktop app is handed a folder path and writes the register back beside it.
A browser cannot do that, so the web app originally wrote everything into a
sandbox under ``web/data/`` and handed back a download link - which means the
engineer fishes the workbook out of Downloads and moves it to the job folder by
hand, every issue.

Packaged as an executable the server and the engineer are the same machine, so
that detour is unnecessary: the process can write straight to ``P:\\Jobs\\17172``
like the desktop app does. This module is the part that makes that safe.

Two rules hold everything together:

* **Loopback only.** Path browsing and writing to a chosen folder are offered
  only to a client connected from this machine. The README's other deployment -
  one shared server on a team network - still works, it just falls back to the
  sandbox and download links, because letting a remote browser enumerate and
  write to the server's disk is a different program with different risks.
* **Hand back only what we wrote.** ``/api/open`` and ``/api/reveal`` act on an
  allow-list this module records as files are written. The browser may name a
  real path, but never one the app did not itself produce.
"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path

# Set FABDOC_LOCAL=0 to run as a shared server: no path browsing, no writing
# outside the sandbox, for anyone. Defaults on, because the packaged executable
# is the primary way this app is run.
LOCAL_MODE = os.environ.get("FABDOC_LOCAL", "1") != "0"

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

# Directory listings are for picking a folder, not for trawling one - a Windows
# system folder with 40k entries would be a slow response nobody reads.
MAX_ENTRIES = 400

# Paths this process has written. Opening a file is a side effect on the user's
# machine, so it is offered for these and nothing else.
_WRITTEN: set[str] = set()


class NotLocal(PermissionError):
    """Raised when a non-loopback client asks for a local-disk operation."""


def is_local_client(host: str | None) -> bool:
    """Is the request coming from this machine?"""
    return LOCAL_MODE and (host or "") in _LOOPBACK


def require_local(host: str | None) -> None:
    if not is_local_client(host):
        raise NotLocal(
            "Saving to a folder on this computer is available only when the app "
            "is running locally. Use the download links instead."
        )


# ---------------------------------------------------------------------------
# The written-file allow-list
# ---------------------------------------------------------------------------


def remember(path: str | Path) -> Path:
    """Record a file this app wrote, making it openable and downloadable."""
    resolved = Path(path).resolve()
    _WRITTEN.add(str(resolved).lower())
    return resolved


def is_remembered(path: str | Path) -> bool:
    try:
        return str(Path(path).resolve()).lower() in _WRITTEN
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


def drives() -> list[str]:
    """Mounted drive roots on Windows; ``/`` elsewhere."""
    if sys.platform != "win32":
        return ["/"]
    found = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            found.append(root)
    return found


def start_folder(preferred: str = "") -> Path:
    """Where the folder picker opens: the last used folder, else home."""
    for candidate in (preferred, str(Path.home())):
        if candidate:
            path = Path(candidate)
            if path.is_dir():
                return path
    return Path(drives()[0])


def list_dir(raw: str = "", suffixes: str = "") -> dict[str, object]:
    """One level of the filesystem, for the picker.

    Folders always. Files only when the caller names the extensions it wants -
    choosing *where* a workbook goes has no use for a list of the PDFs already
    in the folder, but choosing a member list very much does.
    """
    path = start_folder(raw)
    try:
        path = path.resolve()
    except OSError:
        path = start_folder("")

    wanted = {s.strip().lower() for s in suffixes.split(",") if s.strip()}
    dirs: list[str] = []
    files: list[str] = []
    error = ""
    try:
        for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            if len(dirs) + len(files) >= MAX_ENTRIES:
                break
            if child.name.startswith((".", "~$")):
                continue          # dotfiles, and Excel's lock files
            try:
                if child.is_dir():
                    dirs.append(child.name)
                elif wanted and child.suffix.lower() in wanted:
                    files.append(child.name)
            except OSError:
                continue          # a disconnected network drive, or no rights
    except (OSError, PermissionError) as exc:
        error = f"Cannot list this folder: {exc}"

    parent = str(path.parent) if path.parent != path else ""
    return {
        "path": str(path),
        "parent": parent,
        "dirs": dirs,
        "files": files,
        "drives": drives(),
        "writable": os.access(path, os.W_OK),
        "truncated": len(dirs) + len(files) >= MAX_ENTRIES,
        "error": error,
    }


def make_dir(parent: str, name: str) -> str:
    """Create a subfolder from the picker, so a new job folder needs no detour."""
    clean = (name or "").strip().strip(". ")
    if not clean or any(c in clean for c in '\\/:*?"<>|'):
        raise ValueError("That folder name contains characters Windows will not accept.")
    target = Path(parent) / clean
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def check_folder(raw: str) -> tuple[Path | None, str]:
    """Validate a typed output folder. Returns ``(path, problem)``.

    The engineer types this path, so a typo has to come back as a sentence in
    the page rather than as a traceback after a five-minute extraction run.
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return None, "No folder given."
    path = Path(text)
    if not path.is_absolute():
        return None, f"'{text}' is not a full path - include the drive, e.g. C:\\Jobs."
    if path.exists() and not path.is_dir():
        return None, f"'{text}' is a file, not a folder."
    if not path.exists():
        return path, f"'{text}' does not exist yet - it will be created."
    if not os.access(path, os.W_OK):
        return None, f"'{text}' cannot be written to. Check your permissions."
    return path, ""


def safe_component(raw: str, fallback: str) -> str:
    """One path component with nothing in it the OS will reject."""
    clean = "".join(c for c in (raw or "").strip() if c not in '\\/:*?"<>|').strip(". ")
    return clean or fallback


def safe_name(raw: str, fallback: str) -> str:
    """A workbook file name, keeping the engineer's wording.

    The extension is supplied when it is missing: "Drawing Register" typed into
    the name box should produce a workbook Excel will open, not a file Windows
    has no handler for.
    """
    clean = safe_component(raw, "")
    if not clean:
        return fallback
    if not clean.lower().endswith((".xlsx", ".xlsm")):
        clean += ".xlsx"
    return clean


def resolve_target(folder: str, name: str, fallback_dir: Path, fallback_name: str,
                   host: str | None) -> Path:
    """Where a workbook should be written.

    Falls back to the sandbox whenever a local folder is not available or not
    asked for, which is what keeps a shared deployment working unchanged.
    """
    filename = safe_name(name, fallback_name)
    if folder.strip() and is_local_client(host):
        path, problem = check_folder(folder)
        if path is None:
            raise ValueError(problem)
        path.mkdir(parents=True, exist_ok=True)
        return path / filename
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir / filename


def open_path(path: str | Path) -> None:
    """Open a file or folder with whatever the OS uses for it.

    Only ever called for a path this process wrote - see ``is_remembered``.
    """
    target = Path(path)
    if sys.platform == "win32":
        os.startfile(str(target))       # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def reveal_path(path: str | Path) -> None:
    """Show a file in its folder, so the engineer can see it landed."""
    target = Path(path)
    if sys.platform == "win32" and target.is_file():
        subprocess.Popen(["explorer", "/select,", str(target)])
        return
    open_path(target if target.is_dir() else target.parent)
