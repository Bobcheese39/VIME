"""Info command handler."""

import logging

logger = logging.getLogger("vime")


def handle(state, payload):
    """Return detailed info about a table."""
    session = state.session_for(payload)
    if session is None:
        logger.warning("Info requested with no file open")
        return {"ok": False, "error": "No file open"}

    try:
        session.ensure_open()
    except Exception as exc:
        logger.warning("Failed to reopen session %s: %s", session.filepath, exc)
        return {"ok": False, "error": str(exc)}

    name = payload.get("name", "")
    logger.debug("Info requested for table: %s", name)

    df = session.load_table(name)
    if df is None:
        logger.warning("Info table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    import numpy as np
    from tabulate import tabulate

    discovered = [str(col) for col in df.columns]
    hidden = []
    visible = list(discovered)
    if session.config is not None:
        try:
            visible = session.config.merge_table_columns(name, discovered)
            toggle = payload.get("toggle_column")
            if toggle is not None and toggle != "":
                session.config.toggle_hidden(name, _resolve_info_column(df, toggle))
                visible = session.config.get_columns(name) or []
            hidden = session.config.get_hidden(name)
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("Failed to sync table config for %s: %s", name, exc)
    elif payload.get("toggle_column") not in (None, ""):
        return {"ok": False, "error": "Column config unavailable"}

    hidden_set = set(hidden)
    visible = [col for col in visible if col not in hidden_set]
    index_of = {col: index for index, col in enumerate(discovered, start=1)}
    col_map = {str(col): col for col in df.columns}

    lines = []
    lines.append(f"Table: {name}")
    lines.append(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")
    lines.append("")
    lines.append("Columns:")
    lines.append("\u2500" * 50)
    for col in visible:
        actual = col_map.get(col)
        if actual is None:
            continue
        lines.append(_column_line(index_of.get(col, 0), actual, df[actual]))
    lines.append("\u2500" * 50)

    if hidden:
        lines.append("")
        lines.append("Hidden:")
        lines.append("\u2500" * 50)
        for col in hidden:
            actual = col_map.get(col)
            if actual is None:
                continue
            lines.append(_column_line(index_of.get(col, 0), actual, df[actual]))
        lines.append("\u2500" * 50)

    numeric_set = {str(col) for col in df.select_dtypes(include=[np.number]).columns}
    numeric_names = [col for col in visible if col in numeric_set]
    if numeric_names:
        lines.append("")
        lines.append("Numeric Summary:")
        desc = df[[col_map[col] for col in numeric_names]].describe().T
        lines.append(
            tabulate(
                desc,
                headers="keys",
                tablefmt="plain",
                stralign="left",
                numalign="left",
            )
        )

    return {
        "ok": True,
        "content": "\n".join(lines),
        "columns": visible,
        "hidden": hidden,
    }


def _resolve_info_column(df, reference):
    """Resolve a 1-based index or name against the full table column list."""
    names = [str(col) for col in df.columns]
    try:
        index = int(reference)
    except (TypeError, ValueError):
        if str(reference) in names:
            return str(reference)
        raise KeyError("unknown column {!r}".format(reference))
    if index < 1 or index > len(names):
        raise ValueError("column {} is out of range (1-{})".format(index, len(names)))
    return names[index - 1]


def _column_line(index, col, series):
    return f"  {index:>3}  {str(col):<30} {str(series.dtype):<12} ({series.count()} non-null)"
