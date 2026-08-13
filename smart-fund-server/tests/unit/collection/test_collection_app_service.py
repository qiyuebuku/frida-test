import pytest

import src.application.services.collection_app_service as module


@pytest.mark.asyncio
async def test_live_schedule_continues_while_backfill_owns_checkpoint(monkeypatch):
    calls = []

    class FakeAggregator:
        last_saved_ids = []

        async def run_source(self, source_name, **kwargs):
            calls.append((source_name, kwargs))
            return {
                "fetched_count": 1,
                "valid_count": 1,
                "saved_count": 1,
                "checkpoint_before": kwargs["state_override"],
                "checkpoint_after": {"mode": "incremental"},
            }

    class FakeStates:
        def get(self, aggregator, source_name):
            return {
                "mode": "backfill",
                "target_time": "2026-01-01",
                "cursor": {"page": 9},
                "newest_time": "2026-08-07",
            }

    monkeypatch.setattr(module, "_collection_aggregator_class", lambda _name: FakeAggregator)
    monkeypatch.setattr(module, "CollectionStateRepositoryImpl", FakeStates)
    monkeypatch.setattr(module, "record_collection", lambda *_args: None)

    result = await module.CollectionAppService().run_scheduled_collection_source(
        "macro", "pboc_usdcny"
    )

    assert result is not None
    assert calls[0][1]["state_override"]["mode"] == "incremental"
    assert calls[0][1]["state_override"]["target_time"] is None
    assert calls[0][1]["persist_checkpoint"] is False
