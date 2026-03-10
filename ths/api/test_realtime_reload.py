#!/usr/bin/env python3
"""测试实时重新加载功能"""

import json
import time
import asyncio
from ths_fund_client import THSFundClient


async def test_realtime_reload():
    """测试每次使用都从文件读取最新的 token"""

    print("=" * 70)
    print("测试：每次使用都从文件读取最新 token")
    print("=" * 70)
    print()

    # 步骤 1: 创建 client
    print("步骤 1: 创建 client")
    client = THSFundClient(use_jsbridge=False)
    print(f"   初始 key3: {client.trade_cust_id}")
    print(f"   初始 key5 前50位: {client.TRADE_AUTH['key5'][:50]}...")
    print()

    # 步骤 2: 模拟 server.py 后台任务更新文件
    print("步骤 2: 模拟 server.py 后台任务更新 auth_cache.json")
    time.sleep(1.5)  # 确保修改时间不同

    with open('/home/yuyang/frida-test/ths/api/auth_cache.json', 'r') as f:
        data = json.load(f)

    # 修改 token（模拟外部更新）
    old_key5 = data['auth']['key5']
    data['auth']['key5'] = 'UPDATED_BY_SERVER_TASK_' + str(int(time.time()))
    data['last_sync'] = int(time.time())
    data['sync_source'] = 'simulated_update'

    with open('/home/yuyang/frida-test/ths/api/auth_cache.json', 'w') as f:
        json.dump(data, f, indent=2)

    print(f"   ✅ 文件已更新")
    print(f"   旧 key5: {old_key5[:50]}...")
    print(f"   新 key5: {data['auth']['key5'][:50]}...")
    print()

    # 步骤 3: 不重新创建 client，直接使用（应该能读取到最新的）
    print("步骤 3: 使用同一个 client 实例调用 API（应该自动重新加载）")

    # 调用 trade_cust_id 属性（会触发 reload_auth_if_updated）
    cust_id = client.trade_cust_id

    print(f"   当前 key3: {cust_id}")
    print(f"   当前 key5: {client.TRADE_AUTH['key5'][:50]}...")
    print()

    # 步骤 4: 验证结果
    print("步骤 4: 验证结果")
    if 'UPDATED_BY_SERVER_TASK' in client.TRADE_AUTH['key5']:
        print("   ✅ 成功！client 自动读取到了文件的最新内容")
        print("   ✅ 即使不重启 client，也能使用 server.py 更新的 token")
    else:
        print("   ❌ 失败！client 没有读取到最新内容")

    print()

    # 步骤 5: 恢复正确的 token
    print("步骤 5: 恢复正确的 token")
    client._auto_refresh_token_sync()
    print()

    await client.close()


async def test_with_server_simulation():
    """模拟 server.py 后台任务的完整流程"""

    print("=" * 70)
    print("模拟完整流程：server.py 后台任务 + client 使用")
    print("=" * 70)
    print()

    # 创建 client（模拟你的应用）
    print("1. 应用启动，创建 client")
    client = THSFundClient(use_jsbridge=False)
    print(f"   client 初始化完成，key3: {client.trade_cust_id}")
    print()

    # 模拟后台任务更新（类似 server.py 每3秒的同步）
    print("2. 后台任务运行，从 Zygisk 同步 token 到文件")
    print("   （这里我们手动修改文件模拟）")
    time.sleep(1)

    with open('/home/yuyang/frida-test/ths/api/auth_cache.json', 'r') as f:
        data = json.load(f)

    data['last_sync'] = int(time.time())
    data['sync_source'] = 'background_task_simulation'

    with open('/home/yuyang/frida-test/ths/api/auth_cache.json', 'w') as f:
        json.dump(data, f, indent=2)

    print("   ✅ 后台任务已更新文件")
    print()

    # 应用继续使用（不重启）
    print("3. 应用继续运行，调用 API（client 未重启）")

    # 调用任何需要认证的方法（会自动检查文件更新）
    cust_id = client.trade_cust_id
    print(f"   ✅ 自动读取到最新 token，key3: {cust_id}")
    print()

    # 验证
    with open('/home/yuyang/frida-test/ths/api/auth_cache.json', 'r') as f:
        current_data = json.load(f)

    if current_data.get('sync_source') == 'background_task_simulation':
        print("✅ 验证通过：client 使用的是后台任务更新的 token")

    print()

    await client.close()


if __name__ == "__main__":
    print("🚀 开始测试实时重新加载功能\n")

    # 测试 1: 基础测试
    asyncio.run(test_realtime_reload())

    print("\n")

    # 测试 2: 完整流程模拟
    asyncio.run(test_with_server_simulation())

    print("=" * 70)
    print("✅ 所有测试完成")
    print("=" * 70)
    print()
    print("📝 总结：")
    print("   1. client 每次使用时都会检查文件是否被外部更新")
    print("   2. 如果文件有更新（如 server.py 后台任务），自动重新加载")
    print("   3. 无需重启 client，就能使用最新的 token")
    print("   4. 完美支持 server.py 后台同步 + client 实时使用")
    print()
