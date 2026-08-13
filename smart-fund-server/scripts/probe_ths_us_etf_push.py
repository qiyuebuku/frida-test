#!/usr/bin/env python3
"""Probe whether one dynamically discovered US ETF board accepts push=1."""

from __future__ import annotations

import asyncio
import json

from src.infrastructure.clients.ths import THSClient


async def main() -> None:
    client = THSClient()
    try:
        response = await client._request_native_unified(
            lane="ranking",
            online_id="probe-us-etf-push",
            protocol_id=1360,
            page_id=2371,
            timeout_seconds=15,
            request_dic=(
                "stockcode=D2F6\r\nsortid=199112\r\nstartrow=0\r\n"
                "rowcount=1\r\nsortorder=0\r\npush=1"
            ),
        )
        print(json.dumps(response, ensure_ascii=False, default=str))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
