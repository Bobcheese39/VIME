# VIME Python Backend

Persistent HTTP server that loads HDF5 files into memory and serves table data, metadata, and plots to the Vim frontend. Built on Python's `http.server` with a threaded handler, requiring no external web framework.

## Architecture

```mermaid
flowchart TD
    Launcher["scripts/vime_launcher.py<br>(starts server + Vim)"]
    Launcher -->|subprocess| Server

    Server[vime_server.py] --> HTTPLayer["server/http.py<br>VimeHTTPServer"]
    Server --> StateObj["server/state.py<br>ServerState"]

    HTTPLayer -->|"routes POST /{cmd}"| Dispatch["server/app.py<br>dispatch()"]

    Dispatch --> CmdOpen["commands/open.py"]
    Dispatch --> CmdTable["commands/table.py"]
    Dispatch --> CmdPlot["commands/plot.py"]
    Dispatch --> CmdInfo["commands/info.py"]
    Dispatch --> CmdCompute["commands/compute.py"]

    CmdOpen --> Loader["data_loader.py<br>DataLoader"]
    CmdTable --> Loader
    CmdTable --> Formatters["server/formatters.py"]
    CmdInfo --> Loader
    CmdPlot --> PlotEngine["plotter.py<br>BrailleCanvas"]
    CmdCompute --> TestCompute["test_compute.py"]

    StateObj --> Loader
    StateObj --> ConfigMgr["config.py<br>Config"]
```

## Data Flow

```mermaid
sequenceDiagram
    participant Vim
    participant HTTP as HTTP Server
    participant Dispatch as app.dispatch
    participant State as ServerState
    participant Loader as DataLoader
    participant HDF5 as HDF5 File

    Vim->>HTTP: POST /open {file}
    HTTP->>Dispatch: dispatch(payload)
    Dispatch->>State: close_handles()
    Dispatch->>Loader: open(filepath)
    Loader->>HDF5: pd.HDFStore or h5py.File
    Loader-->>Dispatch: table list
    Dispatch-->>Vim: {ok, tables}

    Vim->>HTTP: POST /table {name, head}
    HTTP->>Dispatch: dispatch(payload)
    Dispatch->>State: load_table(name)
    State->>Loader: load_table(name)
    Loader->>HDF5: read DataFrame
    Dispatch-->>Vim: {ok, content, columns}

    Vim->>HTTP: POST /plot {cols, type, width, height}
    HTTP->>Dispatch: dispatch(payload)
    Dispatch->>State: current_df
    Dispatch-->>Vim: {ok, content}
```

## Class and Module Overview

### vime_server.py

Server entry point. Parses CLI arguments (`--host`, `--port`, `--port-retries`), creates a `ServerState`, builds the HTTP handler via `make_handler()`, binds the server with port fallback, and runs `serve_forever()`. Handles graceful shutdown on `KeyboardInterrupt`.

### ServerState (`server/state.py`)

Central state object passed to all command handlers. Holds:

| Attribute         | Type                      | Description                                        |
|-------------------|---------------------------|----------------------------------------------------|
| `loader`          | `DataLoader`              | HDF5 file reader                                   |
| `current_df`      | `DataFrame` or `None`     | Last-loaded table (used by plot)                   |
| `current_table`   | `str` or `None`           | Name of the last-loaded table                      |
| `virtual_tables`  | `dict`                    | Tables created by compute jobs                     |
| `compute_thread`  | `Thread` or `None`        | Background compute thread                          |
| `compute`         | `ComputeState`            | Current compute job status                         |
| `config`          | `Config` or `None`        | Column ordering configuration                      |

Key methods:
- `load_table(name)` -- loads from virtual tables first, then falls back to the file-backed loader.
- `get_table_list()` -- merges file-backed tables with virtual tables.
- `close_handles()` -- closes all open HDF5 file handles.

### ComputeState / ComputeStatus (`server/state.py`)

Tracks background compute job progress. `ComputeStatus` is an enum with values: `IDLE`, `RUNNING`, `DONE`, `ERROR`. `ComputeState` is a dataclass holding the status, a message, an optional table name (on success), and an optional error string (on failure).

### DataLoader (`data_loader.py`)

Reads HDF5 files using a dual-backend strategy:

1. **Pandas HDFStore** (preferred) -- tried first. Works for files created with `pd.to_hdf()`. Provides column names, dtypes, and indexes natively.
2. **h5py fallback** -- used when the pandas backend finds no tables or fails. Handles generic HDF5 datasets by converting structured arrays, scalars, 1-D, 2-D, and higher-dimensional arrays into DataFrames.

Key methods:

| Method              | Description                                              |
|---------------------|----------------------------------------------------------|
| `open(filepath)`    | Opens the file, selects a backend, returns table list    |
| `close()`           | Closes all file handles                                  |
| `list_tables()`     | Returns list of `{name, rows, cols}` dicts               |
| `load_table(name)`  | Reads a table as a pandas DataFrame                      |
| `load_table_fast(name)` | Reads raw data (skipping DataFrame construction)     |

### Config (`config.py`)

Manages per-table column ordering stored in `config.json` at the project root. When a table is loaded, `merge_table_columns()` preserves any user-defined column order and appends newly discovered columns. Changes are persisted atomically (write to `.tmp`, then `os.replace`).

### BrailleCanvas / braille_plot (`plotter.py`)

Unicode braille plotting engine. Each character cell encodes a 2x4 sub-pixel grid using braille characters (U+2800--U+28FF), giving 2x horizontal and 4x vertical resolution compared to regular character plots.

`BrailleCanvas` provides:
- `set_pixel(px, py)` -- set a sub-pixel
- `line(x0, y0, x1, y1)` -- draw a line using Bresenham's algorithm
- `render()` -- return the canvas as a list of strings

`braille_plot(x, y, ...)` is the high-level function that:
1. Computes data bounds and margins
2. Maps data coordinates to sub-pixel coordinates
3. Draws points (scatter) or connected line segments (line, sorted by x)
4. Adds y-axis labels, x-axis ticks, and axis labels

### VimeHTTPServer / VimeHandler (`server/http.py`)

Threaded HTTP server (`ThreadingHTTPServer` subclass) with strict port exclusivity (`allow_reuse_address = False`). The handler class is created dynamically by `make_handler(dispatch_fn, close_handles_fn)` to capture the dispatch and cleanup functions in a closure.

Routes:
- `GET /health` -- returns `{"ok": true}`
- `POST /shutdown` -- closes handles, responds, then shuts down the server in a daemon thread
- `POST /{cmd}` -- extracts the command from the URL path, parses the JSON body, injects `cmd` into the payload, and calls `dispatch_fn`

`bind_http_server(host, start_port, max_attempts, handler_cls)` tries binding to sequential ports starting from `start_port`, incrementing on `EADDRINUSE` up to `max_attempts` times.

### dispatch (`server/app.py`)

Routes command names to handler functions:

| Command           | Handler                    |
|-------------------|----------------------------|
| `open`            | `commands.open.handle`     |
| `table`           | `commands.table.handle`    |
| `plot`            | `commands.plot.handle`     |
| `info`            | `commands.info.handle`     |
| `close`           | `_handle_close` (inline)   |
| `compute_start`   | `commands.compute.handle_start` |
| `compute_status`  | `commands.compute.handle_status` |

All handlers receive `(state, payload)` and return a dict. Exceptions are caught and returned as `{"ok": false, "error": "..."}`.

### NumpyEncoder / format_fast_table (`server/formatters.py`)

`NumpyEncoder` is a `json.JSONEncoder` subclass that transparently converts `np.integer`, `np.floating`, `np.bool_`, and `np.ndarray` to native Python types. Used for all JSON responses.

`format_fast_table(name, data)` renders raw table data (from `load_table_fast`) using `np.array2string` for a quick, unformatted dump.

### Command Handlers (`server/commands/`)

#### open.py

Validates the file path, closes existing handles, calls `state.loader.open(filepath)`, and returns `{"ok": true, "tables": [...]}`.

#### table.py

Loads a table by name. In **normal mode**: loads as a DataFrame, applies column config ordering, formats with `tabulate` (plain format), and returns the formatted text with a header line showing shape info. In **fast mode**: reads raw data via `load_table_fast` and formats with `format_fast_table`.

#### plot.py

Generates a braille plot from the currently loaded DataFrame (`state.current_df`). Resolves column references (by integer index or string name), converts to float, removes NaN pairs, and calls `braille_plot()`. Returns the plot as a string.

#### info.py

Loads a table and returns metadata: table name, shape (`rows x cols`), column names with dtypes, and a numeric summary (`df.describe()`). Formatted with `tabulate`.

#### compute.py

Manages background compute jobs. `handle_start` checks that no job is already running, then launches `test_compute()` in a daemon thread. The thread stores its result as a virtual table in `state.virtual_tables` under a timestamped name (`/__computed__/compute_YYYYMMDD_HHMMSS`). `handle_status` returns the current `ComputeState` so the Vim plugin can poll for completion.

## HTTP API Reference

All command endpoints accept `POST` with a JSON body and return JSON. Every response includes an `ok` boolean field.

### GET /health

Health check. Returns `{"ok": true}` when the server is running.

### POST /shutdown

Closes all file handles and shuts down the server. Returns `{"ok": true}` before shutdown.

### POST /open

Open an HDF5 file and return its table list.

**Request:**
```json
{"file": "/path/to/data.h5"}
```

**Response:**
```json
{
  "ok": true,
  "tables": [
    {"name": "/experiment/results", "rows": 1000, "cols": 5},
    {"name": "/summary", "rows": 50, "cols": 3}
  ]
}
```

### POST /table

Load a table and return its formatted content.

**Request:**
```json
{"name": "/experiment/results", "head": 100, "fast": false}
```

| Field  | Type   | Default | Description                                |
|--------|--------|---------|--------------------------------------------|
| `name` | string | --      | Table path within the HDF5 file            |
| `head` | int    | `100`   | Maximum rows to return (`0` for all)       |
| `fast` | bool   | `false` | Use raw fast-path (skips DataFrame)        |

**Response:**
```json
{
  "ok": true,
  "content": "/experiment/results  [1000 rows x 5 cols]\n\ncol_a  col_b  ...",
  "columns": ["col_a", "col_b", "col_c"],
  "name": "/experiment/results"
}
```

### POST /plot

Generate a braille Unicode plot from the currently loaded table.

**Request:**
```json
{"cols": [0, 1], "type": "line", "width": 72, "height": 20}
```

| Field    | Type        | Default  | Description                              |
|----------|-------------|----------|------------------------------------------|
| `cols`   | list        | --       | Two column references (index or name)    |
| `type`   | string      | `"line"` | Plot type: `"line"` or `"scatter"`       |
| `width`  | int         | `72`     | Plot width in characters                 |
| `height` | int         | `20`     | Plot height in character rows            |

**Response:**
```json
{
  "ok": true,
  "content": "Plot: col_a vs col_b  (/experiment/results)\n\n..."
}
```

### POST /info

Return metadata for a table.

**Request:**
```json
{"name": "/experiment/results"}
```

**Response:**
```json
{
  "ok": true,
  "content": "Table: /experiment/results\nShape: 1000 rows x 5 cols\n..."
}
```

### POST /compute_start

Start a background compute job.

**Request:**
```json
{}
```

**Response:**
```json
{"ok": true, "status": "running", "message": "Computing..."}
```

### POST /compute_status

Poll the current compute job status.

**Response (running):**
```json
{"ok": true, "status": "running", "message": "Computing...", "table": null, "error": null}
```

**Response (done):**
```json
{"ok": true, "status": "done", "message": "Compute done: /__computed__/compute_20260213_120000", "table": "/__computed__/compute_20260213_120000", "error": null}
```

**Response (error):**
```json
{"ok": true, "status": "error", "message": "Compute failed", "table": null, "error": "..."}
```

## Launcher (`scripts/vime_launcher.py`)

Cross-platform launcher that orchestrates the server and Vim lifecycle:

1. **Check for existing server** -- scans the port range for a healthy server (`GET /health`).
2. **Start server** (if needed) -- spawns `vime_server.py` as a subprocess. On Windows, uses `CREATE_NEW_PROCESS_GROUP` to isolate it from Ctrl-C.
3. **Wait for health** -- polls until the server responds healthy or a timeout expires (default 30s).
4. **Launch Vim** -- runs `vim` with `--cmd` arguments to set `g:vime_http_host`, `g:vime_http_port`, and optionally `g:vime_owns_server`.
5. **Cleanup** -- after Vim exits, sends `POST /shutdown` and terminates the server process if it was started by the wrapper.

### Environment Variables

| Variable                       | Default      | Description                              |
|--------------------------------|--------------|------------------------------------------|
| `VIME_PYTHON`                  | `sys.executable` | Python interpreter to run the server |
| `VIME_HTTP_HOST`               | `127.0.0.1`  | Host address for the server              |
| `VIME_HTTP_PORT`               | `51789`      | Starting port number                     |
| `VIME_HTTP_PORT_RETRIES`       | `100`        | Number of sequential ports to try        |
| `VIME_SERVER_STARTUP_TIMEOUT`  | `30`         | Seconds to wait for server health        |

## Running the Server Standalone

The server can be started independently of the wrapper for development or testing:

```bash
cd python
python vime_server.py --host 127.0.0.1 --port 51789
```

Then connect Vim manually:

```vim
let g:vime_http_host = '127.0.0.1'
let g:vime_http_port = 51789
:VimeOpen /path/to/data.h5
```
