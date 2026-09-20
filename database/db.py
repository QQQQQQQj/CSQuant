"""数据库连接与 schema 初始化（SQLite WAL）。

多线程安全：Streamlit 多线程服务器与调度器可能共享同一连接，
`check_same_thread=False` 仅放行跨线程访问、并不串行化并发调用
（Python 3.12 下并发 execute 会抛 InterfaceError）。
因此 connect() 返回 SerializedConnection 代理：execute/fetch/commit
全部经同一 RLock 串行化（并发写测试见 tests/test_deep_coverage.py）。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class _LockedCursor:
    """游标代理：fetch 阶段同样持锁（sqlite3 在 fetch 时才逐步执行语句）。"""

    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_lock", lock)

    def fetchone(self):
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self):
        with self._lock:
            return self._cursor.fetchall()

    def fetchmany(self, size: int = 1):
        with self._lock:
            return self._cursor.fetchmany(size)

    def __iter__(self):
        with self._lock:
            return iter(self._cursor.fetchall())

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class SerializedConnection:
    """线程串行化连接代理（同一 RLock 保护全部 execute/commit）。"""

    def __init__(self, conn: sqlite3.Connection):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", threading.RLock())

    def execute(self, sql: str, params=()):
        with self._lock:
            return _LockedCursor(self._conn.execute(sql, params), self._lock)

    def executemany(self, sql: str, seq):
        with self._lock:
            return _LockedCursor(self._conn.executemany(sql, seq), self._lock)

    def executescript(self, script: str):
        with self._lock:
            return self._conn.executescript(script)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def rollback(self):
        with self._lock:
            self._conn.rollback()

    def close(self):
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


def connect(db_path: str | Path) -> SerializedConnection:
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：允许跨线程；串行化由 SerializedConnection 保证
    raw = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA foreign_keys = ON")
    return SerializedConnection(raw)


def init_schema(conn: SerializedConnection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.commit()


def _migrate(conn: SerializedConnection) -> None:
    """轻量列级迁移：已存在的表补齐新增列（CREATE IF NOT EXISTS 不会改旧表）。"""
    required = {
        "market_anomalies": [("relative_strength", "REAL")],
    }
    for table, columns in required.items():
        existing = {row[1] for row in
                    conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
