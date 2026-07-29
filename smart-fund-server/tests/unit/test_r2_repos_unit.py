"""T-R2.x 单元测试: domain repository 接口 + 隔离约束"""
import os

import pytest


@pytest.mark.unit
def test_r2_1_1_news_repo_abstract_methods():
    """T-R2.1-1: NewsRepository 抽象方法齐全"""
    from src.domain.collection.repositories.news_repository import NewsRepository
    methods = NewsRepository.__abstractmethods__
    assert "upsert_batch" in methods
    assert "find_today_titles" in methods
    assert "find_unextracted" in methods
    assert "mark_extracted" in methods


@pytest.mark.unit
def test_r2_1_2_domain_does_not_import_sqlalchemy():
    """T-R2.1-2: domain/collection/repositories/ 不能 import SQLAlchemy/psycopg2"""
    repo_dir = "/home/yuyang/frida-test/smart-fund-server/src/domain/collection/repositories"
    for f in os.listdir(repo_dir):
        if not f.endswith(".py"):
            continue
        content = open(os.path.join(repo_dir, f)).read()
        assert "import sqlalchemy" not in content, f"{f} imports sqlalchemy"
        assert "import psycopg2" not in content, f"{f} imports psycopg2"
        assert "from sqlalchemy" not in content, f"{f} imports from sqlalchemy"
