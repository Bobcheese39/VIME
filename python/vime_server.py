#!/usr/bin/env python3
"""
VIME Server - Persistent Python backend for the VIME Vim H5 viewer.

Communicates with Vim over HTTP using JSON request/response payloads.
Protocol: Client sends POST requests with JSON payloads, server responds with JSON.

Keeps HDF5 data in memory so files only need to be loaded once.
"""

import sys
import os
import logging
import argparse
import threading
import time

logger = logging.getLogger("vime")


def configure_logging(debug=False):
    if logger.handlers:
        return
    formatter = logging.Formatter(
        "VIME [%(levelname)s] %(asctime)s  %(message)s", "%H:%M:%S"
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if debug:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fh = logging.FileHandler(os.path.join(root_dir, "debug.txt"), mode="w")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)


def main():
    parser = argparse.ArgumentParser(description="VIME HTTP server")
    parser.add_argument("--host", default=os.environ.get("VIME_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIME_HTTP_PORT", "51789")))
    parser.add_argument(
        "--port-retries",
        type=int,
        default=int(os.environ.get("VIME_HTTP_PORT_RETRIES", "100")),
        help="Maximum number of incremental ports to try, starting from --port",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=int(os.environ.get("VIME_IDLE_TIMEOUT", "900")),
        help="Shut down after this many seconds with no requests (0 disables)",
    )
    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="Enable DEBUG logging and write to debug.txt",
    )
    args = parser.parse_args()

    configure_logging(debug=args.debug)

    from server.state import ServerState
    from server.app import dispatch
    from server.http import make_handler, bind_http_server

    state = ServerState()
    dispatch_fn = lambda payload: dispatch(state, payload)
    handler_cls = make_handler(dispatch_fn, state.close_handles, state.mark_activity)

    try:
        httpd, bound_port = bind_http_server(
            args.host, args.port, args.port_retries, handler_cls
        )
    except Exception as exc:
        logger.error("Failed to bind HTTP server: %s", exc)
        sys.exit(1)

    if args.idle_timeout > 0:
        start_idle_watcher(httpd, state, args.idle_timeout)

    logger.info("VIME HTTP server listening on %s:%s", args.host, bound_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("HTTP server interrupted, shutting down")
    finally:
        state.close_handles()
        httpd.server_close()
        logger.info("goodbye!")


def start_idle_watcher(httpd, state, idle_timeout):
    """Start a daemon thread that shuts the server down after *idle_timeout*
    seconds with no requests.
    """
    # Poll a few times per timeout window, but at least every second.
    interval = max(1, min(idle_timeout, 30))

    def _watch():
        while True:
            time.sleep(interval)
            idle = time.monotonic() - state.last_activity
            if idle >= idle_timeout:
                logger.info(
                    "Idle for %.0fs (>= %ds), shutting down", idle, idle_timeout
                )
                state.close_handles()
                httpd.shutdown()
                return

    thread = threading.Thread(target=_watch, name="vime-idle-watcher", daemon=True)
    thread.start()
    logger.info("Idle watcher started (timeout=%ds)", idle_timeout)


if __name__ == "__main__":
    main()
