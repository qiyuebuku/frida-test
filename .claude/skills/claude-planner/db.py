"""轻量 SQLite 任务存储 — 从 smart-fund-server/src/infrastructure/db/task_db.py 精简"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "planner.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tables():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                status          TEXT NOT NULL DEFAULT 'pending',
                progress        INTEGER NOT NULL DEFAULT 0,
                progress_msg    TEXT,

                prompt          TEXT,
                context_file    TEXT,
                result          TEXT,
                partial_result  TEXT,
                tool_calls      TEXT DEFAULT '[]',

                backend         TEXT,
                model           TEXT,
                session_id      TEXT,
                session_name    TEXT,
                cwd             TEXT,

                messages        TEXT DEFAULT '[]',
                error_msg       TEXT,
                usage           TEXT DEFAULT '{}',
                pending_dialog  TEXT,

                created_at      TEXT DEFAULT (datetime('now', 'localtime')),
                completed_at    TEXT,
                duration_sec    INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        # 兼容旧表：添加 usage 列
        for col, default in [("usage", "'{}'"), ("pending_dialog", "NULL")]:
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")


def create_task(**kwargs) -> int:
    with _conn() as conn:
        cols, vals = [], []
        for k, v in kwargs.items():
            cols.append(k)
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        placeholders = ", ".join("?" * len(cols))
        conn.execute(f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})", vals)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_task(task_id: int, **kwargs):
    with _conn() as conn:
        sets, vals = [], []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        vals.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)


def get_task(task_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        # 解析 JSON 字段
        for field in ("tool_calls", "messages", "usage", "pending_dialog"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except json.JSONDecodeError:
                    pass
        return d


def list_tasks(status: str = None, limit: int = 50) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def delete_task(task_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
