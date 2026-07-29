"""T-R1.2 测试: scripts/init_db.py"""
import subprocess
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INIT_DB = PROJECT_ROOT / "scripts" / "init_db.py"


def _conn():
    return psycopg2.connect(
        host="119.23.227.187", port=5432, dbname="jettask_test", user="jettask", password="123456"
    )


def _count_ft_tables() -> int:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'ft_%'"
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


def _run_init_db(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/home/yuyang/anaconda3/envs/jettask/bin/python", str(INIT_DB), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.integration
def test_r1_2_1_init_creates_all_tables():
    """T-R1.2-1: 跑 init_db.py --target=test 重建 jettask_test"""
    result = _run_init_db("--target=test")
    assert result.returncode == 0, f"init_db 失败:\n{result.stderr}\n{result.stdout}"
    n = _count_ft_tables()
    # 18 重构范围 + 7 legacy(其中 ft_raw_data 是分区表,会有 5 个物理表)
    assert n >= 25, f"预期至少 25 张 ft_* 表,实际 {n}"


@pytest.mark.integration
def test_r1_2_2_init_idempotent():
    """T-R1.2-2: 重复跑 init_db.py 不报错(幂等)"""
    n1 = _count_ft_tables()
    for _ in range(3):
        result = _run_init_db("--target=test")
        assert result.returncode == 0, f"重复执行失败:\n{result.stderr}"
    n2 = _count_ft_tables()
    assert n1 == n2, f"幂等性破坏: {n1} → {n2}"


@pytest.mark.integration
def test_r1_2_3_prod_requires_yes():
    """T-R1.2-3: --target=prod 必须加 --yes 才执行"""
    result = _run_init_db("--target=prod")
    assert result.returncode != 0
    assert "--yes" in result.stdout or "--yes" in result.stderr
