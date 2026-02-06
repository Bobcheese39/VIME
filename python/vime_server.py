#!/usr/bin/env python3
"""
VIME Server - Persistent Python backend for the VIME Vim H5 viewer.

Communicates with Vim over stdin/stdout using the Vim JSON channel protocol.
Protocol: Vim sends [msgid, {command}], server responds with [msgid, {result}].

Keeps HDF5 data in memory so files only need to be loaded once.
"""

import sys
import json
import os
from plotter import braille_plot
import numpy as np
import pandas as pd
from tabulate import tabulate


class VimeServer:
    """Persistent server that holds H5 data and responds to Vim commands."""

    def __init__(self):
        self.store = None          # pd.HDFStore object
        self.filepath = None       # Path to the currently open file
        self.current_df = None     # Last-fetched DataFrame
        self.current_table = None  # Name of the last-fetched table

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def dispatch(self, payload):
        """Route a command dict to the appropriate handler."""
        cmd = payload.get("cmd", "")
        handlers = {
            "open": self.cmd_open,
            "list": self.cmd_list,
            "table": self.cmd_table,
            "plot": self.cmd_plot,
            "info": self.cmd_info,
            "close": self.cmd_close,
        }
        handler = handlers.get(cmd)
        if handler is None:
            return {"ok": False, "error": f"Unknown command: {cmd}"}
        try:
            return handler(payload)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def cmd_open(self, payload):
        """Open an HDF5 file and return the list of tables."""
        filepath = payload.get("file", "")
        if not filepath or not os.path.isfile(filepath):
            return {"ok": False, "error": f"File not found: {filepath}"}

        # Close any previously open store
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass

        self.filepath = filepath
        self.store = pd.HDFStore(filepath, mode="r")
        self.current_df = None
        self.current_table = None

        tables = self._get_table_list()
        return {"ok": True, "tables": tables}

    def cmd_list(self, _payload):
        """Return the list of tables in the currently open file."""
        if self.store is None:
            return {"ok": False, "error": "No file open"}
        tables = self._get_table_list()
        return {"ok": True, "tables": tables}

    def cmd_table(self, payload):
        """Read a table and return its formatted content."""
        if self.store is None:
            return {"ok": False, "error": "No file open"}

        name = payload.get("name", "")
        head = payload.get("head", 100)

        if name not in self.store:
            return {"ok": False, "error": f"Table not found: {name}"}

        df = pd.read_hdf(self.filepath, key=name)
        self.current_df = df
        self.current_table = name

        display_df = df.head(head) if head and len(df) > head else df

        content = tabulate(
            display_df,
            headers="keys",
            tablefmt="heavy_grid",
            showindex=False,
            stralign="left",
            numalign="left",
        )

        # Add a header line with table info
        shape_info = f"  [{len(df)} rows x {len(df.columns)} cols]"
        if head and len(df) > head:
            shape_info += f"  (showing first {head})"
        header = f"{name}{shape_info}"

        columns = list(df.columns)
        return {
            "ok": True,
            "content": header + "\n\n" + content,
            "columns": columns,
            "name": name,
        }

    def cmd_plot(self, payload):
        """Generate a braille Unicode plot from the current table."""
        if self.current_df is None:
            return {"ok": False, "error": "No table loaded. Open a table first."}

        cols = payload.get("cols", [])
        plot_type = payload.get("type", "line")
        width = payload.get("width", 72)
        height = payload.get("height", 20)

        if len(cols) < 2:
            return {"ok": False, "error": "Need at least 2 column indices (x y)"}

        df = self.current_df

        # Resolve column references (index or name)
        try:
            x_col = self._resolve_column(df, cols[0])
            y_col = self._resolve_column(df, cols[1])
        except (IndexError, KeyError) as exc:
            return {"ok": False, "error": f"Invalid column: {exc}"}

        x = df[x_col].values.astype(float)
        y = df[y_col].values.astype(float)

        # Remove NaN pairs
        mask = ~(np.isnan(x) | np.isnan(y))
        x, y = x[mask], y[mask]

        if len(x) == 0:
            return {"ok": False, "error": "No valid data points to plot"}

        plot_lines = braille_plot(x, y, width=width, height=height,
                                  x_label=str(x_col), y_label=str(y_col),
                                  plot_type=plot_type)
        title = f"Plot: {x_col} vs {y_col}  ({self.current_table})"
        content = title + "\n\n" + "\n".join(plot_lines)
        return {"ok": True, "content": content}

    def cmd_info(self, payload):
        """Return detailed info about a table."""
        if self.store is None:
            return {"ok": False, "error": "No file open"}

        name = payload.get("name", "")
        if name not in self.store:
            return {"ok": False, "error": f"Table not found: {name}"}

        df = pd.read_hdf(self.filepath, key=name)

        lines = []
        lines.append(f"Table: {name}")
        lines.append(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")
        lines.append("")
        lines.append("Columns:")
        lines.append("─" * 50)
        for i, col in enumerate(df.columns):
            dtype = df[col].dtype
            non_null = df[col].count()
            lines.append(f"  {i:>3}  {col:<30} {str(dtype):<12} ({non_null} non-null)")
        lines.append("─" * 50)

        # Numeric summary
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        if len(numeric_cols) > 0:
            lines.append("")
            lines.append("Numeric Summary:")
            desc = df[numeric_cols].describe().T
            lines.append(tabulate(desc, headers="keys", tablefmt="heavy_grid",
                                  stralign="left", numalign="left"))

        return {"ok": True, "content": "\n".join(lines)}

    def cmd_close(self, _payload):
        """Close the store and exit."""
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass
        sys.exit(0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_table_list(self):
        """Return a list of dicts with table metadata."""
        tables = []
        for key in self.store.keys():
            try:
                storer = self.store.get_storer(key)
                nrows = storer.nrows if hasattr(storer, "nrows") else "?"
                if hasattr(storer, "ncols"):
                    ncols = storer.ncols
                elif hasattr(storer, "attrs") and hasattr(storer.attrs, "non_index_axes"):
                    axes = storer.attrs.non_index_axes
                    ncols = len(axes[0][1]) if axes else "?"
                else:
                    ncols = "?"
            except Exception:
                nrows = "?"
                ncols = "?"
            tables.append({"name": key, "rows": nrows, "cols": ncols})
        return tables

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
# Main loop - Vim JSON channel protocol
# ======================================================================

def main():
    """Main event loop: read JSON commands from stdin, write responses to stdout."""
    server = VimeServer()

    # Ensure stdout is unbuffered for reliable communication
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        # Vim JSON channel protocol: [msgid, payload]
        if not isinstance(msg, list) or len(msg) < 2:
            continue

        msgid = msg[0]
        payload = msg[1]

        if not isinstance(payload, dict):
            response = {"ok": False, "error": "Payload must be a dict"}
        else:
            response = server.dispatch(payload)

        # Send response: [msgid, result]
        out = json.dumps([msgid, response])
        sys.stdout.write(out + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
