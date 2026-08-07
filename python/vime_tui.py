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
class AppState:
    files: list
    file_index: int = 0
    view: str = "datasets"
    datasets: list = field(default_factory=list)
    selected_dataset: int = 0
    selected_row: int = 0
    table_offset: int = 0
    table_rows: list = field(default_factory=list)
    table_columns: list = field(default_factory=list)
    total_rows: int = 0
    info_content: str = ""
    plot_content: str = ""
    plot_header: str = "Plot"
    plot_request: object = None
    plot_charset: str = "braille"
    table_borders: str = "default"
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
    column_offset: int = 0
    column_order: list = field(default_factory=list)
    filter_spec: object = None
    filter_text: str = ""
    mode: str = "default"
    width: int = 80
    height: int = 24
    dirty: bool = True
    needs_reopen: bool = False

    @property
    def active_file(self):
        return self.files[self.file_index]

    @property
    def active_dataset(self):
        if not self.datasets:
            return ""
        return self.datasets[self.selected_dataset]["name"]

    @property
    def page_limit(self):
        return max(1, self.height - 8)


def _crop(text, width):
    return text[:max(0, width)]


def render_dataset_list(state, width, height):
    lines = [
        "VIME  {}  [file {}/{}]".format(
            os.path.basename(state.active_file), state.file_index + 1, len(state.files)
        ),
        "=" * max(0, width),
    ]
    available = max(0, height - 5)
    start = max(0, state.selected_dataset - available + 1)
    for index, item in enumerate(state.datasets[start:start + available], start):
        marker = ">" if index == state.selected_dataset else " "
        lines.append("{} {:<45} ({} rows x {} cols)".format(
            marker, item["name"], item["rows"], item["cols"]
        ))
    if not state.datasets:
        lines.append("  No datasets found")
    return lines


def render_table(state, width, height):
    end = min(state.total_rows, state.table_offset + len(state.table_rows))
    lines = [
        "{}  [rows {}-{} of {}]".format(
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
    for index, row in enumerate(state.table_rows[:max(0, height - 6)]):
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


def render_footer(state, width):
    if state.prompt:
        labels = {
            "plot": "Plot (x y [type] [logx|logy|group=|agg=|x=a:b|y=a:b|sort=x]): ",
            "filter": "Filter (column operator value): ",
            "yank": "Yank columns to front: ",
        }
        text = labels.get(state.prompt_kind, "> ") + state.prompt_text
    elif state.pending_g:
        text = (state.command_buffer or "") + "G"
    elif state.command_buffer:
        text = state.command_buffer
    elif state.error:
        text = "Error: " + state.error
    elif state.message:
        text = state.message
    elif state.view == "datasets":
        text = "↑/↓ or j/k move  Enter open  i info  c compute  r refresh  ←/→ file  q quit"
    elif state.view == "table":
        status = []
        if state.filter_text:
            status.append("filter: " + state.filter_text)
        if state.table_columns:
            status.append("cols {}-{}/{}".format(
                state.column_offset + 1,
                max(visible_column_indices(state, state.width) or [0]) + 1,
                len(state.table_columns),
            ))
        text = "  ".join(status + [
            "j/k page  h/l column  b/e viewport  p plot  f filter  y yank  o options  Esc back"
        ])
    elif state.view == "plot":
        text = "Esc back  r replot  o options  q quit"
    elif state.view == "options":
        text = "Press 1-{} to cycle  Esc back  q quit".format(len(OPTIONS))
    else:
        text = "Esc back  r refresh  o options  q quit"
    return _crop(text, width)


def render_frame(state, width, height):
    """Pure full-frame renderer."""
    if state.view == "table":
        lines = render_table(state, width, height)
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

    body_height = max(0, height - 1)
    visible = [_crop(line, width) for line in lines[:body_height]]
    visible.extend("" for _ in range(body_height - len(visible)))
    visible.append(render_footer(state, width))
    return "\n".join(visible)


class Application:
    def __init__(self, host, port, files):
        self.state = AppState(files=[os.path.abspath(path) for path in files])
        self.state.mode = load_default_mode()
        self.state.table_borders = load_table_borders()
        self.state.plot_charset = load_plot_charset()
        self.client = HttpClient(host, port)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.running = True
        self.options_return_view = "datasets"
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
            self.state.message = "{}: {}".format(opt["label"], opt["display"].get(value, value))
            self.state.error = ""
            if opt["state_attr"] == "mode":
                self.free_vim_requested = value == "free_vim"
        except OSError as exc:
            self.state.error = "Could not save settings: {}".format(exc)

    def submit(self, kind, command=None, payload=None):
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
        self.state.message = "Loading..."
        self.state.error = ""
        self.state.dirty = True
        return True

    def open_file(self):
        self.submit("open", "open")

    def request_table(self, offset=None):
        if not self.state.active_dataset:
            return
        if offset is not None:
            self.state.table_offset = max(0, offset)
        payload = {
            "dataset": self.state.active_dataset,
            "offset": self.state.table_offset,
            "limit": self.state.page_limit,
        }
        if self.state.column_order:
            payload["columns"] = self.state.column_order
        if self.state.filter_spec:
            payload["filter"] = self.state.filter_spec
        self.submit("table_page", "table_page", payload)

    def request_info(self):
        if self.state.active_dataset:
            self.submit("info", "info", {"name": self.state.active_dataset})

    def start_plot(self, payload=None):
        if payload is None:
            try:
                payload = self.parse_plot_prompt(self.state.prompt_text)
            except (ValueError, IndexError, KeyError) as exc:
                self.state.error = str(exc)
                return
        payload = dict(payload)
        payload["dataset"] = self.state.active_dataset
        payload["width"] = max(20, self.state.width - 2)
        # Match render_text_view body budget (height - 3 under frame footer).
        payload["height"] = max(8, self.state.height - 3)
        payload["charset"] = self.state.plot_charset
        if self.state.filter_spec:
            payload["filter"] = self.state.filter_spec
        self.state.plot_request = {
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
        }
        self.state.prompt = False
        self.state.prompt_kind = ""
        self.state.job_kind = "plot"
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
        self.state.view = "job"
        self.submit("compute_start", "compute_start")

    def switch_file(self, step):
        if len(self.state.files) < 2:
            return
        self.state.file_index = (self.state.file_index + step) % len(self.state.files)
        self.state.datasets = []
        self.state.selected_dataset = 0
        self.reset_table_view()
        self.state.view = "datasets"
        self.open_file()

    def reset_table_view(self):
        self.state.table_offset = 0
        self.state.selected_row = 0
        self.state.table_rows = []
        self.state.table_columns = []
        self.state.column_order = []
        self.state.column_offset = 0
        self.state.filter_spec = None
        self.state.filter_text = ""

    def check_pending(self):
        future = self.state.pending
        if future is None or not future.done():
            return
        kind = self.state.pending_kind
        self.state.pending = None
        self.state.pending_kind = ""
        try:
            response = future.result()
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Request failed"))
            self.handle_response(kind, response)
        except Exception as exc:
            self.state.message = ""
            self.state.error = str(exc)
            if isinstance(exc, (urllib.error.URLError, OSError, TimeoutError)):
                self.state.needs_reopen = True
            self.state.next_keepalive = time.monotonic() + 2
        self.state.dirty = True

    def handle_response(self, kind, response):
        self.state.message = response.get("message", "")
        self.state.error = ""
        if kind in ("open", "list_tables"):
            self.state.datasets = response.get("tables", [])
            self.state.selected_dataset = min(
                self.state.selected_dataset, max(0, len(self.state.datasets) - 1)
            )
            self.state.view = "datasets"
            if kind == "open":
                self.submit("resume_plot", "plot_status")
        elif kind == "table_page":
            self.state.table_rows = response["rows"]
            self.state.table_columns = response["columns"]
            self.state.table_offset = response["offset"]
            self.state.total_rows = response["total_rows"]
            self.state.column_offset = min(
                self.state.column_offset, max(0, len(self.state.table_columns) - 1)
            )
            if not self.state.column_order:
                self.state.column_order = list(self.state.table_columns)
            self.state.selected_row = min(
                self.state.selected_row, max(0, len(self.state.table_rows) - 1)
            )
            self.state.view = "table"
        elif kind == "info":
            self.state.info_content = response.get("content", "")
            self.state.view = "info"
        elif kind in ("plot_start", "compute_start"):
            self.state.job_status = response.get("status", "running")
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
                self.state.view = "plot"
            elif self.state.job_status == "done" and kind == "compute_status":
                self.state.message = response.get("message", "Compute done")
            elif self.state.job_status == "error":
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
        if self.state.view == "job" and self.state.job_status == "running":
            if now >= self.state.next_poll:
                command = self.state.job_kind + "_status"
                self.submit(command, command)
                return
        if (
            self.state.view == "plot"
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
        elif key == "backspace":
            self.state.prompt_text = self.state.prompt_text[:-1]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.state.prompt_text += key
        self.state.dirty = True

    def move_pages(self, pages):
        last = max(0, ((self.state.total_rows - 1) // self.state.page_limit)
                   * self.state.page_limit)
        target = min(last, max(0, self.state.table_offset + pages * self.state.page_limit))
        self.state.selected_row = 0
        if target != self.state.table_offset:
            self.request_table(target)

    def move_columns(self, amount):
        maximum = max(0, len(self.state.table_columns) - 1)
        self.state.column_offset = min(maximum, max(0, self.state.column_offset + amount))

    def viewport_columns(self):
        return max(1, len(visible_column_indices(self.state, self.state.width)))

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
            elif self.state.view == "options":
                self.state.view = self.options_return_view
                self.state.dirty = True
                return
            elif self.state.view == "table":
                self.state.view = "datasets"
            elif self.state.view != "datasets":
                self.state.view = "table" if self.state.table_columns else "datasets"
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
            if self.state.view == "table":
                self.request_table()
            elif self.state.view == "info":
                self.request_info()
            elif self.state.view == "plot":
                self.open_prompt("plot", "1 2 line")
            elif self.state.view == "job" and self.state.job_kind:
                command = self.state.job_kind + "_status"
                self.submit(command, command)
            else:
                self.submit("list_tables", "list_tables")
            return

        if self.state.view == "datasets":
            if key in ("up", "k") and self.state.datasets:
                self.state.selected_dataset = max(0, self.state.selected_dataset - 1)
            elif key in ("down", "j") and self.state.datasets:
                self.state.selected_dataset = min(
                    len(self.state.datasets) - 1, self.state.selected_dataset + 1
                )
            elif key == "left":
                self.switch_file(-1)
            elif key == "right":
                self.switch_file(1)
            elif key == "enter":
                self.reset_table_view()
                self.request_table()
            elif key == "i":
                self.request_info()
            elif key == "c":
                self.start_compute()
        elif self.state.view == "table":
            if self.state.pending_g:
                if key == "G":
                    count = self.consume_count(default=0)
                    last = max(0, ((self.state.total_rows - 1) // self.state.page_limit)
                               * self.state.page_limit)
                    self.state.pending_g = False
                    self.request_table(max(0, last - count * self.state.page_limit))
                    self.state.selected_row = 0
                    return
                self.state.pending_g = False
                self.state.command_buffer = ""
            if isinstance(key, str) and len(key) == 1 and key.isdigit():
                self.state.command_buffer += key
            elif key in ("up",):
                self.state.command_buffer = ""
                if self.state.selected_row > 0:
                    self.state.selected_row -= 1
                elif self.state.table_offset > 0:
                    self.request_table(max(0, self.state.table_offset - self.state.page_limit))
            elif key in ("down",):
                self.state.command_buffer = ""
                if self.state.selected_row + 1 < len(self.state.table_rows):
                    self.state.selected_row += 1
                elif self.state.table_offset + len(self.state.table_rows) < self.state.total_rows:
                    self.state.selected_row = 0
                    self.request_table(self.state.table_offset + self.state.page_limit)
            elif key == "j":
                self.move_pages(self.consume_count())
            elif key == "k":
                self.move_pages(-self.consume_count())
            elif key == "g":
                count = self.consume_count(default=0)
                self.request_table(min(
                    max(0, ((self.state.total_rows - 1) // self.state.page_limit)
                        * self.state.page_limit),
                    count * self.state.page_limit,
                ))
                self.state.selected_row = 0
            elif key == "G":
                self.state.pending_g = True
            elif key == "page_up":
                self.state.command_buffer = ""
                self.move_pages(-1)
            elif key == "page_down":
                self.state.command_buffer = ""
                self.move_pages(1)
            elif key in ("h", "left"):
                self.move_columns(-self.consume_count())
            elif key in ("l", "right"):
                self.move_columns(self.consume_count())
            elif key in ("b", "ctrl_b"):
                self.move_columns(-self.consume_count() * self.viewport_columns())
            elif key in ("e", "ctrl_e"):
                self.move_columns(self.consume_count() * self.viewport_columns())
            elif key == "i":
                self.state.command_buffer = ""
                self.request_info()
            elif key == "p":
                self.state.command_buffer = ""
                self.open_prompt("plot", "1 2 line")
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
                if self.state.view == "table" and self.state.pending is None:
                    self.request_table()
                elif self.state.view == "plot" and self.state.plot_request is not None:
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
