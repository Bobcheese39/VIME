"""Table command handler."""

import logging

logger = logging.getLogger("vime")


def handle(state, payload):
    """Read a table and return its formatted content."""
    session = state.session_for(payload)
    if session is None:
        logger.warning("Table requested with no file open")
        return {"ok": False, "error": "No file open"}

    try:
        session.ensure_open()
    except Exception as exc:
        logger.warning("Failed to reopen session %s: %s", session.filepath, exc)
        return {"ok": False, "error": str(exc)}

    name = payload.get("name", "")
    logger.debug("Loading table: %s", name)

    df = session.load_table(name)
    if df is None:
        logger.warning("Table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    df = _apply_column_config(session, name, df)
    session.current_df = df
    session.current_table = name

    from tabulate import tabulate

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
        return df
    return df.loc[:, ordered_actual]
