#!/usr/bin/env python3
"""
Create an HDF5 file with one table per CSV file.
Table names are derived from CSV filenames (without extension).
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


def csv_to_h5(csv_paths, h5_path, directory=None):
    """
    Write one HDF5 table per CSV file. Filename (stem) = table name.

    Args:
        csv_paths: List of paths to CSV files (or basenames if directory is set).
        h5_path: Path of the output .h5 file.
        directory: If set, csv_paths are joined with this directory.
    """
    if directory is not None:
        directory = Path(directory)
        resolved = [directory / Path(p).name for p in csv_paths]
    else:
        resolved = [Path(p) for p in csv_paths]

    missing = [p for p in resolved if not p.exists()]
    if missing:
        print("Missing files:", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    h5_path = Path(h5_path)
    with pd.HDFStore(str(h5_path), mode="w") as store:
        for path in resolved:
            name = path.stem
            df = pd.read_csv(path)
            store.put(f"/{name}", df, format="table")
            print(f"  {name}: {len(df)} rows")

    print(f"Wrote {len(resolved)} tables to {h5_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Create an H5 file with one table per CSV; filenames become table names."
    )
    ap.add_argument(
        "output",
        help="Output .h5 file path",
    )
    ap.add_argument(
        "csv_files",
        nargs="+",
        help="CSV file paths, or a single directory to use all .csv files in it",
    )
    ap.add_argument(
        "-d",
        "--dir",
        metavar="DIR",
        default=None,
        help="Directory containing the CSV files (csv_files are basenames)",
    )
    args = ap.parse_args()

    # If a single argument is an existing directory, use all .csv files in it
    if len(args.csv_files) == 1:
        p = Path(args.csv_files[0])
        if p.is_dir():
            csv_paths = sorted(p.glob("*.csv"))
            if not csv_paths:
                print(f"No .csv files in {p}", file=sys.stderr)
                sys.exit(1)
            csv_to_h5([str(f) for f in csv_paths], args.output, directory=None)
            return

    csv_to_h5(args.csv_files, args.output, directory=args.dir)


if __name__ == "__main__":
    main()
