"""Entry point for the packaged executable.

A frozen build cannot run ``python -m uvicorn app.main:app`` - there is no
python and no command line - so the server is started in-process here instead.

This is also the only place that decides what "running the app" means for an
engineer double-clicking an icon: bind to loopback, find a port that is free,
open the browser at it, and keep the console as the log until it is closed.

It runs unfrozen too, so the packaged behaviour can be tested without building:

    python run_app.py
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Running from a real folder, fabdoc lives one level up. Frozen, it is inside
# the bundle and already importable; this insert is harmless either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HOST = "127.0.0.1"
FIRST_PORT = 8000
PORT_ATTEMPTS = 20


def free_port(host: str, first: int, attempts: int) -> int:
    """The first port nothing else is listening on.

    A second copy of the app, or anything else already on 8000, would otherwise
    fail with a bare "address in use" traceback in a window that closes.
    """
    for offset in range(attempts):
        port = first + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(
        f"No free port between {first} and {first + attempts - 1}. "
        "Close whatever is using them and start the app again."
    )


def open_when_ready(url: str, timeout: float = 25.0) -> None:
    """Open the browser once the server answers, not before.

    Launching it first shows the engineer a connection error while uvicorn is
    still starting, which on a cold PyInstaller unpack takes a few seconds.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((HOST, int(url.rsplit(":", 1)[1]))) == 0:
                break
        time.sleep(0.3)
    try:
        webbrowser.open(url)
    except Exception:
        pass          # the URL is printed on the console regardless


def main() -> int:
    # The packaged app is one engineer on one machine, which is what earns it
    # access to real folders. Set FABDOC_LOCAL=0 to run it as a shared server.
    os.environ.setdefault("FABDOC_LOCAL", "1")

    import uvicorn

    from app.main import app

    port = free_port(HOST, FIRST_PORT, PORT_ATTEMPTS)
    url = f"http://{HOST}:{port}"

    from app import workspace as ws
    print()
    print(f"  FabDoc Organizer is running at {url}")
    print(f"  Its files are kept in {ws.DATA_ROOT}")
    print("  Close this window to stop it.")
    print()

    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    try:
        uvicorn.run(app, host=HOST, port=port, log_level="info")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    # PyInstaller re-executes the binary for any child process; without this a
    # frozen build can spawn copies of itself instead of starting.
    multiprocessing.freeze_support()
    raise SystemExit(main())
