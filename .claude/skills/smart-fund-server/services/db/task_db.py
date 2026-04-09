"""异步任务数据库操作 - sa_tasks 表"""

from datetime import datetime

from psycopg2.extras import RealDictCursor

from services.db.fund_db import get_conn


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sa_tasks (
    id            SERIAL PRIMARY KEY,
    task_type     VARCHAR(30) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress      INT NOT NULL DEFAULT 0,
    progress_msg  VARCHAR(200),

    input_type    VARCHAR(20),
    image_path    VARCHAR(500),
    ocr_record_id INT,

    title         VARCHAR(200),
    summary       VARCHAR(500),
    result        TEXT,
    result_data   JSONB,

    client_id     VARCHAR(100),
    error_msg     TEXT,
    partial_result TEXT,
    tool_calls    JSONB,
    config        JSONB,
    created_at    TIMESTAMP DEFAULT NOW(),
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    duration_sec  INT
);
CREATE INDEX IF NOT EXISTS idx_sa_tasks_status ON sa_tasks(status);
CREATE INDEX IF NOT EXISTS idx_sa_tasks_type ON sa_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_sa_tasks_created ON sa_tasks(created_at DESC);
"""

TYPE_LABELS = {
    "fund_holdings": "持仓分析",
    "chat_reply": "智能回复",
    "ocr": "文字识别",
    "table": "表格识别",
    "search": "搜索内容",
    "full_page": "完整页面",
    "fund_trade_run": "每日交易决策",
    "fund_review": "持仓审视",
}


ALTER_TABLE_SQL = """
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS partial_result TEXT;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS tool_calls JSONB;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS config JSONB;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS skill_name VARCHAR(64);
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS command_id VARCHAR(64);
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS session_id VARCHAR(64);
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS messages JSONB DEFAULT '[]';
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS terminal_log TEXT;
ALTER TABLE sa_tasks ADD COLUMN IF NOT EXISTS terminal_log_expanded TEXT;
CREATE INDEX IF NOT EXISTS idx_sa_tasks_skill ON sa_tasks(skill_name);
"""


def init_task_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(ALTER_TABLE_SQL)
        conn.commit()
    print("[DB] sa_tasks table ready", flush=True)


def create_task(task_type: str, input_type: str = None, image_path: str = None,
                client_id: str = None, title: str = None, config: dict = None,
                skill_name: str = None, command_id: str = None) -> int:
    import json
    if not title:
        label = TYPE_LABELS.get(task_type, task_type)
        title = f"{label} {datetime.now().strftime('%m-%d %H:%M')}"

    config_json = json.dumps(config, ensure_ascii=False) if config else None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sa_tasks (task_type, input_type, image_path, client_id, title, config, skill_name, command_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (task_type, input_type, image_path, client_id, title, config_json, skill_name, command_id))
            task_id = cur.fetchone()[0]
        conn.commit()
    return task_id


def update_task(task_id: int, **kwargs):
    if not kwargs:
        return
    import json
    sets = []
    vals = []
    json_fields = {"result_data", "tool_calls", "config", "messages"}
    for k, v in kwargs.items():
        sets.append(f"{k} = %s")
        if k in json_fields and isinstance(v, (dict, list)):
            vals.append(json.dumps(v, ensure_ascii=False))
        else:
            vals.append(v)
    vals.append(task_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sa_tasks SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()


def get_task(task_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM sa_tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def save_terminal_log(task_id: int, log: str):
    """保存终端累积日志到数据库（不会用更短的内容覆盖已有的长日志）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sa_tasks SET terminal_log = %s
                   WHERE id = %s
                   AND (terminal_log IS NULL OR length(terminal_log) <= length(%s))""",
                (log, task_id, log)
            )
        conn.commit()


def get_terminal_log(task_id: int) -> str | None:
    """获取终端累积日志"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT terminal_log FROM sa_tasks WHERE id = %s",
                (task_id,)
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def list_tasks(status: str = None, task_type: str = None,
               skill_name: str = None,
               limit: int = 20, offset: int = 0,
               next_token: str = None) -> tuple[list[dict], int, str | None]:
    """返回 (任务列表, 总数, next_token)

    next_token 格式: "{id}_{created_at_iso}"，基于 (created_at DESC, id DESC) 游标翻页。
    """
    where = []
    params = []
    if status and status != "all":
        where.append("status = %s")
        params.append(status)
    if task_type:
        where.append("task_type = %s")
        params.append(task_type)
    if skill_name:
        where.append("skill_name = %s")
        params.append(skill_name)

    # 游标翻页：next_token = "{id}_{created_at_iso}"
    if next_token:
        try:
            parts = next_token.split("_", 1)
            cursor_id = int(parts[0])
            cursor_ts = parts[1]
            where.append("(created_at, id) < (%s, %s)")
            params.extend([cursor_ts, cursor_id])
        except (ValueError, IndexError):
            pass  # 无效 token，忽略

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 总数（不含游标条件）
            count_where = []
            count_params = []
            if status and status != "all":
                count_where.append("status = %s")
                count_params.append(status)
            if task_type:
                count_where.append("task_type = %s")
                count_params.append(task_type)
            if skill_name:
                count_where.append("skill_name = %s")
                count_params.append(skill_name)
            count_sql = f"WHERE {' AND '.join(count_where)}" if count_where else ""
            cur.execute(f"SELECT COUNT(*) FROM sa_tasks {count_sql}", count_params)
            total = cur.fetchone()["count"]

            cur.execute(f"""
                SELECT id, task_type, status, progress, progress_msg,
                       title, summary, image_path, error_msg,
                       client_id, skill_name, command_id,
                       created_at, started_at, completed_at, duration_sec
                FROM sa_tasks
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            """, params + [limit])
            items = [dict(row) for row in cur.fetchall()]

    # 生成下一页 token
    new_next_token = None
    if len(items) == limit and items:
        last = items[-1]
        ts = last["created_at"]
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        new_next_token = f"{last['id']}_{ts}"

    return items, total, new_next_token


def delete_task(task_id: int) -> bool:
    """删除任务记录，返回是否成功"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sa_tasks WHERE id = %s", (task_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
