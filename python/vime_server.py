#!/usr/bin/env python3
"""
VIME Server - Persistent Python backend for the VIME Vim H5 viewer.

Communicates with Vim over HTTP using JSON request/response payloads.
Protocol: Client sends POST requests with JSON payloads, server responds with JSON.

Keeps HDF5 data in memory so files only need to be loaded once.
"""

import sys
import json
import os
import threading
import time
import logging
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from plotter import braille_plot
import numpy as np
from tabulate import tabulate
from data_loader import DataLoader
from test_compute import test_compute



class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types transparently."""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


logger = logging.getLogger("vime")


def configure_logging():
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "VIME %(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class VimeServer:
    """Persistent server that holds H5 data and responds to HTTP requests."""

    def __init__(self):
        self.loader = DataLoader()
        self.current_df = None     # Last-fetched DataFrame
        self.current_table = None  # Name of the last-fetched table
        self.virtual_tables = {}   # Virtual tables created by compute jobs
        self.compute_thread = None
        self.compute_state = "idle"
        self.compute_message = ""
        self.compute_table_name = None
        self.compute_error = None

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def dispatch(self, payload):
        """Route a command dict to the appropriate handler."""
        cmd = payload.get("cmd", "")
        logger.info("Dispatch command: %s", cmd)
        handlers = {
            "open": self.cmd_open,
            "list": self.cmd_list,
            "table": self.cmd_table,
            "plot": self.cmd_plot,
            "info": self.cmd_info,
            "close": self.cmd_close,
            "compute_start": self.cmd_compute_start,
            "compute_status": self.cmd_compute_status,
        }
        handler = handlers.get(cmd)
        if handler is None:
            logger.warning("Unknown command: %s", cmd)
            return {"ok": False, "error": f"Unknown command: {cmd}"}
        try:
            return handler(payload)
        except Exception as exc:
            logger.exception("Command failed: %s", cmd)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _close_handles(self):
        """Close any open file handles."""
        logger.info("Closing open file handles")
        self.loader.close()

    def cmd_open(self, payload):
        """Open an HDF5 file and return the list of tables."""
        filepath = payload.get("file", "")
        logger.info("Opening file: %s", filepath)
        if not filepath or not os.path.isfile(filepath):
            logger.warning("File not found: %s", filepath)
            return {"ok": False, "error": f"File not found: {filepath}"}

        self._close_handles()
        self.current_df = None
        self.current_table = None

        try:
            self.loader.open(filepath)
        except Exception as exc:
            logger.exception("Failed to open file: %s", filepath)
            return {"ok": False, "error": str(exc)}
        logger.info("File opened: %s", filepath)
        return {"ok": True, "tables": self._get_table_list()}

    def cmd_list(self, _payload):
        """Return the list of tables in the currently open file."""
        if not self.loader.is_open:
            logger.warning("List requested with no file open")
            return {"ok": False, "error": "No file open"}
        tables = self._get_table_list()
        logger.info("Listed %d tables", len(tables))
        return {"ok": True, "tables": tables}

    def cmd_table(self, payload):
        """Read a table and return its formatted content."""
        if not self.loader.is_open:
            logger.warning("Table requested with no file open")
            return {"ok": False, "error": "No file open"}

        name = payload.get("name", "")
        head = payload.get("head", 100)
        fast = bool(payload.get("fast", True))
        logger.info("Loading table: %s (head=%s fast=%s)", name, head, fast)

        if fast:
            data = self.loader.load_table_fast(name)
            if data is None:
                logger.warning("Fast table not found or unsupported: %s", name)
                return {"ok": False, "error": f"Table not found or fast read unsupported: {name}"}
            content = self._format_fast_table(name, data)
            return {"ok": True, "content": content, "columns": [], "name": name, "fast": True}

        df = self._load_table(name)
        if df is None:
            logger.warning("Table not found: %s", name)
            return {"ok": False, "error": f"Table not found: {name}"}

        self.current_df = df
        self.current_table = name

        display_df = df.head(head) if head and len(df) > head else df

        content = tabulate(
            display_df,
            headers="keys",
            tablefmt="plain",
            showindex=False,
            stralign="left",
            numalign="left",
        )

        # Add a header line with table info
        shape_info = f"  [{len(df)} rows x {len(df.columns)} cols]"
        if head and len(df) > head:
            shape_info += f"  (showing first {head})"
        header = f"{name}{shape_info}"

        columns = [str(c) for c in df.columns]
        logger.info("Loaded table: %s (rows=%d cols=%d)", name, len(df), len(df.columns))
        return {
            "ok": True,
            "content": header + "\n\n" + content,
            "columns": columns,
            "name": name,
        }

    def cmd_plot(self, payload):
        """Generate a braille Unicode plot from the current table."""
        if self.current_df is None:
            logger.warning("Plot requested with no table loaded")
            return {"ok": False, "error": "No table loaded. Open a table first."}

        cols = payload.get("cols", [])
        plot_type = payload.get("type", "line")
        width = payload.get("width", 72)
        height = payload.get("height", 20)
        logger.info("Plot request: cols=%s type=%s size=%sx%s", cols, plot_type, width, height)

        if len(cols) < 2:
            return {"ok": False, "error": "Need at least 2 column indices (x y)"}

        df = self.current_df

        # Resolve column references (index or name)
        try:
            x_col = self._resolve_column(df, cols[0])
            y_col = self._resolve_column(df, cols[1])
        except (IndexError, KeyError) as exc:
            logger.warning("Invalid plot column: %s", exc)
            return {"ok": False, "error": f"Invalid column: {exc}"}

        # Convert to float with error handling for non-numeric data
        try:
            x = df[x_col].values.astype(float)
        except (ValueError, TypeError) as exc:
            logger.warning("Non-numeric x column %s: %s", x_col, exc)
            return {"ok": False, "error": f"Cannot convert column '{x_col}' to numeric: {exc}"}
        
        try:
            y = df[y_col].values.astype(float)
        except (ValueError, TypeError) as exc:
            logger.warning("Non-numeric y column %s: %s", y_col, exc)
            return {"ok": False, "error": f"Cannot convert column '{y_col}' to numeric: {exc}"}

        # Remove NaN pairs
        mask = ~(np.isnan(x) | np.isnan(y))
        x, y = x[mask], y[mask]

        if len(x) == 0:
            logger.warning("No valid data points after NaN filtering")
            return {"ok": False, "error": "No valid data points to plot"}

        plot_lines = braille_plot(x, y, width=width, height=height,
                                  x_label=str(x_col), y_label=str(y_col),
                                  plot_type=plot_type)
        title = f"Plot: {x_col} vs {y_col}  ({self.current_table})"
        content = title + "\n\n" + "\n".join(plot_lines)
        logger.info("Plot generated: %s vs %s (%d points)", x_col, y_col, len(x))
        return {"ok": True, "content": content}

    def cmd_info(self, payload):
        """Return detailed info about a table."""
        if not self.loader.is_open:
            logger.warning("Info requested with no file open")
            return {"ok": False, "error": "No file open"}

        name = payload.get("name", "")
        logger.info("Info requested for table: %s", name)

        df = self._load_table(name)
        if df is None:
            logger.warning("Info table not found: %s", name)
            return {"ok": False, "error": f"Table not found: {name}"}

        lines = []
        lines.append(f"Table: {name}")
        lines.append(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")
        lines.append("")
        lines.append("Columns:")
        lines.append("─" * 50)
        for i, col in enumerate(df.columns):
            dtype = df[col].dtype
            non_null = df[col].count()
            lines.append(f"  {i:>3}  {str(col):<30} {str(dtype):<12} ({non_null} non-null)")
        lines.append("─" * 50)

        # Numeric summary
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        if len(numeric_cols) > 0:
            lines.append("")
            lines.append("Numeric Summary:")
            desc = df[numeric_cols].describe().T
            lines.append(
                tabulate(
                    desc,
                    headers="keys",
                    tablefmt="plain",
                    stralign="left",
                    numalign="left",
                )
            )

        return {"ok": True, "content": "\n".join(lines)}

    def cmd_close(self, _payload):
        """Close the store (HTTP shutdown handled separately)."""
        logger.info("Close requested")
        self._close_handles()
        return {"ok": True}

    def cmd_compute_start(self, _payload):
        """Start a background compute job using test_compute()."""
        if self.compute_thread is not None and self.compute_thread.is_alive():
            logger.warning("Compute start requested while already running")
            return {"ok": False, "error": "Compute already running", "status": "running"}

        self.compute_state = "running"
        self.compute_message = "Computing..."
        self.compute_table_name = None
        self.compute_error = None

        self.compute_thread = threading.Thread(
            target=self._run_compute_job, name="vime-compute", daemon=True
        )
        self.compute_thread.start()
        logger.info("Compute thread started")
        return {"ok": True, "status": self.compute_state, "message": self.compute_message}

    def cmd_compute_status(self, _payload):
        """Return the current compute job status."""
        logger.info("Compute status requested: %s", self.compute_state)
        return {
            "ok": True,
            "status": self.compute_state,
            "message": self.compute_message,
            "table": self.compute_table_name,
            "error": self.compute_error,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_table(self, name):
        """Load a table/dataset as a DataFrame from either backend.
        
        Returns:
            DataFrame if successful, None if not found.
        """
        if name in self.virtual_tables:
            logger.info("Loading virtual table: %s", name)
            return self.virtual_tables[name]["df"]
        logger.info("Loading table from store: %s", name)
        return self.loader.load_table(name)

    def _get_table_list(self):
        """Return a list of dicts with table metadata."""
        tables = list(self.loader.list_tables())
        if self.virtual_tables:
            logger.info("Adding %d virtual tables", len(self.virtual_tables))
            tables.extend(
                {
                    "name": entry["name"],
                    "rows": entry["rows"],
                    "cols": entry["cols"],
                }
                for entry in self.virtual_tables.values()
            )
        return tables

    @staticmethod
    def _format_fast_table(name, data):
        """Return a fast, raw string representation of table data."""
        try:
            shape = getattr(data, "shape", None)
            shape_info = f"  {shape}" if shape is not None else ""
            size = int(np.size(data))
            arr = np.asarray(data)
            body = np.array2string(
                arr,
                threshold=size if size > 0 else 1,
                max_line_width=200,
            )
            return f"{name}{shape_info}  [fast]\n\n{body}"
        except Exception:
            return f"{name}  [fast]\n\n{str(data)}"

    def _new_compute_name(self):
        base = "/__computed__/compute"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = f"{base}_{stamp}"
        if name not in self.virtual_tables:
            return name
        suffix = 1
        while f"{name}_{suffix}" in self.virtual_tables:
            suffix += 1
        return f"{name}_{suffix}"

    def _run_compute_job(self):
        try:
            logger.info("Compute job started")
            df = test_compute()
            name = self._new_compute_name()
            self.virtual_tables[name] = {
                "name": name,
                "df": df,
                "rows": int(df.shape[0]),
                "cols": int(df.shape[1]),
            }
            self.compute_table_name = name
            self.compute_state = "done"
            self.compute_message = f"Compute done: {name}"
            self.compute_error = None
            logger.info("Compute job completed: %s", name)
        except Exception as exc:
            self.compute_state = "error"
            self.compute_message = "Compute failed"
            self.compute_error = str(exc)
            logger.exception("Compute job failed")

    @staticmethod
    def _resolve_column(df, ref):
        """Resolve a column reference (int index or string name)."""
        if isinstance(ref, int):
            if ref < 0 or ref >= len(df.columns):
                raise IndexError(f"Column index {ref} out of range (0-{len(df.columns)-1})")
            return df.columns[ref]
        # Try as string name
        if ref in df.columns:
            return ref
        # Try parsing as int
        try:
            idx = int(ref)
            if 0 <= idx < len(df.columns):
                return df.columns[idx]
        except (ValueError, TypeError):
            pass
        raise KeyError(ref)





# ======================================================================
# HTTP server
# ======================================================================

def _parse_request_json(handler):
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


def make_handler(vime_server):
    class VimeHandler(BaseHTTPRequestHandler):
        server_version = "VIMEHTTP/1.0"

        def _send_json(self, status_code, payload):
            body = json.dumps(payload, cls=NumpyEncoder).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route_to_cmd(self, path):
            routes = {
                "/open": "open",
                "/list": "list",
                "/table": "table",
                "/plot": "plot",
                "/info": "info",
                "/compute_start": "compute_start",
                "/compute_status": "compute_status",
                "/close": "close",
                }
            return routes.get(path)
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
                vime_server._close_handles()
                self._send_json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            cmd = self._route_to_cmd(parsed.path)
            if cmd is None:
                self._send_json(404, {"ok": False, "error": "Unknown route"})
                return

            payload = _parse_request_json(self)
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
                return

            payload["cmd"] = cmd
            response = vime_server.dispatch(payload)
            self._send_json(200, response)

        def log_message(self, fmt, *args):
            logger.info("%s - %s", self.address_string(), fmt % args)

    return VimeHandler


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="VIME HTTP server")
    parser.add_argument("--host", default=os.environ.get("VIME_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIME_HTTP_PORT", "51789")))
    args = parser.parse_args()

    vime = VimeServer()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(vime))
    logger.info("VIME HTTP server listening on %s:%s", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("HTTP server interrupted, shutting down")
    finally:
        vime._close_handles()
        httpd.server_close()


if __name__ == "__main__":
    main()
