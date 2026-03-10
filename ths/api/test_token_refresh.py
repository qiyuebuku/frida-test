#!/usr/bin/env python3
"""测试 Token 自动刷新功能（降级策略）"""

from ths_fund_client import THSFundClient


def test_fetch_from_zygisk():
    """测试从 Zygisk 获取 token"""
    print("=" * 70)
    print("测试 1: 从 Zygisk 获取 token")
    print("=" * 70)

    client = THSFundClient(use_jsbridge=False)

    token_data = client._fetch_token_from_zygisk()

    if token_data:
        print("✅ 成功从 Zygisk 获取 token")
        print(f"   账户: {token_data['key3']}")
        print(f"   key5 前50位: {token_data['key5'][:50]}...")
    else:
        print("⚠️ Zygisk 未捕获到 token（可能 App 未运行或未在基金页面）")

    print()


def test_login_by_password():
    """测试密码登录"""
    print("=" * 70)
    print("测试 2: 密码登录")
    print("=" * 70)

    client = THSFundClient(use_jsbridge=False)

    token_data = client._login_by_password()

    if token_data:
        print("✅ 密码登录成功（⚠️ 会踢掉手机端）")
        print(f"   账户: {token_data['key3']}")
        print(f"   key5 前50位: {token_data['key5'][:50]}...")
    else:
        print("❌ 密码登录失败")

    print()


def test_auto_refresh():
    """测试自动刷新（降级策略）"""
    print("=" * 70)
    print("测试 3: 自动刷新（降级策略：Zygisk 优先 → 密码登录兜底）")
    print("=" * 70)

    client = THSFundClient(use_jsbridge=False)

    print("触发自动刷新...")
    client._auto_refresh_token_sync()

    print()


def test_strategy_explanation():
    """说明降级策略"""
    print("=" * 70)
    print("降级策略说明")
    print("=" * 70)
    print()
    print("优先级 1: 从 Zygisk 获取 token")
    print("  ✅ 优点: 不会踢掉手机端")
    print("  ❌ 缺点: 需要手机连接 + Zygisk 已捕获 token")
    print()
    print("优先级 2: 使用密码登录")
    print("  ✅ 优点: 完全自动化，无需手机")
    print("  ❌ 缺点: 会踢掉手机端（单点登录限制）")
    print()
    print("工作流程:")
    print("  1. 尝试从 Zygisk 获取")
    print("  2. 如果失败，使用密码登录")
    print("  3. 两者都失败，继续使用当前 token")
    print()


if __name__ == "__main__":
    print("🚀 开始测试 Token 刷新功能\n")

    # 测试 1: Zygisk
    test_fetch_from_zygisk()

    # 测试 2: 密码登录
    test_login_by_password()

    # 测试 3: 自动刷新
    test_auto_refresh()

    # 说明降级策略
    test_strategy_explanation()

    print("=" * 70)
    print("✅ 测试完成")
    print("=" * 70)
