"""What the packaged executable depends on.

The build is the one path that cannot be exercised by importing a module, so
these cover the parts of it that are ordinary Python: the entry point, and the
two places the code asks whether it is frozen. A build that starts and then dies
looking for its templates is the failure worth preventing here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))


def test_the_entry_point_imports_without_starting_a_server():
    """Importing run_app must not bind a port or open a browser."""
    import run_app

    assert callable(run_app.main)
    assert run_app.HOST == "127.0.0.1", "a packaged app must not bind a public address"


def test_it_finds_a_free_port_when_the_first_is_taken():
    """A second copy, or anything else on 8000, must not be a bare traceback."""
    import socket

    import run_app

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind((run_app.HOST, 0))
        taken = held.getsockname()[1]
        held.listen(1)
        assert run_app.free_port(run_app.HOST, taken, 5) == taken + 1


def test_no_free_port_is_a_message_not_a_traceback():
    import socket

    import run_app

    holders = []
    try:
        base = run_app.free_port(run_app.HOST, 8300, 40)
        for offset in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((run_app.HOST, base + offset))
            s.listen(1)
            holders.append(s)
        with pytest.raises(SystemExit) as caught:
            run_app.free_port(run_app.HOST, base, 3)
        assert "free port" in str(caught.value)
    finally:
        for s in holders:
            s.close()


def test_the_build_script_ships_the_templates_and_static_files():
    """app.main reads them from the bundle root, so they must be added there.

    A build missing these succeeds and then fails on the first request, which
    is a much worse way to find out.
    """
    script = (WEB / "Build FabDoc Web.bat").read_text(encoding="utf-8")
    assert '--add-data "templates;templates"' in script
    assert '--add-data "static;static"' in script
    # fabdoc is imported through a runtime sys.path insert a static analyser
    # cannot follow, so the path has to be given explicitly.
    assert '--paths ".."' in script
    for package in ("uvicorn", "fitz"):
        assert f"--collect-all {package}" in script, f"{package} loads modules by name"


def test_the_build_excludes_the_scientific_stack():
    """PyInstaller follows imports through everything installed, not just ours.

    On a machine that also does data work the analysis walked
    pandas -> scipy -> tensorflow -> keras and started bundling gigabytes into
    a drawing register. Nothing in the app imports any of them.
    """
    script = (WEB / "Build FabDoc Web.bat").read_text(encoding="utf-8")
    for heavy in ("tensorflow", "keras", "torch", "pandas", "scipy", "numpy",
                  "matplotlib", "IPython"):
        assert f"--exclude-module {heavy}" in script, f"{heavy} would be bundled"


def test_the_app_really_does_not_import_the_excluded_packages():
    """The exclusions are only safe while that stays true."""
    import ast

    roots = [WEB / "app", WEB / "run_app.py",
             Path(__file__).resolve().parents[1] / "fabdoc"]
    files = []
    for root in roots:
        files.extend([root] if root.is_file() else sorted(root.rglob("*.py")))

    banned = {"tensorflow", "keras", "torch", "pandas", "scipy", "numpy",
              "matplotlib", "sklearn", "IPython", "PIL", "cv2"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            clash = banned.intersection(names)
            assert not clash, f"{path.name} imports {clash}, which the build excludes"


def test_data_lives_beside_the_executable_when_frozen(monkeypatch, tmp_path: Path):
    """A one-file build unpacks to a temp directory that is deleted on exit.

    Keeping the trackers in there would lose every package chain on close.
    """
    from app import workspace as ws

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "FabDoc Organizer.exe"))
    monkeypatch.delenv("FABDOC_DATA", raising=False)
    assert ws._data_root() == tmp_path / "data"


def test_the_data_folder_can_be_moved_with_an_env_var(monkeypatch, tmp_path: Path):
    from app import workspace as ws

    monkeypatch.setenv("FABDOC_DATA", str(tmp_path / "shared"))
    assert ws._data_root() == tmp_path / "shared"
