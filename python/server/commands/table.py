"""Table command handler."""

import logging

from tabulate import tabulate

logger = logging.getLogger("vime")


def handle(state, payload):
    """Read a table and return its formatted content."""
    if not state.loader.is_open:
        logger.warning("Table requested with no file open")
        return {"ok": False, "error": "No file open"}

    name = payload.get("name", "")
    logger.debug("Loading table: %s", name)

    df = state.load_table(name)
    if df is None:
        logger.warning("Table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    df = _apply_column_config(state, name, df)
    state.current_df = df
    state.current_table = name

    content = tabulate(
        df,
        headers="keys",
        tablefmt="plain",
        showindex=False,
        stralign="left",
        numalign="left",
    )

    header = f"{name}  [{len(df)} rows x {len(df.columns)} cols]"

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
