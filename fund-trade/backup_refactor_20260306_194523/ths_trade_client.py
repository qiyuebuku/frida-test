#!/usr/bin/env python3
"""同花顺基金交易客户端 - 同步版本（简化版，只包含交易功能）"""

import hashlib
import json
import requests
from typing import Dict, Any


class THSTradeClient:
    """同花顺基金交易客户端（同步版本）

    专注于交易功能：买入/卖出
    直接调用 JSBridge 代理，不依赖异步客户端
    """

    def __init__(self, cust_id: str = "100113970166", proxy_port: int = 18900):
        """初始化交易客户端

        Args:
            cust_id: 客户ID
            proxy_port: JSBridge 代理端口（默认 18900）
        """
        self.cust_id = cust_id
        self.proxy_url = f"http://localhost:{proxy_port}"
        self.session = requests.Session()

        # 交易认证参数（从 Hook 自动获取）
        self.trade_auth = self._refresh_auth()

        # 交易密码（MD5）
        self.trade_password_md5 = ""

    def _refresh_auth(self) -> Dict[str, str]:
        """从 Hook 代理获取最新的交易认证参数"""
        try:
            resp = self.session.get(f"{self.proxy_url}/auth", timeout=3)
            data = resp.json()
            if data.get("available") and data.get("key5"):
                return {
                    "key1": data.get("key1", ""),
                    "key2": data.get("key2", ""),
                    "key3": data.get("key3", ""),
                    "key4": data.get("key4", ""),
                    "key5": data.get("key5", ""),
                    "userId": data.get("userId", ""),
                    "sessionId": data.get("sessionId", ""),
                }
        except Exception as e:
            print(f"警告: 无法刷新认证参数: {e}")

        # 返回默认值
        return {
            "key1": "7246091a5f126b63",
            "key2": "2293a78f6581c12bbb334759458d4de3",
            "key3": self.cust_id,
            "key4": "auth",
            "key5": "xWxp7fH6r0HmWg3WS0B3OtAMTa/nwu3vCZJpOBH2MoN6nvJi2y7CrC1tS1bLNYUG",
            "userId": "",
            "sessionId": "",
        }

    def set_password(self, password: str):
        """设置交易密码

        Args:
            password: 明文密码或MD5哈希
        """
        if len(password) == 32 and all(c in "0123456789abcdefABCDEF" for c in password):
            self.trade_password_md5 = password.upper()
        else:
            self.trade_password_md5 = hashlib.md5(password.encode()).hexdigest().upper()

    def _call_jsbridge(self, data: Dict[str, Any], timeout: int = 40) -> Dict[str, Any]:
        """通过 JSBridge 调用 API

        Args:
            data: JSBridge 请求数据
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

        except Exception as e:
            raise Exception(f"JSBridge请求失败: {e}")

    def buy_fund(self, fund_code: str, amount: float) -> Dict[str, Any]:
        """买入基金（完整6步流程）

        Args:
            fund_code: 基金代码
            amount: 买入金额（元）

        Returns:
            买入结果
        """
        if not self.trade_password_md5:
            raise ValueError("未设置交易密码，请先调用 set_password()")

        # 刷新认证
        self.trade_auth = self._refresh_auth()

        money = f"{amount:.2f}"
        auth = self.trade_auth

        print(f"💸 买入 {fund_code} {money} 元")

        # 步骤1：买入初始化
        print("  [1/6] 买入初始化...")
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

        # 提取数据
        bank_cards = init_resp.get("result", {}).get("data", {}).get("bankCardSplitListResult", [])
        if not bank_cards:
            raise Exception(f"未获取到银行卡信息: {init_resp}")

        transaction_account_id = bank_cards[0].get("transActionAccountId", "")
        fund_info = init_resp.get("result", {}).get("data", {}).get("paramOpenFundAccBean", {})
        fund_name = fund_info.get("fundName", "")
        fund_risk_level = str(init_resp.get("result", {}).get("data", {}).get("fundRiskLevel", "4"))

        # 步骤2：账户状态检查
        print("  [2/6] 账户状态检查...")
        acct_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/account/dubbo/accountInfo/getCustAccoStatus",
            "params": {"version": "VOCATIONCODE_22"},
            "Header": {"Content-Type": "application/x-www-form-urlencoded", "custId": self.cust_id},
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        acct_resp = self._call_jsbridge(acct_data)
        user_risk_level = str(acct_resp.get("result", {}).get("data", {}).get("riskLevel", "4"))

        # 步骤3：交易前签约检查
        print("  [3/6] 签约检查...")
        check_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/sign_contract/v1/check_before_trade",
            "params": {
                "fundCode": fund_code,
                "applicationAmount": money,
                "transactionAccountId": transaction_account_id
            },
            "Header": {"Content-Type": "application/x-www-form-urlencoded", "custId": self.cust_id},
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        self._call_jsbridge(check_data)

        # 步骤4：获取短信验证状态
        print("  [4/6] 短信验证...")
        sms_dto = json.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
        })
        getsms_data = {
            "method": "POST",
            "url": f"https://trade.5ifund.com/rs/trade/shen/getsms/{self.cust_id}",
            "params": {
                "key1": auth["key1"],
                "key2": auth["key2"],
                "key5": auth["key5"],
                "rsBuySmsDTO": sms_dto,
                "key3": auth["key3"],
                "key4": "auth",
            },
            "K5type": "normal"
        }
        self._call_jsbridge(getsms_data)

        # 步骤5：获取交易序列号
        print("  [5/6] 获取交易序列号...")
        checksms_dto = json.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
            "isCheckSmsCode": 0,
            "custId": self.cust_id,
        })
        checksms_data = {
            "method": "POST",
            "url": f"https://trade.5ifund.com/rs/trade/shen/checksms/{self.cust_id}",
            "params": {
                "key1": auth["key1"],
                "key2": auth["key2"],
                "key5": auth["key5"],
                "rsBuySmsDTO": checksms_dto,
                "key3": auth["key3"],
                "key4": "auth",
                "smsRandom": "",
            },
            "K5type": "normal"
        }
        checksms_resp = self._call_jsbridge(checksms_data)

        # 提取 tradeInfoSeq
        trade_info_seq = (checksms_resp.get("result", {}).get("singleData", {}).get("tradeInfoSeq") or
                         checksms_resp.get("singleData", {}).get("tradeInfoSeq") or
                         checksms_resp.get("tradeInfoSeq", ""))
        if not trade_info_seq:
            raise Exception(f"未获取到 tradeInfoSeq: {checksms_resp}")

        # 步骤6：提交买入订单
        print("  [6/6] 提交订单...")
        agreement_str = json.dumps([{"protocalCode": "JJ_WDMCXY", "protocalVersion": "20230426"}])
        buy_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/buy",
            "params": {
                "signFlag": "1",
                "useWallet": "1",
                "money": money,
                "tradeInfoSeq": trade_info_seq,
                "fundCode": fund_code,
                "tradePassword": self.trade_password_md5,
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
        buy_resp = self._call_jsbridge(buy_data)

        # 检查响应
        result_obj = buy_resp.get("result", {})
        error_code = result_obj.get("code", "")
        error_msg = result_obj.get("message", "")

        if error_code != "0000" and error_code != "":
            raise Exception(f"买入失败 [{error_code}]: {error_msg}")

        buy_data_result = result_obj.get("data", {})
        app_sheet_serial_no = buy_data_result.get("appSheetSerialNo", "")

        print(f"✅ 买入成功！订单号: {app_sheet_serial_no}")

        return {
            "success": True,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "amount": money,
            "app_sheet_serial_no": app_sheet_serial_no,
        }

    def sell_fund(self, fund_code: str, share_vol: float = None, sell_all: bool = False) -> Dict[str, Any]:
        """赎回基金

        Args:
            fund_code: 基金代码
            share_vol: 赎回份额（与 sell_all 二选一）
            sell_all: 是否全部赎回

        Returns:
            赎回结果
        """
        if not self.trade_password_md5:
            raise ValueError("未设置交易密码，请先调用 set_password()")

        if not sell_all and share_vol is None:
            raise ValueError("请指定赎回份额或使用全部赎回")

        # 刷新认证
        self.trade_auth = self._refresh_auth()

        print(f"📤 赎回 {fund_code}")

        # 步骤1：赎回初始化
        print("  [1/2] 赎回初始化...")
        render_data = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/redemption/v1/render",
            "params": {"fundCode": fund_code},
            "Header": {"Content-Type": "application/x-www-form-urlencoded", "custId": self.cust_id},
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        render_resp = self._call_jsbridge(render_data)

        render_result = render_resp.get("result", {})
        if render_result.get("code") not in ("0000", ""):
            raise Exception(f"赎回初始化失败: {render_result.get('message')}")

        render_data_obj = render_result.get("data", {})
        fund_info = render_data_obj.get("fundInfo", {})
        defender_token = render_data_obj.get("defenderToken", {})
        dt = defender_token.get("dt", "")

        if not dt:
            raise Exception("未获取到 defenderToken")

        # 步骤2：提交赎回
        print("  [2/2] 提交赎回...")
        redeem_params = {
            "fundCode": fund_code,
            "fundName": fund_info.get("fundName", ""),
            "shareType": fund_info.get("shareType", "0"),
            "shareVol": f"{share_vol:.2f}" if share_vol else "0",
            "tradePassword": self.trade_password_md5,
            "operator": "145",
            "redemptionType": "0",
            "largeRedemptionFlag": "0",
        }

        redeem_jsdata = {
            "method": "POST",
            "url": "https://trade.5ifund.com/rz/trade/dubbo/redemption/v2/redeem",
            "params": redeem_params,
            "Header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "custId": self.cust_id,
                "dt": dt
            },
            "needToken": True,
            "K5type": "none",
            "requestType": "guomiSSL"
        }
        redeem_resp = self._call_jsbridge(redeem_jsdata)

        redeem_result = redeem_resp.get("result", {})
        if redeem_result.get("code") not in ("0000", ""):
            raise Exception(f"赎回失败: {redeem_result.get('message')}")

        app_sheet_serial_no = redeem_result.get("data", {}).get("appSheetSerialNo", "")

        print(f"✅ 赎回成功！订单号: {app_sheet_serial_no}")

        return {
            "success": True,
            "fund_code": fund_code,
            "fund_name": fund_info.get("fundName", ""),
            "share_vol": f"{share_vol:.2f}" if share_vol else "全部",
            "app_sheet_serial_no": app_sheet_serial_no,
        }


if __name__ == "__main__":
    # 测试
    client = THSTradeClient()
    client.set_password("ruan19980418")

    # 测试买入
    result = client.buy_fund("008087", 100.0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
