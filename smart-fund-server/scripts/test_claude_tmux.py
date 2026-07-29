#!/usr/bin/env python3
"""
通过 tmux 控制 Claude CLI 交互模式

核心思路：
- tmux 中启动 claude → send-keys 发送输入 → capture-pane -p 获取纯文本屏幕
- 通过对比前后屏幕内容差异来提取 Claude 的回答
- 支持：实时流式输出 / 追问（上下文保持）/ 随时中断
"""

import subprocess
import sys
import time
import re

SESSION_NAME = "claude_test"
# Claude CLI 的输入提示符
PROMPT_CHAR = "❯"


def tmux(*args) -> str:
    """执行 tmux 命令"""
    result = subprocess.run(
        ["tmux"] + list(args),
        capture_output=True, text=True, timeout=10
    )
    return result.stdout


def capture_pane(start_line: int = 0) -> str:
    """捕获 tmux 窗格的纯文本内容（自动去掉 ANSI）

    Args:
        start_line: 从滚动缓冲区的哪一行开始 (负数=历史行)
    """
    # -p: 输出到 stdout; -S: 起始行; -E: 结束行
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", SESSION_NAME, "-p", "-S", str(start_line)],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout


def send_keys(text: str, enter: bool = True):
    """向 tmux 窗格发送按键"""
    tmux("send-keys", "-t", SESSION_NAME, text, "Enter" if enter else "")


def send_ctrl_c():
    """发送 Ctrl+C"""
    tmux("send-keys", "-t", SESSION_NAME, "C-c")


def is_at_prompt(screen: str) -> bool:
    """检查屏幕中是否有输入提示符（Claude 准备好接受输入）"""
    lines = screen.strip().split('\n')
    # 检查最后几行是否有提示符
    for line in lines[-5:]:
        if PROMPT_CHAR in line:
            return True
    return False


def extract_content(screen: str) -> list[str]:
    """从屏幕内容中提取有意义的文本行"""
    lines = []
    for line in screen.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        # 过滤 TUI 噪音
        if any(c in stripped for c in '╭╰│╮╯▐▛▜▘▝'):
            continue
        if stripped.startswith('─' * 5):
            continue
        if stripped in (PROMPT_CHAR, '>', '?', '? for shortcuts'):
            continue
        if any(p in stripped for p in [
            'medium · /effort', 'Claude Code v', 'Welcome back',
            'Opus 4.6', '~/frida-test', '~/…/',
            'Checking for update', 'MCP server',
            'What\'s new', 'Added ', 'Recent activity',
            'No recent activity', 'Tips for getting',
            'Run /init', '/release-notes', '/resume for more',
            'Opus now defaults', '⧉ In ', 'ctrl+g',
            '? for shortcu', 'Brewed for',
        ]):
            continue
        lines.append(stripped)
    return lines


class ClaudeTmuxSession:
    """通过 tmux 控制 Claude CLI"""

    def __init__(self, timeout=60):
        self.timeout = timeout
        self._known_lines = set()  # 已知的屏幕内容（用于 diff）

        # 清理旧 session
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME],
            capture_output=True, timeout=5
        )

        # 创建新 session，启动 claude
        print("[启动] 在 tmux 中启动 Claude CLI ...")
        tmux("new-session", "-d", "-s", SESSION_NAME,
             "-x", "120", "-y", "40", "claude")

        # 等待 Claude CLI 初始化
        self._wait_for_prompt(timeout=20)

        # 记录初始屏幕内容
        initial = capture_pane(-500)
        self._known_lines = set(extract_content(initial))

        print("[就绪] Claude CLI 已启动\n")

    def _wait_for_prompt(self, timeout: int = 30) -> bool:
        """等待提示符出现"""
        start = time.time()
        while time.time() - start < timeout:
            screen = capture_pane()
            if is_at_prompt(screen):
                return True
            time.sleep(0.5)
        print("[!] 等待提示符超时")
        return False

    def ask(self, question: str, stream: bool = True) -> str:
        """发送问题，等待回答

        Args:
            question: 问题文本
            stream: 是否实时输出新内容

        Returns:
            Claude 的回答文本
        """
        print(f"[问] {question}")

        # 记录发送前的屏幕内容
        before = capture_pane(-500)
        before_lines = set(extract_content(before))
        # 也加入问题本身（避免把问题当回答）
        before_lines.add(question.strip())
        self._known_lines.update(before_lines)

        # 发送问题
        send_keys(question)

        # 等待回答
        start = time.time()
        last_content = ""
        idle_count = 0
        printed_lines = set()  # 已打印过的行（避免重复）

        while time.time() - start < self.timeout:
            time.sleep(1)

            screen = capture_pane(-500)
            current_lines = extract_content(screen)

            # 新增的行 = 当前行 - 已知行
            new_lines = [l for l in current_lines if l not in self._known_lines]

            if stream and new_lines:
                for line in new_lines:
                    if line not in printed_lines:
                        print(f"  {line}")
                        printed_lines.add(line)

            # 检测回答是否结束
            content = '\n'.join(current_lines)
            if content == last_content:
                idle_count += 1
                # 屏幕内容稳定 + 出现提示符 = 回答完成
                if idle_count >= 2 and is_at_prompt(screen):
                    break
            else:
                idle_count = 0
                last_content = content

        # 提取回答（新内容）
        final_screen = capture_pane(-500)
        final_lines = extract_content(final_screen)
        reply_lines = [l for l in final_lines if l not in self._known_lines]

        # 更新已知内容
        self._known_lines.update(set(final_lines))

        reply = '\n'.join(reply_lines)
        elapsed = time.time() - start
        print(f"[完成] {elapsed:.1f}s, {len(reply)} 字符\n")
        return reply

    def interrupt(self):
        """Ctrl+C 中断当前生成"""
        print("[中断] 发送 Ctrl+C")
        send_ctrl_c()
        time.sleep(2)

    def close(self):
        """退出"""
        print("[退出] 关闭 tmux session")
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME],
            capture_output=True, timeout=5
        )


# ==================== 测试 ====================


def test_basic_and_followup():
    """测试基本对话 + 追问"""
    print("=" * 60)
    print("测试 1: 基本对话 + 追问（上下文保持）")
    print("=" * 60)

    cli = ClaudeTmuxSession(timeout=45)
    try:
        r1 = cli.ask("请只回答一个数字，不要解释，不要用工具：1+1等于几？")
        print(f"  回答1: [{r1}]")

        r2 = cli.ask("把刚才的结果乘以5，同样只回答数字，不要用工具")
        print(f"  回答2: [{r2}]")

        has_2 = '2' in r1
        has_10 = '10' in r2
        print(f"  第一题含'2': {'✅' if has_2 else '❌'}")
        print(f"  追问含'10' (上下文保持): {'✅' if has_10 else '❌'}")
        return has_2 and has_10
    finally:
        cli.close()


def test_interrupt():
    """测试中断 + 继续对话"""
    print("=" * 60)
    print("测试 2: 中断生成 + 继续对话")
    print("=" * 60)

    cli = ClaudeTmuxSession(timeout=45)
    try:
        # 发长文请求
        send_keys("写一篇500字的短文，主题是人工智能的未来。不要用任何工具。")
        time.sleep(5)

        # 中断
        cli.interrupt()

        # 继续对话
        r = cli.ask("你好，只回答两个字：你好")
        print(f"  中断后回答: [{r}]")
        return len(r) > 0
    finally:
        cli.close()


def test_realtime_stream():
    """测试实时流式输出"""
    print("=" * 60)
    print("测试 3: 实时流式输出")
    print("=" * 60)

    cli = ClaudeTmuxSession(timeout=45)
    try:
        print("[*] 以下为实时输出:")
        print("-" * 40)
        r = cli.ask("从1数到5，每个数字单独一行，不要用工具，不要额外解释")
        print("-" * 40)
        print(f"  完整回答: [{r}]")

        # 检查是否包含 1-5
        has_nums = all(str(i) in r for i in range(1, 6))
        print(f"  包含1-5: {'✅' if has_nums else '❌'}")
        return has_nums
    finally:
        cli.close()


def test_mid_task_message():
    """测试：任务执行中发送新消息"""
    print("=" * 60)
    print("测试 4: 任务执行中插入消息（核心能力）")
    print("=" * 60)

    cli = ClaudeTmuxSession(timeout=90)
    try:
        # 发一个需要较长时间的任务
        send_keys("从1数到50，每个数字单独一行，不要用工具")

        # 等 Claude 开始回答
        time.sleep(5)
        print("[*] Claude 正在回答中，此时插入新消息...")

        # 在生成过程中直接输入新消息
        send_keys("停下来，现在告诉我你之前数到了几")

        # 等待处理完
        cli._wait_for_prompt(timeout=60)
        time.sleep(2)

        # 捕获最终屏幕
        screen = capture_pane(-1000)
        lines = extract_content(screen)
        reply = '\n'.join(lines[-20:])  # 取最后20行

        print(f"  最终屏幕（最后几行）:")
        for line in lines[-15:]:
            print(f"    {line}")

        # 如果 Claude 回应了插入的消息，说明成功
        success = '数' in reply or '到' in reply or any(str(i) in reply for i in range(5, 50))
        print(f"\n  执行中消息插入: {'✅ 成功' if success else '❌ 未检测到响应'}")
        return success
    finally:
        cli.close()


if __name__ == "__main__":
    print("Claude CLI + tmux 交互测试")
    print()

    tests = [
        ("基本对话+追问", test_basic_and_followup),
        ("中断生成", test_interrupt),
        ("实时流式输出", test_realtime_stream),
        ("执行中消息插入", test_mid_task_message),
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
