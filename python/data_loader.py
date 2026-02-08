#!/usr/bin/env python3
"""
Data loading utilities for VIME.

Handles opening HDF5 files via pandas or h5py, listing datasets,
and converting datasets into pandas DataFrames.
"""

import sys
import logging
import pandas as pd
import h5py


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
        logger.info("Opening HDF5 file: %s", filepath)
        self.close()
        self.filepath = filepath

        # Try pandas HDFStore first (works for pandas-formatted H5 files)
        pandas_ok = False
        try:
            store = pd.HDFStore(filepath, mode="r")
            keys = store.keys()
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
        logger.info("Listing tables (backend=%s)", self.backend)
        if self.backend == "h5py":
            return self._get_table_list_h5py()
        if self.backend == "pandas":
            return self._get_table_list_pandas()
        return []

    def load_table(self, name):
        """Load a table/dataset as a DataFrame from either backend."""
        logger.info("Loading table: %s (backend=%s)", name, self.backend)
        if self.backend == "pandas":
            if name not in self.store:
                logger.warning("Table not found in pandas store: %s", name)
                return None
            return self.store[name]
        if self.backend == "h5py":
            return self._h5py_read_dataset(name)
        return None

    def load_table_fast(self, name):
        """Load a table/dataset as raw data, skipping pandas DataFrames."""
        logger.info("Fast loading table: %s (backend=%s)", name, self.backend)
        if self.backend == "pandas":
            if name not in self.store:
                logger.warning("Table not found in pandas store: %s", name)
                return None
            return self._pandas_read_table_fast(name)
        if self.backend == "h5py":
            return self._h5py_read_dataset_raw(name)
        return None

    def _get_table_list_pandas(self):
        """Return table metadata using the pandas HDFStore backend."""
        tables = []
        for key in self.store.keys():
            try:
                storer = self.store.get_storer(key)
                nrows = int(storer.nrows) if hasattr(storer, "nrows") else "?"
                if hasattr(storer, "ncols"):
                    ncols = int(storer.ncols)
                elif hasattr(storer, "attrs") and hasattr(storer.attrs, "non_index_axes"):
                    axes = storer.attrs.non_index_axes
                    ncols = int(len(axes[0][1])) if axes else "?"
                else:
                    ncols = "?"
            except Exception as exc:
                sys.stderr.write(f"VIME: Warning - could not get metadata for {key}: {exc}\n")
                logger.warning("Failed to read metadata for %s", key)
                nrows = "?"
                ncols = "?"
            tables.append({"name": key, "rows": nrows, "cols": ncols})
        logger.info("Collected %d pandas tables", len(tables))
        return tables

    def _get_table_list_h5py(self):
        """Return dataset metadata using the h5py fallback backend."""
        datasets = []

        def _visitor(name, obj):
            if isinstance(obj, h5py.Dataset):
                shape = obj.shape
                nrows = int(shape[0]) if len(shape) >= 1 else 1
                ncols = int(shape[1]) if len(shape) >= 2 else 1
                datasets.append({"name": "/" + name, "rows": nrows, "cols": ncols})

        self.h5file.visititems(_visitor)
        logger.info("Collected %d h5py datasets", len(datasets))
        return datasets

    def _pandas_read_table_fast(self, name):
        """Read table data from pandas-backed HDF5 without DataFrames."""
        try:
            storer = self.store.get_storer(name)
        except Exception as exc:
            logger.warning("Failed to get pandas storer for %s: %s", name, exc)
            return None

        table = getattr(storer, "table", None)
        if table is not None:
            try:
                return table.read()
            except Exception as exc:
                logger.warning("Failed to read PyTables table for %s: %s", name, exc)
                return None

        if hasattr(storer, "_read_array"):
            try:
                return storer._read_array()
            except Exception as exc:
                logger.warning("Failed to read array for %s: %s", name, exc)
                return None

        logger.warning("Fast read not supported for pandas storer: %s", name)
        return None

    def _h5py_read_dataset(self, name):
        """Read an h5py dataset and return it as a DataFrame."""
        # Strip leading slash for h5py lookup
        key = name.lstrip("/")
        if key not in self.h5file:
            logger.warning("Dataset not found in h5py file: %s", name)
            return None
        ds = self.h5file[key]
        if not isinstance(ds, h5py.Dataset):
            logger.warning("H5 object is not a dataset: %s", name)
            return None

        arr = ds[()]
        logger.info("Read dataset %s with shape %s", name, getattr(arr, "shape", "scalar"))

        # Handle structured arrays (compound dtypes, e.g. from MATLAB)
        if arr.dtype.names is not None:
            return pd.DataFrame({col: arr[col] for col in arr.dtype.names})

        # Scalar
        if arr.ndim == 0:
            return pd.DataFrame({"value": [arr.item()]})

        # 1-D array
        if arr.ndim == 1:
            return pd.DataFrame({0: arr})

        # 2-D array
        if arr.ndim == 2:
            return pd.DataFrame(arr)

        # Higher-dimensional: flatten trailing dims
        reshaped = arr.reshape(arr.shape[0], -1)
        return pd.DataFrame(reshaped)

    def _h5py_read_dataset_raw(self, name):
        """Read an h5py dataset and return raw data without DataFrames."""
        key = name.lstrip("/")
        if key not in self.h5file:
            logger.warning("Dataset not found in h5py file: %s", name)
            return None
        ds = self.h5file[key]
        if not isinstance(ds, h5py.Dataset):
            logger.warning("H5 object is not a dataset: %s", name)
            return None
        return ds[()]
