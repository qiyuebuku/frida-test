"""天天基金交易客户端（trade.5ifund.com）- 包含所有交易和账户相关方法"""

import asyncio
import json
from datetime import datetime, timedelta

from src.infrastructure.clients.base import BaseClient


class TianTianClient(BaseClient):
    """天天基金交易客户端"""

    TRADE_BASE_URL = ""

    TRADE_HEADERS = {
        "User-Agent": "Hexin_Gphone/11.47.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
        "Client-Referer": "",
    }

    # 交易认证参数（完全依赖 auth_manager 动态获取，不再硬编码）
    # 如果需要兜底，请确保 auth_manager 能正常工作（app 在线 + JSBridge 可用）
    TRADE_AUTH = {
        "key1": "",
        "key2": "",
        "key3": "",
        "key4": "auth",
        "key5": "",
        "userId": "",
        "sessionId": "",
    }

    # 交易密码（明文），从 config.json 读取或通过 update_trade_password() 设置
    TRADE_PASSWORD = ""

    # 交易 Cookie（从 auth_cache.json 动态加载，不再硬编码）
    TRADE_COOKIE = ""

    def __init__(self, timeout=10.0):
        super().__init__(timeout=timeout)
        from src.infrastructure.config.settings import TRADE_BASE_URL
        self.TRADE_BASE_URL = TRADE_BASE_URL
        self._load_auth_cache()
        self._load_trade_password()

    # ========== 认证相关 ==========

    def _load_auth_cache(self):
        """从 ft_config 加载认证参数到 TRADE_AUTH"""
        from src.infrastructure.db import fund_db
        try:
            data = fund_db.get_auth()
            auth = data.get("auth", {})

            self.TRADE_AUTH["key1"] = auth.get("key1", "")
            self.TRADE_AUTH["key2"] = auth.get("key2", "")
            self.TRADE_AUTH["key3"] = auth.get("key3", "")
            self.TRADE_AUTH["key4"] = auth.get("key4", "auth")
            self.TRADE_AUTH["key5"] = auth.get("key5", "")
            self.TRADE_AUTH["userId"] = auth.get("userId", "")
            self.TRADE_AUTH["sessionId"] = auth.get("sessionId", "")

            self.TRADE_COOKIE = auth.get("cookie", "")

            if self.TRADE_AUTH.get("key1"):
                print(f"✅ 已从数据库加载认证参数")
                print(f"   key1: {self.TRADE_AUTH['key1'][:16]}...")
                print(f"   key3: {self.TRADE_AUTH['key3']}")
                print(f"   cookie: {len(self.TRADE_COOKIE)} 字符" if self.TRADE_COOKIE else "   cookie: 未加载")
            else:
                print(f"⚠️  数据库中无认证信息，请推送 token")
        except Exception as e:
            print(f"⚠️  加载认证信息失败: {e}")

    def _load_trade_password(self):
        """从 ft_config 读取交易密码"""
        from src.infrastructure.db import fund_db
        try:
            config = fund_db.get_config()
            password = config.get("trade_password", "")
            if password:
                self.TRADE_PASSWORD = password
        except Exception as e:
            print(f"警告: 读取交易密码失败: {e}")

    def _refresh_trade_auth(self) -> dict:
        """获取交易认证参数（仅从本地缓存读取，不连接手机）

        Returns:
            认证参数字典 {"key1", "key2", "key3", "key4", "key5", "userId", "sessionId"}

        Raises:
            RuntimeError: 认证参数无效或已过期
        """
        # 检查文件是否被外部更新
        self.reload_auth_if_updated()

        # 检查缓存中是否有有效的 key1 + key5
        if self.TRADE_AUTH.get("key1") and self.TRADE_AUTH.get("key5"):
            return self.TRADE_AUTH.copy()

        raise RuntimeError(
            "Token 已过期，请在本地运行 client.py refresh-token 刷新"
        )

    def _is_cache_valid(self) -> bool:
        """检查数据库中的 token 是否还在有效期内"""
        try:
            import time
            from src.infrastructure.db import fund_db
            data = fund_db.get_auth()
            expires_at = data.get("expires_at")
            if not expires_at:
                return True  # 无过期时间，假设有效
            return int(time.time()) < expires_at
        except Exception:
            return False

    def _update_auth_cache(self, auth: dict):
        """更新认证信息到数据库"""
        try:
            import time
            from src.infrastructure.db import fund_db
            cache_data = {
                "auth": {
                    "key1": auth.get("key1", ""),
                    "key2": auth.get("key2", ""),
                    "key3": auth.get("key3", ""),
                    "key4": auth.get("key4", "auth"),
                    "key5": auth.get("key5", ""),
                    "userId": auth.get("userId", ""),
                    "sessionId": auth.get("sessionId", ""),
                    "cookie": auth.get("cookie", ""),
                },
                "expires_at": auth.get("expires_at"),
                "last_sync": int(time.time()),
                "sync_source": "zygisk_auto"
            }
            fund_db.save_auth(cache_data)
            self._load_auth_cache()
        except Exception as e:
            print(f"⚠️ 更新认证信息失败: {e}")

    def reload_auth(self):
        """外部调用：重新从数据库加载认证信息"""
        self._load_auth_cache()

    def reload_auth_if_updated(self) -> bool:
        """兼容旧调用：直接重新加载"""
        self._load_auth_cache()
        return True

    def update_trade_auth(self, key1: str = None, key2: str = None, key3: str = None,
                          key5: str = None, user_id: str = None, session_id: str = None,
                          cookie: str = None):
        """更新交易认证参数（token 过期后需要重新从 Hook 捕获）"""
        if key1:
            self.TRADE_AUTH["key1"] = key1
        if key2:
            self.TRADE_AUTH["key2"] = key2
        if key3:
            self.TRADE_AUTH["key3"] = key3
        if key5:
            self.TRADE_AUTH["key5"] = key5
        if user_id:
            self.TRADE_AUTH["userId"] = user_id
        if session_id:
            self.TRADE_AUTH["sessionId"] = session_id
        if cookie:
            self.TRADE_COOKIE = cookie

    def update_trade_password(self, password: str):
        """设置交易密码（明文）"""
        self.TRADE_PASSWORD = password

    # ========== 交易请求基础 ==========

    def _trade_auth_params(self) -> dict:
        """构建交易 API 的 URL 认证参数（每次都从文件读取最新的）"""
        # 检查文件是否被外部更新
        self.reload_auth_if_updated()

        auth = self.TRADE_AUTH

        return {
            "key1": auth.get("key1", ""),
            "key2": auth.get("key2", ""),
            "key5": auth.get("key5", ""),
            "key3": auth.get("key3", ""),
            "key4": auth.get("key4", "auth"),
        }

    def _trade_cookie_header(self) -> dict:
        """构建交易 API 的 Cookie 头"""
        return {**self.TRADE_HEADERS, "cookie": self.TRADE_COOKIE}

    async def _trade_get(self, path: str, extra_params: dict = None, k5_type: str = "normal") -> dict:
        """交易 API GET 请求（key1-key5 认证）

        Args:
            path: API路径
            extra_params: 额外URL参数
            k5_type: K5验证类型，"normal"或"none"（默认"normal"）
        """
        params = self._trade_auth_params()
        if extra_params:
            params.update(extra_params)
        resp = await self._client.get(
            f"{self.TRADE_BASE_URL}{path}",
            params=params,
            headers=self._trade_cookie_header(),
        )
        resp.raise_for_status()
        return resp.json()

    async def _trade_post_form(self, path: str, data: dict, extra_headers: dict = None, k5_type: str = "none") -> dict:
        """交易 API POST 请求（form-encoded body）

        Args:
            path: API路径
            data: 表单数据
            extra_headers: 额外HTTP头
            k5_type: K5验证类型，"normal"或"none"（默认"none"）
        """
        # 添加认证参数到 data
        auth = self._trade_auth_params()
        full_data = {**auth, **data}

        # 构建 Headers
        headers = {
            "Cache-Control": "max-age=60",
            "User-Agent": "Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
            "Client-Referer": "",
            "custId": auth.get("key3", ""),
            "token": auth.get("key5", ""),
            "source": "SDK",
        }
        if extra_headers:
            headers.update(extra_headers)

        resp = await self._client.post(
            f"{self.TRADE_BASE_URL}{path}",
            data=full_data,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def _trade_post_json(self, path: str, json_data: dict, referer: str = None) -> dict:
        """交易 API POST 请求（JSON body）"""
        headers = self._trade_cookie_header()
        if referer:
            headers["referer"] = referer
        resp = await self._client.post(
            f"{self.TRADE_BASE_URL}{path}",
            json=json_data,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def _proxy_request(self, path: str, method: str = "POST",
                             body: str = None, content_type: str = None) -> dict:
        """直接调用 /rz/ 路径的 API（需要 App 级认证 Headers）"""
        self.reload_auth_if_updated()
        # 处理URL查询参数
        url_path = path
        query_params = {}
        if '?' in path:
            url_path, query_string = path.split('?', 1)
            for pair in query_string.split('&'):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    query_params[k] = v

        # 构建 Headers
        auth = self.TRADE_AUTH
        headers = {
            "cookie": self.TRADE_COOKIE,
            "User-Agent": "Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
            "custId": auth.get("key3", ""),
            "token": auth.get("key5", ""),
            "source": "SDK",
            "Accept": "application/json, text/plain, */*",
            "Client-Referer": "",
        }
        if content_type:
            headers["Content-Type"] = content_type

        url = f"{self.TRADE_BASE_URL}{url_path}"

        if method.upper() == "GET":
            resp = await self._client.get(
                url,
                params=query_params if query_params else None,
                headers=headers,
                timeout=15.0,
            )
        else:  # POST
            if body and content_type == "application/x-www-form-urlencoded":
                body_data = {}
                for pair in body.split('&'):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        body_data[k] = v
                resp = await self._client.post(
                    url,
                    params=query_params if query_params else None,
                    data=body_data,
                    headers=headers,
                    timeout=15.0,
                )
            elif body and content_type == "application/json":
                import json as json_lib
                body_data = json_lib.loads(body) if isinstance(body, str) else body
                resp = await self._client.post(
                    url,
                    params=query_params if query_params else None,
                    json=body_data,
                    headers=headers,
                    timeout=15.0,
                )
            else:
                resp = await self._client.post(
                    url,
                    params=query_params if query_params else None,
                    headers=headers,
                    timeout=15.0,
                )

        resp.raise_for_status()
        return resp.json()

    # ========== 交易操作 ==========

    async def buy_fund(self, fund_code: str, amount: float, use_wallet: bool = True,
                       password: str = None) -> dict:
        """买入基金（完整6步流程）

        fund_code: 基金代码
        amount: 买入金额（元）
        use_wallet: 是否使用活期宝支付（默认True）
        password: 交易密码（明文，优先级高于配置文件）
        返回: 订单信息（含 appSheetSerialNo）
        """
        import hashlib
        pwd_plain = password or self.TRADE_PASSWORD
        if not pwd_plain:
            raise ValueError("未设置交易密码，请在 ft_config 中配置或通过参数传入")

        # 转换为 MD5
        pwd = hashlib.md5(pwd_plain.encode()).hexdigest().upper()

        money = f"{amount:.2f}"
        cust_id = self.trade_cust_id

        # Step 1: 买入初始化 — 获取 transActionAccountId、费率、基金信息
        init_resp = await self._proxy_request(
            "/rz/trade/dubbo/subscribe/init",
            body=f"fundCode={fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        # _proxy_request 已经解析好了 result 层，直接取 data
        init_data = init_resp.get("data", init_resp)
        # transActionAccountId 在 bankCardSplitListResult[0] 中
        bank_cards = init_data.get("bankCardSplitListResult", [])
        if not bank_cards:
            raise ValueError(f"买入初始化失败，未获取到银行卡信息: {init_resp}")
        bank_card = bank_cards[0]
        transaction_account_id = bank_card.get("transActionAccountId", "")
        if not transaction_account_id:
            raise ValueError(f"买入初始化失败，未获取到 transActionAccountId: {init_resp}")

        fund_info = init_data.get("paramOpenFundAccBean", {})
        fund_name = fund_info.get("fundName", "")
        bank_name = bank_card.get("bankName", "")
        fund_risk_level = str(init_data.get("fundRiskLevel", "4"))

        # Step 2: 账户状态检查
        acct_resp = await self._proxy_request(
            "/rz/account/dubbo/accountInfo/getCustAccoStatus",
            body="version=VOCATIONCODE_22",
            content_type="application/x-www-form-urlencoded",
        )
        acct_data = acct_resp.get("data", {})
        user_risk_level = str(acct_data.get("riskLevel", "4"))

        # Step 3: 交易前签约检查
        await self._proxy_request(
            "/rz/trade/dubbo/sign_contract/v1/check_before_trade",
            body=f"fundCode={fund_code}&applicationAmount={money}&transactionAccountId={transaction_account_id}",
            content_type="application/x-www-form-urlencoded",
        )

        # Step 4: 获取短信验证状态
        auth = self.TRADE_AUTH
        sms_dto = json.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
        })
        await self._trade_post_form(
            f"/rs/trade/shen/getsms/{cust_id}",
            data={
                "key1": auth["key1"],
                "key2": auth["key2"],
                "key5": auth["key5"],
                "rsBuySmsDTO": sms_dto,
                "key3": auth["key3"],
                "key4": "auth",
            },
            k5_type="normal"  # getsms/checksms必须使用normal验证
        )

        # Step 5: 校验短信 → 获取 tradeInfoSeq
        checksms_dto = json.dumps({
            "money": money,
            "fundCode": fund_code,
            "fundRiskLevel": fund_risk_level,
            "payType": "0",
            "userRiskLevel": user_risk_level,
            "isCheckSmsCode": 0,
            "custId": cust_id,
        })
        checksms_resp = await self._trade_post_form(
            f"/rs/trade/shen/checksms/{cust_id}",
            data={
                "key1": auth["key1"],
                "key2": auth["key2"],
                "key5": auth["key5"],
                "rsBuySmsDTO": checksms_dto,
                "key3": auth["key3"],
                "key4": "auth",
                "smsRandom": "",
            },
            k5_type="normal"  # getsms/checksms必须使用normal验证
        )
        trade_info_seq = checksms_resp.get("singleData", {}).get("tradeInfoSeq", "") or checksms_resp.get("tradeInfoSeq", "")
        if not trade_info_seq:
            raise ValueError(f"获取 tradeInfoSeq 失败: {checksms_resp}")

        # Step 6: 提交买入订单
        agreement_str = json.dumps([{
            "protocalCode": "JJ_WDMCXY",
            "protocalVersion": "20230426",
        }])
        buy_body = (
            f"signFlag=1"
            f"&useWallet={'1' if use_wallet else '0'}"
            f"&money={money}"
            f"&tradeInfoSeq={trade_info_seq}"
            f"&fundCode={fund_code}"
            f"&tradePassword={pwd}"
            f"&buyType=1"
            f"&transactionAccountId={transaction_account_id}"
            f"&operator=145"
            f"&agreementStr={agreement_str}"
        )
        buy_resp = await self._proxy_request(
            "/rz/trade/dubbo/buy",
            body=buy_body,
            content_type="application/x-www-form-urlencoded",
        )

        buy_data = buy_resp.get("data", buy_resp)
        return {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "amount": money,
            "app_sheet_serial_no": buy_data.get("appSheetSerialNo", ""),
            "accept_time": buy_data.get("acceptTime", ""),
            "confirm_date": buy_data.get("confirmDate", "") or fund_info.get("confirmDay", ""),
            "bank_name": bank_name,
            "charge": buy_data.get("charge", "0.00"),
            "raw_response": buy_resp,
        }

    async def sell_fund(self, fund_code: str, share_vol: str = None,
                        sell_all: bool = False, password: str = None) -> dict:
        """赎回基金（2步流程：render初始化 → 提交赎回）

        fund_code: 基金代码
        share_vol: 赎回份额（字符串，如 "100.00"）
        sell_all: 是否全部赎回（使用全部可用份额）
        password: 交易密码（明文，优先级高于配置文件）
        返回: 赎回结果
        """
        import hashlib
        pwd_plain = password or self.TRADE_PASSWORD
        if not pwd_plain:
            raise ValueError("未设置交易密码，请在 ft_config 中配置或通过参数传入")

        # 转换为 MD5
        pwd = hashlib.md5(pwd_plain.encode()).hexdigest().upper()

        # Step 1: 从持仓列表中找到目标基金
        pos_resp = await self.get_fund_positions()
        pos_data = pos_resp.get("singleData", {}).get("fundGeneral", {}).get("fundPositonCombinedList", [])
        target = None
        for p in pos_data:
            if p.get("fundCode") == fund_code:
                target = p
                break
        if not target:
            raise ValueError(f"未在持仓中找到基金 {fund_code}")

        available_vol = target.get("availableVol") or target.get("availableShare") or "0"
        available_vol_str = str(available_vol)
        try:
            available_vol_float = float(available_vol_str)
        except (ValueError, TypeError):
            available_vol_float = 0.0
        if available_vol_float <= 0:
            raise ValueError(f"基金 {fund_code} 可用份额为 {available_vol_str}，不足以赎回")

        transaction_account_id = target.get("transactionAccountId") or target.get("transActionAccountId") or target.get("transAccIdList") or ""
        fund_name = target.get("fundName") or ""
        share_type = target.get("shareType") or "0"

        # Step 2: 调用 render API 初始化赎回
        render_resp = await self._proxy_request(
            "/rz/trade/dubbo/redemption/v1/render",
            body=f"transactionAccountId={transaction_account_id}&fundCode={fund_code}",
            content_type="application/x-www-form-urlencoded",
        )
        render_code = render_resp.get("code", "")
        if render_code not in ("0000", ""):
            raise ValueError(f"赎回初始化失败: {render_resp.get('message', render_code)}")
        render_data = render_resp.get("data", render_resp)
        # 从 render 中补充信息
        if not transaction_account_id:
            transaction_account_id = render_data.get("transactionAccountId") or ""
        if not fund_name:
            fund_name = render_data.get("fundName") or ""

        # Step 3: 确定赎回份额
        if sell_all:
            share_vol = f"{available_vol_float:.2f}"
        elif share_vol:
            try:
                vol = float(share_vol)
            except (ValueError, TypeError):
                raise ValueError(f"无效的赎回份额: {share_vol}")
            if vol > available_vol_float:
                raise ValueError(f"赎回份额 {vol:.2f} 超过可用份额 {available_vol_float:.2f}")
            share_vol = f"{vol:.2f}"
        else:
            raise ValueError("请指定赎回份额 (--shares) 或使用全部赎回 (--all)")

        # Step 4: 提交赎回
        redeem_body = json.dumps({
            "redemptionType": "0",
            "shareType": share_type,
            "fundCode": fund_code,
            "fundName": fund_name,
            "transActionAccountId": transaction_account_id,
            "tradePassword": pwd,
            "shareVol": share_vol,
            "operator": "145",
            "largeRedemptionFlag": "0",
        })
        redeem_resp = await self._proxy_request(
            "/rz/trade/dubbo/redemption/v2/redeem",
            body=redeem_body,
            content_type="application/json",
        )

        return {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "share_vol": share_vol,
            "available_vol": available_vol_str,
            "render_data": render_data,
            "redeem_result": redeem_resp,
        }

    async def cancel_order(self, app_sheet_serial_no: str, password: str = None) -> dict:
        """撤销交易订单

        app_sheet_serial_no: 订单号
        password: 交易密码（明文，优先级高于配置文件）
        """
        import hashlib
        pwd_plain = password or self.TRADE_PASSWORD
        if not pwd_plain:
            raise ValueError("未设置交易密码，请在 ft_config 中配置或通过参数传入")

        # 转换为 MD5
        pwd = hashlib.md5(pwd_plain.encode()).hexdigest().upper()

        # 先从订单列表确认该订单是否可撤（endFlag=="0" && processStatus=="1"）
        list_resp = await self.get_order_list(limit=50)
        list_data = list_resp.get("singleData", list_resp).get("data", [])
        order_in_list = None
        for o in list_data:
            if o.get("appSheetSerialNo") == app_sheet_serial_no:
                order_in_list = o
                break
        if order_in_list:
            end_flag = order_in_list.get("endFlag", "")
            process_status = order_in_list.get("processStatus", "")
            if end_flag != "0" or process_status != "1":
                raise ValueError(f"该订单不可撤销（endFlag={end_flag}, processStatus={process_status}）")

        # 获取订单详情，拿到撤单所需的 realFundCode、transactionAccountId
        detail_resp = await self.get_order_detail(app_sheet_serial_no)
        detail_data = detail_resp.get("data", detail_resp)

        real_fund_code = detail_data.get("realFundCode", detail_data.get("fundCode", ""))
        transaction_account_id = detail_data.get("transactionAccountId", "")
        fund_name = detail_data.get("fundName", "")
        amount = detail_data.get("applicationAmount", "")

        # 执行撤单
        body_parts = [
            "revokeType=2",
            f"transActionAccountId={transaction_account_id}",
            f"revokeAppSheetNo={app_sheet_serial_no}",
            f"fundCode={real_fund_code}",
            f"tradePassword={pwd}",
            "shareType=0",
            "operator=145",
        ]
        revoke_resp = await self._proxy_request(
            "/rz/trade/dubbo/revoke",
            body="&".join(body_parts),
            content_type="application/x-www-form-urlencoded",
        )

        return {
            "fund_code": real_fund_code,
            "fund_name": fund_name,
            "amount": amount,
            "order_no": app_sheet_serial_no,
            "revoke_result": revoke_resp,
        }

    # ========== 订单查询 ==========

    async def get_order_detail(self, app_sheet_serial_no: str) -> dict:
        """查询交易订单详情"""
        return await self._proxy_request(
            f"/rz/positionqryweb/dubbo/order/v2/detail?appSheetSerialNo={app_sheet_serial_no}",
            method="GET",
        )

    async def get_order_list(self, start_date: str = None, end_date: str = None,
                             op_type: str = "all", limit: int = 20, offset: int = 1) -> dict:
        """查询交易订单列表

        start_date: 开始日期 YYYYMMDD，默认近30天
        end_date: 结束日期 YYYYMMDD，默认今天
        op_type: 操作类型 all=全部
        limit: 每页条数
        offset: 页码（从1开始）
        """
        now = datetime.now()
        if not end_date:
            end_date = now.strftime("%Y%m%d")
        if not start_date:
            start_date = (now - timedelta(days=30)).strftime("%Y%m%d")

        cust_id = self.trade_cust_id

        return await self._trade_get(
            f"/rs/query/v1/currentHistoryInfo/{cust_id}",
            extra_params={
                # 根据官方格式，key3 和 key4 不应该是空字符串
                # 它们会被 _trade_get 自动添加，这里不需要覆盖
                "opType": op_type,
                "productType": "all",
                "startDate": start_date,
                "endDate": end_date,
                "limit": str(limit),
                "offset": str(offset),
            },
        )

    # ========== 账户查询 ==========

    @property
    def trade_cust_id(self) -> str:
        """获取交易客户ID（每次都检查文件更新）"""
        self.reload_auth_if_updated()
        return self.TRADE_AUTH["key3"]

    async def get_account_overview(self) -> dict:
        """账户总览（总资产、累计盈亏、当日盈亏、银行卡数、风险等级等）"""
        return await self._trade_get(
            f"/rs/incomequery/queryzcsharemobilehomenine/{self.trade_cust_id}"
        )

    async def get_fund_positions(self) -> dict:
        """基金持仓列表（持仓基金明细、市值、收益等）"""
        return await self._trade_get(
            f"/rs/fundpositionquery/fundpositionassemble/{self.trade_cust_id}"
        )

    async def get_wallet_info(self) -> dict:
        """活期宝/超级T+0（货币基金收益、可用余额等）"""
        return await self._trade_get(
            f"/rs/query/supertzeromobilehome3/{self.trade_cust_id}"
        )

    async def get_wallet_home(self) -> dict:
        """钱包首页（活期宝余额、冻结金额、累计收益等）"""
        return await self._proxy_request(
            "/rz/wallet/dubbo/v1/queryWalletHomePage",
            method="POST",
            body=f"custId={self.trade_cust_id}",
            content_type="application/x-www-form-urlencoded",
        )

    async def get_auto_invest_list(self, page_size: int = 20, status: str = "N") -> dict:
        """定投计划列表
        status: N=全部, 1=执行中, 2=暂停
        """
        body = json.dumps({
            "pageSize": page_size,
            "protocolStatusList": [],
            "productType": None,
            "lastProtocolNo": "",
            "fundCode": None,
            "transactionAccountIds": [None],
            "status": status,
            "type": None,
        })
        return await self._proxy_request(
            "/rz/positionqryweb/dubbo/protocol/v3/automatic_invest/list",
            method="POST",
            body=body,
            content_type="application/json",
        )

    async def get_auto_invest_summary(self) -> dict:
        """定投汇总（总金额、下次执行日期、正常/暂停计划数等）"""
        return await self._proxy_request(
            "/rz/positionqryweb/dubbo/protocol/v3/automatic_invest/summary",
            method="POST",
            body=f"custId={self.trade_cust_id}",
            content_type="application/x-www-form-urlencoded",
        )

    async def get_account_binding(self) -> dict:
        """账户绑定信息（客户ID、姓名、身份证号等）"""
        auth = self.TRADE_AUTH
        return await self._trade_post_form(
            "/rz/account/bind/query_bind/v1/result",
            data={
                "temporary": "0",
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
            },
            extra_headers={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "token": auth["key5"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    async def get_account_open_status(self) -> dict:
        """开户状态查询"""
        auth = self.TRADE_AUTH
        return await self._trade_post_form(
            "/rz/account/open_account/getOpenAccountByUserId/v1/result",
            data={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "version": "1",
                "token": auth["key5"],
            },
            extra_headers={
                "custId": auth["key3"],
                "sessionId": auth["sessionId"],
                "userId": auth["userId"],
                "version": "1",
                "token": auth["key5"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    async def get_trade_account_all(self) -> dict:
        """获取所有交易账户数据（一次性并发查询）"""
        overview_t = self.get_account_overview()
        positions_t = self.get_fund_positions()
        wallet_t = self.get_wallet_info()
        wallet_home_t = self.get_wallet_home()
        auto_invest_t = self.get_auto_invest_list()
        auto_summary_t = self.get_auto_invest_summary()
        binding_t = self.get_account_binding()

        results = await asyncio.gather(
            overview_t, positions_t, wallet_t, wallet_home_t,
            auto_invest_t, auto_summary_t, binding_t,
            return_exceptions=True,
        )

        def _safe(r):
            return r if not isinstance(r, Exception) else {"error": str(r)}

        return {
            "account_overview": _safe(results[0]),
            "fund_positions": _safe(results[1]),
            "wallet_info": _safe(results[2]),
            "wallet_home": _safe(results[3]),
            "auto_invest_list": _safe(results[4]),
            "auto_invest_summary": _safe(results[5]),
            "account_binding": _safe(results[6]),
        }
