# VIME

A responsive terminal HDF5 viewer with a standard-library Python TUI and a
persistent Python HTTP daemon.

```text
vime data.h5
  ├─ starts or reuses the detached daemon
  └─ runs the ANSI terminal frontend
       └─ localhost HTTP/JSON
            ├─ persistent HDF5 handles and per-file sessions
            ├─ page-bounded table reads
            └─ background plot and compute jobs
```

Closing the TUI does not stop the daemon. Open handles, computed tables, and
running jobs remain available until the daemon's idle timeout expires.

## Requirements

- Python 3.7+
- `h5py`, `pandas`, `numpy`, `tabulate`, and `tables`
- An ANSI-capable terminal

VIME uses `msvcrt` and Windows virtual-terminal processing on Windows, and
`termios`/`select` on POSIX. Vim, curl, and curses are not required.

```bash
pip install -r requirements.txt
```

## Usage

Put `scripts/` on `PATH`, then open one or more files:

```bash
vime data.h5
vime first.h5 second.h5
```

The shell and PowerShell wrappers both delegate to
`scripts/vime_launcher.py`.

Keys:

- `j`/`k`: previous/next table page; counts work (`5j`)
- `g`/`GG`: first/last page; `5g` and `5GG` offset from that edge
- `h`/`l`: move one column
- Left/Right in a table: move one visible column viewport
- `l`/Right at the last column: cycle back to the first column
- Up/Down: move the selected row
- Enter in the dataset list: open the selected table full-window
- Right in the dataset list/sidebar: open or focus a table in a vertical split
- Left in the sidebar: close the selected table when it is open
- Left at the first table column: focus the sidebar
- Up at the first row of a split table: change that split to horizontal
- Tab: cycle focus through open table panes
- `b`: focus the sidebar in split view, or go back outside prompts
- Page Up/Page Down: change table page
- `f`: filter with `column operator value` (for example `f 2 >= 10`)
- `u`: clear the active filter
- `y`: move columns to the front (for example `y 2 3 1`, then Enter)
- `i`: dataset information; type a column number and Enter to hide or unhide it
- `p`: enter two plot columns and optional `line`/`scatter`
- `c`: start the demo background compute job
- `r`: refresh the current view
- `o`: open the numbered Default/Free Vim options
- Escape or Ctrl+B: cancel an active text prompt, discard a pending count, or
  go back one view (same as `b`, but usable inside prompts)
- `q`: quit the TUI (the daemon remains alive)

Column numbers are 1-based. The plot prompt accepts column numbers or names,
for example `1 3 line` or
`time value scatter`.

Filters support `<`, `<=`, `==`, `!=`, `>=`, and `>`. The right side may be
an integer, float, or another column name. Arbitrary HDF5 filters require one
full dataset scan; the daemon caches the active filtered result for fast page
navigation.

Free Vim opens a read-only temporary text snapshot of the current view in the
installed `vim`. Exiting Vim returns to the live TUI. Native Vim keys remain
available and VIME-owned Vim mappings use the `,` prefix (`,q` closes the
snapshot).

## Supported data

- pandas table-format HDF5: page reads use `HDFStore.select`
- pandas fixed-format HDF5: pandas requires a documented full-load fallback
- generic h5py datasets: first-axis hyperslab reads
- structured arrays, scalars, 1-D, 2-D, and higher-dimensional datasets

Higher-dimensional rows are flattened across trailing dimensions for display.
Table pages are capped at 1000 rows by the daemon and terminal cells are sent
as display-safe strings.

## Configuration

Environment variables:

- `VIME_HTTP_HOST` (default `127.0.0.1`)
- `VIME_HTTP_PORT` (default `51789`)
- `VIME_HTTP_PORT_RETRIES` (default `100`)
- `VIME_PYTHON` (default current Python executable)
- `VIME_SERVER_STARTUP_TIMEOUT` (default `30` seconds)
- `VIME_IDLE_TIMEOUT` (default `900` seconds; `0` disables shutdown)
- `VIME_MAX_SESSIONS` (default `16`, with LRU eviction)

The launcher scans the configured port range for an existing healthy daemon
before starting one. The TUI sends keepalive requests while active.

Column order and hidden columns are stored in `config.json`. Hidden columns
are skipped when a table is opened.

Startup UI settings are stored in `settings.cfg`:

```ini
[ui]
default_mode = default
table_borders = default

[plot]
charset = braille
```

Press `o` to open the Options menu; each numbered row cycles its own choices
on repeat presses (table mode: `default`/`vim`, table borders:
`default`/`none`, plot charset: `braille`/`ascii`/`simple`/`extended`), and
selections are saved immediately as the next startup default.

## Development

Run the checks:

```bash
python -m unittest discover -s python -p "test_*.py"
```

Run the server directly:

```bash
cd python
python vime_server.py --host 127.0.0.1 --port 51789 --idle-timeout 0
```

Run the TUI against it:

```bash
python vime_tui.py --host 127.0.0.1 --port 51789 data.h5
```

See [python/README.md](python/README.md) for the daemon API.
