"""Table command handler."""

import logging
import numbers
import operator

from data_loader import DataLoader

logger = logging.getLogger("vime")
MAX_PAGE_SIZE = 1000


def handle_page(state, payload):
    """Return one display-safe page without loading the complete table."""
    session = state.session_for(payload)
    if session is None:
        return {"ok": False, "error": "No file open"}

    try:
        session.ensure_open()
        offset = max(0, int(payload.get("offset", 0)))
        limit = min(MAX_PAGE_SIZE, max(1, int(payload.get("limit", 100))))
    except (TypeError, ValueError):
        return {"ok": False, "error": "offset and limit must be integers"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    name = payload.get("dataset", payload.get("name", ""))
    requested = payload.get("columns")
    if requested is not None and not isinstance(requested, list):
        return {"ok": False, "error": "columns must be a list"}
    columns = _page_columns(session, name, requested)

    filter_spec = payload.get("filter")
    if filter_spec is not None:
        try:
            filtered = _filtered_table(session, name, filter_spec)
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"Invalid filter: {exc}"}
        if filtered is None:
            return {"ok": False, "error": f"Table not found: {name}"}
        total_rows = len(filtered)
        df = DataLoader._select_columns(filtered.iloc[offset:offset + limit], columns)
    else:
        df = session.load_table_slice(name, offset, offset + limit, columns)
        if df is None:
            return {"ok": False, "error": f"Table not found: {name}"}
        metadata = next(
            (item for item in session.get_table_list() if item["name"] == name),
            None,
        )
        total_rows = metadata["rows"] if metadata is not None else offset + len(df)
        if not isinstance(total_rows, int):
            total_rows = offset + len(df)

    if requested is None:
        df = _apply_column_config(session, name, df)

    float_formatting = payload.get("float_formatting") is True
    path_formatting = payload.get("path_formatting") is True
    return {
        "ok": True,
        "dataset": name,
        "offset": offset,
        "limit": limit,
        "total_rows": total_rows,
        "columns": [str(column) for column in df.columns],
        "rows": [
            [
                _display_cell(value, float_formatting, path_formatting)
                for value in row
            ]
            for row in df.itertuples(index=False, name=None)
        ],
    }


FILTER_OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}


def _filtered_table(session, table_name, spec):
    """Return a validated filtered frame, reusing one bounded cache entry."""
    if not isinstance(spec, dict):
        raise TypeError("filter must be an object")
    left_ref = spec.get("left")
    operation = spec.get("operator")
    right = spec.get("right")
    if operation not in FILTER_OPERATORS:
        raise ValueError("unsupported operator")
    if not isinstance(right, dict) or right.get("kind") not in ("number", "column"):
        raise TypeError("right side must be a number or column")

    right_kind = right["kind"]
    right_value = right.get("value")
    if right_kind == "number" and (
        isinstance(right_value, bool) or not isinstance(right_value, (int, float))
    ):
        raise TypeError("numeric value must be an integer or float")

    key = (table_name, str(left_ref), operation, right_kind, repr(right_value))
    if session.filtered_cache is not None and session.filtered_cache[0] == key:
        return session.filtered_cache[1]

    frame = session.load_table(table_name)
    if frame is None:
        return None
    left = _resolve_column(frame, left_ref)
    rhs = right_value if right_kind == "number" else frame[_resolve_column(frame, right_value)]
    try:
        mask = FILTER_OPERATORS[operation](frame[left], rhs)
        result = frame.loc[mask]
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc

    # ponytail: arbitrary HDF5 filtering needs a full scan; one cached result
    # keeps paging fast and bounded. Add backend chunk/query paths if profiling
    # shows datasets too large for one filtered DataFrame.
    session.filtered_cache = (key, result)
    return result


def _resolve_column(frame, reference):
    by_name = {str(column): column for column in frame.columns}
    try:
        return by_name[str(reference)]
    except KeyError:
        raise KeyError("unknown column {!r}".format(reference))


def _display_cell(value, float_formatting=False, path_formatting=False):
    """Convert a scalar to a stable, JSON-safe terminal string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if (
        float_formatting
        and isinstance(value, numbers.Real)
        and not isinstance(value, numbers.Integral)
    ):
        return "{:.2f}".format(value)
    text = str(value)
    return text.rsplit("/", 1)[-1] if path_formatting else text


def _page_columns(session, table_name, requested):
    """Visible columns to load: strip hidden names, or use stored visible order."""
    if session.config is None:
        return requested
    hidden = set(session.config.get_hidden(table_name))
    if requested is not None:
        return [column for column in requested if str(column) not in hidden]
    visible = session.config.get_columns(table_name)
    if visible is None:
        return None
    return [column for column in visible if column not in hidden]


def _apply_column_config(session, table_name, df):
    """Apply configured column order/visibility for a table."""
    if session.config is None:
        return df

    discovered = [str(col) for col in df.columns]
    try:
        configured = session.config.merge_table_columns(table_name, discovered)
    except Exception as exc:
        logger.warning("Failed to sync table config for %s: %s", table_name, exc)
        return df

    col_map = {str(col): col for col in df.columns}
    ordered_actual = [col_map[col] for col in configured if col in col_map]
    if not ordered_actual:
        return df.iloc[:, 0:0] if not configured else df
    return df.loc[:, ordered_actual]
