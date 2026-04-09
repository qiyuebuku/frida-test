"""OCR 记录数据库操作"""

from services.db.fund_db import get_conn

from psycopg2.extras import RealDictCursor


def init_ocr_tables():
    """创建 OCR 记录表"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sa_ocr_records (
                    id SERIAL PRIMARY KEY,
                    action VARCHAR(30) NOT NULL,
                    raw_text TEXT,
                    markdown_text TEXT,
                    structured_data TEXT,
                    image_path VARCHAR(500),
                    client_id VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW()
                );
                ALTER TABLE sa_ocr_records ADD COLUMN IF NOT EXISTS structured_data TEXT;
                ALTER TABLE sa_ocr_records ADD COLUMN IF NOT EXISTS flat_data JSONB;
                CREATE INDEX IF NOT EXISTS idx_sa_ocr_action ON sa_ocr_records(action);
                CREATE INDEX IF NOT EXISTS idx_sa_ocr_created ON sa_ocr_records(created_at);
            """)
        conn.commit()
    print("[DB] sa_ocr_records table ready", flush=True)


def save_ocr_record(action: str, raw_text: str, markdown_text: str,
                    image_path: str = None, client_id: str = None,
                    structured_data: str = None) -> int:
    """保存 OCR 识别记录，返回记录 ID"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sa_ocr_records (action, raw_text, markdown_text, structured_data, image_path, client_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (action, raw_text, markdown_text, structured_data, image_path, client_id))
            record_id = cur.fetchone()[0]
        conn.commit()
    return record_id


def update_ocr_structured_data(record_id: int, structured_data: str, flat_data: str = None):
    """更新 OCR 记录的结构化数据和扁平化数据"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE sa_ocr_records SET structured_data = %s, flat_data = %s WHERE id = %s
            """, (structured_data, flat_data, record_id))
        conn.commit()


def get_ocr_records(action: str = None, limit: int = 20) -> list[dict]:
    """查询 OCR 记录，可按 action 筛选"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if action:
                cur.execute("""
                    SELECT id, action, raw_text, markdown_text, structured_data, flat_data, image_path, client_id, created_at
                    FROM sa_ocr_records
                    WHERE action = %s
                    ORDER BY created_at DESC LIMIT %s
                """, (action, limit))
            else:
                cur.execute("""
                    SELECT id, action, raw_text, markdown_text, structured_data, flat_data, image_path, client_id, created_at
                    FROM sa_ocr_records
                    ORDER BY created_at DESC LIMIT %s
                """, (limit,))
            return [dict(row) for row in cur.fetchall()]
