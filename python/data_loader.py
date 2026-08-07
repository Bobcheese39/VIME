#!/usr/bin/env python3
"""
Data loading utilities for VIME.

Handles opening HDF5 files via pandas or h5py, listing datasets,
and converting datasets into pandas DataFrames.
"""

import sys
import logging


logger = logging.getLogger("vime.data_loader")


class DataLoader:
    """Load and list HDF5 tables using pandas or h5py backends."""

    def __init__(self):
        self.store = None          # pd.HDFStore object (pandas backend)
        self.h5file = None         # h5py.File object (h5py fallback backend)
        self.backend = None        # "pandas" or "h5py"
        self.filepath = None       # Path to the currently open file

    @property
    def is_open(self):
        return self.backend is not None

    def close(self):
        """Close any open file handles."""
        logger.info("Closing data handles")
        if self.store is not None:
            try:
                self.store.close()
            except Exception as exc:
                sys.stderr.write(f"VIME: Warning - failed to close pandas store: {exc}\n")
            self.store = None
        if self.h5file is not None:
            try:
                self.h5file.close()
            except Exception as exc:
                sys.stderr.write(f"VIME: Warning - failed to close h5py file: {exc}\n")
            self.h5file = None
        self.backend = None
        self.filepath = None

    def open(self, filepath):
        """Open an HDF5 file and return the list of tables."""
        import pandas as pd
        import h5py

        logger.info("Opening HDF5 file: %s", filepath)
        self.close()
        self.filepath = filepath

        # Try pandas HDFStore first (works for pandas-formatted H5 files)
        pandas_ok = False
        try:
            store = pd.HDFStore(filepath, mode="r")
            # Recent pandas/PyTables combinations can expose a synthetic "/"
            # key for mixed fixed/table stores; it is not a readable storer.
            keys = [key for key in store.keys() if key != "/"]
            if keys:
                # Successfully opened with pandas and has tables
                self.store = store
                self.backend = "pandas"
                pandas_ok = True
                logger.info("Opened with pandas backend (%d tables)", len(keys))
            else:
                # No tables found, close and try h5py
                store.close()
                logger.info("No pandas tables found, falling back to h5py")
        except Exception as exc:
            # Not a pandas HDF5 file or other error, will try h5py fallback
            sys.stderr.write(f"VIME: pandas HDFStore failed, trying h5py fallback: {exc}\n")
            logger.warning("Pandas HDFStore failed, falling back to h5py")

        # Fall back to h5py for non-pandas HDF5 files
        if not pandas_ok:
            try:
                self.h5file = h5py.File(filepath, "r")
                self.backend = "h5py"
                logger.info("Opened with h5py backend")
            except Exception as exc:
                self.h5file = None
                logger.exception("Failed to open HDF5 with h5py")
                raise RuntimeError(f"Failed to open HDF5: {exc}") from exc

        return self.list_tables()

    def list_tables(self):
        """Return a list of dicts with table metadata."""
        logger.debug("Listing tables (backend=%s)", self.backend)
        if self.backend == "h5py":
            return self._get_table_list_h5py()
        if self.backend == "pandas":
            return self._get_table_list_pandas()
        return []

    def load_table(self, name, columns=None):
        """Load a table/dataset as a DataFrame from either backend."""
        logger.debug("Loading table: %s (backend=%s)", name, self.backend)
        if self.backend == "pandas":
            if name not in self.store:
                logger.warning("Table not found in pandas store: %s", name)
                return None
            storer = self.store.get_storer(name)
            if columns and getattr(storer, "is_table", False):
                return self.store.select(name, columns=columns)
            return self._select_columns(self.store[name], columns)
        if self.backend == "h5py":
            return self._h5py_read_dataset(name, columns=columns)
        return None

    def load_table_slice(self, name, start, stop, columns=None):
        """Load rows ``start:stop`` without reading the complete dataset.

        Pandas fixed-format stores cannot be sliced by PyTables, so they use
        the unavoidable full-load fallback before applying ``iloc``.
        """
        start = max(0, int(start))
        stop = max(start, int(stop))
        logger.debug(
            "Loading table slice: %s[%d:%d] (backend=%s)",
            name, start, stop, self.backend,
        )

        if self.backend == "pandas":
            if name not in self.store:
                return None
            storer = self.store.get_storer(name)
            if getattr(storer, "is_table", False):
                df = self.store.select(
                    name, start=start, stop=stop, columns=columns or None
                )
                return df
            else:
                # ponytail: fixed stores have no partial-read API; callers
                # should convert large fixed stores to table format.
                df = self.store[name].iloc[start:stop]
            return self._select_columns(df, columns)

        if self.backend == "h5py":
            return self._h5py_read_dataset(name, start=start, stop=stop, columns=columns)
        return None

    @staticmethod
    def _select_columns(df, columns):
        """Select columns by their display names while preserving order."""
        if not columns:
            return df
        by_name = {str(column): column for column in df.columns}
        selected = [by_name[str(column)] for column in columns if str(column) in by_name]
        return df.loc[:, selected]

    def _get_table_list_pandas(self):
        """Return table metadata using the pandas HDFStore backend."""
        tables = []
        for key in self.store.keys():
            if key == "/":
                continue
            try:
                storer = self.store.get_storer(key)
                shape = getattr(storer, "shape", None)
                raw_nrows = getattr(storer, "nrows", None)
                nrows = int(raw_nrows if raw_nrows is not None else shape[0])
                if hasattr(storer, "ncols"):
                    raw_ncols = storer.ncols
                    ncols = int(raw_ncols if raw_ncols is not None else shape[1])
                elif hasattr(storer, "attrs") and hasattr(storer.attrs, "non_index_axes"):
                    axes = storer.attrs.non_index_axes
                    ncols = int(len(axes[0][1])) if axes else "?"
                elif shape is not None and len(shape) > 1:
                    ncols = int(shape[1])
                else:
                    ncols = "?"
            except Exception as exc:
                sys.stderr.write(f"VIME: Warning - could not get metadata for {key}: {exc}\n")
                logger.warning("Failed to read metadata for %s", key)
                nrows = "?"
                ncols = "?"
            tables.append({"name": key, "rows": nrows, "cols": ncols})
        logger.debug("Collected %d pandas tables", len(tables))
        return tables

    def _get_table_list_h5py(self):
        """Return dataset metadata using the h5py fallback backend."""
        import h5py

        datasets = []

        def _visitor(name, obj):
            if isinstance(obj, h5py.Dataset):
                shape = obj.shape
                nrows = int(shape[0]) if len(shape) >= 1 else 1
                ncols = int(shape[1]) if len(shape) >= 2 else 1
                datasets.append({"name": "/" + name, "rows": nrows, "cols": ncols})

        self.h5file.visititems(_visitor)
        logger.debug("Collected %d h5py datasets", len(datasets))
        return datasets

    def _h5py_read_dataset(self, name, start=None, stop=None, columns=None):
        """Read an h5py dataset and return it as a DataFrame."""
        import pandas as pd
        import h5py
        import numpy as np

        # Strip leading slash for h5py lookup
        key = name.lstrip("/")
        if key not in self.h5file:
            logger.warning("Dataset not found in h5py file: %s", name)
            return None
        ds = self.h5file[key]
        if not isinstance(ds, h5py.Dataset):
            logger.warning("H5 object is not a dataset: %s", name)
            return None

        if ds.ndim == 0:
            arr = ds[()]
        elif start is None:
            arr = ds[()]
        else:
            arr = ds[start:stop]
        logger.debug("Read dataset %s with shape %s", name, getattr(arr, "shape", "scalar"))

        # Handle structured arrays (compound dtypes, e.g. from MATLAB)
        if arr.dtype.names is not None:
            df = pd.DataFrame({col: arr[col] for col in arr.dtype.names})
            return self._select_columns(df, columns)

        # Scalar
        if arr.ndim == 0:
            df = pd.DataFrame({"value": [arr.item()]})
            if start is not None and start > 0:
                df = df.iloc[0:0]
            return self._select_columns(df, columns)

        # 1-D array
        if arr.ndim == 1:
            return self._select_columns(pd.DataFrame({0: arr}), columns)

        # 2-D array
        if arr.ndim == 2:
            return self._select_columns(pd.DataFrame(arr), columns)

        # Higher-dimensional: flatten trailing dims
        trailing = int(np.prod(arr.shape[1:]))
        reshaped = arr.reshape((arr.shape[0], trailing))
        return self._select_columns(pd.DataFrame(reshaped), columns)

