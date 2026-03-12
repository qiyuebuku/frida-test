import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "jettask",
    "user": "jettask",
    "password": "123456",
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def init_tables():
    """创建 OCR 记录表 + 任务表"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sa_ocr_records (
                    id SERIAL PRIMARY KEY,
                    action VARCHAR(30) NOT NULL,
                    raw_text TEXT,
                    markdown_text TEXT,
                    image_path VARCHAR(500),
                    client_id VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_sa_ocr_action ON sa_ocr_records(action);
                CREATE INDEX IF NOT EXISTS idx_sa_ocr_created ON sa_ocr_records(created_at);

                CREATE TABLE IF NOT EXISTS sa_tasks (
                    id SERIAL PRIMARY KEY,
                    task_type VARCHAR(30) NOT NULL,
                    skill_name VARCHAR(64),
                    command_id VARCHAR(64),
                    status VARCHAR(20) DEFAULT 'pending',
                    progress INT DEFAULT 0,
                    progress_msg VARCHAR(200),
                    input_type VARCHAR(20),
                    input_data TEXT,
                    image_path VARCHAR(500),
                    ocr_record_id INT,
                    title VARCHAR(200),
                    summary VARCHAR(500),
                    result TEXT,
                    result_data JSONB,
                    partial_result TEXT,
                    tool_calls JSONB,
                    config JSONB,
                    client_id VARCHAR(100),
                    error_msg TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    duration_sec INT
                );
                CREATE INDEX IF NOT EXISTS idx_sa_tasks_status ON sa_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_sa_tasks_created ON sa_tasks(created_at);
            """)
        conn.commit()
        # 兼容：为已有的 sa_tasks 表添加 v3 新字段
        for col, typ in [("skill_name", "VARCHAR(64)"), ("command_id", "VARCHAR(64)")]:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"ALTER TABLE sa_tasks ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sa_tasks_skill ON sa_tasks(skill_name)")
            conn.commit()
        except Exception:
            conn.rollback()
    print("[DB] sa_ocr_records + sa_tasks tables ready", flush=True)


def save_ocr_record(action: str, raw_text: str, markdown_text: str,
                    image_path: str = None, client_id: str = None) -> int:
    """保存 OCR 识别记录，返回记录 ID"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sa_ocr_records (action, raw_text, markdown_text, image_path, client_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (action, raw_text, markdown_text, image_path, client_id))
            record_id = cur.fetchone()[0]
        conn.commit()
    return record_id


def create_task(task_type: str, skill_name: str = None, command_id: str = None,
                input_type: str = None, input_data: str = None,
                image_path: str = None, client_id: str = None,
                config: dict = None, title: str = None) -> int:
    """创建任务，返回 task_id"""
    import json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sa_tasks (task_type, skill_name, command_id, input_type,
                    input_data, image_path, client_id, config, title)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (task_type, skill_name, command_id, input_type,
                  input_data, image_path, client_id,
                  json.dumps(config) if config else None, title))
            task_id = cur.fetchone()[0]
        conn.commit()
    return task_id


def update_task(task_id: int, **kwargs):
    """更新任务字段"""
    import json
    allowed = {"status", "progress", "progress_msg", "title", "summary",
               "result", "result_data", "partial_result", "tool_calls",
               "error_msg", "started_at", "completed_at", "duration_sec"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = []
    for k, v in fields.items():
        if k in ("result_data", "tool_calls") and isinstance(v, (dict, list)):
            values.append(json.dumps(v))
        else:
            values.append(v)
    values.append(task_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sa_tasks SET {set_clause} WHERE id = %s", values)
        conn.commit()


def get_task(task_id: int) -> dict | None:
    """获取单个任务"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM sa_tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_tasks(status: str = None, task_type: str = None,
              skill_name: str = None, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """查询任务列表，返回 (tasks, total)"""
    conditions = []
    params = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if task_type:
        conditions.append("task_type = %s")
        params.append(task_type)
    if skill_name:
        conditions.append("skill_name = %s")
        params.append(skill_name)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT COUNT(*) as cnt FROM sa_tasks {where}", params)
            total = cur.fetchone()["cnt"]

            cur.execute(f"""
                SELECT * FROM sa_tasks {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s
            """, params + [limit, offset])
            tasks = [dict(row) for row in cur.fetchall()]
    return tasks, total


def get_ocr_records(action: str = None, limit: int = 20) -> list[dict]:
    """查询 OCR 记录，可按 action 筛选"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if action:
                cur.execute("""
                    SELECT id, action, raw_text, markdown_text, image_path, client_id, created_at
                    FROM sa_ocr_records
                    WHERE action = %s
                    ORDER BY created_at DESC LIMIT %s
                """, (action, limit))
            else:
                cur.execute("""
                    SELECT id, action, raw_text, markdown_text, image_path, client_id, created_at
                    FROM sa_ocr_records
                    ORDER BY created_at DESC LIMIT %s
                """, (limit,))
            return [dict(row) for row in cur.fetchall()]
