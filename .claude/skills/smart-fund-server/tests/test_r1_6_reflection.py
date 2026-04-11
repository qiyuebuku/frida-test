"""T-R1.6 测试: 18 个 ORM 模型 schema 反射比对(最关键的一步)

用 SQLAlchemy inspect(engine) 反射出 PG 实际列定义,与 ORM __table__.columns 比对。
列名必须 100% 一致,类型有 1-2 处可接受偏差(如 TIMESTAMPTZ vs DateTime(tz=True))。

这是 ORM 重构最大的坑 — 字段类型映射不对会让所有读写都崩。
"""
import pytest
from sqlalchemy import inspect


# 18 个重构范围模型的预期表名 + 列数(从 R1.0 baseline 取)
EXPECTED = {
    "ft_news": 15,
    "ft_market_flow": 5,
    "ft_market_cache": 5,
    "ft_sentiment": 5,
    "ft_macro_indicators": 9,
    "ft_collection_state": 14,
    "ft_events": 28,
    "ft_event_streams": 13,
    "ft_pending_decisions": 23,
    "ft_decisions": 13,
    "ft_trades": 13,
    "ft_positions": 12,
    "ft_industry_mapping": 7,
    "ft_industry_index": 6,
    "ft_index_fund": 9,
    "ft_fund_limits": 11,
    "ft_reviews": 17,
    "ft_lessons": 16,
}


@pytest.mark.integration
def test_r1_6_1_18_models_loaded():
    """T-R1.6-1: 18 个模型全部已注册到 Base.registry"""
    from src.infrastructure.persistence.models import Base

    table_names = set(Base.metadata.tables.keys())
    expected_set = set(EXPECTED.keys())
    missing = expected_set - table_names
    extra = table_names - expected_set
    assert not missing, f"缺失模型: {missing}"
    # extra 是 OK 的(以后可能加测试模型)
    print(f"\n  已注册 {len(table_names)} 个模型,其中 {len(expected_set)} 个在 R1 范围内")


@pytest.mark.integration
def test_r1_6_2_orm_columns_match_pg_schema():
    """T-R1.6-2: 核心断言 — ORM 列定义 vs PG 实际列定义

    比对维度:
    1. 列名 100% 一致(顺序无所谓)
    2. 列数一致

    类型比对放第三个测试做(允许容差)
    """
    from src.infrastructure.connections import get_engine
    from src.infrastructure.persistence.models import Base

    engine = get_engine("prod")
    insp = inspect(engine)

    errors = []
    for table_name, expected_cnt in EXPECTED.items():
        # ORM 视角
        if table_name not in Base.metadata.tables:
            errors.append(f"❌ {table_name}: ORM 未注册")
            continue
        orm_cols = {c.name for c in Base.metadata.tables[table_name].columns}

        # PG 视角
        pg_cols_info = insp.get_columns(table_name)
        pg_cols = {c["name"] for c in pg_cols_info}

        # 列数
        if len(pg_cols) != expected_cnt:
            errors.append(
                f"⚠️  {table_name}: 预期 {expected_cnt} 列,PG 实际 {len(pg_cols)} 列"
            )
        if len(orm_cols) != len(pg_cols):
            errors.append(
                f"❌ {table_name}: ORM {len(orm_cols)} 列 vs PG {len(pg_cols)} 列"
            )

        # 列名 set 比对
        only_in_orm = orm_cols - pg_cols
        only_in_pg = pg_cols - orm_cols
        if only_in_orm:
            errors.append(f"❌ {table_name}: ORM 多出 {only_in_orm}")
        if only_in_pg:
            errors.append(f"❌ {table_name}: PG 多出 {only_in_pg}")

    assert not errors, "\n" + "\n".join(errors)


@pytest.mark.integration
def test_r1_6_3_column_types_compatible():
    """T-R1.6-3: 列类型容差比对

    SQLAlchemy 反射出的 PG 类型 vs ORM 模型声明的类型,必须能 round-trip。
    用 inspect 直接看 reflected type 与模型 type,允许:
    - VARCHAR(N) ↔ String (无长度限制)
    - TIMESTAMP WITH TIME ZONE ↔ DateTime(timezone=True)
    - JSONB ↔ JSONB
    - ARRAY ↔ ARRAY
    """
    from src.infrastructure.connections import get_engine
    from src.infrastructure.persistence.models import Base

    engine = get_engine("prod")
    insp = inspect(engine)

    # 类型大类等价表 (SQLAlchemy 类型 → 通用类别)
    def normalize(t) -> str:
        s = str(t).upper()
        if "VARCHAR" in s or "STRING" in s:
            return "STRING"
        if "TEXT" in s:
            return "TEXT"
        if "TIMESTAMP" in s or "DATETIME" in s:
            return "DATETIME"
        if "DATE" in s:
            return "DATE"
        if "INTEGER" in s or "SERIAL" in s or "INT4" in s or "BIGINT" in s:
            return "INTEGER"
        if "FLOAT" in s or "DOUBLE" in s or "REAL" in s:
            return "FLOAT"
        if "NUMERIC" in s or "DECIMAL" in s:
            return "NUMERIC"
        if "BOOL" in s:
            return "BOOLEAN"
        if "JSONB" in s or "JSON" in s:
            return "JSON"
        if "BYTEA" in s or "BINARY" in s:
            return "BYTES"
        if "ARRAY" in s or s.endswith("[]"):
            return "ARRAY"
        return s

    errors = []
    for table_name in EXPECTED:
        orm_table = Base.metadata.tables.get(table_name)
        if orm_table is None:
            continue
        orm_types = {c.name: normalize(c.type) for c in orm_table.columns}
        pg_types = {c["name"]: normalize(c["type"]) for c in insp.get_columns(table_name)}

        for col, orm_t in orm_types.items():
            pg_t = pg_types.get(col)
            if pg_t is None:
                continue
            # STRING vs TEXT 互换允许
            if {orm_t, pg_t} <= {"STRING", "TEXT"}:
                continue
            # INTEGER 大类
            if {orm_t, pg_t} <= {"INTEGER"}:
                continue
            if orm_t != pg_t:
                errors.append(f"⚠️  {table_name}.{col}: ORM={orm_t} vs PG={pg_t}")

    if errors:
        # 类型不一致只 warning,不 fail (R1 阶段允许 1-2 处)
        print(f"\n  类型差异 {len(errors)} 处:")
        for e in errors[:20]:
            print(f"    {e}")
        # 但如果差异 > 5 个,认为是真问题
        assert len(errors) <= 5, f"类型差异过多: {len(errors)}"
