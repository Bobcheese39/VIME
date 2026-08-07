"""Runnable checks for VIME's pure terminal helpers."""

import unittest
import os
import tempfile

from terminal import (
    ALT_SCREEN_OFF, CURSOR_SHOW, TerminalSession, cleanup_sequence, decode_escape
)
import vime_tui
from vime_tui import (
    AppState, Application, OPTIONS, load_default_mode, render_frame, save_default_mode
)


class TerminalHelpersTest(unittest.TestCase):
    def test_arrow_and_page_keys(self):
        self.assertEqual(decode_escape("\x1b[A"), "up")
        self.assertEqual(decode_escape("\x1b[D"), "left")
        self.assertEqual(decode_escape("\x1b[5~"), "page_up")
        self.assertEqual(decode_escape("\x1b[6~"), "page_down")

    def test_escape_and_unknown_sequences(self):
        self.assertEqual(decode_escape("\x1b"), "escape")
        self.assertIsNone(decode_escape("\x1b[99~"))

    def test_cleanup_restores_cursor_and_screen(self):
        cleanup = cleanup_sequence()
        self.assertIn(CURSOR_SHOW, cleanup)
        self.assertTrue(cleanup.endswith(ALT_SCREEN_OFF))
        self.assertEqual(TerminalSession._normalize_character("\x02"), "ctrl_b")
        self.assertEqual(TerminalSession._normalize_character("\x05"), "ctrl_e")

    def test_complete_frames_follow_terminal_size(self):
        state = AppState(files=["sample.h5"])
        state.datasets = [{"name": "/xy", "rows": 2, "cols": 2}]
        state.view = "table"
        state.table_columns = ["x", "y"]
        state.table_rows = [["1", "2"], ["3", "4"]]
        state.total_rows = 2
        for width, height in ((40, 10), (80, 20)):
            frame = render_frame(state, width, height)
            self.assertEqual(len(frame.splitlines()), height)
            self.assertTrue(all(len(line) <= width for line in frame.splitlines()))
        state.view = "plot"
        state.plot_content = "Plot\n\n⣿⣷⣤"
        self.assertEqual(len(render_frame(state, 40, 10).splitlines()), 10)

    def test_horizontal_view_and_vim_page_counts(self):
        state = AppState(files=["sample.h5"], width=12, height=10)
        state.view = "table"
        state.table_columns = ["alpha", "beta", "gamma"]
        state.table_rows = [["1", "2", "3"]]
        state.total_rows = 30
        state.column_offset = 1
        frame = render_frame(state, state.width, state.height)
        self.assertNotIn("alpha", frame)
        self.assertIn("beta", frame)

        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state = state
        offsets = []

        def request(offset=None):
            if offset is not None:
                state.table_offset = offset
            offsets.append(state.table_offset)

        app.request_table = request
        for key in ("5", "j"):
            app.handle_key(key)
        self.assertEqual(offsets[-1], 10)
        for key in ("5", "g"):
            app.handle_key(key)
        self.assertEqual(offsets[-1], 10)
        for key in ("5", "G", "G"):
            app.handle_key(key)
        self.assertEqual(offsets[-1], 18)
        app.close()

    def test_yank_order_and_settings_round_trip(self):
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state.view = "table"
        app.state.table_columns = ["a", "b", "c", "d"]
        app.state.prompt = True
        app.state.prompt_kind = "yank"
        app.state.prompt_text = "2 3 1"
        app.request_table = lambda offset=None: None
        app.apply_yank()
        self.assertEqual(app.state.column_order, ["b", "c", "a", "d"])
        app.state.table_columns = ["0", "1"]
        self.assertEqual(app.resolve_column("1"), "0")
        app.state.table_columns = ["time", "value", "site"]
        parsed = app.parse_plot_prompt("time value scatter logy group=site x=0:10")
        self.assertEqual(parsed["type"], "scatter")
        self.assertEqual(parsed["y_scale"], "log")
        self.assertEqual(parsed["groupby"], "site")
        self.assertEqual(parsed["x_lim"], [0.0, 10.0])
        with self.assertRaises(ValueError):
            app.parse_plot_prompt("time value line weird=1")
        with self.assertRaises(ValueError):
            app.parse_plot_prompt("time value nope")
        app.close()

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.cfg")
            self.assertEqual(load_default_mode(path), "default")
            save_default_mode("free_vim", path)
            self.assertEqual(load_default_mode(path), "free_vim")

    def test_options_menu_cycles_and_stays_open(self):
        """Pressing a row's digit repeatedly cycles its choices in place."""
        original_path = vime_tui.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as directory:
            vime_tui.SETTINGS_PATH = os.path.join(directory, "settings.cfg")
            try:
                app = Application("127.0.0.1", 1, ["sample.h5"])
                app.state.view = "options"
                charset_key = str(OPTIONS.index(
                    next(opt for opt in OPTIONS if opt["state_attr"] == "plot_charset")
                ) + 1)
                self.assertEqual(app.state.plot_charset, "braille")
                app.handle_key(charset_key)
                self.assertEqual(app.state.plot_charset, "ascii")
                self.assertEqual(app.state.view, "options")  # stays on the menu
                app.handle_key(charset_key)
                self.assertEqual(app.state.plot_charset, "simple")
                app.close()
            finally:
                vime_tui.SETTINGS_PATH = original_path


if __name__ == "__main__":
    unittest.main()
