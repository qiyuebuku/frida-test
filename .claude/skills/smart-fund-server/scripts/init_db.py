"""数据库初始化脚本

按文件名顺序执行 schema/*.sql,在指定数据库中建出全部 ft_* 表 + 索引。

幂等：所有 DDL 都用 IF NOT EXISTS 形式（pg_dump 默认 CREATE TABLE 不带 IF NOT EXISTS,
本脚本会先 DROP IF EXISTS 再执行)。

用法:
    python scripts/init_db.py                  # 默认 target=test (jettask_test 库)
    python scripts/init_db.py --target=prod    # 生产库 (危险!需 --yes 确认)
    python scripts/init_db.py --target=test --no-drop   # 不 drop 已存在的表

环境变量(可选,默认值在 conftest 同一份):
    DB_HOST / DB_PORT / DB_USER / DB_PASSWORD
    TARGET_DB                                   # 优先级最高
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schema"

DB_DEFAULTS = {
    "host": os.environ.get("DB_HOST", "119.23.227.187"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "user": os.environ.get("DB_USER", "jettask"),
    "password": os.environ.get("DB_PASSWORD", "123456"),
}


def _resolve_dbname(target: str) -> str:
    if env := os.environ.get("TARGET_DB"):
        return env
    if target == "prod":
        return "jettask"
    if target == "test":
        return "jettask_test"
    raise ValueError(f"--target 必须是 prod/test,实际 {target!r}")


def _list_schema_files() -> list[Path]:
    """按文件名前缀顺序排列 schema/*.sql

    排序保证: tables 在 indexes 之前;legacy(05_) 在 indexes(99_) 之前
    """
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"schema/ 目录无 .sql 文件: {SCHEMA_DIR}")
    return files


def _drop_all_ft_tables(conn) -> int:
    """先 drop 所有 ft_* 表(包括分区表),让重新执行 schema 能干净建出"""
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname='public' AND tablename LIKE 'ft_%'
    """)
    tables = [r[0] for r in cur.fetchall()]
    if tables:
        cur.execute(f"DROP TABLE IF EXISTS {', '.join(tables)} CASCADE")
        conn.commit()
    return len(tables)


def init(target: str, drop_existing: bool, prod_confirm: bool) -> None:
    dbname = _resolve_dbname(target)
    print(f"目标数据库: {dbname} @ {DB_DEFAULTS['host']}")

    if dbname == "jettask" and not prod_confirm:
        print("❌ 直接对生产库 init 需要加 --yes 显式确认")
        sys.exit(1)

    schema_files = _list_schema_files()
    print(f"schema 文件: {len(schema_files)}")
    for f in schema_files:
        print(f"  - {f.name}")
    print()

    conn = psycopg2.connect(dbname=dbname, **DB_DEFAULTS)
    try:
        if drop_existing:
            n = _drop_all_ft_tables(conn)
            print(f"🧹 drop {n} 张已存在 ft_* 表")

        cur = conn.cursor()
        for f in schema_files:
            sql = f.read_text()
            try:
                cur.execute(sql)
                conn.commit()
                print(f"✅ {f.name}")
            except Exception as e:
                conn.rollback()
                print(f"❌ {f.name}: {e}")
                raise

        # 验证: 列出建出来的表
        cur.execute("""
            SELECT COUNT(*) FROM pg_tables
            WHERE schemaname='public' AND tablename LIKE 'ft_%'
        """)
        n_tables = cur.fetchone()[0]
        print(f"\n🎉 完成,共建 {n_tables} 张 ft_* 表")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="test", choices=["test", "prod"], help="目标数据库 (默认 test)")
    parser.add_argument("--no-drop", action="store_true", help="不 drop 已存在的表(增量执行)")
    parser.add_argument("--yes", action="store_true", help="对生产库的危险操作显式确认")
    args = parser.parse_args()

    init(target=args.target, drop_existing=not args.no_drop, prod_confirm=args.yes)


if __name__ == "__main__":
    main()
