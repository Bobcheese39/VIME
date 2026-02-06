import os
import sys
import argparse
from tabulate import tabulate
# import data_model as DataModel # custom library. Essentially a wrapper for h5py.
import h5py # for reading h5 files
import curses


def output_table(data): 
    if data.extractions.is_table_available(args.tablename):
        table = data.extractions[args.tablename]
        print(tabulate(table, headers=table.columns, stralign="left", numalign="left", showindex=False, tablefmt="grid"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--tablename', type=str)

    args = parser.parse_args()

    cwd = os.getcwd()
    h5_file = os.path.join(cwd, args.h5_file)
    data = h5py.File(h5_file, 'r')
    
    if args.tablename:
        output_table(data, args.tablename)