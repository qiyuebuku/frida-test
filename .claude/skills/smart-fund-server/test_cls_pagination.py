#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLS 分页测试：验证 page 2 为空问题
运行：PYTHONPATH=. python test_cls_pagination.py
"""

import os
import asyncio
from datetime import datetime
from src.infrastructure.clients.cls import CLSClient

# 清除代理环境变量
for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(key, None)

async def test_pagination_without_cache():
    """测试基础分页（绕过缓存）"""
    print("=" * 70)
    print("CLS 分页测试：获取 Page 1 和 Page 2")
    print("=" * 70)
    
    # 创建客户端（禁用缓存）
    client = CLSClient()
    
    try:
        # Page 1: 获取前 10 条
        print("\n[Page 1] 获取前 10 条快讯...")
        page1 = await client.get_telegraph_list(rn=10)
        print(f"  ✓ 返回 {len(page1)} 条")
        
        if len(page1) == 0:
            print("  ✗ ERROR: Page 1 为空，无法测试分页")
            return
        
        # 打印 Page 1 的时间戳
        print("\n  Page 1 项目（按 ctime 降序）:")
        for i, item in enumerate(page1, 1):
            ctime = item.get("ctime")
            title = item.get("title", "")[:50]
            print(f"    {i:2d}. [ctime={ctime}] {title}")
        
        # 获取最后一条的 ctime 作为翻页游标
        last_ctime = page1[-1].get("ctime")
        print(f"\n  最后一条 ctime: {last_ctime}")
        if last_ctime is None:
            print("  ✗ ERROR: 无法获取 ctime，无法翻页")
            return
        
        # Page 2: 使用 last_time 翻页
        print(f"\n[Page 2] 使用 last_time={last_ctime} 获取下一页...")
        page2 = await client.get_telegraph_list(rn=10, last_time=last_ctime)
        print(f"  ✓ 返回 {len(page2)} 条")
        
        if len(page2) == 0:
            print("  ✗ PROBLEM: Page 2 为空！")
            print("\n  可能原因：")
            print("    1. CLS API 实际上只有 10 条数据")
            print("    2. last_time 参数格式错误")
            print("    3. CLS API 不支持该翻页方式")
        else:
            # 打印 Page 2 的时间戳
            print("\n  Page 2 项目（按 ctime 降序）:")
            for i, item in enumerate(page2, 1):
                ctime = item.get("ctime")
                title = item.get("title", "")[:50]
                print(f"    {i:2d}. [ctime={ctime}] {title}")
            
            # 验证数据连贯性
            print(f"\n  时间戳验证:")
            page1_min = min(item.get("ctime", 0) for item in page1)
            page2_max = max(item.get("ctime", 0) for item in page2)
            print(f"    Page 1 最小 ctime: {page1_min}")
            print(f"    Page 2 最大 ctime: {page2_max}")
            if page1_min > page2_max:
                print(f"    ✓ 时间递减正确（Page 1 较新）")
            else:
                print(f"    ✗ 时间顺序异常（Page 2 不应该有比 Page 1 更新的数据）")
        
        print("\n" + "=" * 70)
        
    finally:
        await client.close()

async def test_cache_behavior():
    """测试缓存行为"""
    print("\n" + "=" * 70)
    print("缓存测试：相同参数是否返回缓存")
    print("=" * 70)
    
    client = CLSClient()
    
    try:
        # 第一次调用
        print("\n[Call 1] get_telegraph_list(rn=10)...")
        page1_call1 = await client.get_telegraph_list(rn=10)
        print(f"  返回 {len(page1_call1)} 条")
        first_id = page1_call1[0].get("id") if page1_call1 else None
        
        # 立即再调用一次（应该命中缓存）
        print("[Call 2] 立即再次 get_telegraph_list(rn=10)...")
        page1_call2 = await client.get_telegraph_list(rn=10)
        print(f"  返回 {len(page1_call2)} 条")
        second_id = page1_call2[0].get("id") if page1_call2 else None
        
        if first_id == second_id:
            print(f"  ✓ 两次返回相同的数据（可能来自缓存）")
        else:
            print(f"  ✗ 返回不同的数据")
        
        print("\n" + "=" * 70)
        
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_pagination_without_cache())
    asyncio.run(test_cache_behavior())
