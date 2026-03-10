#!/usr/bin/env python3
"""client.py 自动化测试 - 通过子进程调用 client.py 验证所有命令

用法:
  python test_client.py              # 运行全部测试
  python test_client.py --quick      # 只测试核心命令（约30秒）
  python test_client.py --group 基金详情  # 只测试指定分组
  python test_client.py --list       # 列出所有测试分组

测试策略:
  - 通过 subprocess 调用 client.py，检查退出码和输出
  - 交易操作：真实买入最低金额 → 立即撤单（不会产生实际扣款）
  - 不需要手机连接（refresh-token 跳过）
  - 超时限制：单个测试最多 15 秒
"""

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_PY = os.path.join(SCRIPT_DIR, "client.py")
PYTHON = sys.executable
TIMEOUT = 15  # 单个测试超时秒数

TEST_FUND = "006888"
TEST_STOCK = "600519"
FAKE_FUND = "999999"       # 不存在的基金，用于测试错误处理
FAKE_ORDER = "00000000000000000000"  # 不存在的订单号


@dataclass
class TestResult:
    name: str
    group: str
    passed: bool
    duration: float = 0.0
    error: str = ""


@dataclass
class TestSuite:
    results: list = field(default_factory=list)

    def add(self, result: TestResult):
        self.results.append(result)

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        print(f"\n{'='*60}")
        print(f"测试结果: {passed}/{total} 通过, {failed} 失败")
        print(f"总耗时: {sum(r.duration for r in self.results):.1f}s")

        if failed > 0:
            print(f"\n失败的测试:")
            for r in self.results:
                if not r.passed:
                    print(f"  [{r.group}] {r.name}: {r.error}")
        print(f"{'='*60}")
        return failed == 0


def run_client(*args) -> subprocess.CompletedProcess:
    """调用 client.py 并返回结果"""
    cmd = [PYTHON, CLIENT_PY] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    )


def run_test(suite: TestSuite, name: str, group: str, *client_args, expect_output=None):
    """运行单个测试：调用 client.py 并检查退出码"""
    start = time.time()
    try:
        result = run_client(*client_args)
        duration = time.time() - start

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            if len(err) > 120:
                err = err[:120] + "..."
            suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=f"exit={result.returncode}: {err}"))
            print(f"  \u274c {name} ({duration:.1f}s) - exit={result.returncode}")
            return

        if expect_output and expect_output not in result.stdout:
            suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=f"输出中未找到 '{expect_output}'"))
            print(f"  \u274c {name} ({duration:.1f}s) - 输出不符")
            return

        suite.add(TestResult(name=name, group=group, passed=True, duration=duration))
        print(f"  \u2705 {name} ({duration:.1f}s)")

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=f"超时 ({TIMEOUT}s)"))
        print(f"  \u274c {name} ({duration:.1f}s) - 超时")

    except Exception as e:
        duration = time.time() - start
        err = str(e)[:120]
        suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=err))
        print(f"  \u274c {name} ({duration:.1f}s) - {err}")


def run_test_expect_fail(suite: TestSuite, name: str, group: str, *client_args, expect_stderr=None):
    """运行单个测试：预期命令失败（exit!=0），验证错误处理正常"""
    start = time.time()
    try:
        result = run_client(*client_args)
        duration = time.time() - start

        if result.returncode == 0:
            suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error="预期失败但命令成功了"))
            print(f"  \u274c {name} ({duration:.1f}s) - 预期失败但成功了")
            return

        # 命令失败了（符合预期），检查是否有预期的错误信息
        output = result.stderr.strip() or result.stdout.strip()
        if expect_stderr and expect_stderr not in output:
            suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=f"错误输出中未找到 '{expect_stderr}'"))
            print(f"  \u274c {name} ({duration:.1f}s) - 错误输出不符")
            return

        suite.add(TestResult(name=name, group=group, passed=True, duration=duration))
        print(f"  \u2705 {name} ({duration:.1f}s)")

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=f"超时 ({TIMEOUT}s)"))
        print(f"  \u274c {name} ({duration:.1f}s) - 超时")

    except Exception as e:
        duration = time.time() - start
        err = str(e)[:120]
        suite.add(TestResult(name=name, group=group, passed=False, duration=duration, error=err))
        print(f"  \u274c {name} ({duration:.1f}s) - {err}")



# ==================== 测试定义 ====================

def tests_health(suite: TestSuite):
    """系统健康"""
    run_test(suite, "认证状态", "系统", "auth-status")


def tests_fund_detail(suite: TestSuite):
    """基金详情"""
    run_test(suite, "基金综合详情", "基金详情", TEST_FUND, "detail")
    run_test(suite, "产品详情", "基金详情", TEST_FUND, "product")
    run_test(suite, "基金搜索", "基金详情", "search", "纳斯达克")


def tests_nav(suite: TestSuite):
    """净值走势"""
    run_test(suite, "净值走势", "净值", TEST_FUND, "nav", "--period", "month", "--count", "5")
    run_test(suite, "实时估值", "净值", TEST_FUND, "realtime")


def tests_performance(suite: TestSuite):
    """业绩表现"""
    run_test(suite, "阶段涨幅排名", "业绩", TEST_FUND, "rank")
    run_test(suite, "年度收益率", "业绩", TEST_FUND, "year_return")
    run_test(suite, "最大回撤", "业绩", TEST_FUND, "drawdown")
    run_test(suite, "收益稳定度", "业绩", TEST_FUND, "stability", "--count", "5")


def tests_holdings(suite: TestSuite):
    """持仓信息"""
    run_test(suite, "前十大持仓", "持仓", TEST_FUND, "holdings")
    run_test(suite, "持仓概览", "持仓", TEST_FUND, "hold_overview")
    run_test(suite, "投资风格", "持仓", TEST_FUND, "style")
    run_test(suite, "资产配置", "持仓", TEST_FUND, "asset")
    run_test(suite, "持仓估值", "持仓", TEST_FUND, "valuation")


def tests_manager(suite: TestSuite):
    """基金经理"""
    run_test(suite, "经理档案", "基金经理", TEST_FUND, "manager")


def tests_technical(suite: TestSuite):
    """技术面"""
    run_test(suite, "净值技术面", "技术面", TEST_FUND, "nav_technical")
    run_test(suite, "RSI指标", "技术面", TEST_FUND, "rsi")


def tests_market(suite: TestSuite):
    """大盘与市场"""
    run_test(suite, "大盘总览", "市场", "market_overview", "--count", "3")
    run_test(suite, "市场环境", "市场", TEST_FUND, "market")
    run_test(suite, "资金流向", "市场", "capital_flow", "--cf-days", "5")
    run_test(suite, "板块排行", "市场", "sector_rank", "--count", "5")
    run_test(suite, "个股排行", "市场", "stock_rank", "--count", "5")
    run_test(suite, "货币风向", "市场", "currency", "--currency-days", "30")


def tests_hotlist(suite: TestSuite):
    """热榜"""
    run_test(suite, "个股热榜", "热榜", "hotlist", "--count", "5")
    run_test(suite, "热榜话题", "热榜", "hotlist_topics")
    run_test(suite, "推荐头条", "热榜", "headlines")
    run_test(suite, "快讯", "热榜", "flash_news", "--tag", "重要", "--count", "3")
    run_test(suite, "滚动快讯", "热榜", "news_feed")


def tests_ranking(suite: TestSuite):
    """基金排行"""
    run_test(suite, "基金排行", "排行", "ranking", "--count", "5")
    run_test(suite, "策略筛选", "排行", "screen")
    run_test(suite, "基金公司", "排行", "companies")


def tests_trade_account(suite: TestSuite):
    """交易账户（只读）"""
    run_test(suite, "账户总览", "交易账户", "account")
    run_test(suite, "基金持仓", "交易账户", "positions")
    run_test(suite, "活期宝", "交易账户", "wallet")
    run_test(suite, "订单列表", "交易账户", "orders")


def tests_trade_rule(suite: TestSuite):
    """交易规则"""
    run_test(suite, "交易规则", "交易规则", TEST_FUND, "trade_rule")
    run_test(suite, "规模变动", "交易规则", TEST_FUND, "scale_change")
    run_test(suite, "分红历史", "交易规则", TEST_FUND, "dividend")


def tests_stock(suite: TestSuite):
    """个股查询"""
    run_test(suite, "个股行情", "个股", "stock_quote", TEST_STOCK)
    run_test(suite, "K线数据", "个股", "stock_kline", TEST_STOCK, "--count", "5")
    run_test(suite, "资金流向", "个股", "stock_flow", TEST_STOCK, "--stock-days", "5")


def tests_decisions(suite: TestSuite):
    """决策管理"""
    run_test(suite, "今日决策", "决策", "today-decisions")
    run_test(suite, "最近决策", "决策", "recent-decisions")
    run_test(suite, "本地持仓", "决策", "positions")
    run_test(suite, "观望天数", "决策", "watch-streaks")


def tests_risk(suite: TestSuite):
    """风控模块"""
    run_test(suite, "风控快照", "风控", "snapshot")
    run_test(suite, "前置检查", "风控", "preflight")


def tests_other(suite: TestSuite):
    """其他功能"""
    run_test(suite, "基金公告", "其他", TEST_FUND, "announcements", "--count", "3")
    run_test(suite, "基金资讯", "其他", TEST_FUND, "news", "--count", "3")
    run_test(suite, "基金追踪", "其他", TEST_FUND, "fund_flow")
    run_test(suite, "持仓变动", "其他", TEST_FUND, "position")


def tests_trade_error(suite: TestSuite):
    """交易错误处理"""
    # 买入-缺少金额
    run_test_expect_fail(suite, "买入-缺少金额", "交易异常", "buy", TEST_FUND)
    # 买入-金额为0
    run_test_expect_fail(suite, "买入-金额为0", "交易异常", "buy", TEST_FUND, "--amount", "0")
    # 买入-不存在的基金
    run_test_expect_fail(suite, "买入-无效基金", "交易异常", "buy", FAKE_FUND, "--amount", "10")
    # 赎回-未持仓基金
    run_test_expect_fail(suite, "赎回-未持仓基金", "交易异常", "sell", FAKE_FUND, "--all")
    # 撤单-无效订单号
    run_test_expect_fail(suite, "撤单-无效订单", "交易异常", "cancel", FAKE_ORDER)


def tests_trade_buy_cancel(suite: TestSuite):
    """买入→撤单 完整流程（真实买入100元，立即撤单，不会实际扣款）"""
    group = "买入撤单"
    buy_fund = "022365"  # 永赢科技智选C，最低买入100元

    # Step 1: 买入
    start = time.time()
    try:
        result = run_client("buy", buy_fund, "--amount", "100")
        duration = time.time() - start

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            if len(err) > 120:
                err = err[:120] + "..."
            suite.add(TestResult(name="买入100元", group=group, passed=False, duration=duration, error=f"exit={result.returncode}: {err}"))
            print(f"  \u274c 买入100元 ({duration:.1f}s) - exit={result.returncode}")
            print(f"     跳过后续撤单测试")
            return

        # 从输出中提取订单号
        order_no = ""
        for line in result.stdout.splitlines():
            m = re.search(r"订单号:\s*(\d{10,})", line)
            if m:
                order_no = m.group(1)
                break

        if not order_no:
            suite.add(TestResult(name="买入100元", group=group, passed=False, duration=duration, error="输出中未找到订单号"))
            print(f"  \u274c 买入100元 ({duration:.1f}s) - 未找到订单号")
            print(f"     stdout: {result.stdout[:200]}")
            return

        suite.add(TestResult(name="买入100元", group=group, passed=True, duration=duration))
        print(f"  \u2705 买入100元 ({duration:.1f}s) - 订单号: {order_no}")

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        suite.add(TestResult(name="买入100元", group=group, passed=False, duration=duration, error=f"超时 ({TIMEOUT}s)"))
        print(f"  \u274c 买入100元 ({duration:.1f}s) - 超时")
        return
    except Exception as e:
        duration = time.time() - start
        suite.add(TestResult(name="买入100元", group=group, passed=False, duration=duration, error=str(e)[:120]))
        print(f"  \u274c 买入100元 ({duration:.1f}s) - {e}")
        return

    # Step 2: 查询订单确认存在
    run_test(suite, f"查询订单 {order_no[:8]}...", group, "order", order_no)

    # Step 3: 撤单
    start = time.time()
    try:
        result = run_client("cancel", order_no)
        duration = time.time() - start

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            if len(err) > 120:
                err = err[:120] + "..."
            suite.add(TestResult(name="撤单", group=group, passed=False, duration=duration, error=f"exit={result.returncode}: {err}"))
            print(f"  \u274c 撤单 ({duration:.1f}s) - exit={result.returncode}")
            return

        # 检查撤单结果
        if "成功" in result.stdout:
            suite.add(TestResult(name="撤单", group=group, passed=True, duration=duration))
            print(f"  \u2705 撤单 ({duration:.1f}s)")
        else:
            suite.add(TestResult(name="撤单", group=group, passed=False, duration=duration, error="输出中未找到'成功'"))
            print(f"  \u274c 撤单 ({duration:.1f}s) - 撤单结果不明")

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        suite.add(TestResult(name="撤单", group=group, passed=False, duration=duration, error=f"超时 ({TIMEOUT}s)"))
        print(f"  \u274c 撤单 ({duration:.1f}s) - 超时")
    except Exception as e:
        duration = time.time() - start
        suite.add(TestResult(name="撤单", group=group, passed=False, duration=duration, error=str(e)[:120]))
        print(f"  \u274c 撤单 ({duration:.1f}s) - {e}")


# ==================== 测试分组注册 ====================

ALL_GROUPS = {
    "系统": tests_health,
    "基金详情": tests_fund_detail,
    "净值": tests_nav,
    "业绩": tests_performance,
    "持仓": tests_holdings,
    "基金经理": tests_manager,
    "技术面": tests_technical,
    "市场": tests_market,
    "热榜": tests_hotlist,
    "排行": tests_ranking,
    "交易账户": tests_trade_account,
    "交易规则": tests_trade_rule,
    "个股": tests_stock,
    "决策": tests_decisions,
    "风控": tests_risk,
    "其他": tests_other,
    "交易异常": tests_trade_error,
    "买入撤单": tests_trade_buy_cancel,
}

QUICK_GROUPS = ["系统", "基金详情", "业绩", "持仓", "市场", "决策", "风控"]


def main():
    parser = argparse.ArgumentParser(description="client.py 自动化测试")
    parser.add_argument("--quick", action="store_true", help="只测试核心命令")
    parser.add_argument("--group", type=str, help="只测试指定分组")
    parser.add_argument("--list", action="store_true", help="列出所有分组")
    args = parser.parse_args()

    if args.list:
        print("可用的测试分组:")
        for name in ALL_GROUPS:
            print(f"  {name}")
        return

    # 先检查服务是否可用
    try:
        result = run_client("auth-status")
        if result.returncode != 0 and "无法连接" in result.stderr:
            print(f"服务不可用，请先启动: cd smart-fund-server && python main.py")
            sys.exit(1)
    except Exception as e:
        print(f"检查服务失败: {e}")
        sys.exit(1)

    suite = TestSuite()

    if args.group:
        if args.group not in ALL_GROUPS:
            print(f"未知分组: {args.group}, 可用: {', '.join(ALL_GROUPS.keys())}")
            sys.exit(1)
        groups = {args.group: ALL_GROUPS[args.group]}
    elif args.quick:
        groups = {k: ALL_GROUPS[k] for k in QUICK_GROUPS}
    else:
        groups = ALL_GROUPS

    for group_name, test_fn in groups.items():
        print(f"\n[{group_name}]")
        test_fn(suite)

    all_passed = suite.summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
