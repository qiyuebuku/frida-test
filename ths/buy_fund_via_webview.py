#!/usr/bin/env python3
"""
通过 WebView JavaScript 调用同花顺基金买入接口

原理：
1. 利用已经注入的 Hook，通过 adb 向 WebView 注入 JavaScript
2. JavaScript 调用 WebViewJavascriptBridge.callHandler('clientRequestHX', ...)
3. Native 层处理请求并返回结果

注意：此脚本需要同花顺 App 已打开基金详情页面（WebView 已加载）
"""

import subprocess
import json
import hashlib
import time

ADB = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe -s 3B15BJ00GZL00000"

def encrypt_password(password: str) -> str:
    """MD5 加密交易密码"""
    return hashlib.md5(password.encode()).hexdigest().upper()

def inject_js(js_code: str):
    """通过 adb 注入 JavaScript 到当前 WebView"""
    # 转义 JavaScript 代码中的特殊字符
    escaped_js = js_code.replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')

    # 通过 input tap 模拟点击地址栏，然后输入 javascript: URL
    # 但这种方法不可靠，更好的方法是通过 Hook 中的 evaluateJavascript

    print(f"⚠️  当前脚本需要手动在 App 的基金页面中执行以下 JavaScript:")
    print("=" * 80)
    print(js_code)
    print("=" * 80)
    print("\n执行方法：")
    print("1. 在 Chrome DevTools 中连接到 WebView（chrome://inspect）")
    print("2. 或者通过 Hook 日志查看 JavaScript 执行结果")

def query_positions():
    """查询持仓"""
    js_code = """
(function() {
    if (typeof window.WebViewJavascriptBridge === 'undefined') {
        console.log('❌ WebViewJavascriptBridge 未初始化');
        return;
    }

    window.WebViewJavascriptBridge.callHandler('clientRequestHX', {
        method: 'GET',
        url: 'https://trade.5ifund.com/rs/fundpositionquery/fundpositionassemble/100113970166',
        params: {},
        K5type: 'normal'
    }, function(response) {
        console.log('✅ 持仓查询结果:');
        console.log(JSON.stringify(response, null, 2));
    });
})();
"""
    inject_js(js_code)

def buy_fund_step1_init(fund_code: str):
    """买入步骤1：初始化"""
    js_code = f"""
(function() {{
    var fundCode = '{fund_code}';
    var custId = '100113970166';

    window.WebViewJavascriptBridge.callHandler('clientRequestHX', {{
        method: 'GET',
        url: 'https://trade.5ifund.com/rs/trade/buy/' + custId + '/initwithincome2/safeforhand/' + fundCode,
        params: {{}},
        K5type: 'normal'
    }}, function(response) {{
        console.log('✅ 买入初始化结果:');
        console.log(JSON.stringify(response, null, 2));
    }});
}})();
"""
    inject_js(js_code)

def buy_fund_step2_get_seq(fund_code: str):
    """买入步骤2：获取交易序列号"""
    js_code = f"""
(function() {{
    window.WebViewJavascriptBridge.callHandler('clientRequestHX', {{
        method: 'POST',
        url: 'https://trade.5ifund.com/rz/trade/dubbo/subscribe/init',
        params: {{
            fundCode: '{fund_code}'
        }},
        Header: {{
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'custId': '100113970166',
            'source': 'SDK'
        }},
        needToken: true,
        K5type: 'none',
        requestType: 'guomiSSL'
    }}, function(response) {{
        console.log('✅ 交易序列号获取结果:');
        console.log(JSON.stringify(response, null, 2));

        // 保存结果供步骤3使用
        window._fundBuyData = {{
            fundCode: '{fund_code}',
            tradeInfoSeq: response.tradeInfoSeq,
            transactionAccountId: response.transactionAccountId
        }};
        console.log('✅ 交易参数已保存到 window._fundBuyData');
    }});
}})();
"""
    inject_js(js_code)

def buy_fund_step3_submit(fund_code: str, amount: float, trade_password: str):
    """买入步骤3：提交订单"""
    encrypted_pwd = encrypt_password(trade_password)

    js_code = f"""
(function() {{
    if (typeof window._fundBuyData === 'undefined') {{
        console.log('❌ 请先执行步骤2获取交易序列号');
        return;
    }}

    var data = window._fundBuyData;

    window.WebViewJavascriptBridge.callHandler('clientRequestHX', {{
        method: 'POST',
        url: 'https://trade.5ifund.com/rz/trade/dubbo/buy',
        params: {{
            buyType: '1',
            transactionAccountId: data.transactionAccountId,
            tradePassword: '{encrypted_pwd}',
            money: '{amount:.2f}',
            fundCode: '{fund_code}',
            useWallet: '1',
            signFlag: '1',
            tradeInfoSeq: data.tradeInfoSeq,
            operator: '145',
            agreementStr: JSON.stringify([{{
                title: '同花顺钱包服务协议',
                agreementUrl: 'https://trade.5ifund.com/fetrade/ifundTradeHelp/protocol/buy.html'
            }}])
        }},
        needToken: true,
        K5type: 'none',
        Header: {{
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded'
        }},
        requestType: 'guomiSSL'
    }}, function(response) {{
        console.log('✅ 买入订单提交结果:');
        console.log(JSON.stringify(response, null, 2));

        if (response.appSheetSerialNo) {{
            window._fundBuyOrder = {{
                appSheetSerialNo: response.appSheetSerialNo
            }};
            console.log('✅ 申请单号: ' + response.appSheetSerialNo);
        }}
    }});
}})();
"""
    inject_js(js_code)

def main():
    print("=" * 80)
    print("同花顺基金买入 - WebView JavaScript 调用方案")
    print("=" * 80)
    print("\n⚠️  前提条件：")
    print("1. 同花顺 App 已启动")
    print("2. 已进入基金详情页面（或基金列表页面）")
    print("3. WebView 已加载完成")
    print("\n📋 推荐使用 Chrome DevTools 连接 WebView:")
    print("1. 在电脑浏览器打开 chrome://inspect")
    print("2. 找到同花顺 App 的 WebView")
    print("3. 点击 'inspect' 打开开发者工具")
    print("4. 复制粘贴下方的 JavaScript 代码到 Console 执行")
    print("\n" + "=" * 80)

    # 生成各步骤的 JavaScript 代码
    FUND_CODE = "012922"  # 测试基金代码
    AMOUNT = 1.0          # 测试金额
    PASSWORD = "123456"   # 交易密码（请替换）

    print("\n\n📝 步骤0：测试连接（查询持仓）")
    query_positions()

    print("\n\n📝 步骤1：买入初始化")
    buy_fund_step1_init(FUND_CODE)

    print("\n\n📝 步骤2：获取交易序列号")
    buy_fund_step2_get_seq(FUND_CODE)

    print("\n\n📝 步骤3：提交买入订单（⚠️ 真实交易！）")
    print(f"⚠️  警告：执行此步骤将提交真实买入订单！")
    print(f"⚠️  基金代码: {FUND_CODE}")
    print(f"⚠️  买入金额: {AMOUNT} 元")
    buy_fund_step3_submit(FUND_CODE, AMOUNT, PASSWORD)

    print("\n\n" + "=" * 80)
    print("✅ 所有 JavaScript 代码已生成")
    print("请在 Chrome DevTools 中依次执行步骤0-3的代码")
    print("=" * 80)

if __name__ == "__main__":
    main()
