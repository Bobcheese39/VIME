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

logger = logging.getLogger("vime")


def configure_logging():
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "VIME [%(levelname)s] %(asctime)s  %(message)s", "%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main():
    configure_logging()

    parser = argparse.ArgumentParser(description="VIME HTTP server")
    parser.add_argument("--host", default=os.environ.get("VIME_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIME_HTTP_PORT", "51789")))
    parser.add_argument(
        "--port-retries",
        type=int,
        default=int(os.environ.get("VIME_HTTP_PORT_RETRIES", "100")),
        help="Maximum number of incremental ports to try, starting from --port",
    )
    args = parser.parse_args()

    from server.state import ServerState
    from server.app import dispatch
    from server.http import make_handler, bind_http_server

    state = ServerState()
    dispatch_fn = lambda payload: dispatch(state, payload)
    handler_cls = make_handler(dispatch_fn, state.close_handles)

    try:
        httpd, bound_port = bind_http_server(
            args.host, args.port, args.port_retries, handler_cls
        )
    except Exception as exc:
        logger.error("Failed to bind HTTP server: %s", exc)
        sys.exit(1)

    logger.info("VIME HTTP server listening on %s:%s", args.host, bound_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("HTTP server interrupted, shutting down")
    finally:
        state.close_handles()
        httpd.server_close()
        logger.info("goodbye!")


if __name__ == "__main__":
    main()
