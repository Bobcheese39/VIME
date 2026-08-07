"""Runnable checks for terminal plot prep/render."""

import unittest

import numpy as np

from plotter import (
    VALID_CHARSETS,
    VALID_PLOT_TYPES,
    downsample_xy,
    nice_ticks,
    render_plot,
)


class PlotterTest(unittest.TestCase):
    def test_braille_and_ascii_share_bounds(self):
        x = np.linspace(0, 10, 50)
        y = np.sin(x)
        braille = render_plot(
            [{"label": "", "x": x, "y": y}],
            width=40, height=12, plot_type="line", charset="braille",
        )
        ascii_lines = render_plot(
            [{"label": "", "x": x, "y": y}],
            width=40, height=12, plot_type="line", charset="ascii",
        )
        self.assertEqual(len(braille), len(ascii_lines))
        self.assertTrue(any("⠁" in line or "⣿" in line or "⠄" in line for line in braille))
        self.assertTrue(any("*" in line for line in ascii_lines))
        self.assertFalse(any("\u2800" <= ch <= "\u28ff" for line in ascii_lines for ch in line))

    def test_downsample_preserves_envelope(self):
        x = np.arange(1000, dtype=float)
        y = np.sin(x / 20.0) * 100
        y[100] = 500
        y[500] = -400
        xs, ys = downsample_xy(x, y, max_points=40)
        self.assertLessEqual(len(xs), 40)
        self.assertGreaterEqual(ys.max(), 500 * 0.99)
        self.assertLessEqual(ys.min(), -400 * 0.99)

    def test_invalid_type_errors(self):
        with self.assertRaises(ValueError):
            render_plot(
                [{"label": "", "x": np.array([1.0]), "y": np.array([2.0])}],
                plot_type="nope",
            )
        self.assertIn("line", VALID_PLOT_TYPES)

    def test_log_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            render_plot(
                [{"label": "", "x": np.array([1.0, 2.0]), "y": np.array([0.0, 1.0])}],
                y_scale="log",
            )

    def test_hist_and_bar(self):
        x = np.arange(20, dtype=float)
        y = np.linspace(1, 5, 20)
        hist = render_plot(
            [{"label": "", "x": x, "y": y}],
            width=36, height=12, plot_type="hist", charset="ascii",
        )
        bar = render_plot(
            [{"label": "a", "x": x, "y": y}],
            width=36, height=12, plot_type="bar", charset="ascii",
        )
        self.assertGreater(len(hist), 5)
        self.assertGreater(len(bar), 5)
        self.assertTrue(any("#" in line for line in bar))

    def test_nice_ticks(self):
        ticks = nice_ticks(0.2, 9.7, count=5)
        self.assertGreaterEqual(len(ticks), 2)

    def test_simple_and_extended_charsets(self):
        x = np.linspace(0, 10, 50)
        y = np.sin(x)
        outputs = {
            charset: render_plot(
                [{"label": "", "x": x, "y": y}],
                width=40, height=12, plot_type="line", charset=charset,
            )
            for charset in VALID_CHARSETS
        }
        lengths = {len(lines) for lines in outputs.values()}
        self.assertEqual(lengths, {len(outputs["braille"])})
        # extended uses quadrant-block glyphs distinct from braille dots.
        self.assertTrue(any(
            ch in "▘▝▀▖▌▞▛▗▚▐▜▄▙▟█" for line in outputs["extended"] for ch in line
        ))
        self.assertFalse(any(
            "\u2800" <= ch <= "\u28ff" for line in outputs["extended"] for ch in line
        ))
        # simple stays plain-ASCII like ascii, but never diversifies markers.
        self.assertFalse(any(
            "\u2800" <= ch <= "\u28ff" or ord(ch) > 0x2500
            for line in outputs["simple"] for ch in line
        ))

    def test_groupby_legend_scatter_density(self):
        series = [
            {"label": "a", "x": np.array([0.0, 1.0, 1.0]), "y": np.array([0.0, 1.0, 1.0])},
            {"label": "b", "x": np.array([0.0, 1.0]), "y": np.array([1.0, 0.0])},
        ]
        lines = render_plot(series, width=40, height=12, plot_type="scatter", charset="ascii")
        joined = "\n".join(lines)
        self.assertIn("a=", joined)
        self.assertIn("b=", joined)


if __name__ == "__main__":
    unittest.main()
