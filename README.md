# VIME - Vim H5 File Viewer

A fast, lightweight HDF5 file viewer using Vim as the frontend and a persistent Python backend. Data is loaded once into memory, so browsing tables and plotting is instant.

## Requirements

- **Vim 8+** (for `job_start()` / `ch_evalexpr()` channel support)
- **Python 3.6+**
- Python packages: `h5py`, `pandas`, `numpy`, `tabulate`, `tables`

## Installation

1. Install the Python dependencies:

```bash
pip install -r requirements.txt
```

2. Add VIME to your Vim runtime path. Add this line to your `~/.vimrc`:

```vim
set runtimepath+=~/path/to/VIME
```

Replace `~/path/to/VIME` with the actual path to this directory.

3. Restart Vim or run `:source ~/.vimrc`.

## Usage

### Opening an H5 File

Simply open an `.h5` or `.hdf5` file with Vim:

```bash
vim data.h5
```

VIME intercepts the open command, starts the Python backend, and displays a list of all tables in the file.

You can also open a file from within Vim:

```vim
:VimeOpen /path/to/data.h5
```

### Table List View

When you open an H5 file, you see a list of all tables with their dimensions:

```
 VIME - data.h5
 ============================================================

 Tables:

   /experiment/results                      (1000 rows x 5 cols)
   /experiment/metadata                     (1 rows x 12 cols)
   /summary                                 (50 rows x 3 cols)
```

**Keybindings:**

| Key       | Action                              |
|-----------|-------------------------------------|
| `<Enter>` | Open the table under the cursor     |
| `,i`      | Show info (shape, dtypes, summary)  |
| `,r`      | Refresh the table list              |
| `,q`      | Quit VIME and stop the backend      |

### Table Content View

Shows the table data formatted as a grid (first 100 rows by default):

**Keybindings:**

| Key    | Action                                  |
|--------|-----------------------------------------|
| `,p`   | Plot prompt (enter column indices)      |
| `,b`   | Back to table list                      |
| `,h`   | Change row limit (head N)               |
| `,a`   | Show all rows                           |
| `,i`   | Show table info                         |
| `,q`   | Close buffer                            |

**Commands:**

```vim
:VimePlot 1 3          " Line plot of column 1 vs column 3
:VimePlot 0 2 scatter  " Scatter plot of column 0 vs column 2
:VimePlot time value   " Plot by column name
```

### Plot View

Displays an ASCII plot in a new buffer.

**Keybindings:**

| Key  | Action            |
|------|-------------------|
| `,b` | Back to table     |
| `,q` | Close plot        |

### Other Commands

```vim
:VimeInfo   " Show detailed info about the current table
```

## Architecture

```
 Vim (frontend)  <-- JSON channel -->  Python (backend)
   - keybindings                          - h5py / pandas
   - buffer mgmt                          - tabulate
   - display                              - ASCII plotter
```

The Python backend runs as a persistent subprocess managed by Vim's `job_start()`. Communication uses Vim's built-in JSON channel protocol over stdin/stdout. The H5 file is opened once and kept in memory, making subsequent operations fast.

## File Structure

```
VIME/
  plugin/
    vime.vim           # Vim plugin
  python/
    vime_server.py     # Python backend server
  requirements.txt     # Python dependencies
  README.md            # This file
```

## Supported H5 Formats

VIME is designed for Pandas HDFStore files (created with `pd.to_hdf()`). These store DataFrames with column names, types, and indexes.

## Troubleshooting

- **"Server script not found"**: Make sure the `runtimepath` in your `.vimrc` points to the VIME directory correctly.
- **"Failed to start server"**: Ensure `python3` is on your PATH and the dependencies are installed.
- **Large tables are slow**: Use `,h` to limit the number of rows displayed, or the default 100-row head.
- **Check server errors**: Any Python errors are reported via `:messages` in Vim.
