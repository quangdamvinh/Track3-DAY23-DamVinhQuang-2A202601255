"""Checkpointer adapter."""

from __future__ import annotations

import sqlite3
from typing import Any


def build_checkpointer(
    kind: str = "memory",
    database_url: str | None = None,
) -> Any | None:
    """Return a LangGraph checkpointer.

    Supported backends:
    - none: no persistence
    - memory: in-memory MemorySaver
    - sqlite: persistent SQLite checkpointer

    For SQLite, the database path is taken from database_url.
    """
    if kind == "none":
        return None

    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError(
                "SQLite checkpointer requires langgraph-checkpoint-sqlite. "
                "Install it with: pip install langgraph-checkpoint-sqlite"
            ) from exc

        db_path = database_url or "langgraph_checkpoints.db"

        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
        )

        # Enable WAL mode for better read/write behavior.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        return SqliteSaver(conn=conn)

    if kind == "postgres":
        raise NotImplementedError(
            "Postgres checkpointer is optional and is not required for this lab."
        )

    raise ValueError(f"Unknown checkpointer kind: {kind}")