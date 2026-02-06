#!/usr/bin/env python3
"""
Convert MATLAB .mat files to HDF5 (.h5) with pandas-style tables.
Output is compatible with VIME and pd.read_hdf().
"""

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd
import scipy.io


def _sanitize_key(name: str) -> str:
    """Make a valid HDF5 / pandas key (no leading slash for pandas)."""
    # Remove or replace characters that cause issues
    s = re.sub(r"[^\w\-./]", "_", str(name))
    return s.strip("/") or "unnamed"


def _array_to_dataframe(arr: np.ndarray) -> pd.DataFrame:
    """Convert a numeric array to a DataFrame (1D or 2D)."""
    arr = np.atleast_2d(np.asarray(arr))
    if arr.size == 0:
        return pd.DataFrame()
    return pd.DataFrame(arr)


def _extract_mat_value(val, prefix: str, out_keys: list, store: pd.HDFStore):
    """Recursively extract values from loadmat result and write to HDFStore."""
    if isinstance(val, np.ndarray):
        if val.dtype.fields is not None:
            # Structured array (MATLAB struct array)
            for name in val.dtype.names or []:
                sub_key = f"{prefix}/{name}" if prefix else name
                _extract_mat_value(val[name], sub_key, out_keys, store)
            return
        if val.dtype == object:
            # Object array (e.g. cell or mixed)
            flat = val.ravel()
            for i, item in enumerate(flat):
                if item is None:
                    continue
                sub_key = f"{prefix}_{i}" if prefix else f"item_{i}"
                _extract_mat_value(item, sub_key, out_keys, store)
            return
        # Numeric array
        df = _array_to_dataframe(val)
        if df.size == 0:
            return
        key = f"/{_sanitize_key(prefix)}" if prefix else f"/{_sanitize_key('data')}"
        store.put(key, df, format="table")
        out_keys.append(key.lstrip("/"))
        return

    if isinstance(val, (np.floating, np.integer, np.bool_)):
        val = float(val) if isinstance(val, np.floating) else int(val)
    if isinstance(val, (int, float, bool)):
        df = pd.DataFrame([{"value": val}])
        key = f"/{_sanitize_key(prefix)}" if prefix else "/scalar"
        store.put(key, df, format="table")
        out_keys.append(key.lstrip("/"))
        return

    if hasattr(val, "_fieldnames"):
        # numpy record/void (struct element)
        for name in val._fieldnames:
            sub_key = f"{prefix}/{name}" if prefix else name
            _extract_mat_value(getattr(val, name), sub_key, out_keys, store)
        return

    if isinstance(val, (list, tuple)):
        arr = np.asarray(val)
        df = _array_to_dataframe(arr)
        if df.size > 0:
            key = f"/{_sanitize_key(prefix)}" if prefix else "/list"
            store.put(key, df, format="table")
            out_keys.append(key.lstrip("/"))


def mat_to_h5(mat_path: str, h5_path: str | None = None, flatten_structs: bool = True) -> str:
    """
    Convert a .mat file to .h5 (pandas HDFStore, format='table').

    Args:
        mat_path: Path to the .mat file.
        h5_path: Output .h5 path. Default: same name with .h5 extension.
        flatten_structs: If True, nested struct fields get keys like "parent/child".

    Returns:
        Path to the written .h5 file.
    """
    if not os.path.isfile(mat_path):
        raise FileNotFoundError(mat_path)

    if h5_path is None:
        h5_path = os.path.splitext(mat_path)[0] + ".h5"

    data = scipy.io.loadmat(
        mat_path,
        struct_as_record=False,
        squeeze_me=False,
        mat_dtype=True,
    )

    skip = {"__header__", "__version__", "__globals__"}
    written_keys = []

    with pd.HDFStore(h5_path, mode="w", complevel=4) as store:
        for key in data:
            if key in skip:
                continue
            val = data[key]
            _extract_mat_value(val, _sanitize_key(key), written_keys, store)

    return h5_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert MATLAB .mat files to HDF5 (.h5) with pandas tables (VIME-compatible)."
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="Path(s) to .mat file(s)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output .h5 path (only valid for single input; default: input name with .h5)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress per-file messages",
    )
    args = parser.parse_args()

    if args.output and len(args.input) > 1:
        print("error: -o/--output is only valid for a single input file", file=sys.stderr)
        sys.exit(1)

    for mat_path in args.input:
        try:
            out_path = args.output if len(args.input) == 1 else None
            h5_path = mat_to_h5(mat_path, h5_path=out_path)
            if not args.quiet:
                print(h5_path)
        except Exception as e:
            print(f"error: {mat_path}: {e}", file=sys.stderr)
            sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
