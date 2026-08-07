# VIME Python architecture

VIME has two Python processes:

1. `scripts/vime_launcher.py` starts or reuses a detached `vime_server.py`.
2. `vime_tui.py` runs in the foreground and communicates with the daemon over
   localhost HTTP/JSON.

The daemon owns HDF5 handles, per-file sessions, virtual computed tables, and
background plot/compute state. TUI exit does not destroy that state.

## Modules

- `vime_server.py`: daemon entry point and idle watcher
- `server/http.py`: threaded HTTP server, JSON transport, health route
- `server/app.py`: command dispatch
- `server/state.py`: per-file sessions, LRU eviction, and job state
- `server/commands/`: open, list, page, info, plot, and compute handlers
- `data_loader.py`: pandas/h5py loading and bounded row slices
- `plotter.py`: braille/quadrant-block/ASCII terminal plotting
- `terminal.py`: cross-platform ANSI screen and keyboard handling
- `vime_tui.py`: state, event loop, pure frame rendering, and HTTP client

The TUI uses one `ThreadPoolExecutor(max_workers=1)` for network requests.
Input and complete-frame rendering stay on the main thread. Each redraw is one
`stdout.write()` call.

## Persistence

`ServerState` keys sessions by normalized file path. A session retains:

- its `DataLoader` and open HDF5 handle
- virtual tables produced by compute jobs
- plot and compute threads/status

The least-recently-used session is closed and removed after
`VIME_MAX_SESSIONS` is exceeded. Access transparently reopens a closed handle.
Every HTTP request, including `/health`, resets the daemon idle timer.

The launcher scans for a healthy daemon before starting another detached
process. Windows uses a detached process group; POSIX starts a new session.

## Page loading

`DataLoader.load_table_slice(name, start, stop, columns=None)` provides the
bounded read primitive:

- pandas table stores use `HDFStore.select(start=..., stop=..., columns=...)`
- pandas fixed stores must load fully before `iloc` because PyTables cannot
  slice that format
- h5py datasets use `dataset[start:stop]` on the first axis
- scalar datasets are represented as one row
- structured fields become columns
- dimensions after the first are flattened for arrays above 2-D

Computed virtual tables use the same session-level slice API.

## HTTP API

All command requests are JSON `POST`s and include the file path identifying
the session. Responses contain an `ok` boolean and, on failure, `error`.

### `GET /health`

Returns:

```json
{"ok": true}
```

### `POST /open`

Request:

```json
{"file": "/path/data.h5"}
```

Response:

```json
{
  "ok": true,
  "tables": [{"name": "/samples", "rows": 1000, "cols": 4}]
}
```

Opening lists metadata only; it does not load every dataset.

### `POST /list_tables`

Request:

```json
{"file": "/path/data.h5"}
```

Returns the same `tables` shape as `/open`, including virtual compute results.

### `POST /table_page`

Request:

```json
{
  "file": "/path/data.h5",
  "dataset": "/samples",
  "offset": 100,
  "limit": 25,
  "columns": ["time", "value"],
  "filter": {
    "left": "value",
    "operator": ">=",
    "right": {"kind": "number", "value": 10}
  }
}
```

`columns` and `filter` are optional. Explicit column order is preserved.
Filter operators are `<`, `<=`, `==`, `!=`, `>=`, and `>`; the right side
kind is `number` or `column`. `offset` is clamped to zero or above and `limit`
is clamped to 1-1000.

Response:

```json
{
  "ok": true,
  "dataset": "/samples",
  "offset": 100,
  "limit": 25,
  "total_rows": 1000,
  "columns": ["time", "value"],
  "rows": [["2026-01-01 00:00:00", "12.5"]]
}
```

Every cell is a string, so bytes, pandas timestamps, NumPy scalars, NaN, and
infinity do not require special JSON encoding.

Unfiltered reads remain page-bounded. A filter over an arbitrary HDF5 backend
requires a full scan; each file session retains only its most recent filtered
DataFrame so subsequent pages are fast without unbounded cache growth.

### `POST /info`

Request:

```json
{"file": "/path/data.h5", "name": "/samples"}
```

Returns `content` containing shape, dtypes, non-null counts, and numeric
summary. This on-demand analysis loads the selected dataset.

### `POST /plot_start`

Request:

```json
{
  "file": "/path/data.h5",
  "dataset": "/samples",
  "columns": ["time", "value"],
  "type": "line",
  "width": 72,
  "height": 20
}
```

The dataset and both columns are explicit; plotting does not depend on a
previous table request. The response reports `running`. Poll `/plot_status`
until it reports `done` with `content`, or `error`.

### `POST /compute_start`

Starts the existing demo worker thread for the file session. Poll
`/compute_status`. A successful result includes the generated virtual table
name in `table`; refresh the dataset list to display it.

CPU-heavy work remains thread-based until profiling demonstrates GIL
contention worth the DataFrame serialization cost of a process pool.

## Terminal layer

`TerminalSession` restores terminal state from its context-manager `finally`
path on normal exit, exceptions, and `KeyboardInterrupt`.

- Windows: `ctypes` enables virtual-terminal output; `msvcrt` reads keys
- POSIX: `termios`/`tty` enable cbreak input; `select` provides timeouts
- both: alternate screen, cursor hide/show, ANSI complete-frame output, and
  `shutil.get_terminal_size()` resize checks

The default TUI supports Vim counts and page motions (`5j`, `g`, `GG`),
horizontal column viewports, filtering, and 1-based column reordering. `o`
opens the Options menu. Free Vim temporarily restores the real terminal
and opens a read-only text snapshot, then resumes the TUI when Vim exits.

### Options menu

Each numbered row cycles its own setting independently: pressing the same
digit repeatedly advances through that row's choices (wrapping around), and
the menu stays open so you can keep cycling. The active choice is shown in
`[brackets]`.

```
VIME Options
======
1  table mode:      [default]  vim
2  table borders:   [default]  none
3  plot charset:    [braille]  ascii  simple  extended
```

`settings.cfg` persists these under `[ui] default_mode` (`default`/`free_vim`),
`[ui] table_borders` (`default`/`none`), and `[plot] charset`
(`braille`/`ascii`/`simple`/`extended`). Plot charset used to be
auto-detected from the terminal's output encoding; it is now only ever set
via this menu (default `braille`).

## Tests

```bash
python -m unittest discover -s python -p "test_*.py"
```

The checks create temporary pandas table/fixed stores and generic h5py files.
They cover slice bounds, structured/scalar/1-D/2-D/higher-dimensional and
empty datasets, display-safe values, HTTP page bounds, explicit plotting,
session reuse, continuing compute state, key decoding, cleanup sequences, and
frame resizing.
