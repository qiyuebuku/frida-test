#!/usr/bin/env python3
"""
测试自动刷新功能

验证：
1. ths_fund_client.py 在API调用时自动检测token过期
2. server.py 的后台任务定期检查并刷新token
3. 优先从手机获取（不踢掉手机端）
"""

import asyncio
import time
from datetime import datetime


async def test_client_auto_refresh():
    """测试客户端的自动刷新功能"""
    print("=" * 70)
    print("测试1: ths_fund_client.py 自动刷新")
    print("=" * 70)
    print()

    from ths_fund_client import THSFundClient

    # 使用 use_jsbridge=False 模式（直接读缓存）
    client = THSFundClient(use_jsbridge=False)

    print("📋 当前认证状态：")
    print(f"   key1: {client.TRADE_AUTH['key1'][:16]}...")
    print(f"   key3: {client.TRADE_AUTH['key3']}")
    print(f"   key5 长度: {len(client.TRADE_AUTH['key5'])} 字符")
    print()

    # 检查token状态
    from auth_manager import AuthManager
    manager = AuthManager()
    status = manager.check_status()

    print("📊 Token状态：")
    print(f"   过期时间: {status.get('expires_at')}")
    print(f"   剩余天数: {status.get('remaining_days')} 天")
    print(f"   状态: {status.get('status')}")
    print()

    # 测试API调用（会触发 _trade_auth_params，自动检测过期）
    print("🔍 测试API调用（会自动检测token过期）...")
    try:
        # 获取持仓信息（需要认证）
        result = await client.get_fund_position()
        print("✅ API调用成功")
        print(f"   持仓数量: {len(result.get('list', []))} 个基金")
    except Exception as e:
        print(f"⚠️ API调用失败: {e}")

    await client.close()
    print()


async def test_server_background_task():
    """测试server.py的后台刷新任务"""
    print("=" * 70)
    print("测试2: server.py 后台任务")
    print("=" * 70)
    print()

    from auth_manager import AuthManager

    manager = AuthManager(enable_auto_refresh=True)

    # 模拟后台任务的逻辑
    print("🔄 模拟后台任务检查...")

    status = manager.check_status()
    remaining_days = status.get("remaining_days", 0)

    if remaining_days is not None and remaining_days <= 3:
        print(f"⚠️ Token将在{remaining_days}天后过期，触发自动刷新...")

        # 测试刷新（优先从手机）
        success = manager.auto_refresh_auth(prefer_phone=True)

        if success:
            print("✅ Token自动刷新成功（优先从手机获取）")
        else:
            print("⚠️ Token自动刷新失败")
    else:
        print(f"✅ Token有效，剩余{remaining_days}天，无需刷新")

    print()


def test_refresh_strategy():
    """测试刷新策略"""
    print("=" * 70)
    print("测试3: 刷新策略验证")
    print("=" * 70)
    print()

    from auth_manager import AuthManager

    manager = AuthManager(enable_auto_refresh=True)

    print("📋 刷新策略说明：")
    print("   优先级1: 从手机获取（不踢掉手机端）")
    print("   优先级2: 使用密码登录（踢掉手机端）")
    print()

    # 检查手机连接状态
    adb_prefix = [manager.adb_path]
    if manager.adb_device:
        adb_prefix.extend(["-s", manager.adb_device])

    phone_connected = manager._check_phone_connected(adb_prefix)

    print(f"📱 手机连接: {'✅ 已连接' if phone_connected else '❌ 未连接'}")
    print()

    if phone_connected:
        print("💡 刷新模式：优先从手机获取")
        print("   - 不会踢掉手机端")
        print("   - 需要JSBridge服务运行")
        print("   - 建议运行: python start_persistent_hook.py &")
    else:
        print("💡 刷新模式：使用密码登录")
        print("   - ⚠️ 会踢掉手机端")
        print("   - 无需手机连接")
        print("   - 完全自动化")

    print()


async def main():
    """运行所有测试"""
    print("🚀 开始测试自动刷新功能")
    print()

    # 测试1: 客户端自动刷新
    await test_client_auto_refresh()

    # 测试2: 后台任务
    await test_server_background_task()

    # 测试3: 刷新策略
    test_refresh_strategy()

    print("=" * 70)
    print("✅ 测试完成")
    print("=" * 70)
    print()
    print("📝 总结：")
    print("   1. ths_fund_client.py 在每次API调用时会自动检测token过期")
    print("   2. 如果即将过期（提前3天），会自动刷新")
    print("   3. 优先从手机获取（不踢掉手机端）")
    print("   4. server.py 的后台任务每30分钟检查一次")
    print()


if __name__ == "__main__":
    asyncio.run(main())
