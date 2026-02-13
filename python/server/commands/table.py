"""Table command handler."""

import logging

from tabulate import tabulate

from server.formatters import format_fast_table

logger = logging.getLogger("vime")


def handle(state, payload):
    """Read a table and return its formatted content."""
    if not state.loader.is_open:
        logger.warning("Table requested with no file open")
        return {"ok": False, "error": "No file open"}

    name = payload.get("name", "")
    head = payload.get("head", 100)
    fast = bool(payload.get("fast", False))
    logger.debug("Loading table: %s (head=%s fast=%s)", name, head, fast)

    if fast:
        data = state.loader.load_table_fast(name)
        if data is None:
            logger.warning("Fast table not found or unsupported: %s", name)
            return {"ok": False, "error": f"Table not found or fast read unsupported: {name}"}
        content = format_fast_table(name, data)
        return {"ok": True, "content": content, "columns": [], "name": name, "fast": True}

    df = state.load_table(name)
    if df is None:
        logger.warning("Table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    df = _apply_column_config(state, name, df)
    state.current_df = df
    state.current_table = name

    display_df = df.head(head) if head and len(df) > head else df

    content = tabulate(
        display_df,
        headers="keys",
        tablefmt="plain",
        showindex=False,
        stralign="left",
        numalign="left",
    )

    # Add a header line with table info
    shape_info = f"  [{len(df)} rows x {len(df.columns)} cols]"
    if head and len(df) > head:
        shape_info += f"  (showing first {head})"
    header = f"{name}{shape_info}"

    columns = [str(c) for c in df.columns]
    logger.debug("Loaded table: %s (rows=%d cols=%d)", name, len(df), len(df.columns))
    return {
        "ok": True,
        "content": header + "\n\n" + content,
        "columns": columns,
        "name": name,
    }


def _apply_column_config(state, table_name, df):
    """Apply configured column order/visibility for a table."""
    if state.config is None:
        return df

    discovered = [str(col) for col in df.columns]
    try:
        configured = state.config.merge_table_columns(table_name, discovered)
    except Exception as exc:
        logger.warning("Failed to sync table config for %s: %s", table_name, exc)
        return df

    col_map = {str(col): col for col in df.columns}
    ordered_actual = [col_map[col] for col in configured if col in col_map]
    if not ordered_actual:
        return df
    return df.loc[:, ordered_actual]
