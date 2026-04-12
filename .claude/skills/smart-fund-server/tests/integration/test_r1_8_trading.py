"""T-R1.8 测试: 8 个交易层 ORM 模型只读 smoke"""
import pytest
from sqlalchemy import select


@pytest.mark.integration
def test_r1_8_1_eight_models_select_one():
    """T-R1.8-1: 8 个交易模型每个跑 select(M).limit(1)"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import (
        Decision, FundLimit, IndexFund, IndustryIndex, IndustryMapping,
        PendingDecision, Position, Trade,
    )

    models = [
        PendingDecision, Decision, Trade, Position,
        IndustryMapping, IndustryIndex, IndexFund, FundLimit,
    ]
    with get_session("prod") as s:
        for M in models:
            row = s.scalars(select(M).limit(1)).first()
            if row is not None:
                assert repr(row)


@pytest.mark.integration
def test_r1_8_2_pending_decision_dry_run_filter():
    """T-R1.8-2: 能用 ORM 过滤 dry_run 决策"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import PendingDecision

    with get_session("prod") as s:
        rows = s.scalars(
            select(PendingDecision)
            .where(PendingDecision.dry_run.is_(True))
            .limit(5)
        ).all()
        for r in rows:
            assert r.dry_run is True


@pytest.mark.integration
def test_r1_8_3_industry_mapping_keywords_jsonb():
    """T-R1.8-3: IndustryMapping.keywords / aliases 自动反序列化为 list"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import IndustryMapping

    with get_session("prod") as s:
        m = s.scalars(select(IndustryMapping).limit(1)).first()
        if m:
            assert isinstance(m.keywords, list)
            assert isinstance(m.aliases, list)


@pytest.mark.integration
def test_r1_8_4_event_driven_decisions_query():
    """T-R1.8-4: 能查询 event_driven 来源的决策"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import PendingDecision

    with get_session("prod") as s:
        rows = s.scalars(
            select(PendingDecision)
            .where(PendingDecision.decision_source == "event_driven")
            .limit(3)
        ).all()
        for r in rows:
            assert r.decision_source == "event_driven"
            # event_driven 决策应有 score
            if r.score is not None:
                assert isinstance(r.score, float)
