"""Shared SQLite connection helpers for backend runtime code."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def get_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Return a configured SQLite connection with consistent runtime options."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_readonly_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Return a read-only SQLite connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000;")
    try:
        yield conn
    finally:
        conn.close()
