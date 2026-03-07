#!/usr/bin/env python3
"""
同花顺基金交易接口测试脚本

功能：
1. 查询账户信息
2. 查询持仓
3. 模拟买入流程（不执行真实交易）

前提条件：
- 同花顺 App 已启动并完成 Hook 注入
- 代理服务器运行在 localhost:18900
"""

import hashlib
import json
import requests
from typing import Dict, Any, Optional

class THSFundTrader:
    """同花顺基金交易客户端"""

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

    def _call_api(self, method: str, url: str, params: Optional[Dict] = None,
                  headers: Optional[Dict] = None, need_token: bool = False,
                  k5_type: str = "normal") -> Dict[str, Any]:
        """
        通过 JSBridge 代理调用 API

        Args:
            method: HTTP 方法（GET/POST）
            url: API URL
            params: 请求参数
            headers: 自定义请求头
            need_token: 是否需要 K5 Token
            k5_type: K5 加密类型（normal/none）

        Returns:
            API 响应数据
        """
        # 构造 JSBridge 请求
        bridge_request = {
            "handlerName": "clientRequestHX",
            "data": {
                "method": method,
                "url": url,
                "params": params or {},
                "K5type": k5_type,
                "needToken": need_token,
                "Header": headers or {}
            }
        }

        # 通过代理发送请求
        try:
            response = self.session.post(
                self.proxy_url,
                json=bridge_request,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ API 调用失败: {e}")
            return {"error": str(e)}

    def query_positions(self) -> Dict[str, Any]:
        """
        查询基金持仓

        Returns:
            持仓信息
        """
        print(f"\n🔍 查询持仓...")
        url = f"https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/{self.cust_id}"
        return self._call_api("GET", url)

    def query_assets(self) -> Dict[str, Any]:
        """
        查询总资产

        Returns:
            资产信息
        """
        print(f"\n💰 查询总资产...")
        url = f"https://trade.5ifund.com/rs/incomequery/queryzcsharemobilehomenine/{self.cust_id}"
        return self._call_api("GET", url)

    def query_wallet(self) -> Dict[str, Any]:
        """
        查询钱包余额

        Returns:
            钱包信息
        """
        print(f"\n💳 查询钱包余额...")
        url = "https://trade.5ifund.com/rz/wallet/dubbo/v1/queryWalletHomePage"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "custId": self.cust_id,
            "source": "SDK"
        }
        params = {"custId": self.cust_id}
        return self._call_api("POST", url, params, headers, need_token=True)

    def init_buy(self, fund_code: str) -> Dict[str, Any]:
        """
        买入初始化（步骤1：获取可用余额和费率）

        Args:
            fund_code: 基金代码（例如：012922）

        Returns:
            初始化信息
        """
        print(f"\n📋 买入初始化（步骤1/3）...")
        url = f"https://trade.5ifund.com/rs/trade/buy/{self.cust_id}/initwithincome2/safeforhand/{fund_code}"
        return self._call_api("GET", url)

    def get_trade_seq(self, fund_code: str) -> Dict[str, Any]:
        """
        获取交易序列号（步骤2：必须在买入前调用）

        Args:
            fund_code: 基金代码

        Returns:
            包含 tradeInfoSeq 和 transactionAccountId
        """
        print(f"\n🔢 获取交易序列号（步骤2/3）...")
        url = "https://trade.5ifund.com/rz/trade/dubbo/subscribe/init"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "custId": self.cust_id,
            "source": "SDK"
        }
        params = {"fundCode": fund_code}
        return self._call_api("POST", url, params, headers, need_token=True)

    def buy_fund(self, fund_code: str, amount: float, trade_password: str,
                 dry_run: bool = True) -> Dict[str, Any]:
        """
        买入基金（完整流程）

        Args:
            fund_code: 基金代码（例如：012922）
            amount: 买入金额（元）
            trade_password: 交易密码（6位数字，明文）
            dry_run: 是否为测试模式（True=不真正提交，False=真实交易）

        Returns:
            买入结果
        """
        print(f"\n{'🧪 [测试模式]' if dry_run else '💸'} 买入基金: {fund_code}, 金额: {amount} 元")

        # 步骤1：初始化
        init_result = self.init_buy(fund_code)
        if "error" in init_result:
            return init_result

        # 步骤2：获取交易序列号
        seq_result = self.get_trade_seq(fund_code)
        if "error" in seq_result:
            return seq_result

        trade_info_seq = seq_result.get("tradeInfoSeq")
        trans_account_id = seq_result.get("transactionAccountId")

        if not trade_info_seq or not trans_account_id:
            return {"error": "无法获取交易序列号或账户ID"}

        print(f"  ✅ 交易序列号: {trade_info_seq}")
        print(f"  ✅ 交易账户ID: {trans_account_id}")

        if dry_run:
            print("\n⚠️  [测试模式] 跳过实际买入提交")
            return {
                "status": "dry_run",
                "message": "测试模式，未提交订单",
                "tradeInfoSeq": trade_info_seq,
                "transactionAccountId": trans_account_id
            }

        # 步骤3：提交买入订单
        print(f"\n💸 提交买入订单（步骤3/3）...")
        url = "https://trade.5ifund.com/rz/trade/dubbo/buy"
        params = {
            "buyType": "1",
            "transactionAccountId": trans_account_id,
            "tradePassword": self.encrypt_password(trade_password),
            "money": f"{amount:.2f}",
            "fundCode": fund_code,
            "useWallet": "1",
            "signFlag": "1",
            "tradeInfoSeq": trade_info_seq,
            "operator": "145",
            "agreementStr": json.dumps([
                {
                    "title": "同花顺钱包服务协议",
                    "agreementUrl": "https://trade.5ifund.com/fetrade/ifundTradeHelp/protocol/buy.html"
                }
            ])
        }

        return self._call_api("POST", url, params, need_token=True)

    def query_buy_result(self, app_sheet_serial_no: str) -> Dict[str, Any]:
        """
        查询买入结果

        Args:
            app_sheet_serial_no: 申请单号（从买入接口返回）

        Returns:
            买入结果详情
        """
        print(f"\n🔍 查询买入结果...")
        url = f"https://trade.5ifund.com/rs/tz/trade/paywithcoupon/{self.cust_id}/result"
        params = {"appSheetSerialNo": app_sheet_serial_no}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        return self._call_api("POST", url, params, headers)


def main():
    """测试主函数"""
    print("=" * 60)
    print("同花顺基金交易接口测试")
    print("=" * 60)

    # 配置参数（从日志中提取）
    CUST_ID = "100113970166"  # 客户ID
    USER_ID = "690359103"     # 用户ID
    FUND_CODE = "012922"      # 测试基金代码
    TRADE_PASSWORD = "123456" # 交易密码（示例，请替换为真实密码）

    # 创建交易客户端
    trader = THSFundTrader(CUST_ID, USER_ID)

    # 测试1：查询持仓
    positions = trader.query_positions()
    print(f"持仓结果: {json.dumps(positions, indent=2, ensure_ascii=False)}")

    # 测试2：查询资产
    assets = trader.query_assets()
    print(f"资产结果: {json.dumps(assets, indent=2, ensure_ascii=False)}")

    # 测试3：查询钱包
    wallet = trader.query_wallet()
    print(f"钱包结果: {json.dumps(wallet, indent=2, ensure_ascii=False)}")

    # 测试4：模拟买入流程（测试模式，不真正提交）
    buy_result = trader.buy_fund(
        fund_code=FUND_CODE,
        amount=1.0,
        trade_password=TRADE_PASSWORD,
        dry_run=True  # 测试模式
    )
    print(f"\n买入测试结果: {json.dumps(buy_result, indent=2, ensure_ascii=False)}")

    print("\n" + "=" * 60)
    print("⚠️  注意事项：")
    print("1. 以上测试均在测试模式下运行，未执行真实交易")
    print("2. 如需真实交易，请将 dry_run=False")
    print("3. 交易密码已 MD5 加密，服务端无法看到明文")
    print("4. 所有请求通过 App 内的 JSBridge 代理，认证由 Native 层处理")
    print("=" * 60)


if __name__ == "__main__":
    main()
