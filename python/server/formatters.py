"""JSON encoding and table formatting utilities."""

import json
import numpy as np


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types transparently."""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def format_fast_table(name, data):
    """Return a fast, raw string representation of table data."""
    try:
        shape = getattr(data, "shape", None)
        shape_info = f"  {shape}" if shape is not None else ""
        size = int(np.size(data))
        arr = np.asarray(data)
        body = np.array2string(
            arr,
            threshold=size if size > 0 else 1,
            max_line_width=200,
        )
        return f"{name}{shape_info}  [fast]\n\n{body}"
    except Exception:
        return f"{name}  [fast]\n\n{str(data)}"
