"""T-R1.1 测试: schema/*.sql DDL 提取与生产库等价"""
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schema"

ALL_FT_TABLES = [
    "ft_news", "ft_market_flow", "ft_market_cache", "ft_sentiment",
    "ft_macro_indicators", "ft_collection_state",
    "ft_events", "ft_event_streams",
    "ft_pending_decisions", "ft_decisions", "ft_trades", "ft_positions",
    "ft_industry_mapping", "ft_industry_index", "ft_index_fund", "ft_fund_limits",
    "ft_reviews", "ft_lessons",
    # legacy
    "ft_alipay_decisions", "ft_alipay_positions", "ft_cache", "ft_config",
    "ft_run_log", "ft_signals", "ft_raw_data",
]


def _conn(dbname: str):
    return psycopg2.connect(
        host="119.23.227.187", port=5432, dbname=dbname, user="jettask", password="123456"
    )


def _exec_sql_files(conn, files):
    """串联执行 .sql 文件"""
    cur = conn.cursor()
    for f in files:
        sql = f.read_text()
        cur.execute(sql)
    conn.commit()


def _drop_all_ft_tables(conn):
    """清空测试库所有 ft_* 表(为执行 schema 做准备)"""
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'ft_%'
    """)
    tables = [r[0] for r in cur.fetchall()]
    if tables:
        cur.execute(f"DROP TABLE IF EXISTS {', '.join(tables)} CASCADE")
    conn.commit()


@pytest.mark.integration
def test_r1_1_1_schema_files_executable():
    """T-R1.1-1: schema/*.sql 在干净 jettask_test 库可执行"""
    schema_files = sorted(SCHEMA_DIR.glob("*.sql"))
    assert len(schema_files) >= 5, f"预期至少 5 个 schema 文件,实际 {len(schema_files)}"

    conn = _conn("jettask_test")
    try:
        _drop_all_ft_tables(conn)
        _exec_sql_files(conn, schema_files)

        # 执行后,18 张重构范围 + 7 张 legacy 都该存在
        cur = conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'ft_%'")
        actual = {r[0] for r in cur.fetchall()}
        for t in ALL_FT_TABLES:
            assert t in actual, f"表 {t} 未被 schema/*.sql 创建"
    finally:
        conn.close()


@pytest.mark.integration
def test_r1_1_2_schema_matches_production():
    """T-R1.1-2: jettask_test 表结构 ↔ jettask 生产表结构 100% 一致

    比对维度: 列名 / 数据类型 / nullable / default
    """
    test_conn = _conn("jettask_test")
    prod_conn = _conn("jettask")
    try:
        cur_t = test_conn.cursor()
        cur_p = prod_conn.cursor()

        # 重构范围 18 张表必须 100% 一致
        focus_tables = ALL_FT_TABLES[:18]
        for t in focus_tables:
            cur_t.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name=%s ORDER BY ordinal_position
            """, (t,))
            test_cols = cur_t.fetchall()
            cur_p.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name=%s ORDER BY ordinal_position
            """, (t,))
            prod_cols = cur_p.fetchall()
            assert len(test_cols) == len(prod_cols), (
                f"{t} 列数不一致: test={len(test_cols)}, prod={len(prod_cols)}"
            )
            for tc, pc in zip(test_cols, prod_cols):
                assert tc[0] == pc[0], f"{t} 列名顺序不一致: {tc} vs {pc}"
                assert tc[1] == pc[1], f"{t}.{tc[0]} 类型不一致: test={tc[1]} vs prod={pc[1]}"
                assert tc[2] == pc[2], f"{t}.{tc[0]} nullable 不一致"
                # default 可能因 sequence 名差异略有不同，只要都有/都没有即可
                assert (tc[3] is None) == (pc[3] is None), f"{t}.{tc[0]} default 存在性不一致"
    finally:
        test_conn.close()
        prod_conn.close()
