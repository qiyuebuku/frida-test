#!/usr/bin/env python3
"""认证参数管理器 - 持久化 + 自动刷新"""

import json
import base64
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict

AUTH_CACHE_FILE = Path(__file__).parent / "auth_cache.json"
CONFIG_FILE = Path(__file__).parent / "config.json"


class AuthManager:
    """认证参数管理器"""

    def __init__(self, jsbridge_url: str = "http://localhost:18900/auth",
                 adb_device: str = None, adb_path: str = None,
                 enable_auto_refresh: bool = None):
        """
        Args:
            jsbridge_url: JSBridge 服务地址
            adb_device: adb 设备标识（如 "3B15BJ00GZL00000"），None 则从配置文件读取
            adb_path: adb 可执行文件路径（如 "/mnt/d/.../adb.exe"），None 则从配置文件读取
            enable_auto_refresh: 是否启用自动刷新，None 则从配置文件读取
        """
        # 加载配置文件
        config = self._load_config()

        self.jsbridge_url = jsbridge_url
        self.cache_file = AUTH_CACHE_FILE
        self.adb_device = adb_device or config.get("adb_device")
        self.adb_path = adb_path or config.get("adb_path", "adb")
        self.enable_auto_refresh = enable_auto_refresh if enable_auto_refresh is not None else config.get("enable_auto_refresh", False)
        self.app_package = config.get("app_package", "com.hexin.plat.android")

    def _load_config(self) -> Dict:
        """加载配置文件"""
        if not CONFIG_FILE.exists():
            return {}

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"警告: 加载配置文件失败: {e}")
            return {}

    def get_auth(self, force_refresh: bool = False) -> Optional[Dict]:
        """获取认证参数（优先使用缓存，过期或强制刷新时从 JSBridge 获取）

        Args:
            force_refresh: 强制刷新（忽略缓存）

        Returns:
            {"key1", "key2", "key3", "key4", "key5", "userId", "sessionId"}
            或 None（获取失败）
        """
        if not force_refresh:
            # 尝试从缓存读取
            cached = self._load_cache()
            if cached and not self._is_expired(cached):
                # 检查是否即将过期（提前 3 天），且启用了自动刷新
                if self.enable_auto_refresh and self._is_expired(cached, buffer_days=3):
                    print("⚠️ 认证参数将在 3 天内过期，尝试自动刷新...")
                    if self.auto_refresh_auth(self.adb_device):
                        # 刷新成功，重新加载缓存
                        cached = self._load_cache()
                        if cached:
                            return cached["auth"]
                    else:
                        print("⚠️ 自动刷新失败，继续使用当前缓存")

                return cached["auth"]

        # 缓存无效或强制刷新
        # 如果启用自动刷新，先尝试自动打开 app
        if self.enable_auto_refresh and not force_refresh:
            if self.auto_refresh_auth(self.adb_device):
                cached = self._load_cache()
                if cached:
                    return cached["auth"]

        # 直接从 JSBridge 获取（需要 app 已经在交易页面）
        return self._refresh_from_jsbridge()

    def _load_cache(self) -> Optional[Dict]:
        """从文件加载缓存"""
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"警告: 加载认证缓存失败: {e}")
            return None

    def _save_cache(self, auth: Dict):
        """保存认证参数到文件"""
        try:
            # 解析 key5 获取过期时间
            exp_time = self._parse_key5_exp(auth.get("key5", ""))

            data = {
                "auth": auth,
                "cached_at": int(time.time()),
                "expires_at": exp_time,
            }

            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"✅ 认证参数已缓存到 {self.cache_file}")
            if exp_time:
                exp_dt = datetime.fromtimestamp(exp_time)
                print(f"   过期时间: {exp_dt} (剩余 {(exp_dt - datetime.now()).days} 天)")
        except Exception as e:
            print(f"警告: 保存认证缓存失败: {e}")

    def _parse_key5_exp(self, key5: str) -> Optional[int]:
        """解析 key5 token 的过期时间（秒级时间戳）"""
        if not key5 or "." not in key5:
            return None

        try:
            # JWT 格式: header.payload
            header_b64 = key5.split('.')[0]
            header = json.loads(base64.b64decode(header_b64))
            exp_ms = header.get("exp")

            if exp_ms:
                return int(exp_ms / 1000)
        except Exception as e:
            print(f"警告: 解析 key5 过期时间失败: {e}")

        return None

    def _is_expired(self, cached_data: Dict, buffer_days: int = 1) -> bool:
        """检查缓存是否过期

        Args:
            cached_data: 缓存数据
            buffer_days: 提前刷新的天数（默认 1 天）
        """
        exp_time = cached_data.get("expires_at")
        if not exp_time:
            # 无过期时间，保守起见认为已过期
            return True

        now = int(time.time())
        # 提前 N 天刷新（避免临界状态）
        buffer_seconds = buffer_days * 24 * 3600

        if now + buffer_seconds >= exp_time:
            return True

        return False

    def is_expiring_soon(self, days: int = 3) -> bool:
        """检查认证是否即将过期（提前 N 天预警）"""
        cached = self._load_cache()
        if not cached:
            return True
        return self._is_expired(cached, buffer_days=days)

    def _refresh_from_jsbridge(self) -> Optional[Dict]:
        """从 JSBridge 刷新认证参数"""
        try:
            import requests
            resp = requests.get(self.jsbridge_url, timeout=5)
            data = resp.json()

            if data.get("available") and data.get("key5"):
                auth = {
                    "key1": data.get("key1", ""),
                    "key2": data.get("key2", ""),
                    "key3": data.get("key3", ""),
                    "key4": data.get("key4", ""),
                    "key5": data.get("key5", ""),
                    "userId": data.get("userId", ""),
                    "sessionId": data.get("sessionId", ""),
                }

                # 保存到缓存
                self._save_cache(auth)
                return auth
            else:
                print(f"⚠️ JSBridge 不可用: {data}")
                return None
        except Exception as e:
            print(f"⚠️ 从 JSBridge 刷新认证失败: {e}")
            return None

    def auto_refresh_auth(self, adb_device: str = None, app_package: str = None) -> bool:
        """自动刷新认证参数（通过 adb 控制手机打开 app）

        Args:
            adb_device: adb 设备标识（如 "3B15BJ00GZL00000"），None 则使用 self.adb_device
            app_package: 同花顺 app 包名，None 则使用 self.app_package

        Returns:
            True=刷新成功，False=失败
        """
        import subprocess
        import time as time_module

        print("🔄 开始自动刷新认证参数...")

        # 使用参数或实例变量中的值
        device = adb_device or self.adb_device
        package = app_package or self.app_package

        # 构建 adb 命令前缀
        adb_prefix = [self.adb_path]
        if device:
            adb_prefix.extend(["-s", device])

        try:
            # Step 1: 检查 adb 连接
            result = subprocess.run(
                adb_prefix + ["devices"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0 or "device" not in result.stdout:
                print("❌ adb 设备未连接，请检查手机连接")
                return False
            print("✅ adb 设备已连接")

            # Step 2: 检查 JSBridge 是否可用（如果已经可用，直接刷新）
            try:
                import requests
                resp = requests.get(self.jsbridge_url, timeout=2)
                if resp.json().get("available"):
                    print("✅ WebView 已在交易页面，直接刷新")
                    auth = self._refresh_from_jsbridge()
                    return auth is not None
            except:
                pass

            # Step 3: 启动同花顺 app（只启动，不需要导航）
            print("📱 正在启动同花顺 app...")
            subprocess.run(
                adb_prefix + ["shell", "am", "start", "-n", f"{package}/.main.MainActivity"],
                capture_output=True,
                timeout=10
            )
            print("⏳ 等待 app 启动并发起后台请求...")
            time_module.sleep(5)  # 等待 app 启动并发起 API 请求

            # Step 4: 等待认证参数被捕获（OkHttp Interceptor 会自动捕获）
            print("⏳ 等待 OkHttp Interceptor 捕获认证参数...")
            max_retries = 12  # 最多等待 24 秒
            for i in range(max_retries):
                try:
                    import requests
                    resp = requests.get(self.jsbridge_url, timeout=2)
                    data = resp.json()

                    # 检查是否已经捕获到完整的认证参数
                    has_key5 = data.get("key5") and len(data.get("key5", "")) > 10
                    has_key1 = data.get("key1") and len(data.get("key1", "")) > 0

                    if has_key5:
                        if has_key1:
                            # 完整参数已捕获
                            print("✅ 完整认证参数已捕获 (key1-key5)")
                            auth = self._refresh_from_jsbridge()
                            if auth:
                                print("✅ 认证参数刷新成功！")
                                return True
                        elif i >= 6:  # 等待至少 12 秒后，如果有 key5 就够了
                            print("⚠️ 仅捕获到 key5，key1-key3 将在后续请求中自动补充")
                            auth = self._refresh_from_jsbridge()
                            if auth and auth.get("key5"):
                                print("✅ 认证参数刷新成功（仅 key5）")
                                return True
                except:
                    pass

                if i < max_retries - 1:
                    print(f"⏳ 等待 HTTP 请求触发... ({i+1}/{max_retries})")
                    time_module.sleep(2)

            print("❌ 等待超时：未能捕获到认证参数")
            return False

        except subprocess.TimeoutExpired:
            print("❌ adb 命令超时")
            return False
        except Exception as e:
            print(f"❌ 自动刷新失败: {e}")
            return False

    def check_status(self) -> Dict:
        """检查认证状态"""
        cached = self._load_cache()

        if not cached:
            return {
                "status": "no_cache",
                "message": "无缓存，需要从 JSBridge 获取",
            }

        is_expired = self._is_expired(cached)
        exp_time = cached.get("expires_at")
        cached_at = cached.get("cached_at")

        if exp_time:
            exp_dt = datetime.fromtimestamp(exp_time)
            remaining_days = (exp_dt - datetime.now()).days
        else:
            exp_dt = None
            remaining_days = None

        if cached_at:
            cached_dt = datetime.fromtimestamp(cached_at)
        else:
            cached_dt = None

        return {
            "status": "expired" if is_expired else "valid",
            "cached_at": str(cached_dt) if cached_dt else None,
            "expires_at": str(exp_dt) if exp_dt else None,
            "remaining_days": remaining_days,
            "is_expired": is_expired,
        }


# CLI 工具
if __name__ == "__main__":
    import sys

    # 解析 --device 参数
    adb_device = None
    if "--device" in sys.argv:
        idx = sys.argv.index("--device")
        if idx + 1 < len(sys.argv):
            adb_device = sys.argv[idx + 1]
            sys.argv.pop(idx)  # 移除 --device
            sys.argv.pop(idx)  # 移除 device 值

    # 解析 --adb-path 参数
    adb_path = None
    if "--adb-path" in sys.argv:
        idx = sys.argv.index("--adb-path")
        if idx + 1 < len(sys.argv):
            adb_path = sys.argv[idx + 1]
            sys.argv.pop(idx)  # 移除 --adb-path
            sys.argv.pop(idx)  # 移除 path 值

    manager = AuthManager(adb_device=adb_device, adb_path=adb_path, enable_auto_refresh=True)

    if len(sys.argv) < 2:
        print("用法:")
        print("  python auth_manager.py status              - 查看认证状态")
        print("  python auth_manager.py refresh             - 强制刷新认证（需要 app 在交易页面）")
        print("  python auth_manager.py auto-refresh        - 自动刷新（自动打开 app）")
        print("  python auth_manager.py get                 - 获取认证参数")
        print("\n选项:")
        print("  --device <adb_device>                      - 指定 adb 设备（如 3B15BJ00GZL00000）")
        print("  --adb-path <path>                          - 指定 adb 可执行文件路径")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status = manager.check_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif cmd == "refresh":
        print("正在从 JSBridge 刷新认证参数...")
        auth = manager.get_auth(force_refresh=True)
        if auth:
            print("✅ 刷新成功")
        else:
            print("❌ 刷新失败，请确保同花顺 App 已打开并在交易页面")
            sys.exit(1)

    elif cmd == "auto-refresh":
        print("正在自动刷新认证参数（将自动打开 app）...")
        if manager.auto_refresh_auth(adb_device):
            print("✅ 自动刷新成功")
        else:
            print("❌ 自动刷新失败")
            sys.exit(1)

    elif cmd == "get":
        auth = manager.get_auth()
        if auth:
            print(json.dumps(auth, indent=2, ensure_ascii=False))
        else:
            print("❌ 获取认证失败")
            sys.exit(1)

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
