#!/usr/bin/env python3
"""认证参数自动刷新调度器 - 定期检查并自动刷新"""

import time
from datetime import datetime, timedelta
from auth_manager import AuthManager


def check_and_refresh():
    """检查认证状态，如需刷新则自动执行"""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始检查认证状态...")
    print(f"{'='*60}\n")

    manager = AuthManager()

    # 检查认证状态
    status = manager.check_status()

    if status["status"] == "no_cache":
        print("❌ 无认证缓存，需要刷新")
        refresh_needed = True
    elif status["is_expired"]:
        print(f"❌ 认证已过期（过期时间: {status['expires_at']}），需要刷新")
        refresh_needed = True
    else:
        remaining_days = status.get("remaining_days", 0)
        print(f"✅ 认证状态正常")
        print(f"   缓存时间: {status['cached_at']}")
        print(f"   过期时间: {status['expires_at']}")
        print(f"   剩余天数: {remaining_days} 天")

        # 提前 3 天刷新
        if remaining_days <= 3:
            print(f"⚠️ 剩余天数 ≤ 3 天，提前刷新")
            refresh_needed = True
        else:
            print(f"✅ 无需刷新（剩余 {remaining_days} 天）")
            refresh_needed = False

    # 如果需要刷新，执行自动刷新
    if refresh_needed:
        print("\n🔄 开始自动刷新认证参数...")
        if manager.auto_refresh_auth():
            print("✅ 自动刷新成功！\n")
        else:
            print("❌ 自动刷新失败，请检查手机连接和 app 状态\n")
    else:
        print()


def run_scheduler(check_interval_hours: int = 12):
    """运行调度器

    Args:
        check_interval_hours: 检查间隔（小时），默认 12 小时检查一次
    """
    print(f"🚀 认证参数自动刷新调度器已启动")
    print(f"   检查间隔: 每 {check_interval_hours} 小时")
    print(f"   刷新策略: 剩余天数 ≤ 3 天时自动刷新")
    print(f"   启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 立即执行一次检查
    check_and_refresh()

    # 计算下次检查时间
    interval_seconds = check_interval_hours * 3600
    next_check = datetime.now() + timedelta(seconds=interval_seconds)
    print(f"⏰ 下次检查时间: {next_check.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 持续运行
    try:
        while True:
            time.sleep(interval_seconds)
            check_and_refresh()
            next_check = datetime.now() + timedelta(seconds=interval_seconds)
            print(f"⏰ 下次检查时间: {next_check.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except KeyboardInterrupt:
        print("\n\n👋 调度器已停止")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # 仅执行一次检查
        check_and_refresh()
    else:
        # 运行持续调度器
        interval = int(sys.argv[1]) if len(sys.argv) > 1 else 12
        run_scheduler(check_interval_hours=interval)
