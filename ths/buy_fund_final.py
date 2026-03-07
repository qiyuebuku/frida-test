#!/usr/bin/env python3
"""
同花顺基金买入 - 最终可用版本

通过 JSBridge 转发实现真实买入功能

前提条件：
1. 同花顺 App 已启动
2. 已打开任意基金页面（WebView 已初始化）
3. 代理服务器运行在 localhost:18900
"""

import hashlib
import json
import requests
import time
from typing import Dict, Any, Optional

class THSFundTrader:
    """同花顺基金交易客户端（通过 JSBridge）"""

    def __init__(self, cust_id: str, user_id: str, proxy_port: int = 18900):
        """
        初始化交易客户端

        Args:
            cust_id: 客户ID（例如：100113970166）
            user_id: 用户ID（例如：690359103）
            proxy_port: 代理服务器端口（默认：18900）
        """
        self.cust_id = cust_id
        self.user_id = user_id
        self.proxy_url = f"http://localhost:{proxy_port}"
        self.session = requests.Session()

    @staticmethod
    def encrypt_password(password: str) -> str:
        """
        加密交易密码（MD5）

        Args:
            password: 明文密码（6位数字）

        Returns:
            加密后的密码（32位大写MD5）
        """
        return hashlib.md5(password.encode()).hexdigest().upper()

    def _call_jsbridge(self, handler: str, data: Dict, timeout: int = 35) -> Dict[str, Any]:
        """
        通过 JSBridge 调用 API

        Args:
            handler: JSBridge handler 名称（通常是 "clientRequestHX"）
            data: 请求数据
            timeout: 超时时间（秒）

        Returns:
            API 响应数据
        """
        request_data = {
            "handler": handler,
            "data": data
        }

        try:
            response = self.session.post(
                f"{self.proxy_url}/jsbridge",
                json=request_data,
                timeout=timeout
            )
            response.raise_for_status()

            # 解析响应（可能是双层 JSON）
            result = response.json()
            if isinstance(result, str):
                result = json.loads(result)

            if not result.get("success"):
                raise Exception(f"JSBridge 调用失败: {result.get('error')}")

            return result.get("data", {})

        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP 请求失败: {e}")

    def query_positions(self) -> Dict[str, Any]:
        """
        查询基金持仓

        Returns:
            持仓信息
        """
        print(f"\n🔍 查询持仓...")
        data = {
            "method": "GET",
            "url": f"https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/{self.cust_id}",
            "params": {},
            "K5type": "normal"
        }
        return self._call_jsbridge("clientRequestHX", data)

    def init_buy(self, fund_code: str) -> Dict[str, Any]:
        """
        买入初始化（步骤1：获取可用余额和费率）

        Args:
            fund_code: 基金代码（例如：012922）

        Returns:
            初始化信息
        """
        print(f"\n📋 买入初始化（步骤1/3）...")
        data = {
            "method": "GET",
            "url": f"https://trade.5ifund.com/rs/trade/buy/{self.cust_id}/initwithincome2/safeforhand/{fund_code}",
            "params": {},
            "K5type": "normal"
        }
        return self._call_jsbridge("clientRequestHX", data)

    def get_trade_seq(self, fund_code: str) -> Dict[str, Any]:
        """
        获取交易序列号（步骤2：必须在买入前调用）

        Args:
            fund_code: 基金代码

        Returns:
            包含 tradeInfoSeq 和 transactionAccountId
        """
        print(f"\n🔢 获取交易序列号（步骤2/3）...")
        data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/subscribe/init",
            "params": {"fundCode": fund_code},
            "Header": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": self.cust_id,
                "source": "SDK"
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        result = self._call_jsbridge("clientRequestHX", data)

        # 调试：打印完整响应
        print(f"  📝 完整响应: {json.dumps(result, ensure_ascii=False, indent=2)}")

        # 检查响应格式
        if result.get("errorCode") == 0:
            seq_result = result.get("result", {})
            return seq_result
        else:
            raise Exception(f"获取交易序列号失败: {result.get('message')}")

    def submit_buy(self, fund_code: str, amount: float, trade_password: str,
                   trade_info_seq: str, trans_account_id: str) -> Dict[str, Any]:
        """
        提交买入订单（步骤3）

        Args:
            fund_code: 基金代码
            amount: 买入金额（元）
            trade_password: 交易密码（明文）
            trade_info_seq: 交易序列号（从 get_trade_seq 获取）
            trans_account_id: 交易账户ID（从 get_trade_seq 获取）

        Returns:
            买入结果（包含 appSheetSerialNo）
        """
        print(f"\n💸 提交买入订单（步骤3/3）...")

        encrypted_pwd = self.encrypt_password(trade_password)

        data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/buy",
            "params": {
                "buyType": "1",
                "transactionAccountId": trans_account_id,
                "tradePassword": encrypted_pwd,
                "money": f"{amount:.2f}",
                "fundCode": fund_code,
                "useWallet": "1",
                "signFlag": "1",
                "tradeInfoSeq": trade_info_seq,
                "operator": "145",
                "agreementStr": json.dumps([{
                    "title": "同花顺钱包服务协议",
                    "agreementUrl": "https://trade.5ifund.com/fetrade/ifundTradeHelp/protocol/buy.html"
                }])
            },
            "needToken": True,
            "K5type": "none",
            "Header": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            "requestType": "guomiSSL"
        }

        result = self._call_jsbridge("clientRequestHX", data, timeout=40)

        if result.get("errorCode") == 0:
            return result.get("result", {})
        else:
            raise Exception(f"买入失败: {result.get('message')}")

    def buy_fund(self, fund_code: str, amount: float, trade_password: str) -> Dict[str, Any]:
        """
        买入基金（完整流程）

        Args:
            fund_code: 基金代码（例如：012922）
            amount: 买入金额（元）
            trade_password: 交易密码（6位数字，明文）

        Returns:
            买入结果
        """
        print(f"\n{'=' * 60}")
        print(f"💸 买入基金")
        print(f"{'=' * 60}")
        print(f"  基金代码: {fund_code}")
        print(f"  买入金额: {amount} 元")
        print(f"  客户ID: {self.cust_id}")

        try:
            # 步骤1：初始化（获取可用余额等信息）
            init_result = self.init_buy(fund_code)
            print(f"  ✅ 初始化成功")
            print(f"  📝 初始化结果: {json.dumps(init_result, ensure_ascii=False, indent=2)[:500]}...")

            # 步骤2：获取交易序列号
            seq_result = self.get_trade_seq(fund_code)
            trade_info_seq = seq_result.get("tradeInfoSeq")
            trans_account_id = seq_result.get("transactionAccountId")

            if not trade_info_seq or not trans_account_id:
                raise Exception("无法获取交易序列号或账户ID")

            print(f"  ✅ 交易序列号: {trade_info_seq}")
            print(f"  ✅ 交易账户ID: {trans_account_id}")

            # 步骤3：提交买入订单
            buy_result = self.submit_buy(
                fund_code=fund_code,
                amount=amount,
                trade_password=trade_password,
                trade_info_seq=trade_info_seq,
                trans_account_id=trans_account_id
            )

            app_sheet_serial_no = buy_result.get("appSheetSerialNo")
            print(f"\n{'=' * 60}")
            print(f"✅ 买入成功！")
            print(f"{'=' * 60}")
            print(f"  申请单号: {app_sheet_serial_no}")
            print(f"  基金代码: {fund_code}")
            print(f"  买入金额: {amount} 元")
            print(f"{'=' * 60}\n")

            return {
                "success": True,
                "appSheetSerialNo": app_sheet_serial_no,
                "fundCode": fund_code,
                "amount": amount
            }

        except Exception as e:
            print(f"\n{'=' * 60}")
            print(f"❌ 买入失败")
            print(f"{'=' * 60}")
            print(f"  错误: {e}")
            print(f"{'=' * 60}\n")
            return {
                "success": False,
                "error": str(e)
            }


def main():
    """测试主函数"""
    print("=" * 60)
    print("同花顺基金买入 - 最终可用版本")
    print("=" * 60)

    # 配置参数（从日志中提取的真实数据）
    CUST_ID = "100113970166"  # 客户ID
    USER_ID = "690359103"     # 用户ID

    # 创建交易客户端
    trader = THSFundTrader(CUST_ID, USER_ID)

    # 测试1：查询持仓
    try:
        print("\n" + "=" * 60)
        print("测试1：查询持仓")
        print("=" * 60)

        result = trader.query_positions()

        if result.get("errorCode") == 0:
            positions = result.get("result", {}).get("singleData", {}).get("fundGeneral", {}).get("fundPositonCombinedList", [])
            print(f"\n✅ 持仓基金数量: {len(positions)}")

            for i, pos in enumerate(positions[:3], 1):
                print(f"\n  基金 {i}:")
                print(f"    代码: {pos.get('fundCode')}")
                print(f"    名称: {pos.get('fundName')}")
                print(f"    持有份额: {pos.get('holdVol')}")
                print(f"    最新净值: {pos.get('navValue')}")
        else:
            print(f"❌ 查询失败: {result.get('message')}")
    except Exception as e:
        print(f"❌ 查询持仓失败: {e}")

    # 测试2：买入基金（真实交易！）
    print("\n\n" + "=" * 60)
    print("测试2：买入基金")
    print("=" * 60)
    print("\n⚠️⚠️⚠️ 警告：以下操作将执行真实交易！⚠️⚠️⚠️")
    print("如不想执行，请按 Ctrl+C 退出\n")

    # 等待5秒，给用户取消的机会
    for i in range(5, 0, -1):
        print(f"  {i}秒后开始买入...")
        time.sleep(1)

    # 买入参数
    FUND_CODE = "008087"      # 基金代码
    AMOUNT = 100.0            # 买入金额（100元）
    TRADE_PASSWORD = "123456" # 交易密码（请替换为真实密码）

    try:
        result = trader.buy_fund(
            fund_code=FUND_CODE,
            amount=AMOUNT,
            trade_password=TRADE_PASSWORD
        )

        if result.get("success"):
            print(f"🎉 恭喜！买入成功")
        else:
            print(f"💔 很遗憾，买入失败: {result.get('error')}")

    except Exception as e:
        print(f"❌ 买入过程异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
