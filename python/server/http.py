"""HTTP server layer — decoupled from command logic."""

import errno
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from server.formatters import NumpyEncoder

logger = logging.getLogger("vime")


class VimeHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with strict port exclusivity."""

    allow_reuse_address = False


def _parse_request_json(handler):
    """Parse JSON body from an HTTP request handler."""
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def make_handler(dispatch_fn, close_handles_fn):
    """Create an HTTP request handler class.

    Args:
        dispatch_fn: callable(payload) -> dict, routes commands.
        close_handles_fn: callable(), closes open file handles on shutdown.
    """

    class VimeHandler(BaseHTTPRequestHandler):
        server_version = "VIMEHTTP/1.0"

        def _send_json(self, status_code, payload):
            body = json.dumps(payload, cls=NumpyEncoder).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/shutdown":
                logger.info("Shutdown requested via HTTP")
                close_handles_fn()
                self._send_json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            cmd = parsed.path.lstrip("/")
            if not cmd:
                self._send_json(404, {"ok": False, "error": "Unknown route"})
                return

            payload = _parse_request_json(self)
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
                return

            payload["cmd"] = cmd
            response = dispatch_fn(payload)
            self._send_json(200, response)

        def log_message(self, fmt, *args):
            logger.debug("%s - %s", self.address_string(), fmt % args)

    return VimeHandler


def bind_http_server(host, start_port, max_attempts, handler_cls):
    """Bind HTTP server with incremental port fallback."""
    attempts = max(1, int(max_attempts))
    for offset in range(attempts):
        port = start_port + offset
        try:
            return VimeHTTPServer((host, port), handler_cls), port
        except OSError as exc:
            win_addr_in_use = getattr(errno, "WSAEADDRINUSE", 10048)
            if exc.errno in (errno.EADDRINUSE, win_addr_in_use):
                logger.info("Port %s already in use, trying %s", port, port + 1)
                continue
            raise

    end_port = start_port + attempts - 1
    raise RuntimeError(
        f"No open port found for {host} in range {start_port}-{end_port}"
    )
