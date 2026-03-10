#!/usr/bin/env python3
"""测试直接 HTTP 请求（不依赖 WebView）"""
import asyncio
from ths_fund_client import THSFundClient

async def test_orders():
    # 使用直接 HTTP 模式（use_jsbridge=False）
    client = THSFundClient(use_jsbridge=False)
    
    try:
        # 测试查询订单
        result = await client.get_order_list()
        print("订单查询成功:")
        print(f"  订单数: {len(result.get('singleData', {}).get('data', []))}")
        return True
    except Exception as e:
        print(f"订单查询失败: {e}")
        return False
    finally:
        await client.close()

if __name__ == "__main__":
    success = asyncio.run(test_orders())
    exit(0 if success else 1)
