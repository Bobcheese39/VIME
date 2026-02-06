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
# Braille Plotter
# ======================================================================

# Braille dot bit positions for sub-pixel (dx, dy) within a character cell.
# Each braille character is a 2-wide x 4-tall grid of dots.
# Character = chr(0x2800 + bitmask)
BRAILLE_MAP = [
    [0x01, 0x02, 0x04, 0x40],  # left column  (dx=0), rows 0-3
    [0x08, 0x10, 0x20, 0x80],  # right column (dx=1), rows 0-3
]


class BrailleCanvas:
    """A canvas that renders using braille Unicode characters (U+2800-U+28FF).

    Each character cell encodes a 2x4 sub-pixel grid, giving 2x horizontal
    and 4x vertical resolution compared to regular character plotting.
    """

    def __init__(self, width, height):
        """
        Args:
            width:  canvas width in character cells
            height: canvas height in character cells
        """
        self.char_width = width
        self.char_height = height
        self.pixel_width = width * 2
        self.pixel_height = height * 4
        self._cells = [[0] * width for _ in range(height)]

    def set_pixel(self, px, py):
        """Set a sub-pixel at coordinates (px, py)."""
        if px < 0 or px >= self.pixel_width or py < 0 or py >= self.pixel_height:
            return
        cx = px // 2
        cy = py // 4
        dx = px % 2
        dy = py % 4
        self._cells[cy][cx] |= BRAILLE_MAP[dx][dy]

    def line(self, x0, y0, x1, y1):
        """Draw a line using Bresenham's algorithm on the sub-pixel grid."""
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        while True:
            self.set_pixel(x0, y0)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def render(self):
        """Return list of strings (one per character row)."""
        lines = []
        for row in self._cells:
            lines.append("".join(chr(0x2800 + cell) for cell in row))
        return lines


def braille_plot(x, y, width=72, height=20, x_label="x", y_label="y",
                 plot_type="line"):
    """
    Generate a braille Unicode plot of x vs y data.

    Each character cell uses a 2x4 braille dot grid, giving much higher
    effective resolution than traditional ASCII plotting.

    Args:
        x, y: numpy arrays of data
        width: total width in characters (including y-axis labels)
        height: plot area height in character cells
        x_label, y_label: axis labels
        plot_type: "line" or "scatter"

    Returns:
        List of strings (lines of the plot)
    """
    y_axis_width = 10
    plot_width = width - y_axis_width - 1
    plot_height = height

    if plot_width < 10 or plot_height < 5:
        return ["Plot area too small. Increase width/height."]

    # Data bounds
    x_min, x_max = float(np.min(x)), float(np.max(x))
    y_min, y_max = float(np.min(y)), float(np.max(y))

    # Handle degenerate cases
    if x_max == x_min:
        x_min -= 1
        x_max += 1
    if y_max == y_min:
        y_min -= 1
        y_max += 1

    # Add a small margin
    y_range = y_max - y_min
    y_min -= y_range * 0.05
    y_max += y_range * 0.05

    # Create braille canvas
    canvas = BrailleCanvas(plot_width, plot_height)
    pw = canvas.pixel_width - 1
    ph = canvas.pixel_height - 1

    def to_pixel(xv, yv):
        px = int(round((xv - x_min) / (x_max - x_min) * pw))
        py = int(round((1.0 - (yv - y_min) / (y_max - y_min)) * ph))
        px = max(0, min(pw, px))
        py = max(0, min(ph, py))
        return px, py

    if plot_type == "line":
        # Sort by x for line drawing
        order = np.argsort(x)
        xs, ys = x[order], y[order]

        for i in range(len(xs)):
            px, py = to_pixel(xs[i], ys[i])
            canvas.set_pixel(px, py)

            # Draw line segments between consecutive points
            if i > 0:
                px0, py0 = to_pixel(xs[i - 1], ys[i - 1])
                canvas.line(px0, py0, px, py)
    else:
        # Scatter plot
        for i in range(len(x)):
            px, py = to_pixel(x[i], y[i])
            canvas.set_pixel(px, py)

    # Render the canvas
    braille_lines = canvas.render()

    # Build output with y-axis labels
    lines = []
    y_ticks = [y_max, (y_max + y_min) / 2, y_min]
    y_tick_rows = [0, plot_height // 2, plot_height - 1]
    tick_map = dict(zip(y_tick_rows, y_ticks))

    for row in range(plot_height):
        if row in tick_map:
            label = _format_num(tick_map[row], y_axis_width - 2)
            prefix = f"{label:>{y_axis_width - 1}} ┤"
        else:
            prefix = " " * (y_axis_width - 1) + " │"
        lines.append(prefix + braille_lines[row])

    # X-axis line with Unicode corner
    lines.append(" " * (y_axis_width - 1) + " └" + "─" * plot_width)

    # X-axis tick labels
    left_label = _format_num(x_min, 8)
    mid_label = _format_num((x_min + x_max) / 2, 8)
    right_label = _format_num(x_max, 8)

    tick_str = list(" " * plot_width)
    _place_label(tick_str, 0, left_label)
    _place_label(tick_str, plot_width // 2 - len(mid_label) // 2, mid_label)
    _place_label(tick_str, plot_width - len(right_label), right_label)
    lines.append(" " * y_axis_width + " " + "".join(tick_str))

    # Axis labels
    lines.append("")
    center_x = y_axis_width + plot_width // 2 - len(x_label) // 2
    lines.append(" " * max(0, center_x) + x_label)

    return lines


def _format_num(val, max_width):
    """Format a number to fit within max_width characters."""
    if val == 0:
        s = "0"
    elif abs(val) < 0.01 or abs(val) >= 1e6:
        s = f"{val:.2e}"
    elif val == int(val):
        s = str(int(val))
    else:
        s = f"{val:.2f}"
    if len(s) > max_width:
        s = f"{val:.1e}"
    return s[:max_width]


def _place_label(char_list, pos, label):
    """Place a label string into a character list at the given position."""
    for i, ch in enumerate(label):
        idx = pos + i
        if 0 <= idx < len(char_list):
            char_list[idx] = ch


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
