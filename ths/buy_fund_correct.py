#!/usr/bin/env python3
"""
同花顺基金买入 - 正确的完整流程版本

完整的6步流程：
1. 买入初始化（获取transActionAccountId）
2. 账户状态检查
3. 交易前签约检查
4. 获取短信验证状态
5. 校验短信并获取tradeInfoSeq
6. 提交买入订单
"""

import hashlib
import json
import requests
import time
from typing import Dict, Any

class THSFundTrader:
    """同花顺基金交易客户端（使用JSBridge完整流程）"""

    def __init__(self, cust_id: str, proxy_port: int = 18900):
        """
        初始化交易客户端

        Args:
            cust_id: 客户ID（例如：100113970166）
            proxy_port: 代理服务器端口（默认：18900）
        """
        self.cust_id = cust_id
        self.proxy_url = f"http://localhost:{proxy_port}"
        self.session = requests.Session()

        # 交易认证参数（从之前的日志中提取）
        self.auth = {
            "key1": "7246091a5f126b63",
            "key2": "2293a78f6581c12bbb334759458d4de3",
            "key3": cust_id,  # custId
            "key4": "auth",
            "key5": "xWxp7fH6r0HmWg3WS0B3OtAMTa/nwu3vCZJpOBH2MoN6nvJi2y7CrC1tS1bLNYUG",
            "userId": "690359103",
            "sessionId": "195d198e3a1ef6ebb8e08acd628dc3c4a"
        }

    @staticmethod
    def encrypt_password(password: str) -> str:
        """加密交易密码（MD5）"""
        return hashlib.md5(password.encode()).hexdigest().upper()

    def _call_jsbridge(self, data: Dict, timeout: int = 35) -> Dict[str, Any]:
        """
        通过 JSBridge 调用 API

        Args:
            data: JSBridge请求数据
            timeout: 超时时间（秒）

        Returns:
            API 响应数据
        """
        request_data = {
            "handler": "clientRequestHX",
            "data": data
        }

        try:
            response = self.session.post(
                f"{self.proxy_url}/jsbridge",
                json=request_data,
                timeout=timeout
            )
            response.raise_for_status()

            # 解析响应
            result = response.json()
            if isinstance(result, str):
                result = json.loads(result)

            if not result.get("success"):
                raise Exception(f"JSBridge调用失败: {result.get('error')}")

            return result.get("data", {})

        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP请求失败: {e}")

    def _post_form(self, url: str, params: Dict[str, str]) -> Dict[str, Any]:
        """通过JSBridge发送POST表单请求"""
        data = {
            "method": "POST",
            "url": url,
            "params": params,
            "K5type": "normal"
        }
        return self._call_jsbridge(data)

    def buy_fund(self, fund_code: str, amount: float, trade_password: str) -> Dict[str, Any]:
        """
        买入基金（完整6步流程）

        Args:
            fund_code: 基金代码
            amount: 买入金额（元）
            trade_password: 交易密码（明文）

        Returns:
            买入结果
        """
        print(f"\n{'=' * 60}")
        print(f"💸 买入基金（完整流程）")
        print(f"{'=' * 60}")
        print(f"  基金代码: {fund_code}")
        print(f"  买入金额: {amount} 元")
        print(f"  客户ID: {self.cust_id}")

        try:
            money = f"{amount:.2f}"
            encrypted_pwd = self.encrypt_password(trade_password)

            # 步骤1：买入初始化
            print(f"\n📋 步骤1/6: 买入初始化...")
            init_data = {
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
            init_resp = self._call_jsbridge(init_data)

            # 提取transActionAccountId
            bank_cards = init_resp.get("result", {}).get("data", {}).get("bankCardSplitListResult", [])
            if not bank_cards:
                raise Exception(f"未获取到银行卡信息: {init_resp}")

            transaction_account_id = bank_cards[0].get("transActionAccountId", "")
            if not transaction_account_id:
                raise Exception(f"未获取到transActionAccountId")

            fund_info = init_resp.get("result", {}).get("data", {}).get("paramOpenFundAccBean", {})
            fund_name = fund_info.get("fundName", "")
            fund_risk_level = str(init_resp.get("result", {}).get("data", {}).get("fundRiskLevel", "4"))

            print(f"  ✅ 交易账户ID: {transaction_account_id}")
            print(f"  ✅ 基金名称: {fund_name}")

            # 步骤2：账户状态检查
            print(f"\n📋 步骤2/6: 账户状态检查...")
            acct_data = {
                "method": "POST",
                "url": "https://trade.5ifund.com/rz/account/dubbo/accountInfo/getCustAccoStatus",
                "params": {"version": "VOCATIONCODE_22"},
                "Header": {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "custId": self.cust_id
                },
                "needToken": True,
                "K5type": "none",
                "requestType": "guomiSSL"
            }
            acct_resp = self._call_jsbridge(acct_data)
            user_risk_level = str(acct_resp.get("result", {}).get("data", {}).get("riskLevel", "4"))
            print(f"  ✅ 用户风险等级: {user_risk_level}")

            # 步骤3：交易前签约检查
            print(f"\n📋 步骤3/6: 交易前签约检查...")
            check_data = {
                "method": "POST",
                "url": "https://trade.5ifund.com/rz/trade/dubbo/sign_contract/v1/check_before_trade",
                "params": {
                    "fundCode": fund_code,
                    "applicationAmount": money,
                    "transactionAccountId": transaction_account_id
                },
                "Header": {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "custId": self.cust_id
                },
                "needToken": True,
                "K5type": "none",
                "requestType": "guomiSSL"
            }
            self._call_jsbridge(check_data)
            print(f"  ✅ 签约检查通过")

            # 步骤4：获取短信验证状态
            print(f"\n📋 步骤4/6: 获取短信验证状态...")
            sms_dto = json.dumps({
                "money": money,
                "fundCode": fund_code,
                "fundRiskLevel": fund_risk_level,
                "payType": "0",
                "userRiskLevel": user_risk_level,
            })

            getsms_url = f"https://trade.5ifund.com/rs/trade/shen/getsms/{self.cust_id}"
            getsms_params = {
                "key1": self.auth["key1"],
                "key2": self.auth["key2"],
                "key5": self.auth["key5"],
                "rsBuySmsDTO": sms_dto,
                "key3": self.auth["key3"],
                "key4": "auth",
            }
            self._post_form(getsms_url, getsms_params)
            print(f"  ✅ 短信验证状态已获取")

            # 步骤5：校验短信并获取tradeInfoSeq
            print(f"\n📋 步骤5/6: 获取交易序列号...")
            checksms_dto = json.dumps({
                "money": money,
                "fundCode": fund_code,
                "fundRiskLevel": fund_risk_level,
                "payType": "0",
                "userRiskLevel": user_risk_level,
                "isCheckSmsCode": 0,
                "custId": self.cust_id,
            })

            checksms_url = f"https://trade.5ifund.com/rs/trade/shen/checksms/{self.cust_id}"
            checksms_params = {
                "key1": self.auth["key1"],
                "key2": self.auth["key2"],
                "key5": self.auth["key5"],
                "rsBuySmsDTO": checksms_dto,
                "key3": self.auth["key3"],
                "key4": "auth",
                "smsRandom": "",
            }
            checksms_resp = self._post_form(checksms_url, checksms_params)

            # 提取tradeInfoSeq
            trade_info_seq = (checksms_resp.get("result", {}).get("singleData", {}).get("tradeInfoSeq", "") or
                            checksms_resp.get("singleData", {}).get("tradeInfoSeq", "") or
                            checksms_resp.get("tradeInfoSeq", ""))
            if not trade_info_seq:
                raise Exception(f"未获取到tradeInfoSeq: {checksms_resp}")

            print(f"  ✅ 交易序列号: {trade_info_seq}")

            # 步骤6：提交买入订单
            print(f"\n📋 步骤6/6: 提交买入订单...")
            agreement_str = json.dumps([{
                "protocalCode": "JJ_WDMCXY",
                "protocalVersion": "20230426",
            }])

            buy_data = {
                "method": "POST",
                "url": "https://trade.5ifund.com/rz/trade/dubbo/buy",
                "params": {
                    "signFlag": "1",
                    "useWallet": "1",
                    "money": money,
                    "tradeInfoSeq": trade_info_seq,
                    "fundCode": fund_code,
                    "tradePassword": encrypted_pwd,
                    "buyType": "1",
                    "transactionAccountId": transaction_account_id,
                    "operator": "145",
                    "agreementStr": agreement_str
                },
                "needToken": True,
                "K5type": "none",
                "Header": {
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                "requestType": "guomiSSL"
            }
            buy_resp = self._call_jsbridge(buy_data, timeout=40)

            # 检查响应
            result_obj = buy_resp.get("result", {})
            error_code = result_obj.get("code", "")
            error_msg = result_obj.get("message", "")

            # 检查是否有错误
            if error_code != "0000" and error_code != "":
                raise Exception(f"买入失败 [{error_code}]: {error_msg}")

            # 提取结果
            buy_result = result_obj.get("data", {})
            if not buy_result:
                raise Exception(f"买入响应无数据: {buy_resp}")

            app_sheet_serial_no = buy_result.get("appSheetSerialNo", "")

            print(f"\n{'=' * 60}")
            print(f"✅ 买入成功！")
            print(f"{'=' * 60}")
            print(f"  申请单号: {app_sheet_serial_no}")
            print(f"  基金代码: {fund_code}")
            print(f"  基金名称: {fund_name}")
            print(f"  买入金额: {money} 元")
            print(f"{'=' * 60}\n")

            return {
                "success": True,
                "app_sheet_serial_no": app_sheet_serial_no,
                "fund_code": fund_code,
                "fund_name": fund_name,
                "amount": money
            }

        except Exception as e:
            print(f"\n{'=' * 60}")
            print(f"❌ 买入失败")
            print(f"{'=' * 60}")
            print(f"  错误: {e}")
            print(f"{'=' * 60}\n")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }


def main():
    """测试主函数"""
    print("=" * 60)
    print("同花顺基金买入 - 正确的完整流程版本")
    print("=" * 60)

    # 配置参数
    CUST_ID = "100113970166"
    FUND_CODE = "008087"  # 华夏中证5G通信主题ETF联接C
    AMOUNT = 100.0
    TRADE_PASSWORD = "ruan19980418"

    # 创建交易客户端
    trader = THSFundTrader(CUST_ID)

    # 执行买入
    print(f"\n⚠️  警告：即将执行真实交易！")
    print(f"  基金代码: {FUND_CODE}")
    print(f"  买入金额: {AMOUNT} 元\n")

    # 等待3秒
    for i in range(3, 0, -1):
        print(f"  {i}秒后开始...")
        time.sleep(1)

    result = trader.buy_fund(
        fund_code=FUND_CODE,
        amount=AMOUNT,
        trade_password=TRADE_PASSWORD
    )

    if result.get("success"):
        print(f"🎉 恭喜！买入成功")
    else:
        print(f"💔 很遗憾，买入失败")


if __name__ == "__main__":
    main()
