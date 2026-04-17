"""④ 数据库验证 — 新维度 DB 写入验证

只采集 007380 一只基金的新维度，写入 prod DB，验证数据正确入库。

运行：
    cd /home/yuyang/frida-test/.claude/skills/smart-fund-server
    python scripts/verify_new_dims.py
"""
import sys
import asyncio
import logging
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.observability import configure_logging
configure_logging(level="WARNING")   # 压制杂日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 被验证的新维度 + 对应的客户端方法
TEST_CODE = "007380"
TODAY = date.today()
SINCE = (TODAY - timedelta(days=8)).isoformat()   # 强制 gap>7


async def main():
    from src.infrastructure.clients import init_clients, close_clients
    from src.infrastructure import clients
    from src.domain.collection.services.fund_flow import _expand_timeseries
    from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
        WatchlistDataRepositoryImpl,
    )

    init_clients(timeout=15.0)
    repo = WatchlistDataRepositoryImpl()

    NEW_DIMS = {
        # dim_name: coroutine_factory()
        "nav_technical":     lambda: clients.ths.get_nav_technical(TEST_CODE),
        "nav_sina":          lambda: clients.sina.get_fund_nav(
                                TEST_CODE, date_from=SINCE, date_to=TODAY.isoformat()),
        "periodic_rate":     lambda: clients.ths.get_periodic_rate(TEST_CODE),
        "profit_contribution": lambda: clients.ths.get_profit_contribution(TEST_CODE),
        "asset_allocation":  lambda: clients.ths.get_asset_allocation(TEST_CODE),
        "position_detail":   lambda: clients.ths.get_position_detail(TEST_CODE),
        "trade_rule":        lambda: clients.ths.get_trade_rule(TEST_CODE),
    }

    # 检查 DB 中已有数据（对比用）
    from sqlalchemy import text
    from src.infrastructure.connections import get_session

    before_counts = {}
    for dim in NEW_DIMS:
        with get_session() as s:
            cnt = s.execute(
                text("SELECT COUNT(*) FROM ft_watchlist_data WHERE code=:c AND data_type=:d"),
                {"c": TEST_CODE, "d": dim}
            ).scalar() or 0
        before_counts[dim] = cnt

    logger.info(f"[before] 007380 各维度现有条数: {before_counts}")
    logger.info(f"[collect] 开始采集（since={SINCE}）")

    # 并发采集所有维度
    keys = list(NEW_DIMS.keys())
    raw_results = await asyncio.gather(*[NEW_DIMS[k]() for k in keys], return_exceptions=True)

    # 展开 + 收集
    all_rows = []
    for dim, raw in zip(keys, raw_results):
        if isinstance(raw, Exception):
            logger.warning(f"  [{dim}] 采集失败: {raw}")
            continue
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        rows = _expand_timeseries(TEST_CODE, dim, data, TODAY, SINCE)
        logger.info(f"  [{dim}] 采集 {len(rows)} 条")
        all_rows.extend(rows)

    # 写入 DB
    if all_rows:
        saved = repo.save_batch(all_rows)
        logger.info(f"[save] 写入 {saved} 条")
    else:
        logger.warning("[save] 无数据可写")

    # DB 验证
    print("\n=== DB 验证 ===")
    all_ok = True
    for dim in NEW_DIMS:
        with get_session() as s:
            cnt = s.execute(
                text("SELECT COUNT(*) FROM ft_watchlist_data WHERE code=:c AND data_type=:d"),
                {"c": TEST_CODE, "d": dim}
            ).scalar() or 0
            latest = s.execute(
                text("""SELECT trade_date, data FROM ft_watchlist_data
                        WHERE code=:c AND data_type=:d
                        ORDER BY trade_date DESC LIMIT 1"""),
                {"c": TEST_CODE, "d": dim}
            ).first()

        before = before_counts[dim]
        gained = cnt - before
        if latest:
            td = latest[0]
            d = latest[1]
            keys_str = list(d.keys())[:5] if isinstance(d, dict) else f"list[{len(d)}]"
        else:
            td, keys_str = None, None

        ok = cnt > 0
        status = "[OK]  " if ok else "[FAIL]"
        if not ok:
            all_ok = False
        print(f"{status} {dim:<25} 总计{cnt:>4}条  +{gained:<3} 最新={td}  keys={keys_str}")

    print()
    print("✅ 全部 7 个新维度验证通过" if all_ok else "❌ 有维度验证失败，请检查上方日志")

    await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
