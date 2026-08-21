"""Runnable checks for VIME's pure terminal helpers."""

import unittest
import os
import tempfile
from unittest.mock import patch

from terminal import (
    ALT_SCREEN_OFF, CURSOR_SHOW, SGR_BOLD, SGR_RESET, SGR_REVERSE,
    TerminalSession, cleanup_sequence, crop, decode_escape, pad, paint,
    strip_style, visible_len,
)
import vime_tui
from vime_tui import (
    AppState, Application, OPTIONS, TablePane, load_default_mode, render_frame,
    save_default_mode
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
        self.assertEqual(TerminalSession._normalize_character("\x1b"), "escape")

    def test_sgr_crop_pad_and_paint(self):
        self.assertEqual(visible_len("hi"), 2)
        self.assertEqual(crop("abcdef", 3), "abc")
        self.assertEqual(pad("ab", 4), "ab  ")
        with patch("terminal.style_enabled", return_value=True):
            styled = paint(pad("hi", 5), SGR_REVERSE)
        self.assertTrue(styled.startswith("\x1b[7m"))
        self.assertTrue(styled.endswith(SGR_RESET))
        self.assertEqual(visible_len(styled), 5)
        self.assertEqual(strip_style(styled), "hi   ")
        cropped = crop(styled, 2)
        self.assertEqual(strip_style(cropped), "hi")
        self.assertTrue(cropped.startswith("\x1b[7m"))
        self.assertTrue(cropped.endswith(SGR_RESET))
        with patch("terminal.style_enabled", return_value=False):
            self.assertEqual(paint("hi", SGR_BOLD), "hi")

    def test_chrome_uses_reverse_bars_and_strips_for_plain_text(self):
        state = AppState(files=["sample.h5"], width=40, height=10, view="table")
        state.table_columns = ["x", "y"]
        state.table_rows = [["1", "2"]]
        state.total_rows = 1
        with patch("terminal.style_enabled", return_value=True):
            frame = render_frame(state, state.width, state.height)
        lines = frame.splitlines()
        title, footer = lines[0], lines[-1]
        self.assertIn("\x1b[1;7m", title)
        self.assertEqual(visible_len(title), state.width)
        self.assertTrue(title.endswith(SGR_RESET))
        self.assertIn("\x1b[4m", frame)
        self.assertIn("\x1b[7m", footer)
        self.assertEqual(visible_len(footer), state.width)
        self.assertTrue(footer.endswith(SGR_RESET))
        plain = strip_style(frame)
        self.assertNotIn("\x1b[", plain)
        self.assertIn("x", plain)
        state.error = "boom"
        state.width = 120
        with patch("terminal.style_enabled", return_value=True):
            error_footer = render_frame(state, state.width, state.height).splitlines()[-1]
        self.assertIn("\x1b[31;7m", error_footer)
        self.assertIn("Error: boom", error_footer)
        self.assertEqual(visible_len(error_footer), state.width)

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
            self.assertTrue(all(
                visible_len(line) <= width for line in frame.splitlines()
            ))
        state.view = "plot"
        state.plot_content = "Plot\n\n⣿⣷⣤"
        self.assertEqual(len(render_frame(state, 40, 10).splitlines()), 10)

    def test_table_pages_fill_the_available_pane_rows(self):
        state = AppState(files=["sample.h5"], width=40, height=10, view="table")
        state.table_columns = ["value"]
        state.table_rows = [[str(index)] for index in range(5)]
        state.total_rows = 5
        frame = render_frame(state, state.width, state.height)
        self.assertIn("4", frame)

        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state = state
        self.assertEqual(app.pane_page_limit(state.active_pane), 6)

        state.view = "split"
        state.panes.append(TablePane("/other", table_columns=["value"]))
        pane_width, pane_height = app.pane_size(state.panes[0])
        state.panes[0].table_rows = [
            [str(index)] for index in range(pane_height - 3)
        ]
        self.assertEqual(app.pane_page_limit(state.panes[0]), pane_height - 3)
        self.assertIn(
            str(pane_height - 4),
            vime_tui.render_table(
                state.panes[0], pane_width, pane_height, focused=True
            )[-1],
        )
        app.close()

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
        self.assertEqual(offsets[-1], 24)
        for key in ("5", "g"):
            app.handle_key(key)
        self.assertEqual(offsets[-1], 24)
        for key in ("5", "G", "G"):
            app.handle_key(key)
        self.assertEqual(offsets[-1], 0)
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
                float_key = str(OPTIONS.index(
                    next(opt for opt in OPTIONS if opt["state_attr"] == "float_formatting")
                ) + 1)
                path_key = str(OPTIONS.index(
                    next(opt for opt in OPTIONS if opt["state_attr"] == "path_formatting")
                ) + 1)
                app.handle_key(float_key)
                app.handle_key(path_key)
                self.assertEqual(app.state.float_formatting, "on")
                self.assertEqual(app.state.path_formatting, "on")
                app.close()

                reloaded = Application("127.0.0.1", 1, ["sample.h5"])
                self.assertEqual(reloaded.state.float_formatting, "on")
                self.assertEqual(reloaded.state.path_formatting, "on")
                reloaded.state.panes = [TablePane("/a")]
                submitted = []
                reloaded.submit = (
                    lambda kind, command=None, payload=None, pane=None:
                    submitted.append(payload) or True
                )
                reloaded.request_table()
                self.assertTrue(submitted[-1]["float_formatting"])
                self.assertTrue(submitted[-1]["path_formatting"])
                reloaded.close()
            finally:
                vime_tui.SETTINGS_PATH = original_path

    def test_full_window_and_split_navigation(self):
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state.datasets = [
            {"name": "/a", "rows": 2, "cols": 2},
            {"name": "/b", "rows": 2, "cols": 2},
        ]
        requested = []
        app.request_table = lambda offset=None, pane=None: requested.append(
            pane or app.state.active_pane
        )

        app.handle_key("enter")
        self.assertEqual(app.state.view, "table")
        self.assertEqual(app.state.active_pane.dataset, "/a")
        app.handle_key("b")
        self.assertEqual((app.state.view, app.state.focus), ("datasets", "sidebar"))

        app.handle_key("right")
        self.assertEqual((app.state.view, app.state.focus), ("split", "table"))
        app.handle_key("b")
        app.handle_key("down")
        app.handle_key("right")
        self.assertEqual([pane.dataset for pane in app.state.panes], ["/a", "/b"])
        self.assertEqual(len(requested), 2)

        app.handle_response("table_page", {
            "rows": [["1"]], "columns": ["a"], "offset": 0, "total_rows": 1,
        }, app.state.panes[0])
        self.assertEqual(app.state.panes[0].table_columns, ["a"])
        self.assertEqual(app.state.panes[1].table_columns, [])
        app.close()

    def test_split_layout_focus_reorientation_and_close(self):
        state = AppState(files=["sample.h5"], width=80, height=20)
        state.datasets = [
            {"name": "/a", "rows": 1, "cols": 4},
            {"name": "/b", "rows": 1, "cols": 4},
        ]
        state.panes = [
            TablePane("/a", table_columns=["a", "b", "c", "d"],
                      table_rows=[["1", "2", "3", "4"]], total_rows=1),
            TablePane("/b", table_columns=["a", "b", "c", "d"],
                      table_rows=[["1", "2", "3", "4"]], total_rows=1),
        ]
        state.view = "split"
        state.focus = "table"
        state.active_pane_index = 1
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state = state
        app.request_table = lambda offset=None, pane=None: None

        vertical = render_frame(state, state.width, state.height)
        self.assertEqual(len(vertical.splitlines()), state.height)
        self.assertIn("│", vertical)
        app.handle_key("up")
        self.assertEqual(state.panes[1].split_orientation, "horizontal")
        self.assertIn("─" * 10, render_frame(state, state.width, state.height))

        state.panes[1].column_offset = 2
        app.handle_key("left")
        self.assertLess(state.panes[1].column_offset, 2)
        state.panes[1].column_offset = 0
        app.handle_key("left")
        self.assertEqual(state.focus, "sidebar")
        state.selected_dataset = 1
        app.handle_key("right")
        self.assertEqual(state.focus, "table")
        app.handle_key("\t")
        self.assertEqual(state.active_pane_index, 0)
        app.handle_key("b")
        state.selected_dataset = 1
        app.handle_key("left")
        self.assertEqual([pane.dataset for pane in state.panes], ["/a"])
        app.close()

    def test_split_plot_stays_in_active_pane(self):
        state = AppState(files=["sample.h5"], width=80, height=20)
        state.view = "split"
        state.focus = "table"
        state.panes = [
            TablePane("/a", table_columns=["x", "y"], table_rows=[["1", "2"]]),
            TablePane("/b", table_columns=["x", "y"], table_rows=[["3", "4"]]),
        ]
        state.active_pane_index = 1
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state = state
        submitted = []
        app.submit = lambda kind, command=None, payload=None, pane=None: submitted.append(
            (kind, payload)
        ) or True

        expected_width, expected_height = app.pane_size(state.panes[1])
        app.start_plot({"columns": ["x", "y"], "type": "line"})
        self.assertEqual(state.view, "split")
        self.assertIs(state.plot_pane, state.panes[1])
        self.assertEqual(submitted[-1][1]["width"], max(20, expected_width))
        self.assertEqual(submitted[-1][1]["height"], max(8, expected_height - 2))

        state.job_status = "done"
        state.plot_header = "Pane plot"
        state.plot_content = "PLOT BODY"
        frame = render_frame(state, state.width, state.height)
        self.assertIn("/a", frame)
        self.assertIn("PLOT BODY", frame)
        app.handle_key("b")
        self.assertIsNone(state.plot_pane)
        self.assertEqual(state.view, "split")
        app.close()

    def test_split_master_header_spans_full_width(self):
        state = AppState(files=["radar_data_2.h5"], width=80, height=20)
        state.view = "split"
        state.focus = "table"
        state.panes = [
            TablePane("/a", table_columns=["x"], table_rows=[["1"]], total_rows=1),
        ]
        lines = render_frame(state, state.width, state.height).splitlines()
        self.assertEqual(len(lines), state.height)
        self.assertIn("VIME", lines[0])
        self.assertIn("radar_data_2.h5", lines[0])
        self.assertEqual(visible_len(lines[0]), state.width)
        self.assertNotIn("\x1b[7m", lines[0])
        self.assertNotIn("\x1b[1;7m", lines[0])
        self.assertIn("│", lines[1])
        self.assertIn("/a", lines[1])

    def test_footer_keeps_keybinds_with_message(self):
        state = AppState(files=["sample.h5"], width=120, height=20)
        state.view = "split"
        state.focus = "table"
        state.panes = [
            TablePane("/a", table_columns=["x"], table_rows=[["1"]], total_rows=1),
        ]
        state.message = "Loading..."
        footer = render_frame(state, state.width, state.height).splitlines()[-1]
        self.assertIn("Loading...", footer)
        self.assertIn("↑/↓", footer)
        state.message = ""
        state.error = "boom"
        footer = render_frame(state, state.width, state.height).splitlines()[-1]
        self.assertIn("Error: boom", footer)
        self.assertIn("Tab panes", footer)
        # Narrow width still keeps keybinds even if the status is cropped.
        state.width = 40
        footer = render_frame(state, state.width, state.height).splitlines()[-1]
        self.assertIn("↑/↓", footer)
        self.assertEqual(visible_len(footer), 40)

    def test_plot_prompt_has_no_default_text(self):
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state.view = "table"
        app.state.panes = [
            TablePane("/a", table_columns=["x", "y"], table_rows=[["1", "2"]]),
        ]
        app.state.focus = "table"
        app.handle_key("p")
        self.assertTrue(app.state.prompt)
        self.assertEqual(app.state.prompt_kind, "plot")
        self.assertEqual(app.state.prompt_text, "")
        app.close()

    def test_escape_cancels_input_and_columns_cycle(self):
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state.view = "table"
        app.state.focus = "table"
        app.state.panes = [
            TablePane(
                "/a",
                table_columns=["a", "b", "c", "d"],
                table_rows=[["1", "2", "3", "4"]],
                total_rows=1,
            ),
        ]
        app.request_table = lambda offset=None, pane=None: None

        app.handle_key("f")
        self.assertTrue(app.state.prompt)
        app.handle_key("escape")
        self.assertFalse(app.state.prompt)

        app.handle_key("p")
        self.assertTrue(app.state.prompt)
        app.handle_key("ctrl_b")
        self.assertFalse(app.state.prompt)

        app.handle_key("5")
        self.assertEqual(app.state.command_buffer, "5")
        app.handle_key("escape")
        self.assertEqual(app.state.command_buffer, "")
        self.assertEqual(app.state.view, "table")
        app.handle_key("escape")
        self.assertEqual((app.state.view, app.state.focus), ("datasets", "sidebar"))

        app.state.view = "table"
        app.state.focus = "table"
        for expected in (1, 2, 3, 0):
            app.handle_key("l")
            self.assertEqual(app.state.column_offset, expected)
        app.move_columns(10)
        self.assertEqual(app.state.column_offset, 3)
        app.move_columns(1)
        self.assertEqual(app.state.column_offset, 0)
        app.move_columns(-1)
        self.assertEqual(app.state.column_offset, 0)
        app.close()

    def test_column_guide_appears_above_prompt_toolbar(self):
        state = AppState(files=["sample.h5"], width=60, height=10, view="table")
        state.panes = [
            TablePane(
                "/a",
                table_columns=["time", "value", "site"],
                table_rows=[["1", "2", "west"]],
                total_rows=1,
            ),
        ]
        state.focus = "table"
        state.prompt = True
        for kind in (
            "plot", "filter", "yank", "plot_x", "plot_y", "plot_group",
            "plot_xlim", "plot_ylim",
        ):
            state.prompt_kind = kind
            lines = render_frame(state, state.width, state.height).splitlines()
            self.assertEqual(len(lines), state.height)
            self.assertEqual(
                lines[-2],
                "[1] time  [2] value  [3] site",
            )
        state.prompt = False
        self.assertNotIn("[1] time", render_frame(state, state.width, state.height))

    def test_plot_view_hotkeys_update_and_regenerate_plot(self):
        pane = TablePane(
            "/a",
            table_columns=["time", "value", "site"],
            table_rows=[["1", "2", "west"]],
            total_rows=1,
        )
        state = AppState(files=["sample.h5"], width=80, height=20)
        state.view = "split"
        state.focus = "table"
        state.panes = [pane]
        state.plot_pane = pane
        state.plot_request = {
            "dataset": "/a",
            "columns": ["time", "value"],
            "type": "line",
            "_pane": pane,
        }
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state = state
        submitted = []
        app.submit = lambda kind, command=None, payload=None, pane=None: (
            submitted.append(payload) or True
        )

        for key in ("x", "3", "enter"):
            app.handle_key(key)
        self.assertEqual(submitted[-1]["columns"], ["site", "value"])

        for key in ("y", "1", "enter"):
            app.handle_key(key)
        self.assertEqual(submitted[-1]["columns"], ["site", "time"])

        for key in ("g", "2", "enter"):
            app.handle_key(key)
        self.assertEqual(submitted[-1]["groupby"], "value")

        app.handle_key("x")
        app.handle_key("l")
        self.assertEqual(state.prompt_kind, "plot_xlim")
        for key in ("-", "1", " ", "2", ".", "5", "enter"):
            app.handle_key(key)
        self.assertEqual(submitted[-1]["x_lim"], [-1.0, 2.5])

        app.handle_key("y")
        app.handle_key("l")
        self.assertEqual(state.prompt_kind, "plot_ylim")
        for key in ("0", " ", "1", "0", "enter"):
            app.handle_key(key)
        self.assertEqual(submitted[-1]["y_lim"], [0.0, 10.0])
        self.assertIs(state.plot_request["_pane"], pane)

        count = len(submitted)
        for key in ("x", "9", "enter"):
            app.handle_key(key)
        self.assertEqual(len(submitted), count)
        self.assertIn("Invalid plot update", state.error)
        app.close()

    def test_info_digit_enter_toggles_column(self):
        app = Application("127.0.0.1", 1, ["sample.h5"])
        app.state.view = "info"
        app.state.datasets = [{"name": "/t", "rows": 2, "cols": 3}]
        submitted = []
        app.submit = lambda kind, command=None, payload=None, pane=None: (
            submitted.append((kind, command, payload)) or True
        )
        app.handle_key("1")
        app.handle_key("2")
        app.handle_key("enter")
        self.assertEqual(submitted[-1][:2], ("info_toggle", "info"))
        self.assertEqual(submitted[-1][2]["name"], "/t")
        self.assertEqual(submitted[-1][2]["toggle_column"], "12")

        pane = TablePane("/t", column_order=["a", "b", "c"])
        app.state.panes = [pane]
        refreshed = []
        app.request_table = lambda offset=None, pane=None: refreshed.append(pane)
        app.apply_info_hidden({"columns": ["a", "c"], "hidden": ["b"]})
        self.assertEqual(pane.column_order, ["a", "c"])
        self.assertIs(refreshed[-1], pane)
        app.close()


if __name__ == "__main__":
    unittest.main()
