"""Terminal plotter: braille/quadrant-block/ASCII canvases with numpy prep helpers."""

import logging
import math

import numpy as np

logger = logging.getLogger("vime.plotter")

VALID_PLOT_TYPES = frozenset({"line", "scatter", "bar", "hist"})
# Charsets that draw 1 pixel per cell with a distinct marker glyph, in plain
# ASCII (as opposed to the sub-pixel, density-shaded unicode canvases).
VALID_CHARSETS = frozenset({"braille", "ascii", "simple", "extended"})
MARKER_CHARSETS = frozenset({"ascii", "simple"})
ASCII_MARKERS = list("*+#x%@&o")
# Braille fill levels for density (sparse → solid).
_BRAILLE_LEVELS = [
    0x00, 0x40, 0x44, 0x46, 0x47, 0x67, 0x6F, 0x7F, 0xFF,
]
_ASCII_DENSITY = " .:-=+*#%@"

# Braille dot bit positions for sub-pixel (dx, dy) within a character cell.
BRAILLE_MAP = [
    [0x01, 0x02, 0x04, 0x40],  # left column  (dx=0), rows 0-3
    [0x08, 0x10, 0x20, 0x80],  # right column (dx=1), rows 0-3
]

# Unicode quadrant block chars indexed by a 4-bit mask: 1=TL, 2=TR, 4=BL, 8=BR.
_QUADRANT_CHARS = [
    " ", "▘", "▝", "▀",
    "▖", "▌", "▞", "▛",
    "▗", "▚", "▐", "▜",
    "▄", "▙", "▟", "█",
]


class _SubPixelCanvas:
    """Shared Bresenham line/fill-column logic for sub-pixel unicode canvases.

    Subclasses set ``pixel_width``/``pixel_height`` and implement
    ``set_pixel(px, py)`` and ``render(density=False)``.
    """

    def line(self, x0, y0, x1, y1):
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

    def fill_column(self, cx, y_top, y_bottom):
        """Fill a character column from pixel row y_top..y_bottom inclusive."""
        if cx < 0 or cx >= self.char_width:
            return
        lo, hi = min(y_top, y_bottom), max(y_top, y_bottom)
        scale = self.pixel_width // self.char_width
        for py in range(max(0, lo), min(self.pixel_height, hi + 1)):
            for dx in range(scale):
                self.set_pixel(cx * scale + dx, py)


class BrailleCanvas(_SubPixelCanvas):
    """2x4 sub-pixel canvas using braille Unicode (U+2800-U+28FF)."""

    def __init__(self, width, height):
        self.char_width = width
        self.char_height = height
        self.pixel_width = width * 2
        self.pixel_height = height * 4
        self._cells = [[0] * width for _ in range(height)]
        self._counts = [[0] * width for _ in range(height)]

    def set_pixel(self, px, py):
        if px < 0 or px >= self.pixel_width or py < 0 or py >= self.pixel_height:
            return
        cx, cy = px // 2, py // 4
        self._cells[cy][cx] |= BRAILLE_MAP[px % 2][py % 4]
        self._counts[cy][cx] += 1

    def render(self, density=False):
        lines = []
        for row in range(self.char_height):
            chars = []
            for col in range(self.char_width):
                if density:
                    count = self._counts[row][col]
                    if count <= 0:
                        chars.append(chr(0x2800))
                    else:
                        level = min(len(_BRAILLE_LEVELS) - 1, int(math.log2(count)) + 1)
                        chars.append(chr(0x2800 + _BRAILLE_LEVELS[level]))
                else:
                    chars.append(chr(0x2800 + self._cells[row][col]))
            lines.append("".join(chars))
        return lines


class QuadrantCanvas(_SubPixelCanvas):
    """2x2 sub-pixel canvas using Unicode quadrant block characters."""

    def __init__(self, width, height):
        self.char_width = width
        self.char_height = height
        self.pixel_width = width * 2
        self.pixel_height = height * 2
        self._cells = [[0] * width for _ in range(height)]

    def set_pixel(self, px, py):
        if px < 0 or px >= self.pixel_width or py < 0 or py >= self.pixel_height:
            return
        cx, cy = px // 2, py // 2
        bit = (px % 2) + (py % 2) * 2  # 0=TL, 1=TR, 2=BL, 3=BR
        self._cells[cy][cx] |= 1 << bit

    def render(self, density=False):
        return [
            "".join(_QUADRANT_CHARS[self._cells[row][col]] for col in range(self.char_width))
            for row in range(self.char_height)
        ]


class AsciiCanvas:
    """1 pixel per character cell using ASCII glyphs."""

    def __init__(self, width, height):
        self.char_width = width
        self.char_height = height
        self.pixel_width = width
        self.pixel_height = height
        self._cells = [[" "] * width for _ in range(height)]
        self._counts = [[0] * width for _ in range(height)]

    def set_pixel(self, px, py, marker="*"):
        if px < 0 or px >= self.pixel_width or py < 0 or py >= self.pixel_height:
            return
        self._counts[py][px] += 1
        current = self._cells[py][px]
        if current == " " or current == marker:
            self._cells[py][px] = marker
        else:
            self._cells[py][px] = "#"

    def line(self, x0, y0, x1, y1, marker="*"):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.set_pixel(x0, y0, marker)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def fill_column(self, cx, y_top, y_bottom, marker="#"):
        if cx < 0 or cx >= self.char_width:
            return
        lo, hi = min(y_top, y_bottom), max(y_top, y_bottom)
        for py in range(max(0, lo), min(self.pixel_height, hi + 1)):
            self.set_pixel(cx, py, marker)

    def render(self, density=False):
        if not density:
            return ["".join(row) for row in self._cells]
        lines = []
        for row in range(self.char_height):
            chars = []
            for col in range(self.char_width):
                count = self._counts[row][col]
                if count <= 0:
                    chars.append(" ")
                else:
                    idx = min(len(_ASCII_DENSITY) - 1, int(math.log2(count)) + 1)
                    chars.append(_ASCII_DENSITY[idx])
            lines.append("".join(chars))
        return lines


def _make_canvas(charset, width, height):
    if charset == "extended":
        return QuadrantCanvas(width, height)
    if charset in MARKER_CHARSETS:
        return AsciiCanvas(width, height)
    return BrailleCanvas(width, height)


def _series_marker(charset, index):
    """Glyph used to draw/label series *index* for marker-based charsets."""
    if charset == "simple":
        return "*"
    if charset == "ascii":
        return ASCII_MARKERS[index % len(ASCII_MARKERS)]
    return "•"


def downsample_xy(x, y, max_points):
    """Keep envelope via min/max per x-bin when N exceeds *max_points*."""
    n = len(x)
    if n <= max_points or max_points < 4:
        return x, y
    # ponytail: O(n) bin envelope; switch to LTTB if visual quality needs it.
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    bins = max(2, max_points // 2)
    edges = np.linspace(0, n, bins + 1).astype(int)
    out_x, out_y = [], []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        if lo >= hi:
            continue
        chunk_x, chunk_y = xs[lo:hi], ys[lo:hi]
        j_min = int(np.argmin(chunk_y))
        j_max = int(np.argmax(chunk_y))
        for j in sorted({j_min, j_max}):
            out_x.append(chunk_x[j])
            out_y.append(chunk_y[j])
    return np.asarray(out_x, dtype=float), np.asarray(out_y, dtype=float)


def nice_ticks(lo, hi, count=5):
    """Return ~*count* tick values on a 1/2/5×10ⁿ grid covering [lo, hi]."""
    if not np.isfinite(lo) or not np.isfinite(hi):
        return [0.0]
    if hi == lo:
        return [lo]
    if hi < lo:
        lo, hi = hi, lo
    span = hi - lo
    raw = span / max(1, count - 1)
    exp = math.floor(math.log10(raw)) if raw > 0 else 0
    base = 10 ** exp
    frac = raw / base
    if frac <= 1:
        step = 1 * base
    elif frac <= 2:
        step = 2 * base
    elif frac <= 5:
        step = 5 * base
    else:
        step = 10 * base
    start = math.floor(lo / step) * step
    ticks = []
    value = start
    # Guard against float drift.
    for _ in range(count * 3 + 2):
        if value >= lo - step * 1e-9:
            ticks.append(value)
        if value > hi + step * 1e-9:
            break
        value += step
    if not ticks:
        return [lo, hi]
    return ticks


def _apply_scale(values, scale):
    if scale == "log":
        if np.any(values <= 0):
            raise ValueError("log scale requires strictly positive values")
        return np.log10(values)
    return values


def _invert_scale(values, scale):
    if scale == "log":
        return np.power(10.0, values)
    return values


def _format_num(val, max_width):
    if not np.isfinite(val):
        s = "nan"
    elif val == 0:
        s = "0"
    elif abs(val) < 0.01 or abs(val) >= 1e6:
        s = f"{val:.2e}"
    elif float(val) == int(val) and abs(val) < 1e12:
        s = str(int(val))
    else:
        s = f"{val:.2f}"
    if len(s) > max_width:
        s = f"{val:.1e}"
    return s[:max_width]


def _place_label(char_list, pos, label):
    for i, ch in enumerate(label):
        idx = pos + i
        if 0 <= idx < len(char_list):
            char_list[idx] = ch


def _resolve_limits(data_min, data_max, lim, margin=0.05):
    if lim is not None and len(lim) == 2 and lim[0] is not None and lim[1] is not None:
        lo, hi = float(lim[0]), float(lim[1])
        if hi == lo:
            lo, hi = lo - 1, hi + 1
        return lo, hi
    lo, hi = float(data_min), float(data_max)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * margin
    return lo - pad, hi + pad


def render_plot(
    series,
    *,
    width=72,
    height=20,
    plot_type="line",
    x_label="x",
    y_label="y",
    charset="braille",
    x_scale="linear",
    y_scale="linear",
    x_lim=None,
    y_lim=None,
    sort_x=False,
):
    """Render one or more series to terminal lines.

    *height* is the total output height including axis chrome.
    *series* is a list of ``{"label", "x", "y"}`` dicts with numpy arrays.
    """
    if plot_type not in VALID_PLOT_TYPES:
        raise ValueError("Unknown plot type: {!r}. Use: {}".format(
            plot_type, ", ".join(sorted(VALID_PLOT_TYPES))
        ))
    if not series:
        raise ValueError("No series to plot")

    if charset not in VALID_CHARSETS:
        charset = "braille"
    y_axis_width = 10
    legend = "  ".join(
        "{}={}".format(s["label"], _series_marker(charset, i))
        for i, s in enumerate(series)
        if s.get("label")
    ) if len(series) > 1 else ""

    footer = 3  # axis line, tick labels, blank
    if x_label:
        footer += 1
    if legend:
        footer += 1
    header = 1 if y_label else 0
    plot_height = height - footer - header
    plot_width = width - y_axis_width - 1

    if plot_width < 10 or plot_height < 5:
        logger.warning("Plot area too small (width=%d height=%d)", plot_width, plot_height)
        return ["Plot area too small. Increase width/height."]

    prepared = []
    for item in series:
        x = np.asarray(item["x"], dtype=float)
        y = np.asarray(item["y"], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[mask], y[mask]
        if plot_type != "hist":
            x = _apply_scale(x, x_scale)
            y = _apply_scale(y, y_scale)
        prepared.append({
            "label": item.get("label", ""),
            "x": x,
            "y": y,
        })

    if plot_type == "hist":
        return _render_hist(
            prepared, plot_width, plot_height, width, height,
            y_axis_width, x_label, y_label, charset, y_scale, y_lim, legend, header,
        )
    if plot_type == "bar":
        return _render_bar(
            prepared, plot_width, plot_height, width,
            y_axis_width, x_label, y_label, charset, y_scale, y_lim, legend, header,
        )

    nonempty = [p for p in prepared if len(p["x"])]
    if not nonempty:
        raise ValueError("No valid data points to plot")
    xs_all = np.concatenate([p["x"] for p in nonempty])
    ys_all = np.concatenate([p["y"] for p in nonempty])

    x_min, x_max = _resolve_limits(xs_all.min(), xs_all.max(), x_lim)
    y_min, y_max = _resolve_limits(ys_all.min(), ys_all.max(), y_lim)

    canvas = _make_canvas(charset, plot_width, plot_height)
    pw = canvas.pixel_width - 1
    ph = canvas.pixel_height - 1
    max_points = max(64, plot_width * plot_height * 2)
    use_marker = charset in MARKER_CHARSETS

    def to_pixel(xv, yv):
        px = int(round((xv - x_min) / (x_max - x_min) * pw))
        py = int(round((1.0 - (yv - y_min) / (y_max - y_min)) * ph))
        return max(0, min(pw, px)), max(0, min(ph, py))

    for index, item in enumerate(prepared):
        x, y = item["x"], item["y"]
        if len(x) == 0:
            continue
        if sort_x and plot_type == "line":
            order = np.argsort(x)
            x, y = x[order], y[order]
        x, y = downsample_xy(x, y, max_points)
        marker = _series_marker(charset, index)
        if plot_type == "line":
            prev = None
            for xv, yv in zip(x, y):
                px, py = to_pixel(xv, yv)
                if use_marker:
                    canvas.set_pixel(px, py, marker)
                    if prev is not None:
                        canvas.line(prev[0], prev[1], px, py, marker)
                else:
                    canvas.set_pixel(px, py)
                    if prev is not None:
                        canvas.line(prev[0], prev[1], px, py)
                prev = (px, py)
        else:
            for xv, yv in zip(x, y):
                px, py = to_pixel(xv, yv)
                if use_marker:
                    canvas.set_pixel(px, py, marker)
                else:
                    canvas.set_pixel(px, py)

    density = plot_type == "scatter"
    plot_lines = canvas.render(density=density)
    return _frame_with_axes(
        plot_lines, plot_width, plot_height, y_axis_width,
        x_min, x_max, y_min, y_max, x_scale, y_scale,
        x_label, y_label, legend, header, charset,
    )


def _zero_pixel(y_min, y_max, ph):
    if y_min <= 0 <= y_max and y_max != y_min:
        return int(round((1.0 - (0 - y_min) / (y_max - y_min)) * ph))
    return ph


def _render_bar(prepared, plot_width, plot_height, width, y_axis_width,
                x_label, y_label, charset, y_scale, y_lim, legend, header):
    # Aggregate each series onto shared x bins by mean y.
    xs_all = np.concatenate([p["x"] for p in prepared if len(p["x"])])
    if len(xs_all) == 0:
        raise ValueError("No valid data points to plot")
    x_min, x_max = float(xs_all.min()), float(xs_all.max())
    if x_max == x_min:
        x_min -= 1
        x_max += 1

    canvas = _make_canvas(charset, plot_width, plot_height)
    ph = canvas.pixel_height - 1
    series_bins = []
    ymax_data = 0.0
    for item in prepared:
        bins = np.zeros(plot_width, dtype=float)
        counts = np.zeros(plot_width, dtype=float)
        for xv, yv in zip(item["x"], item["y"]):
            if not (np.isfinite(xv) and np.isfinite(yv)):
                continue
            idx = int((xv - x_min) / (x_max - x_min) * (plot_width - 1))
            idx = max(0, min(plot_width - 1, idx))
            bins[idx] += yv
            counts[idx] += 1
        mask = counts > 0
        bins[mask] /= counts[mask]
        scaled = _apply_scale(bins[mask], y_scale) if mask.any() else bins
        if mask.any():
            bins_out = np.zeros_like(bins)
            bins_out[mask] = scaled
            bins = bins_out
            ymax_data = max(ymax_data, float(np.max(scaled)))
        series_bins.append(bins)

    y_min, y_max = _resolve_limits(0.0 if y_scale == "linear" else max(ymax_data * 0.1, 1e-12),
                                   ymax_data if ymax_data > 0 else 1.0, y_lim)
    baseline = _zero_pixel(y_min, y_max, ph)

    for index, bins in enumerate(series_bins):
        marker = "#" if index == 0 or charset == "simple" else ASCII_MARKERS[index % len(ASCII_MARKERS)]
        for cx, value in enumerate(bins):
            if value == 0 and y_min >= 0:
                continue
            py = int(round((1.0 - (value - y_min) / (y_max - y_min)) * ph))
            py = max(0, min(ph, py))
            if charset in MARKER_CHARSETS:
                canvas.fill_column(cx, py, baseline, marker)
            else:
                canvas.fill_column(cx, py, baseline)

    return _frame_with_axes(
        canvas.render(), plot_width, plot_height, y_axis_width,
        x_min, x_max, y_min, y_max, "linear", y_scale,
        x_label, y_label, legend, header, charset,
    )


def _render_hist(prepared, plot_width, plot_height, width, height,
                 y_axis_width, x_label, y_label, charset, y_scale, y_lim, legend, header):
    values = np.concatenate([p["y"] if len(p["y"]) else p["x"] for p in prepared])
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise ValueError("No valid data points to plot")
    counts, edges = np.histogram(values, bins=plot_width)
    x_min, x_max = float(edges[0]), float(edges[-1])
    heights = _apply_scale(counts.astype(float), y_scale)
    y_min, y_max = _resolve_limits(0.0 if y_scale == "linear" else max(float(heights.max()) * 0.1, 1e-12),
                                   float(heights.max()) if heights.max() > 0 else 1.0, y_lim)

    canvas = _make_canvas(charset, plot_width, plot_height)
    ph = canvas.pixel_height - 1
    baseline = _zero_pixel(y_min, y_max, ph)
    for cx, value in enumerate(heights):
        py = int(round((1.0 - (value - y_min) / (y_max - y_min)) * ph))
        py = max(0, min(ph, py))
        if charset in MARKER_CHARSETS:
            canvas.fill_column(cx, py, baseline, "#")
        else:
            canvas.fill_column(cx, py, baseline)

    return _frame_with_axes(
        canvas.render(), plot_width, plot_height, y_axis_width,
        x_min, x_max, y_min, y_max, "linear", y_scale,
        x_label or "bin", y_label or "count", legend, header, charset,
    )


def _frame_with_axes(plot_lines, plot_width, plot_height, y_axis_width,
                     x_min, x_max, y_min, y_max, x_scale, y_scale,
                     x_label, y_label, legend, header, charset="braille"):
    # Tick values in display (original) units.
    x_disp = _invert_scale(np.array([x_min, x_max], dtype=float), x_scale)
    y_disp = _invert_scale(np.array([y_min, y_max], dtype=float), y_scale)
    y_ticks = nice_ticks(float(y_disp[0]), float(y_disp[1]), count=min(5, plot_height))
    y_tick_rows = {}
    for tick in y_ticks:
        try:
            transformed = float(_apply_scale(np.array([tick]), y_scale)[0])
        except ValueError:
            continue
        if y_max == y_min:
            row = 0
        else:
            row = int(round((1.0 - (transformed - y_min) / (y_max - y_min)) * (plot_height - 1)))
        row = max(0, min(plot_height - 1, row))
        y_tick_rows[row] = tick

    if charset in MARKER_CHARSETS:
        tick_join, spine, corner, axis_line = "+", "|", "+", "-"
    else:
        tick_join, spine, corner, axis_line = "┤", "│", "└", "─"

    lines = []
    if header and y_label:
        center = y_axis_width + plot_width // 2 - len(y_label) // 2
        lines.append(" " * max(0, center) + y_label)

    for row in range(plot_height):
        if row in y_tick_rows:
            label = _format_num(y_tick_rows[row], y_axis_width - 2)
            prefix = f"{label:>{y_axis_width - 1}} {tick_join}"
        else:
            prefix = " " * (y_axis_width - 1) + " " + spine
        lines.append(prefix + plot_lines[row])

    lines.append(" " * (y_axis_width - 1) + " " + corner + axis_line * plot_width)

    x_ticks = nice_ticks(float(x_disp[0]), float(x_disp[1]), count=3)
    tick_str = list(" " * plot_width)
    for tick in x_ticks:
        try:
            transformed = float(_apply_scale(np.array([tick]), x_scale)[0])
        except ValueError:
            continue
        if x_max == x_min:
            pos = 0
        else:
            pos = int(round((transformed - x_min) / (x_max - x_min) * (plot_width - 1)))
        label = _format_num(tick, 8)
        _place_label(tick_str, max(0, min(plot_width - len(label), pos - len(label) // 2)), label)
    lines.append(" " * y_axis_width + " " + "".join(tick_str))
    lines.append("")
    if x_label:
        center_x = y_axis_width + plot_width // 2 - len(x_label) // 2
        lines.append(" " * max(0, center_x) + x_label)
    if legend:
        lines.append(legend)
    return lines


def braille_plot(x, y, width=72, height=20, x_label="x", y_label="y",
                 plot_type="line", charset="braille", **kwargs):
    """Backward-compatible single-series entry point."""
    return render_plot(
        [{"label": "", "x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float)}],
        width=width,
        height=height,
        plot_type=plot_type,
        x_label=x_label,
        y_label=y_label,
        charset=charset,
        **kwargs,
    )
