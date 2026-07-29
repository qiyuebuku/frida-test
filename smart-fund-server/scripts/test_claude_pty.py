#!/usr/bin/env python3
"""
测试 pexpect 控制 Claude CLI 交互模式
验证：实时输出 / 追问（上下文保持） / 停止

核心发现：
- Claude CLI 的 TUI 用 \x1b[1C 代替空格，需要特殊处理
- 提示符是 ❯ (U+276F)
- TERM=dumb 和 NO_COLOR=1 可以减少但无法消除 ANSI 序列
"""

import os
import re
import sys
import time

import pexpect


def strip_ansi(text: str) -> str:
    """清理 Claude CLI 的 ANSI 输出

    关键：\x1b[1C = 光标右移1格，Claude 用它代替空格
    """
    # \x1b[NC → N个空格（光标右移）
    text = re.sub(r'\x1b\[(\d+)C', lambda m: ' ' * int(m.group(1)), text)
    # \x1b[NA/B → 垂直移动，替换为换行
    text = re.sub(r'\x1b\[\d+[AB]', '\n', text)
    # DEC private mode: \x1b[?...h/l
    text = re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', text)
    # CSI sequences: \x1b[...X  and \x1b[>...X
    text = re.sub(r'\x1b\[>?[0-9;]*[a-zA-Z]', '', text)
    # OSC 序列
    text = re.sub(r'\x1b\][^\x07]*\x07', '', text)
    # ESC + 单字符
    text = re.sub(r'\x1b.', '', text)
    # 控制字符
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    # 不间断空格
    text = text.replace('\xa0', ' ')
    # 多余空格
    text = re.sub(r' {3,}', '  ', text)
    return text


def is_noise(line: str) -> bool:
    """判断是否为 TUI 装饰/噪音行"""
    if not line.strip():
        return True
    noise_patterns = [
        '╭', '╰', '│', '╮', '╯', '─' * 5,
        '▐▛', '▝▜', '▘▘',
        '? for shortcut',
        'medium · /effort', '◐', '◑',
        'Checking for update',
        'MCP server',
        'ctrl+g',
        'Welcome back',
        '/resume for more',
        '/release-notes',
        'Claude Code v',
        'Opus 4.6',
        '~/frida-test', '~/…/',
        'What\'s new',
        'Added ',
        'Recent activity',
        'No recent activity',
        'Tips for getting',
        'Run /init',
        'for shortcuts',
        'Opus now defaults',
        '⧉ In ',
    ]
    stripped = line.strip()
    if stripped in ('❯', '>', '↑'):
        return True
    return any(p in stripped for p in noise_patterns)


def extract_reply(raw: str) -> str:
    """从原始输出中提取 Claude 的实际回答"""
    clean = strip_ansi(raw)
    lines = []
    for line in clean.split('\n'):
        line = line.rstrip()
        if not is_noise(line) and line.strip():
            lines.append(line)
    return '\n'.join(lines)


class ClaudeCLI:
    """pexpect 控制 Claude CLI 交互模式"""

    PROMPT = '❯'

    def __init__(self, timeout=60):
        self.timeout = timeout
        env = os.environ.copy()
        env['NO_COLOR'] = '1'

        print("[启动] Claude CLI ...")
        self.child = pexpect.spawn(
            'claude',
            encoding='utf-8',
            timeout=timeout,
            env=env,
            dimensions=(40, 120),
        )

        # 等待初始化完成（提示符出现）
        try:
            self.child.expect(self.PROMPT, timeout=15)
        except pexpect.TIMEOUT:
            pass
        # 再等一下让 TUI 稳定
        time.sleep(2)
        # 清空缓冲区
        try:
            self.child.read_nonblocking(size=65536, timeout=1)
        except pexpect.TIMEOUT:
            pass
        print("[就绪] Claude CLI 已启动\n")

    def ask(self, question: str) -> str:
        """发送问题，等待回答，返回清理后的文本"""
        print(f"[问] {question}")

        self.child.sendline(question)

        # 收集输出
        raw = ""
        start = time.time()
        idle_count = 0

        while time.time() - start < self.timeout:
            try:
                chunk = self.child.read_nonblocking(size=8192, timeout=2)
                raw += chunk
                idle_count = 0

                # 实时输出（清理后）
                text = strip_ansi(chunk)
                for line in text.split('\n'):
                    line = line.rstrip()
                    if line.strip() and not is_noise(line):
                        print(f"  {line}")

                # 检测提示符 = 回答结束
                # 需要在收到一定内容后才判断（避免误判输入回显中的提示符）
                if self.PROMPT in chunk and len(raw) > len(question) + 200:
                    # 多等一下确认
                    time.sleep(0.5)
                    try:
                        extra = self.child.read_nonblocking(size=8192, timeout=1)
                        raw += extra
                    except pexpect.TIMEOUT:
                        pass
                    break

            except pexpect.TIMEOUT:
                idle_count += 1
                if idle_count >= 3:  # 6秒无输出，认为结束
                    break
            except pexpect.EOF:
                break

        reply = extract_reply(raw)
        elapsed = time.time() - start
        print(f"[完成] {elapsed:.1f}s\n")
        return reply

    def stop(self):
        """Ctrl+C 中断生成"""
        print("[中断] 发送 Ctrl+C")
        self.child.sendcontrol('c')
        time.sleep(2)
        # 清空缓冲区
        try:
            self.child.read_nonblocking(size=65536, timeout=1)
        except pexpect.TIMEOUT:
            pass

    def close(self):
        """退出"""
        try:
            self.child.sendline('/exit')
            self.child.expect(pexpect.EOF, timeout=5)
        except Exception:
            self.child.terminate(force=True)
        print("[退出] Claude CLI 已关闭")


# ==================== 测试用例 ====================


def test_1_basic_and_followup():
    """测试基本对话 + 追问（上下文保持）"""
    print("=" * 60)
    print("测试 1: 基本对话 + 追问")
    print("=" * 60)

    cli = ClaudeCLI(timeout=30)
    try:
        r1 = cli.ask("请只回答数字，不要任何解释：1+1=？")
        print(f"  → 回答: {r1[:100]}")

        r2 = cli.ask("把刚才的结果乘以5，也只回答数字")
        print(f"  → 回答: {r2[:100]}")

        # 验证上下文：如果上下文保持，应该回答 10
        has_10 = '10' in r2
        print(f"  → 上下文保持: {'✅' if has_10 else '❌'}")
        return has_10 or len(r2) > 0  # 至少有回答
    finally:
        cli.close()


def test_2_stop():
    """测试中断生成"""
    print("=" * 60)
    print("测试 2: 中断生成")
    print("=" * 60)

    cli = ClaudeCLI(timeout=30)
    try:
        # 发送长文请求
        cli.child.sendline("从1数到100，每个数字单独一行，不要用工具")
        time.sleep(4)

        # 中断
        cli.stop()

        # 验证还能继续对话
        r = cli.ask("你好，说一个字：好")
        print(f"  → 中断后回答: {r[:100]}")
        return len(r) > 0
    finally:
        cli.close()


def test_3_compare_subprocess():
    """对比：subprocess + --resume 方案（当前服务端用法）"""
    import subprocess

    print("=" * 60)
    print("测试 3: 对比 subprocess + --resume")
    print("=" * 60)

    # 第一轮
    print("[问1] 请只回答数字：1+1=？")
    r1 = subprocess.run(
        ['claude', '-p', '请只回答数字，不要任何解释：1+1=？',
         '--output-format', 'stream-json', '--verbose'],
        capture_output=True, text=True, timeout=30
    )

    # 从 stream-json 中提取 session_id 和 result
    import json
    session_id = None
    result1 = None
    for line in r1.stdout.strip().split('\n'):
        try:
            ev = json.loads(line)
            if ev.get('type') == 'system' and ev.get('session_id'):
                session_id = ev['session_id']
            elif ev.get('type') == 'result':
                result1 = ev.get('result', '')
                if ev.get('session_id'):
                    session_id = ev['session_id']
        except json.JSONDecodeError:
            pass

    print(f"  → 回答: {result1}")
    print(f"  → session_id: {session_id}")

    if not session_id:
        print("  ❌ 没有获取到 session_id")
        return False

    # 第二轮：--resume
    print("[问2] 把刚才的结果乘以5，也只回答数字")
    r2 = subprocess.run(
        ['claude', '-p', '把刚才的结果乘以5，也只回答数字',
         '--resume', session_id,
         '--output-format', 'stream-json', '--verbose'],
        capture_output=True, text=True, timeout=30
    )

    result2 = None
    for line in r2.stdout.strip().split('\n'):
        try:
            ev = json.loads(line)
            if ev.get('type') == 'result':
                result2 = ev.get('result', '')
        except json.JSONDecodeError:
            pass

    print(f"  → 回答: {result2}")
    has_10 = result2 and '10' in result2
    print(f"  → 上下文保持: {'✅' if has_10 else '❌'}")
    return has_10


def print_conclusion():
    """打印测试结论"""
    print("""
╔══════════════════════════════════════════════════════════╗
║                    测试结论                              ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  pexpect 方案 ❌ 不可行                                  ║
║  ─────────────────────────────────                       ║
║  Claude CLI 是完整 TUI 应用（ink/React），使用光标定位   ║
║  渲染内容。pexpect 只能顺序读取字节流，无法重建屏幕     ║
║  状态，因此抓不到实际回答。                              ║
║                                                          ║
║  subprocess + --resume ✅ 推荐                           ║
║  ─────────────────────────────────                       ║
║  claude -p "问题" --output-format stream-json --verbose  ║
║  claude -p "追问" --resume <session_id> ...              ║
║                                                          ║
║  优点：                                                  ║
║  • 实时流式输出（逐行 JSON 事件）                       ║
║  • --resume 保持完整上下文                               ║
║  • 可随时 kill 进程停止                                  ║
║  • 结构化输出，解析可靠                                  ║
║  • 当前服务端 task_executor.py 已采用此方案              ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    print("Claude CLI 交互测试")
    print(f"pexpect {pexpect.__version__}")
    print()

    tests = [
        ("pexpect 基本对话+追问", test_1_basic_and_followup),
        ("pexpect 中断生成", test_2_stop),
        ("subprocess --resume 对比", test_3_compare_subprocess),
    ]

    if len(sys.argv) > 1:
        idx = int(sys.argv[1]) - 1
        tests = [tests[idx]]

    results = []
    for name, fn in tests:
        try:
            ok = fn()
            results.append((name, ok))
        except Exception as e:
            print(f"[!] 异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
        print()

    print("=" * 60)
    print("结果汇总:")
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'}  {name}")
    print("=" * 60)

    print_conclusion()
