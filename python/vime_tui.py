#!/usr/bin/env python3
"""Responsive standard-library terminal frontend for VIME."""

import argparse
import configparser
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from terminal import TerminalSession


KEEPALIVE_SECONDS = 120
PLOT_POLL_SECONDS = 0.5
COMPUTE_POLL_SECONDS = 1.0
PLOT_RESIZE_DEBOUNCE = 0.35
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(ROOT_DIR, "settings.cfg")
VALID_MODES = {"default", "free_vim"}
VALID_PLOT_TYPES = frozenset({"line", "scatter", "bar", "hist"})
TABLE_CHROME_ROWS = 4  # title, rule, column header, and column separator
MASTER_HEADER_ROWS = 2  # VIME+filename title and rule (full-width in split)

# Options-menu rows: each cycles its own setting independently on repeated
# presses of its number key. "display" maps a stored value to shown text.
OPTIONS = [
    {
        "state_attr": "mode", "label": "table mode",
        "section": "ui", "field": "default_mode",
        "choices": ("default", "free_vim"), "display": {"free_vim": "vim"},
    },
    {
        "state_attr": "table_borders", "label": "table borders",
        "section": "ui", "field": "table_borders",
        "choices": ("default", "none"), "display": {},
    },
    {
        "state_attr": "plot_charset", "label": "plot charset",
        "section": "plot", "field": "charset",
        "choices": ("braille", "ascii", "simple", "extended"), "display": {},
    },
    {
        "state_attr": "float_formatting", "label": "float formatting",
        "section": "ui", "field": "float_formatting",
        "choices": ("off", "on"), "display": {},
    },
    {
        "state_attr": "path_formatting", "label": "path formatting",
        "section": "ui", "field": "path_formatting",
        "choices": ("off", "on"), "display": {},
    },
]


def _load_setting(section, field, choices, path=None):
    """Read one setting from *path* (default: current SETTINGS_PATH)."""
    parser = configparser.ConfigParser()
    try:
        parser.read(path or SETTINGS_PATH, encoding="utf-8")
        value = parser.get(section, field, fallback=choices[0])
    except (OSError, configparser.Error):
        return choices[0]
    return value if value in choices else choices[0]


def _save_setting(section, field, value, path=None):
    """Persist one setting atomically, preserving other sections/fields."""
    path = path or SETTINGS_PATH
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, field, value)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        parser.write(handle)
    os.replace(temp_path, path)


def load_default_mode(path=None):
    return _load_setting("ui", "default_mode", ("default", "free_vim"), path)


def load_table_borders(path=None):
    return _load_setting("ui", "table_borders", ("default", "none"), path)


def load_plot_charset(path=None):
    return _load_setting(
        "plot", "charset", ("braille", "ascii", "simple", "extended"), path
    )


def load_float_formatting(path=None):
    return _load_setting("ui", "float_formatting", ("off", "on"), path)


def load_path_formatting(path=None):
    return _load_setting("ui", "path_formatting", ("off", "on"), path)


def save_default_mode(mode, path=None):
    if mode not in VALID_MODES:
        raise ValueError("Unknown mode: " + str(mode))
    _save_setting("ui", "default_mode", mode, path)


class HttpClient:
    def __init__(self, host, port):
        self.base_url = "http://{}:{}".format(host, port)

    def post(self, command, payload):
        body = dict(payload)
        body["cmd"] = command
        request = urllib.request.Request(
            self.base_url + "/" + command,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def health(self):
        with urllib.request.urlopen(self.base_url + "/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))


@dataclass
class TablePane:
    dataset: str
    selected_row: int = 0
    table_offset: int = 0
    table_rows: list = field(default_factory=list)
    table_columns: list = field(default_factory=list)
    total_rows: int = 0
    column_offset: int = 0
    column_order: list = field(default_factory=list)
    filter_spec: object = None
    filter_text: str = ""
    split_orientation: str = "vertical"
    table_borders: str = "default"

    @property
    def active_dataset(self):
        return self.dataset


@dataclass
class AppState:
    files: list
    file_index: int = 0
    view: str = "datasets"
    datasets: list = field(default_factory=list)
    selected_dataset: int = 0
    panes: list = field(default_factory=list)
    active_pane_index: int = 0
    focus: str = "sidebar"
    info_content: str = ""
    plot_content: str = ""
    plot_header: str = "Plot"
    plot_request: object = None
    plot_pane: object = None
    plot_charset: str = "braille"
    table_borders: str = "default"
    float_formatting: str = "off"
    path_formatting: str = "off"
    pending: object = None
    pending_kind: str = ""
    job_kind: str = ""
    job_status: str = "idle"
    next_poll: float = 0.0
    next_keepalive: float = 0.0
    next_replot: float = 0.0
    message: str = ""
    error: str = ""
    prompt: bool = False
    prompt_kind: str = ""
    prompt_text: str = ""
    command_buffer: str = ""
    pending_g: bool = False
    pending_pane: object = None
    mode: str = "default"
    width: int = 80
    height: int = 24
    dirty: bool = True
    needs_reopen: bool = False
    queued_panes: list = field(default_factory=list)

    @property
    def active_file(self):
        return self.files[self.file_index]

    @property
    def active_dataset(self):
        pane = self.active_pane
        if self.focus == "table" and pane is not None:
            return pane.dataset
        if not self.datasets:
            return ""
        return self.datasets[self.selected_dataset]["name"]

    @property
    def active_pane(self):
        if not self.panes:
            return None
        self.active_pane_index = min(self.active_pane_index, len(self.panes) - 1)
        return self.panes[self.active_pane_index]

    def pane_for(self, dataset):
        return next((pane for pane in self.panes if pane.dataset == dataset), None)

    # Compatibility properties keep the table helpers small and point all
    # table operations at the focused pane.
    def _pane_value(name):
        def get(self):
            pane = self.active_pane
            return getattr(pane, name) if pane is not None else ([] if name in (
                "table_rows", "table_columns", "column_order"
            ) else None)

        def set_value(self, value):
            pane = self.active_pane
            if pane is None:
                pane = TablePane(self.active_dataset, table_borders=self.table_borders)
                self.panes.append(pane)
            setattr(pane, name, value)
        return property(get, set_value)

    selected_row = _pane_value("selected_row")
    table_offset = _pane_value("table_offset")
    table_rows = _pane_value("table_rows")
    table_columns = _pane_value("table_columns")
    total_rows = _pane_value("total_rows")
    column_offset = _pane_value("column_offset")
    column_order = _pane_value("column_order")
    filter_spec = _pane_value("filter_spec")
    filter_text = _pane_value("filter_text")


def _crop(text, width):
    return text[:max(0, width)]


def render_master_header(state, width, focused=False):
    title = "{}VIME  {}  [file {}/{}]".format(
        "> " if focused else "",
        os.path.basename(state.active_file),
        state.file_index + 1,
        len(state.files),
    )
    return [
        _crop(title, width).ljust(max(0, width)),
        "=" * max(0, width),
    ]


def render_dataset_list(state, width, height, sidebar=False):
    lines = [] if sidebar else render_master_header(state, width)
    available = max(0, height if sidebar else height - 5)
    start = max(0, state.selected_dataset - available + 1)
    for index, item in enumerate(state.datasets[start:start + available], start):
        marker = ">" if index == state.selected_dataset else " "
        opened = "*" if state.pane_for(item["name"]) else " "
        if sidebar:
            lines.append("{}{} {}".format(marker, opened, item["name"]))
        else:
            lines.append("{}{} {:<44} ({} rows x {} cols)".format(
                marker, opened, item["name"], item["rows"], item["cols"]
            ))
    if not state.datasets:
        lines.append("  No datasets found")
    return lines


def render_table(state, width, height, focused=False):
    end = min(state.total_rows, state.table_offset + len(state.table_rows))
    lines = [
        "{}{}  [rows {}-{} of {}]".format(
            "> " if focused else "",
            state.active_dataset,
            state.table_offset + 1 if state.total_rows else 0,
            end,
            state.total_rows,
        ),
        "=" * max(0, width),
    ]
    widths = []
    for index, column in enumerate(state.table_columns):
        values = [str(row[index]) for row in state.table_rows if index < len(row)]
        widths.append(min(30, max([len(str(column))] + [len(value) for value in values])))

    visible = visible_column_indices(state, width, widths)
    borders = state.table_borders != "none"
    sep = " | " if borders else "  "

    def format_row(values):
        cells = [
            str(value)[:widths[index]].ljust(widths[index])
            for index in visible
            for value in [values[index] if index < len(values) else ""]
        ]
        return sep.join(cells)

    lines.append("  " + format_row(state.table_columns))
    if borders:
        lines.append("  " + "-+-".join("-" * widths[index] for index in visible))
    for index, row in enumerate(state.table_rows[:max(0, height - TABLE_CHROME_ROWS)]):
        marker = ">" if index == state.selected_row else " "
        lines.append(marker + " " + format_row(row))
    return lines


def visible_column_indices(state, width, widths=None):
    """Return the contiguous columns that fit in the current viewport."""
    if not state.table_columns:
        return []
    if widths is None:
        widths = []
        for index, column in enumerate(state.table_columns):
            values = [str(row[index]) for row in state.table_rows if index < len(row)]
            widths.append(min(30, max([len(str(column))] + [len(value) for value in values])))
    sep_width = 2 if state.table_borders == "none" else 3
    start = min(max(0, state.column_offset), len(widths) - 1)
    available = max(1, width - 2)
    result = []
    used = 0
    for index in range(start, len(widths)):
        added = widths[index] + (sep_width if result else 0)
        if result and used + added > available:
            break
        result.append(index)
        used += added
    return result


def render_text_view(title, content, width, height):
    # render_frame reserves 1 row for the footer; keep title + rule + body.
    lines = [title, "=" * max(0, width)]
    for line in content.splitlines()[:max(0, height - 3)]:
        lines.append(line)
    return lines


def render_job(state, width, height):
    content = "{} job: {}\n{}".format(
        state.job_kind.capitalize(), state.job_status, state.message
    )
    if state.error:
        content += "\n\nError: " + state.error
    return render_text_view("Background job", content, width, height)


def render_options(state, width, height):
    lines = ["VIME Options", "=" * max(0, width)]
    label_width = max(len(opt["label"]) for opt in OPTIONS)
    for index, opt in enumerate(OPTIONS, start=1):
        current = getattr(state, opt["state_attr"])
        choice_strs = []
        for choice in opt["choices"]:
            text = opt["display"].get(choice, choice)
            choice_strs.append("[{}]".format(text) if choice == current else text)
        lines.append("{}  {}: {}".format(
            index, opt["label"].ljust(label_width), "  ".join(choice_strs)
        ))
    return lines


def footer_keybinds(state):
    if state.view in ("datasets", "split") and state.focus == "sidebar":
        return "↑/↓ move  Enter full  → split/open  ← close open  i info  c compute  q quit"
    if (
        state.view == "plot"
        or (
            state.view == "split"
            and state.focus == "table"
            and state.active_pane is state.plot_pane
        )
    ):
        return "x/y axis  g group  xl/yl limits  b back  r replot  o options  q quit"
    if state.view in ("table", "split"):
        return "↑/↓ row  j/k page  h/l column  ←/→ viewport  Tab panes  b sidebar/back"
    if state.view == "options":
        return "Press 1-{} to cycle  b back  q quit".format(len(OPTIONS))
    return "b back  r refresh  o options  q quit"


def _compose_footer(status, binds, width):
    """Keep keybinds visible: crop status from the right when the line is too long."""
    if not status:
        return _crop(binds, width)
    return _crop(binds + "  |  " + status, width)


def render_footer(state, width):
    if state.prompt:
        labels = {
            "plot": "Plot (x y [type] [logx|logy|group=|agg=|x=a:b|y=a:b|sort=x]): ",
            "filter": "Filter (column operator value): ",
            "yank": "Yank columns to front: ",
            "plot_x": "X column: ",
            "plot_y": "Y column: ",
            "plot_group": "Group-by column: ",
            "plot_xlim": "X limits (min max): ",
            "plot_ylim": "Y limits (min max): ",
        }
        return _crop(labels.get(state.prompt_kind, "> ") + state.prompt_text, width)
    if state.pending_g:
        return _crop((state.command_buffer or "") + "G", width)
    if state.command_buffer:
        return _crop(state.command_buffer, width)

    binds = footer_keybinds(state)
    if state.error:
        return _compose_footer("Error: " + state.error, binds, width)
    if state.message:
        return _compose_footer(state.message, binds, width)

    status = []
    if (
        state.view in ("table", "split")
        and state.focus == "table"
        and state.active_pane is not state.plot_pane
    ):
        pane_width = state.width
        if state.view == "split" and state.active_pane is not None:
            sidebar_width = min(
                30, max(14, state.width // 4), max(1, state.width - 10)
            )
            pane_width = pane_rectangles(
                state.panes,
                max(1, state.width - sidebar_width - 1),
                max(1, state.height - 1 - MASTER_HEADER_ROWS),
            ).get(state.active_pane_index, (state.width, state.height))[0]
        if state.filter_text:
            status.append("filter: " + state.filter_text)
        if state.table_columns:
            status.append("cols {}-{}/{}".format(
                state.column_offset + 1,
                max(visible_column_indices(state.active_pane, pane_width) or [0]) + 1,
                len(state.table_columns),
            ))
    return _compose_footer("  ".join(status), binds, width)


def render_column_guide(state, width):
    """Show 1-based column references while a command expects them."""
    if not state.prompt or state.prompt_kind not in {
        "plot", "filter", "yank", "plot_x", "plot_y", "plot_group",
        "plot_xlim", "plot_ylim",
    }:
        return ""
    return _crop("  ".join(
        "[{}] {}".format(index, column)
        for index, column in enumerate(state.table_columns, start=1)
    ), width)


def _fit(lines, width, height):
    visible = [_crop(line, width).ljust(width) for line in lines[:height]]
    visible.extend(" " * width for _ in range(height - len(visible)))
    return visible


def pane_rectangles(panes, width, height):
    """Return pane sizes for the same recursive split used by the renderer."""
    result = {}

    def visit(index, pane_width, pane_height):
        if index == len(panes) - 1 or (pane_width < 3 and pane_height < 3):
            result[index] = (pane_width, pane_height)
            return
        orientation = panes[index + 1].split_orientation
        if (orientation == "horizontal" or pane_width < 3) and pane_height >= 3:
            first = max(1, (pane_height - 1) // 2)
            result[index] = (pane_width, first)
            visit(index + 1, pane_width, pane_height - first - 1)
        else:
            first = max(1, (pane_width - 1) // 2)
            result[index] = (first, pane_height)
            visit(index + 1, pane_width - first - 1, pane_height)

    if panes and width > 0 and height > 0:
        visit(0, width, height)
    return result


def _render_panes(state, width, height, start=0):
    pane = state.panes[start]
    if start == len(state.panes) - 1 or (width < 3 and height < 3):
        return _fit(
            _render_pane(state, pane, width, height, start),
            width, height
        )
    orientation = state.panes[start + 1].split_orientation
    if (orientation == "horizontal" or width < 3) and height >= 3:
        first = max(1, (height - 1) // 2)
        top = _fit(
            _render_pane(state, pane, width, first, start),
            width, first
        )
        return top + ["─" * width] + _render_panes(
            state, width, height - first - 1, start + 1
        )
    first = max(1, (width - 1) // 2)
    left = _fit(
        _render_pane(state, pane, first, height, start),
        first, height
    )
    right = _render_panes(state, width - first - 1, height, start + 1)
    return [left_line + "│" + right_line for left_line, right_line in zip(left, right)]


def _render_pane(state, pane, width, height, index):
    if pane is state.plot_pane:
        if state.job_status != "done":
            return render_job(state, width, height)
        return render_text_view(
            state.plot_header or "Plot", state.plot_content, width, height
        )
    return render_table(pane, width, height, index == state.active_pane_index)


def render_split(state, width, height):
    if not state.panes:
        return render_dataset_list(state, width, height)
    header = render_master_header(state, width, focused=(state.focus == "sidebar"))
    content_height = max(0, height - MASTER_HEADER_ROWS)
    sidebar_width = min(30, max(14, width // 4), max(1, width - 10))
    table_width = max(1, width - sidebar_width - 1)
    sidebar = _fit(
        render_dataset_list(state, sidebar_width, content_height, sidebar=True),
        sidebar_width, content_height
    )
    tables = _render_panes(state, table_width, content_height)
    return header + [left + "│" + right for left, right in zip(sidebar, tables)]


def render_frame(state, width, height):
    """Pure full-frame renderer."""
    if height <= 0:
        return ""
    column_guide = render_column_guide(state, width) if height > 1 else ""
    body_height = max(0, height - 1 - bool(column_guide))
    if state.view == "split":
        lines = render_split(state, width, body_height)
    elif state.view == "table":
        lines = render_table(state.active_pane, width, body_height, True)
    elif state.view == "info":
        lines = render_text_view("Dataset information", state.info_content, width, height)
    elif state.view == "plot":
        lines = render_text_view(state.plot_header or "Plot", state.plot_content, width, height)
    elif state.view == "job":
        lines = render_job(state, width, height)
    elif state.view == "options":
        lines = render_options(state, width, height)
    else:
        lines = render_dataset_list(state, width, height)

    visible = [_crop(line, width) for line in lines[:body_height]]
    visible.extend("" for _ in range(body_height - len(visible)))
    if column_guide:
        visible.append(column_guide)
    visible.append(render_footer(state, width))
    return "\n".join(visible)


class Application:
    def __init__(self, host, port, files):
        self.state = AppState(files=[os.path.abspath(path) for path in files])
        self.state.mode = load_default_mode()
        self.state.table_borders = load_table_borders()
        self.state.plot_charset = load_plot_charset()
        self.state.float_formatting = load_float_formatting()
        self.state.path_formatting = load_path_formatting()
        self.client = HttpClient(host, port)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.running = True
        self.options_return_view = "datasets"
        self.content_return_view = "datasets"
        self.content_return_focus = "sidebar"
        self.free_vim_requested = self.state.mode == "free_vim"

    def cycle_option(self, opt):
        """Advance one Options-menu row to its next choice and persist it."""
        choices = opt["choices"]
        current = getattr(self.state, opt["state_attr"])
        next_index = (choices.index(current) + 1) % len(choices) if current in choices else 0
        value = choices[next_index]
        try:
            _save_setting(opt["section"], opt["field"], value)
            setattr(self.state, opt["state_attr"], value)
            if opt["state_attr"] == "table_borders":
                for pane in self.state.panes:
                    pane.table_borders = value
            elif opt["state_attr"] in ("float_formatting", "path_formatting"):
                for pane in self.state.panes:
                    self.request_table(pane=pane)
            self.state.message = "{}: {}".format(opt["label"], opt["display"].get(value, value))
            self.state.error = ""
            if opt["state_attr"] == "mode":
                self.free_vim_requested = value == "free_vim"
        except OSError as exc:
            self.state.error = "Could not save settings: {}".format(exc)

    def submit(self, kind, command=None, payload=None, pane=None):
        if self.state.pending is not None:
            return False
        if command is None:
            future = self.executor.submit(self.client.health)
        else:
            body = {"file": self.state.active_file}
            body.update(payload or {})
            future = self.executor.submit(self.client.post, command, body)
        self.state.pending = future
        self.state.pending_kind = kind
        self.state.pending_pane = pane
        self.state.message = "Loading..."
        self.state.error = ""
        self.state.dirty = True
        return True

    def open_file(self):
        self.submit("open", "open")

    def pane_size(self, pane):
        if self.state.view != "split":
            return self.state.width, max(1, self.state.height - 1)
        sidebar_width = min(
            30, max(14, self.state.width // 4), max(1, self.state.width - 10)
        )
        sizes = pane_rectangles(
            self.state.panes,
            max(1, self.state.width - sidebar_width - 1),
            max(1, self.state.height - 1 - MASTER_HEADER_ROWS),
        )
        return sizes.get(self.state.panes.index(pane), (self.state.width, self.state.height))

    def pane_page_limit(self, pane):
        _, pane_height = self.pane_size(pane)
        return max(1, pane_height - TABLE_CHROME_ROWS)

    def request_table(self, offset=None, pane=None):
        pane = pane or self.state.active_pane
        if pane is None:
            return
        if offset is not None:
            pane.table_offset = max(0, offset)
        page_limit = self.pane_page_limit(pane)
        payload = {
            "dataset": pane.dataset,
            "offset": pane.table_offset,
            "limit": page_limit,
            "float_formatting": self.state.float_formatting == "on",
            "path_formatting": self.state.path_formatting == "on",
        }
        if pane.column_order:
            payload["columns"] = pane.column_order
        if pane.filter_spec:
            payload["filter"] = pane.filter_spec
        if not self.submit("table_page", "table_page", payload, pane):
            if pane not in self.state.queued_panes:
                self.state.queued_panes.append(pane)

    def open_selected_table(self, split=False):
        dataset = self.state.active_dataset
        if not dataset:
            return
        pane = self.state.pane_for(dataset)
        created = pane is None
        if pane is None:
            pane = TablePane(dataset, table_borders=self.state.table_borders)
            self.state.panes.append(pane)
        self.state.active_pane_index = self.state.panes.index(pane)
        self.state.focus = "table"
        self.state.view = "split" if split else "table"
        if created:
            self.request_table(pane=pane)

    def close_selected_table(self):
        pane = self.state.pane_for(self.state.active_dataset)
        if pane is None:
            return
        index = self.state.panes.index(pane)
        self.state.panes.remove(pane)
        if pane is self.state.plot_pane:
            self.state.plot_pane = None
        self.state.active_pane_index = min(index, max(0, len(self.state.panes) - 1))
        if not self.state.panes:
            self.state.view = "datasets"
        self.state.focus = "sidebar"

    def request_info(self):
        if self.state.active_dataset:
            self.content_return_view = self.state.view
            self.content_return_focus = self.state.focus
            self.submit("info", "info", {"name": self.state.active_dataset})

    def start_plot(self, payload=None):
        if payload is None:
            try:
                payload = self.parse_plot_prompt(self.state.prompt_text)
            except (ValueError, IndexError, KeyError) as exc:
                self.state.error = str(exc)
                return
        payload = dict(payload)
        target_pane = payload.pop("_pane", None) or self.state.active_pane
        payload["dataset"] = payload.get(
            "dataset",
            target_pane.dataset if target_pane is not None else self.state.active_dataset,
        )
        split_plot = self.state.view == "split" and target_pane is not None
        if split_plot:
            pane_width, pane_height = self.pane_size(target_pane)
            payload["width"] = max(20, pane_width)
            payload["height"] = max(8, pane_height - 3)
        else:
            payload["width"] = max(20, self.state.width - 2)
            # Match render_text_view body budget (height - 3 under frame footer).
            payload["height"] = max(8, self.state.height - 3)
        payload["charset"] = self.state.plot_charset
        if target_pane is not None and target_pane.filter_spec:
            payload["filter"] = target_pane.filter_spec
        self.state.plot_request = {
            "dataset": payload["dataset"],
            "columns": payload["columns"],
            "type": payload["type"],
            "charset": payload["charset"],
            "x_scale": payload.get("x_scale", "linear"),
            "y_scale": payload.get("y_scale", "linear"),
            "x_lim": payload.get("x_lim"),
            "y_lim": payload.get("y_lim"),
            "sort_x": payload.get("sort_x", False),
            "groupby": payload.get("groupby"),
            "agg": payload.get("agg", "mean"),
            "_pane": target_pane,
        }
        self.state.prompt = False
        self.state.prompt_kind = ""
        self.state.job_kind = "plot"
        self.state.job_status = "running"
        self.content_return_view = self.state.view
        self.content_return_focus = self.state.focus
        self.state.plot_pane = target_pane if split_plot else None
        if not split_plot:
            self.state.view = "job"
        self.state.next_replot = 0.0
        self.submit("plot_start", "plot_start", payload)

    def parse_plot_prompt(self, text):
        parts = shlex.split(text)
        if not parts:
            raise ValueError("Enter columns and optional plot options")
        plot_type = "line"
        x_scale, y_scale = "linear", "linear"
        x_lim = y_lim = None
        sort_x = False
        groupby = None
        agg = "mean"
        positional = []
        for part in parts:
            lower = part.lower()
            if lower in VALID_PLOT_TYPES:
                plot_type = lower
            elif lower == "logx":
                x_scale = "log"
            elif lower == "logy":
                y_scale = "log"
            elif lower == "log":
                x_scale = y_scale = "log"
            elif "=" in part:
                key, value = part.split("=", 1)
                key = key.lower()
                if key == "group":
                    groupby = self.resolve_column(value)
                elif key == "agg":
                    agg = value.lower()
                elif key == "sort":
                    sort_x = value.lower() in ("x", "true", "1")
                elif key == "x":
                    x_lim = self._parse_lim_token(value)
                elif key == "y":
                    y_lim = self._parse_lim_token(value)
                else:
                    raise ValueError("Unknown option: " + part)
            else:
                positional.append(part)
        if plot_type == "hist":
            if len(positional) < 1:
                raise ValueError("hist needs a column")
            columns = [self.resolve_column(positional[0])]
            if len(positional) >= 2:
                columns = [self.resolve_column(p) for p in positional[:2]]
            if len(positional) > 2:
                raise ValueError("Unknown plot token: " + positional[2])
        else:
            if len(positional) < 2:
                raise ValueError("Enter two columns, then type/options")
            if len(positional) > 2:
                raise ValueError(
                    "Unknown plot token: {!r}. Types: {}".format(
                        positional[2], ", ".join(sorted(VALID_PLOT_TYPES))
                    )
                )
            columns = [self.resolve_column(p) for p in positional[:2]]
        return {
            "columns": columns,
            "type": plot_type,
            "x_scale": x_scale,
            "y_scale": y_scale,
            "x_lim": x_lim,
            "y_lim": y_lim,
            "sort_x": sort_x,
            "groupby": groupby,
            "agg": agg,
        }

    @staticmethod
    def _parse_lim_token(value):
        if value.lower() == "auto":
            return None
        if ":" not in value:
            raise ValueError("limit must be min:max or auto")
        left, right = value.split(":", 1)
        return [float(left), float(right)]

    def start_compute(self):
        self.state.job_kind = "compute"
        self.content_return_view = self.state.view
        self.content_return_focus = self.state.focus
        self.state.view = "job"
        self.submit("compute_start", "compute_start")

    def switch_file(self, step):
        if len(self.state.files) < 2:
            return
        self.state.file_index = (self.state.file_index + step) % len(self.state.files)
        self.state.datasets = []
        self.state.selected_dataset = 0
        self.state.panes = []
        self.state.active_pane_index = 0
        self.state.focus = "sidebar"
        self.state.view = "datasets"
        self.open_file()

    def reset_table_view(self):
        pane = self.state.active_pane
        if pane is None:
            return
        pane.selected_row = 0
        pane.table_offset = 0
        pane.table_rows = []
        pane.table_columns = []
        pane.column_order = []
        pane.column_offset = 0
        pane.filter_spec = None
        pane.filter_text = ""

    def check_pending(self):
        future = self.state.pending
        if future is None or not future.done():
            return
        kind = self.state.pending_kind
        pane = self.state.pending_pane
        self.state.pending = None
        self.state.pending_kind = ""
        self.state.pending_pane = None
        try:
            response = future.result()
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Request failed"))
            self.handle_response(kind, response, pane)
        except Exception as exc:
            self.state.message = ""
            self.state.error = str(exc)
            if isinstance(exc, (urllib.error.URLError, OSError, TimeoutError)):
                self.state.needs_reopen = True
            self.state.next_keepalive = time.monotonic() + 2
        if self.state.pending is None and self.state.queued_panes:
            self.request_table(pane=self.state.queued_panes.pop(0))
        self.state.dirty = True

    def handle_response(self, kind, response, pane=None):
        self.state.message = response.get("message", "")
        self.state.error = ""
        if kind in ("open", "list_tables"):
            self.state.datasets = response.get("tables", [])
            self.state.selected_dataset = min(
                self.state.selected_dataset, max(0, len(self.state.datasets) - 1)
            )
            if kind == "open" or self.state.view not in ("table", "split"):
                self.state.view = "datasets"
                self.state.focus = "sidebar"
            if kind == "open":
                self.submit("resume_plot", "plot_status")
        elif kind == "table_page":
            pane = pane or self.state.active_pane
            if pane is None:
                return
            pane.table_rows = response["rows"]
            pane.table_columns = response["columns"]
            pane.table_offset = response["offset"]
            pane.total_rows = response["total_rows"]
            pane.column_offset = min(
                pane.column_offset, max(0, len(pane.table_columns) - 1)
            )
            if not pane.column_order:
                pane.column_order = list(pane.table_columns)
            pane.selected_row = min(
                pane.selected_row, max(0, len(pane.table_rows) - 1)
            )
        elif kind == "info":
            self.state.info_content = response.get("content", "")
            self.state.view = "info"
        elif kind in ("plot_start", "compute_start"):
            self.state.job_status = response.get("status", "running")
            if kind != "plot_start" or self.state.plot_pane is None:
                self.state.view = "job"
            self.state.next_poll = time.monotonic()
        elif kind in ("plot_status", "compute_status"):
            self.state.job_status = response.get("status", "idle")
            self.state.error = response.get("error") or ""
            if self.state.job_status == "done" and kind == "plot_status":
                raw = response.get("content", "")
                title, _, body = raw.partition("\n\n")
                self.state.plot_header = title or "Plot"
                self.state.plot_content = body if body else raw
                if self.state.plot_pane is None:
                    self.state.view = "plot"
            elif self.state.job_status == "done" and kind == "compute_status":
                self.state.message = response.get("message", "Compute done")
            elif self.state.job_status == "error":
                if kind != "plot_status" or self.state.plot_pane is None:
                    self.state.view = "job"
            else:
                self.state.next_poll = time.monotonic() + (
                    PLOT_POLL_SECONDS if self.state.job_kind == "plot"
                    else COMPUTE_POLL_SECONDS
                )
        elif kind == "resume_plot":
            if response.get("status") in ("running", "error"):
                self.state.job_kind = "plot"
                self.state.job_status = response["status"]
                self.state.error = response.get("error") or ""
                self.state.view = "job"
                self.state.next_poll = time.monotonic()
            else:
                self.submit("resume_compute", "compute_status")
        elif kind == "resume_compute":
            if response.get("status") in ("running", "error"):
                self.state.job_kind = "compute"
                self.state.job_status = response["status"]
                self.state.error = response.get("error") or ""
                self.state.view = "job"
                self.state.next_poll = time.monotonic()
        elif kind == "health":
            self.state.message = ""
            if self.state.needs_reopen:
                self.state.needs_reopen = False
                self.open_file()
        self.state.next_keepalive = time.monotonic() + KEEPALIVE_SECONDS

    def launch_free_vim(self, terminal):
        self.free_vim_requested = False
        vim = shutil.which("vim")
        if vim is None:
            self.state.error = "Free Vim requires 'vim' on PATH"
            self.state.dirty = True
            return
        path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", encoding="utf-8", delete=False
            ) as handle:
                path = handle.name
                handle.write(render_frame(self.state, self.state.width, self.state.height))
                handle.write("\n")
            with terminal.suspended():
                subprocess.run([
                    vim, "-R", "-n",
                    "-c", "setlocal nomodifiable",
                    "-c", "nnoremap <buffer> ,q :qa!<CR>",
                    path,
                ], check=False)
        except OSError as exc:
            self.state.error = "Could not launch Vim: {}".format(exc)
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self.state.dirty = True

    def run_timers(self):
        now = time.monotonic()
        if self.state.pending is not None:
            return
        if (
            (self.state.view == "job" or self.state.plot_pane is not None)
            and self.state.job_status == "running"
        ):
            if now >= self.state.next_poll:
                command = self.state.job_kind + "_status"
                self.submit(command, command)
                return
        if (
            (self.state.view == "plot" or self.state.plot_pane is not None)
            and self.state.plot_request is not None
            and self.state.next_replot
            and now >= self.state.next_replot
        ):
            self.state.next_replot = 0.0
            self.start_plot(dict(self.state.plot_request))
            return
        if now >= self.state.next_keepalive:
            self.submit("health")

    def resolve_column(self, reference):
        try:
            index = int(reference) - 1
        except (TypeError, ValueError):
            if reference in self.state.table_columns:
                return reference
            raise KeyError(reference)
        if index < 0 or index >= len(self.state.table_columns):
            raise IndexError("{} (valid range 1-{})".format(
                reference, len(self.state.table_columns)
            ))
        return self.state.table_columns[index]

    def open_prompt(self, kind, text=""):
        self.state.prompt = True
        self.state.prompt_kind = kind
        self.state.prompt_text = text
        self.state.error = ""
        self.state.dirty = True

    def apply_plot_edit(self):
        request = self.state.plot_request
        if request is None:
            self.state.error = "No plot to update"
            return
        kind = self.state.prompt_kind
        try:
            parts = shlex.split(self.state.prompt_text)
            if kind in ("plot_xlim", "plot_ylim"):
                if len(parts) != 2:
                    raise ValueError("Enter two numbers")
                request = dict(request)
                request["x_lim" if kind == "plot_xlim" else "y_lim"] = [
                    float(parts[0]), float(parts[1])
                ]
            else:
                if len(parts) != 1:
                    raise ValueError("Enter one column number")
                column = self.resolve_column(parts[0])
                request = dict(request)
                if kind == "plot_group":
                    request["groupby"] = column
                else:
                    columns = list(request["columns"])
                    index = 0 if kind == "plot_x" else 1
                    if index < len(columns):
                        columns[index] = column
                    else:
                        columns.append(column)
                    request["columns"] = columns
        except (ValueError, IndexError, KeyError) as exc:
            self.state.error = "Invalid plot update: {}".format(exc)
            return
        self.start_plot(request)

    def apply_filter(self):
        try:
            parts = shlex.split(self.state.prompt_text)
        except ValueError as exc:
            self.state.error = str(exc)
            return
        if len(parts) != 3:
            self.state.error = "Filter must be: column operator value"
            return
        left, operation, raw_right = parts
        if operation not in ("<", "<=", "==", "!=", ">=", ">"):
            self.state.error = "Unsupported filter operator: " + operation
            return
        try:
            left = self.resolve_column(left)
            try:
                right = {"kind": "number", "value": int(raw_right)}
            except ValueError:
                try:
                    right = {"kind": "number", "value": float(raw_right)}
                except ValueError:
                    right = {"kind": "column", "value": self.resolve_column(raw_right)}
        except (IndexError, KeyError) as exc:
            self.state.error = "Invalid column: {}".format(exc)
            return
        self.state.filter_spec = {
            "left": left,
            "operator": operation,
            "right": right,
        }
        self.state.filter_text = self.state.prompt_text
        self.state.prompt = False
        self.state.prompt_kind = ""
        self.state.table_offset = 0
        self.state.selected_row = 0
        self.request_table(0)

    def apply_yank(self):
        try:
            references = shlex.split(self.state.prompt_text)
            selected = [self.resolve_column(reference) for reference in references]
        except (ValueError, IndexError, KeyError) as exc:
            self.state.error = "Invalid yank: {}".format(exc)
            return
        if not selected:
            self.state.error = "Enter at least one column number"
            return
        selected = list(dict.fromkeys(selected))
        self.state.column_order = selected + [
            column for column in self.state.table_columns if column not in selected
        ]
        self.state.column_offset = 0
        self.state.prompt = False
        self.state.prompt_kind = ""
        self.request_table()

    def handle_prompt_key(self, key):
        if key in ("escape",):
            self.state.prompt = False
            self.state.prompt_kind = ""
        elif key == "enter":
            if self.state.prompt_kind == "plot":
                self.start_plot()
            elif self.state.prompt_kind == "filter":
                self.apply_filter()
            elif self.state.prompt_kind == "yank":
                self.apply_yank()
            elif self.state.prompt_kind.startswith("plot_"):
                self.apply_plot_edit()
        elif key == "backspace":
            self.state.prompt_text = self.state.prompt_text[:-1]
        elif (
            key == "l"
            and not self.state.prompt_text
            and self.state.prompt_kind in ("plot_x", "plot_y")
        ):
            self.state.prompt_kind = (
                "plot_xlim" if self.state.prompt_kind == "plot_x" else "plot_ylim"
            )
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.state.prompt_text += key
        self.state.dirty = True

    def move_pages(self, pages):
        pane = self.state.active_pane
        if pane is None:
            return
        page_limit = self.pane_page_limit(pane)
        last = max(0, ((pane.total_rows - 1) // page_limit) * page_limit)
        target = min(last, max(0, pane.table_offset + pages * page_limit))
        pane.selected_row = 0
        if target != pane.table_offset:
            self.request_table(target)

    def move_columns(self, amount):
        maximum = max(0, len(self.state.table_columns) - 1)
        self.state.column_offset = min(maximum, max(0, self.state.column_offset + amount))

    def viewport_columns(self):
        pane = self.state.active_pane
        if pane is None:
            return 1
        pane_width, _ = self.pane_size(pane)
        return max(1, len(visible_column_indices(pane, pane_width)))

    def consume_count(self, default=1):
        count = int(self.state.command_buffer) if self.state.command_buffer else default
        self.state.command_buffer = ""
        return count

    def handle_key(self, key):
        if key is None:
            return
        if self.state.prompt:
            self.handle_prompt_key(key)
            return
        if key == "q":
            self.running = False
            return
        if key == "o" and self.state.view != "options":
            self.state.command_buffer = ""
            self.state.pending_g = False
            self.options_return_view = self.state.view
            self.state.view = "options"
            self.state.dirty = True
            return
        if key == "escape":
            if self.state.command_buffer or self.state.pending_g:
                self.state.command_buffer = ""
                self.state.pending_g = False
            self.state.dirty = True
            return
        if key == "b":
            self.state.command_buffer = ""
            self.state.pending_g = False
            if (
                self.state.view == "split"
                and self.state.focus == "table"
                and self.state.active_pane is self.state.plot_pane
            ):
                self.state.plot_pane = None
            elif self.state.view == "split":
                self.state.focus = "sidebar"
            elif self.state.view == "options":
                self.state.view = self.options_return_view
            elif self.state.view == "table":
                self.state.view = "datasets"
                self.state.focus = "sidebar"
            elif self.state.view != "datasets":
                self.state.view = self.content_return_view
                self.state.focus = self.content_return_focus
            self.state.error = ""
            self.state.dirty = True
            return
        if self.state.view == "options":
            if isinstance(key, str) and key.isdigit():
                index = int(key) - 1
                if 0 <= index < len(OPTIONS):
                    self.cycle_option(OPTIONS[index])
            self.state.dirty = True
            return
        if key == "r":
            if (
                self.state.view == "split"
                and self.state.focus == "table"
                and self.state.active_pane is self.state.plot_pane
            ):
                self.open_prompt("plot")
            elif self.state.view == "table" or (
                self.state.view == "split" and self.state.focus == "table"
            ):
                self.request_table()
            elif self.state.view == "info":
                self.request_info()
            elif self.state.view == "plot":
                self.open_prompt("plot")
            elif self.state.view == "job" and self.state.job_kind:
                command = self.state.job_kind + "_status"
                self.submit(command, command)
            else:
                self.submit("list_tables", "list_tables")
            return

        if self.state.view == "plot" or (
            self.state.view == "split"
            and self.state.focus == "table"
            and self.state.active_pane is self.state.plot_pane
        ):
            if key == "\t":
                self.state.active_pane_index = (
                    self.state.active_pane_index + 1
                ) % len(self.state.panes)
            elif key == "x":
                self.open_prompt("plot_x")
            elif key == "y":
                self.open_prompt("plot_y")
            elif key == "g":
                self.open_prompt("plot_group")
            self.state.dirty = True
            return

        if self.state.view == "datasets" or (
            self.state.view == "split" and self.state.focus == "sidebar"
        ):
            if key in ("up", "k") and self.state.datasets:
                self.state.selected_dataset = max(0, self.state.selected_dataset - 1)
            elif key in ("down", "j") and self.state.datasets:
                self.state.selected_dataset = min(
                    len(self.state.datasets) - 1, self.state.selected_dataset + 1
                )
            elif key == "left":
                self.close_selected_table()
            elif key == "right":
                self.open_selected_table(split=True)
            elif key == "enter":
                self.open_selected_table(split=False)
            elif key == "i":
                self.request_info()
            elif key == "c":
                self.start_compute()
        elif self.state.view in ("table", "split") and self.state.active_pane is not None:
            pane = self.state.active_pane
            if key == "\t":
                self.state.active_pane_index = (
                    self.state.active_pane_index + 1
                ) % len(self.state.panes)
                self.state.focus = "table"
                self.state.dirty = True
                return
            if self.state.pending_g:
                if key == "G":
                    count = self.consume_count(default=0)
                    page_limit = self.pane_page_limit(pane)
                    last = max(0, ((pane.total_rows - 1) // page_limit) * page_limit)
                    self.state.pending_g = False
                    self.request_table(max(0, last - count * page_limit))
                    pane.selected_row = 0
                    return
                self.state.pending_g = False
                self.state.command_buffer = ""
            if isinstance(key, str) and len(key) == 1 and key.isdigit():
                self.state.command_buffer += key
            elif key in ("up",):
                self.state.command_buffer = ""
                if pane.selected_row > 0:
                    pane.selected_row -= 1
                elif pane.table_offset > 0:
                    self.request_table(max(
                        0, pane.table_offset - self.pane_page_limit(pane)
                    ))
                elif self.state.view == "split" and self.state.active_pane_index > 0:
                    pane.split_orientation = "horizontal"
            elif key in ("down",):
                self.state.command_buffer = ""
                if pane.selected_row + 1 < len(pane.table_rows):
                    pane.selected_row += 1
                elif pane.table_offset + len(pane.table_rows) < pane.total_rows:
                    pane.selected_row = 0
                    self.request_table(pane.table_offset + self.pane_page_limit(pane))
            elif key == "j":
                self.move_pages(self.consume_count())
            elif key == "k":
                self.move_pages(-self.consume_count())
            elif key == "g":
                count = self.consume_count(default=0)
                page_limit = self.pane_page_limit(pane)
                self.request_table(min(
                    max(0, ((pane.total_rows - 1) // page_limit) * page_limit),
                    count * page_limit,
                ))
                pane.selected_row = 0
            elif key == "G":
                self.state.pending_g = True
            elif key == "page_up":
                self.state.command_buffer = ""
                self.move_pages(-1)
            elif key == "page_down":
                self.state.command_buffer = ""
                self.move_pages(1)
            elif key == "h":
                self.move_columns(-self.consume_count())
            elif key == "l":
                self.move_columns(self.consume_count())
            elif key == "left":
                count = self.consume_count()
                if pane.column_offset == 0 and self.state.view == "split":
                    self.state.focus = "sidebar"
                else:
                    self.move_columns(-count * self.viewport_columns())
            elif key == "right":
                self.move_columns(self.consume_count() * self.viewport_columns())
            elif key == "i":
                self.state.command_buffer = ""
                self.request_info()
            elif key == "p":
                self.state.command_buffer = ""
                self.open_prompt("plot")
            elif key == "f":
                self.state.command_buffer = ""
                self.open_prompt("filter")
            elif key == "y":
                self.state.command_buffer = ""
                self.open_prompt("yank")
            elif key == "u" and self.state.filter_spec:
                self.state.command_buffer = ""
                self.state.filter_spec = None
                self.state.filter_text = ""
                self.request_table(0)
        self.state.dirty = True

    def loop(self, terminal):
        self.state.next_keepalive = time.monotonic() + KEEPALIVE_SECONDS
        self.open_file()
        while self.running:
            width, height = terminal.size()
            if (width, height) != (self.state.width, self.state.height):
                self.state.width, self.state.height = width, height
                self.state.dirty = True
                if self.state.view in ("table", "split") and self.state.pending is None:
                    self.request_table()
                if (
                    (self.state.view == "plot" or self.state.plot_pane is not None)
                    and self.state.plot_request is not None
                ):
                    self.state.next_replot = time.monotonic() + PLOT_RESIZE_DEBOUNCE
            self.check_pending()
            self.run_timers()
            if self.state.dirty:
                terminal.write_frame(render_frame(self.state, width, height))
                self.state.dirty = False
            if self.free_vim_requested and self.state.pending is None:
                self.launch_free_vim(terminal)
            self.handle_key(terminal.read_key(0.05))

    def close(self):
        self.executor.shutdown(wait=False)


def main(host, port, files):
    """Run the TUI and return its process-style exit code."""
    if not files:
        print("VIME: provide at least one HDF5 file", file=sys.stderr)
        return 2
    missing = [path for path in files if not os.path.isfile(path)]
    if missing:
        print("VIME: file not found: {}".format(missing[0]), file=sys.stderr)
        return 2

    app = Application(host, port, files)
    try:
        with TerminalSession() as terminal:
            app.loop(terminal)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        app.close()


def _cli():
    parser = argparse.ArgumentParser(description="VIME terminal HDF5 viewer")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--host", default=os.environ.get("VIME_HTTP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("VIME_HTTP_PORT", "51789"))
    )
    args = parser.parse_args()
    return main(args.host, args.port, args.files)


if __name__ == "__main__":
    sys.exit(_cli())
