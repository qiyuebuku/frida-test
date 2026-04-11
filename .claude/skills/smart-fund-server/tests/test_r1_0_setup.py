"""T-R1.0 测试: 测试基础设施 + 基线锁定"""
import json
from pathlib import Path

import pytest


@pytest.mark.smoke
def test_r1_0_1_conftest_loaded(test_db_url, prod_db_url, baseline_path):
    """T-R1.0-1: conftest fixture 被收集"""
    assert "jettask_test" in test_db_url
    assert "jettask" in prod_db_url and "test" not in prod_db_url
    assert isinstance(baseline_path, Path)


@pytest.mark.smoke
def test_r1_0_2_baseline_locked(baseline_path):
    """T-R1.0-2: R1 基线已落档"""
    assert baseline_path.exists(), f"基线文件未找到: {baseline_path}"
    data = json.loads(baseline_path.read_text())
    assert "captured_at" in data
    assert "tables" in data
    assert len(data["tables"]) == 18, f"预期 18 张表,实际 {len(data['tables'])}"
    # 关键表应该有数据(R1 开始前业务已经在跑)
    assert data["tables"]["ft_news"] > 0
    assert data["tables"]["ft_market_flow"] > 0
    assert data["tables"]["ft_collection_state"] > 0
