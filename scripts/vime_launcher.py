#!/usr/bin/env python3
"""
Cross-platform VIME launcher.

Starts the VIME Python backend (if not already running), discovers the
bound port, and runs the standard-library Python TUI.

The detached backend intentionally outlives the TUI.
"""

import argparse
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ── Configuration from environment ─────────────────────────────────────

ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PYTHON_CMD = os.environ.get("VIME_PYTHON", sys.executable or "python3")
HOST = os.environ.get("VIME_HTTP_HOST", "127.0.0.1")
SERVER_SCRIPT = os.path.join(ROOT_DIR, "python", "vime_server.py")


def _safe_int(value, default, minimum=1):
    """Parse *value* as int; return *default* when invalid or below *minimum*."""
    try:
        n = int(value)
        return n if n >= minimum else default
    except (ValueError, TypeError):
        return default


PORT_START = _safe_int(os.environ.get("VIME_HTTP_PORT", "51789"), 51789, minimum=1)
PORT_RETRIES = _safe_int(os.environ.get("VIME_HTTP_PORT_RETRIES", "100"), 100)
STARTUP_TIMEOUT = _safe_int(os.environ.get("VIME_SERVER_STARTUP_TIMEOUT", "30"), 30)
IDLE_TIMEOUT = _safe_int(os.environ.get("VIME_IDLE_TIMEOUT", "900"), 900, minimum=0)

logger = logging.getLogger("vime.launcher")


# ── Argument parsing ──────────────────────────────────────────────────


def parse_args():
    """Parse launcher arguments."""
    parser = argparse.ArgumentParser(
        description="VIME launcher",
        allow_abbrev=False,
    )
    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="Enable DEBUG logging and write to debug.txt",
    )
    parser.add_argument("files", nargs="+", help="HDF5 files to open")
    return parser.parse_args()


def configure_debug_logging():
    """Set up DEBUG-level logging to stderr and to ROOT_DIR/debug.txt."""
    formatter = logging.Formatter(
        "VIME [%(levelname)s] %(asctime)s  %(message)s", "%H:%M:%S"
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(
        os.path.join(ROOT_DIR, "debug.txt"), mode="w",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(stderr_handler)
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)


# ── Helpers ────────────────────────────────────────────────────────────


def is_healthy(port):
    """Return True if the server responds OK on *port*."""
    url = "http://{}:{}/health".format(HOST, port)
    try:
        resp = urllib.request.urlopen(url, timeout=1)
        resp.read()
        resp.close()
        return True
    except (urllib.error.URLError, OSError):
        return False


def find_healthy_port():
    """Scan the port range and return the first healthy port, or None."""
    for offset in range(max(PORT_RETRIES, 1)):
        port = PORT_START + offset
        if is_healthy(port):
            return port
    return None


# ── Server lifecycle ───────────────────────────────────────────────────


def start_server(debug=False):
    """Launch ``vime_server.py`` detached so it persists after the launcher
    (and TUI) exits, and return the Popen handle.
    """
    cmd = [
        PYTHON_CMD,
        SERVER_SCRIPT,
        "--host", HOST,
        "--port", str(PORT_START),
        "--port-retries", str(PORT_RETRIES),
        "--idle-timeout", str(IDLE_TIMEOUT),
    ]
    if debug:
        cmd.append("--debug")
    kwargs = {}
    if not debug:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    # Detach the daemon so it outlives this launcher process. On Windows, use a
    # new process group + DETACHED_PROCESS so Ctrl-C in the TUI console is not
    # forwarded to it. On POSIX, start a new session.
    if sys.platform == "win32":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | detached
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def wait_for_server(server_proc):
    """Poll until a healthy port is found or *STARTUP_TIMEOUT* expires.

    Returns the bound port on success, or None on timeout / early exit.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        # If the server process already exited, give up.
        if server_proc.poll() is not None:
            return None
        port = find_healthy_port()
        if port is not None:
            return port
        time.sleep(0.1)
    return None


def terminate_server(server_proc):
    """Best-effort termination of the server process."""
    if server_proc is None or server_proc.poll() is not None:
        return
    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        server_proc.wait(timeout=3)


# ── Main ───────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    if args.debug:
        configure_debug_logging()
        logger.debug("Debug mode enabled")

    server_proc = None
    active_port = PORT_START

    # 1. Check if a persistent server is already running on one of the ports.
    existing = find_healthy_port()
    if existing is not None:
        active_port = existing
    else:
        # 2. Start the server detached so it persists across TUI sessions.
        server_proc = start_server(debug=args.debug)

        # 3. Wait for the server to bind and become healthy.
        port = wait_for_server(server_proc)
        if port is None:
            end_port = PORT_START + PORT_RETRIES - 1
            print(
                "VIME: backend did not become healthy within {}s "
                "on {} ports {}-{}".format(STARTUP_TIMEOUT, HOST, PORT_START, end_port),
                file=sys.stderr,
            )
            terminate_server(server_proc)
            sys.exit(1)
        active_port = port

    # 4. Run the TUI in-process. The daemon remains detached and reaps itself
    #    via its idle timeout after the terminal frontend exits.
    python_dir = os.path.join(ROOT_DIR, "python")
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)
    from vime_tui import main as tui_main
    return tui_main(HOST, active_port, args.files)


if __name__ == "__main__":
    sys.exit(main())
