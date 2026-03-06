#!/usr/bin/env python3
"""
健康检查模块：检测和修复交易系统的连通性问题
"""
import subprocess
import requests
import sys
import json
import time

class HealthChecker:
    def __init__(self):
        self.adb_path = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
        self.device_id = "3B15BJ00GZL00000"
        self.trade_port = 18900
        self.data_port = 8900

    def check_account_status(self):
        """检查同花顺账号状态"""
        try:
            response = requests.get(
                f"http://127.0.0.1:{self.data_port}/api/trade/positions",
                timeout=5,
                proxies={"http": None, "https": None}
            )
            data = response.json()

            if data.get("code") == "LT99":
                return {
                    "status": "error",
                    "code": "LT99",
                    "message": data.get("message", "账号异常"),
                    "suggestion": "账号在其他设备登录，请：\n1. 在其他设备退出登录\n2. 在本设备重新登录同花顺\n3. 重新运行此脚本"
                }

            if response.status_code == 200 and "listData" in data:
                return {"status": "ok", "message": "账号状态正常"}

            return {"status": "error", "message": f"未知响应: {data}"}

        except requests.exceptions.ConnectionError:
            return {
                "status": "error",
                "message": "无法连接到数据服务(8900端口)",
                "suggestion": "请检查server.py是否运行"
            }
        except Exception as e:
            return {"status": "error", "message": f"账号检查失败: {str(e)}"}

    def check_port_forward(self):
        """检查adb端口转发"""
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.device_id, "forward", "--list"],
                capture_output=True,
                text=True,
                timeout=5
            )

            has_trade_port = f"tcp:{self.trade_port}" in result.stdout
            has_data_port = f"tcp:{self.data_port}" in result.stdout

            return {
                "trade_port": has_trade_port,
                "data_port": has_data_port,
                "all_ok": has_trade_port and has_data_port
            }
        except Exception as e:
            return {
                "trade_port": False,
                "data_port": False,
                "all_ok": False,
                "error": str(e)
            }

    def fix_port_forward(self):
        """修复端口转发"""
        try:
            # 设置交易端口
            subprocess.run(
                [self.adb_path, "-s", self.device_id, "forward",
                 f"tcp:{self.trade_port}", f"tcp:{self.trade_port}"],
                capture_output=True,
                timeout=5
            )

            # 设置数据端口
            subprocess.run(
                [self.adb_path, "-s", self.device_id, "forward",
                 f"tcp:{self.data_port}", f"tcp:{self.data_port}"],
                capture_output=True,
                timeout=5
            )

            # 验证
            time.sleep(1)
            return self.check_port_forward()

        except Exception as e:
            return {"all_ok": False, "error": str(e)}

    def check_trade_proxy(self):
        """检查交易代理连通性"""
        try:
            response = requests.get(
                f"http://127.0.0.1:{self.trade_port}/",
                timeout=5,
                proxies={"http": None, "https": None}
            )
            # 即使返回error也说明代理在运行
            return {"status": "ok", "message": "交易代理正常"}
        except requests.exceptions.ConnectionError:
            return {
                "status": "error",
                "message": "交易代理(18900)不可达",
                "suggestion": "请检查：\n1. 同花顺App是否打开\n2. Frida Hook是否加载\n3. adb端口转发是否正常"
            }
        except Exception as e:
            return {"status": "error", "message": f"代理检查失败: {str(e)}"}

    def run_full_check(self, auto_fix=True):
        """执行完整健康检查"""
        print("=" * 60)
        print("基金交易系统健康检查")
        print("=" * 60)

        results = {}

        # 1. 检查端口转发
        print("\n[1/3] 检查adb端口转发...")
        port_status = self.check_port_forward()
        results["port_forward"] = port_status

        if not port_status["all_ok"]:
            print(f"  ❌ 端口转发异常")
            print(f"     交易端口({self.trade_port}): {'✓' if port_status.get('trade_port') else '✗'}")
            print(f"     数据端口({self.data_port}): {'✓' if port_status.get('data_port') else '✗'}")

            if auto_fix:
                print("  🔧 尝试自动修复...")
                fix_result = self.fix_port_forward()
                if fix_result["all_ok"]:
                    print("  ✅ 端口转发已修复")
                    results["port_forward"] = fix_result
                else:
                    print(f"  ❌ 修复失败: {fix_result.get('error', '未知错误')}")
        else:
            print("  ✅ 端口转发正常")

        # 2. 检查交易代理
        print("\n[2/3] 检查交易代理(18900)...")
        proxy_status = self.check_trade_proxy()
        results["trade_proxy"] = proxy_status

        if proxy_status["status"] == "ok":
            print("  ✅ 交易代理正常")
        else:
            print(f"  ❌ {proxy_status['message']}")
            if "suggestion" in proxy_status:
                print(f"  💡 {proxy_status['suggestion']}")

        # 3. 检查账号状态
        print("\n[3/3] 检查账号状态...")
        account_status = self.check_account_status()
        results["account"] = account_status

        if account_status["status"] == "ok":
            print("  ✅ 账号状态正常")
        else:
            print(f"  ❌ {account_status['message']}")
            if "suggestion" in account_status:
                print(f"  💡 {account_status['suggestion']}")

        # 总结
        print("\n" + "=" * 60)
        all_ok = (
            results["port_forward"]["all_ok"] and
            results["trade_proxy"]["status"] == "ok" and
            results["account"]["status"] == "ok"
        )

        if all_ok:
            print("✅ 所有检查通过，系统就绪！")
            return {"status": "ok", "details": results}
        else:
            print("❌ 发现问题，请根据上述建议修复")
            return {"status": "error", "details": results}

def main():
    import argparse
    parser = argparse.ArgumentParser(description="基金交易系统健康检查")
    parser.add_argument("--no-fix", action="store_true", help="不自动修复问题")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    args = parser.parse_args()

    checker = HealthChecker()
    result = checker.run_full_check(auto_fix=not args.no_fix)

    if args.json:
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))

    sys.exit(0 if result["status"] == "ok" else 1)

if __name__ == "__main__":
    main()
