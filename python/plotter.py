import numpy as np

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
