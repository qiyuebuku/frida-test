"""T-R1.9 测试: 2 个复盘层 ORM 模型只读 smoke"""
import pytest
from sqlalchemy import select


@pytest.mark.integration
def test_r1_9_1_reflection_models_select_one():
    """T-R1.9-1: Review / Lesson 只读 smoke"""
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models import Lesson, Review

    with get_session("prod") as s:
        review = s.scalars(select(Review).limit(1)).first()
        if review:
            assert repr(review)

        lesson = s.scalars(select(Lesson).limit(1)).first()
        if lesson:
            assert repr(lesson)
            # related_sectors 是 ARRAY[String] → list[str]
            if lesson.related_sectors:
                assert isinstance(lesson.related_sectors, list)
                assert all(isinstance(x, str) for x in lesson.related_sectors)
            # source_review_ids 是 ARRAY[Integer] → list[int]
            if lesson.source_review_ids:
                assert isinstance(lesson.source_review_ids, list)
                assert all(isinstance(x, int) for x in lesson.source_review_ids)
