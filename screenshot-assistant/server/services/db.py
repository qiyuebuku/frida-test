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
    """创建 OCR 记录表"""
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
            """)
        conn.commit()
    print("[DB] sa_ocr_records table ready", flush=True)


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
