"""Runnable integration checks for paginated HDF5 loading."""

import json
import os
import tempfile
import threading
import unittest

import h5py
import numpy as np
import pandas as pd

from data_loader import DataLoader
from server.app import dispatch
from server.http import VimeHTTPServer, make_handler
from server.state import ServerState
from vime_tui import HttpClient


class DataLoaderSliceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def path(self, name):
        return os.path.join(self.tempdir.name, name)

    def test_pandas_table_and_fixed_slices(self):
        path = self.path("pandas.h5")
        frame = pd.DataFrame({
            "value": [0.0, np.nan, np.inf, -np.inf],
            "name": ["zero", "café", "三", "four"],
            "when": pd.date_range("2026-01-01", periods=4),
        })
        with pd.HDFStore(path, mode="w") as store:
            store.put("/table", frame, format="table")
            store.put("/fixed", frame, format="fixed")

        loader = DataLoader()
        loader.open(path)
        try:
            page = loader.load_table_slice("/table", 1, 3, ["name", "value"])
            self.assertEqual(list(page.columns), ["name", "value"])
            self.assertEqual(page["name"].tolist(), ["café", "三"])
            fixed = loader.load_table_slice("/fixed", 2, 4)
            self.assertEqual(len(fixed), 2)
            metadata = {item["name"]: item for item in loader.list_tables()}
            self.assertEqual(metadata["/fixed"]["rows"], 4)
            self.assertEqual(metadata["/table"]["cols"], 3)
        finally:
            loader.close()

    def test_h5py_shapes_and_structured_values(self):
        path = self.path("generic.h5")
        structured = np.array(
            [(b"one", 1.5), (b"\xff", np.inf)],
            dtype=[("label", "S8"), ("value", "f8")],
        )
        with h5py.File(path, "w") as handle:
            handle.create_dataset("scalar", data=np.array(7))
            handle.create_dataset("one", data=np.arange(5))
            handle.create_dataset("two", data=np.arange(12).reshape(4, 3))
            handle.create_dataset("higher", data=np.arange(24).reshape(4, 2, 3))
            handle.create_dataset("structured", data=structured)
            handle.create_dataset("empty", shape=(0, 3), dtype="f8")
            handle.create_dataset(
                "unicode", data=np.array(["café", "三"], dtype=h5py.string_dtype())
            )

        loader = DataLoader()
        loader.open(path)
        try:
            self.assertEqual(loader.load_table_slice("/scalar", 0, 1).shape, (1, 1))
            self.assertEqual(loader.load_table_slice("/scalar", 1, 2).shape, (0, 1))
            self.assertEqual(loader.load_table_slice("/one", 1, 4).shape, (3, 1))
            self.assertEqual(loader.load_table_slice("/two", 1, 3).shape, (2, 3))
            self.assertEqual(loader.load_table_slice("/higher", 0, 2).shape, (2, 6))
            self.assertEqual(loader.load_table_slice("/empty", 0, 10).shape, (0, 3))
            labels = loader.load_table_slice("/structured", 0, 2, ["label"])
            self.assertEqual(list(labels.columns), ["label"])
        finally:
            loader.close()

    def test_table_page_is_bounded_and_json_safe(self):
        path = self.path("page.h5")
        structured = np.array(
            [(b"alpha", 1.0), (b"\xff", np.nan), (b"omega", np.inf)],
            dtype=[("label", "S8"), ("value", "f8")],
        )
        with h5py.File(path, "w") as handle:
            handle.create_dataset("rows", data=structured)
            handle.create_dataset("xy", data=np.arange(20).reshape(10, 2))

        state = ServerState()
        state.config = None
        self.assertTrue(dispatch(state, {"cmd": "open", "file": path})["ok"])
        response = dispatch(state, {
            "cmd": "table_page",
            "file": path,
            "dataset": "/rows",
            "offset": 1,
            "limit": 1,
        })
        self.assertTrue(response["ok"])
        self.assertEqual(response["total_rows"], 3)
        self.assertEqual(len(response["rows"]), 1)
        self.assertTrue(all(isinstance(cell, str) for cell in response["rows"][0]))
        json.dumps(response, allow_nan=False)

        filtered = dispatch(state, {
            "cmd": "table_page",
            "file": path,
            "dataset": "/xy",
            "offset": 1,
            "limit": 2,
            "columns": ["1", "0"],
            "filter": {
                "left": "0",
                "operator": ">=",
                "right": {"kind": "number", "value": 10},
            },
        })
        self.assertTrue(filtered["ok"])
        self.assertEqual(filtered["total_rows"], 5)
        self.assertEqual(filtered["columns"], ["1", "0"])
        self.assertEqual(filtered["rows"][0], ["13", "12"])

        compared = dispatch(state, {
            "cmd": "table_page",
            "file": path,
            "dataset": "/xy",
            "filter": {
                "left": "1",
                "operator": ">",
                "right": {"kind": "column", "value": "0"},
            },
        })
        self.assertTrue(compared["ok"])
        self.assertEqual(compared["total_rows"], 10)

        invalid = dispatch(state, {
            "cmd": "table_page",
            "file": path,
            "dataset": "/xy",
            "filter": {
                "left": "0",
                "operator": "in",
                "right": {"kind": "number", "value": 1},
            },
        })
        self.assertFalse(invalid["ok"])

        plot_start = dispatch(state, {
            "cmd": "plot_start",
            "file": path,
            "dataset": "/xy",
            "columns": ["0", "1"],
            "width": 30,
            "height": 10,
            "charset": "ascii",
            "type": "scatter",
        })
        self.assertTrue(plot_start["ok"])
        session = state.get_session(path)
        session.plot_thread.join(timeout=5)
        plot_status = dispatch(state, {"cmd": "plot_status", "file": path})
        self.assertEqual(plot_status["status"], "done")
        self.assertIn("Plot:", plot_status["content"])
        self.assertIn("ascii", plot_status["content"])

        bad_type = dispatch(state, {
            "cmd": "plot_start",
            "file": path,
            "dataset": "/xy",
            "columns": ["0", "1"],
            "type": "nope",
        })
        self.assertTrue(bad_type["ok"])
        session.plot_thread.join(timeout=5)
        bad_status = dispatch(state, {"cmd": "plot_status", "file": path})
        self.assertEqual(bad_status["status"], "error")
        self.assertIn("Unknown plot type", bad_status["error"])

        hist_start = dispatch(state, {
            "cmd": "plot_start",
            "file": path,
            "dataset": "/xy",
            "columns": ["1"],
            "type": "hist",
            "charset": "ascii",
            "width": 30,
            "height": 10,
            "filter": {
                "left": "0",
                "operator": ">=",
                "right": {"kind": "number", "value": 0},
            },
        })
        self.assertTrue(hist_start["ok"])
        session.plot_thread.join(timeout=5)
        hist_status = dispatch(state, {"cmd": "plot_status", "file": path})
        self.assertEqual(hist_status["status"], "done")
        self.assertIn("hist", hist_status["content"])

        compute_start = dispatch(state, {"cmd": "compute_start", "file": path})
        self.assertTrue(compute_start["ok"])
        session.compute_thread.join(timeout=5)
        reopened = dispatch(state, {"cmd": "open", "file": path})
        self.assertTrue(reopened["ok"])
        self.assertIs(state.get_session(path), session)
        self.assertTrue(any(item["name"].startswith("/__computed__") for item in reopened["tables"]))
        state.close_handles()

    def test_http_json_page_round_trip(self):
        path = self.path("http.h5")
        with h5py.File(path, "w") as handle:
            handle.create_dataset("large", data=np.arange(2000).reshape(1000, 2))

        state = ServerState()
        state.config = None
        handler = make_handler(lambda payload: dispatch(state, payload), state.mark_activity)
        server = VimeHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = HttpClient("127.0.0.1", server.server_port)
            self.assertTrue(client.post("open", {"file": path})["ok"])
            page = client.post("table_page", {
                "file": path,
                "dataset": "/large",
                "offset": 500,
                "limit": 7,
            })
            self.assertEqual(page["total_rows"], 1000)
            self.assertEqual(len(page["rows"]), 7)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            state.close_handles()


if __name__ == "__main__":
    unittest.main()
