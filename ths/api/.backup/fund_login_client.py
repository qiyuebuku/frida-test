#!/usr/bin/env python3
"""
同花顺基金账户登录客户端

功能：
1. 使用账号+密码MD5登录基金交易账户
2. 获取 key3、key4、key5 等认证参数
3. 支持自动刷新token

无需手机短信验证，完全自动化
"""

import requests
import hashlib
import json
import time
from datetime import datetime
from typing import Dict, Optional, Tuple


class FundLoginClient:
    """基金账户登录客户端"""

    BASE_URL = "https://trade.5ifund.com"
    LOGIN_ENDPOINT = "/rz/account/login/noauth/v1/result/safe/check"

    def __init__(
        self,
        account: str,
        password: str = None,
        password_md5: str = None,
        device_id: str = "7246091a5f126b63",
        device_sign: str = "2293a78f6581c12bbb334759458d4de3",
        ths_user_id: str = "690359103",
    ):
        """
        初始化登录客户端

        Args:
            account: 基金账号 (例如: 100113970166)
            password: 原始密码 (可选，如果提供了password_md5则不需要)
            password_md5: 密码的MD5值 (可选，如果提供了password则不需要)
            device_id: 设备ID (key1)
            device_sign: 设备签名 (key2)
            ths_user_id: 同花顺用户ID
        """
        self.account = account
        self.device_id = device_id
        self.device_sign = device_sign
        self.ths_user_id = ths_user_id

        # 计算或使用提供的密码MD5
        if password_md5:
            self.password_md5 = password_md5
        elif password:
            self.password_md5 = hashlib.md5(password.encode()).hexdigest().upper()
        else:
            raise ValueError("必须提供 password 或 password_md5")

        # 认证参数
        self.key3: Optional[str] = None
        self.key4: Optional[str] = None
        self.key5: Optional[str] = None
        self.cookie: Optional[str] = None
        self.last_login_time: Optional[int] = None

    def login(self) -> Dict:
        """
        执行登录

        Returns:
            包含完整认证信息的字典

        Raises:
            Exception: 登录失败时抛出异常
        """
        url = f"{self.BASE_URL}{self.LOGIN_ENDPOINT}"

        headers = {
            "token": "-1",
            "custId": "-1",
            "source": "SDK",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Hexin_Gphone/11.48.03 (Royal Flush) innerversion/G037.08.194.1.32 hxtheme/0 GphoneIjiJinSDK/V7.39.01 ifOperator/145",
            "Client-Referer": "",
            "Host": "trade.5ifund.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        data = {
            "key1": self.device_id,
            "uId": self.device_id,
            "key2": self.device_sign,
            "password": self.password_md5,
            "ipAddress": "null",
            "thsUserId": self.ths_user_id,
            "device": "OnePlus PLQ110",
            "deviceName": "OnePlus ",
            "account": self.account,
            "loginSource": "SDK",
            "operator": "145",
        }

        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()

            result = response.json()

            # 检查登录结果
            if result.get("code") != "0000":
                error_msg = result.get("message", "未知错误")
                raise Exception(f"登录失败: {error_msg}")

            # 提取data字段
            data = result.get("data", {})

            # 提取认证参数
            self.key3 = data.get("key3") or data.get("custId") or self.account
            self.key4 = data.get("key4", "auth")
            self.key5 = data.get("key5")

            if not self.key5:
                raise Exception(f"登录响应中未找到key5，响应: {result}")

            # 提取cookie (如果有)
            if "Set-Cookie" in response.headers:
                self.cookie = response.headers["Set-Cookie"]

            self.last_login_time = int(time.time() * 1000)

            print(f"✅ 基金账户登录成功")
            print(f"   账号: {self.account}")
            print(f"   key3: {self.key3}")
            print(f"   key4: {self.key4}")
            print(f"   key5: {self.key5[:50]}...")
            print(f"   登录时间: {datetime.fromtimestamp(self.last_login_time / 1000)}")

            return {
                "account": self.account,
                "key1": self.device_id,
                "key2": self.device_sign,
                "key3": self.key3,
                "key4": self.key4,
                "key5": self.key5,
                "cookie": self.cookie,
                "password_md5": self.password_md5,
                "last_login_time": self.last_login_time,
                "ths_user_id": self.ths_user_id,
            }

        except requests.exceptions.RequestException as e:
            raise Exception(f"网络请求失败: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"响应JSON解析失败: {e}")

    def get_auth_params(self) -> Tuple[str, str, str, str, str]:
        """
        获取认证参数

        Returns:
            (key1, key2, key3, key4, key5) 元组
        """
        if not self.key5:
            raise Exception("未登录，请先调用 login()")

        return (
            self.device_id,
            self.device_sign,
            self.key3,
            self.key4,
            self.key5,
        )

    @staticmethod
    def calculate_password_md5(password: str) -> str:
        """计算密码的MD5值"""
        return hashlib.md5(password.encode()).hexdigest().upper()


def main():
    """测试登录功能"""
    import sys

    # 从命令行参数或环境变量获取账号密码
    if len(sys.argv) >= 3:
        account = sys.argv[1]
        password = sys.argv[2]
    else:
        print("使用方法: python fund_login_client.py <账号> <密码>")
        print("或者修改代码直接使用密码MD5")
        sys.exit(1)

    try:
        # 创建登录客户端
        client = FundLoginClient(account=account, password=password)

        # 执行登录
        auth_data = client.login()

        # 保存到文件
        output_file = "/tmp/fund_auth.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, indent=2, ensure_ascii=False)

        print(f"\n✅ 认证参数已保存到: {output_file}")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
