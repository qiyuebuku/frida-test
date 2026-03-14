#!/usr/bin/env python3
"""测试重构后的 task_executor.py 中 tmux 相关核心组件

测试项：
1. tmux 可用性检查
2. 噪音过滤 + 内容提取
3. ClaudeTmuxSession 创建/就绪/发送/接收
4. 多轮对话（上下文保持）
5. model 参数（haiku 会话）
6. 中断（Ctrl+C）
7. 会话清理
"""

import os
import sys
import time

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.task_executor import (
    _tmux, _is_noise, _extract_content, _is_at_prompt,
    ClaudeTmuxSession, SESSION_PREFIX, PROMPT_CHAR,
    TaskExecutor,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ==================== 1. 单元测试：噪音过滤 ====================

def test_noise_filter():
    print("=" * 60)
    print("测试 1: 噪音过滤 (_is_noise)")
    print("=" * 60)

    # 应该被过滤的噪音
    noise_lines = [
        "",
        "  ",
        PROMPT_CHAR,
        ">",
        "? for shortcuts",
        "╭─────────────────────╮",
        "│  some content  │",
        "╰─────────────────────╯",
        "───────────────",
        "medium · /effort high",
        "Claude Code v1.0.20",
        "Welcome back, yuyang",
        "Opus 4.6",
        "~/frida-test/some/path",
        "Checking for update...",
        "MCP server connected",
        "What's new in v1.0",
        "Recent activity",
        "No recent activity",
        "Run /init to set up",
        "ctrl+g to toggle",
        "◐ Loading...",
    ]

    # 不应该被过滤的正常内容
    content_lines = [
        "这是一个正常的回答",
        "1+1=2",
        "def hello(): pass",
        "基金持仓分析报告",
        "执行命令 ls -la",
    ]

    for line in noise_lines:
        check(f"过滤噪音: '{line[:30]}'", _is_noise(line))

    for line in content_lines:
        check(f"保留内容: '{line[:30]}'", not _is_noise(line))


# ==================== 2. 单元测试：内容提取 ====================

def test_extract_content():
    print("\n" + "=" * 60)
    print("测试 2: 内容提取 (_extract_content)")
    print("=" * 60)

    screen = """╭─────────────────────╮
│  Claude Code v1.0   │
╰─────────────────────╯

这是第一行有意义的内容
这是第二行

medium · /effort high
❯
"""
    lines = _extract_content(screen)
    check("提取有意义行", len(lines) == 2,
          f"期望 2 行，实际 {len(lines)}: {lines}")
    if len(lines) >= 2:
        check("第一行正确", lines[0] == "这是第一行有意义的内容")
        check("第二行正确", lines[1] == "这是第二行")


# ==================== 3. 单元测试：提示符检测 ====================

def test_prompt_detection():
    print("\n" + "=" * 60)
    print("测试 3: 提示符检测 (_is_at_prompt)")
    print("=" * 60)

    has_prompt = f"""some output
more output

{PROMPT_CHAR}
"""
    no_prompt = """some output
more output
still generating...
"""

    check("检测到提示符", _is_at_prompt(has_prompt))
    check("未检测到提示符", not _is_at_prompt(no_prompt))


# ==================== 4. tmux 可用性 ====================

def test_tmux_available():
    print("\n" + "=" * 60)
    print("测试 4: tmux 可用性")
    print("=" * 60)

    output = _tmux("-V")
    check("tmux 已安装", "tmux" in output.lower(), f"输出: {output.strip()}")

    # TaskExecutor._check_tmux 不应抛异常
    try:
        TaskExecutor._check_tmux()
        check("_check_tmux 通过", True)
    except RuntimeError as e:
        check("_check_tmux 通过", False, str(e))


# ==================== 5. 集成测试：tmux 会话 ====================

def test_tmux_session_basic():
    """测试 ClaudeTmuxSession 基本功能：创建 → 发送 → 接收"""
    print("\n" + "=" * 60)
    print("测试 5: tmux 会话基本功能（创建/发送/接收）")
    print("=" * 60)

    session = ClaudeTmuxSession(task_id=99999)
    try:
        check("会话已就绪", session.ready)
        check("会话名包含前缀", session.session_name.startswith(SESSION_PREFIX))

        if not session.ready:
            print("  ⚠️ 会话未就绪，跳过后续测试")
            return

        # 发送简单问题
        session.mark()
        # 也把问题加入已知行
        q = "请只回答一个数字，不要解释，不要用工具：1+1等于几？"
        for line in q.split('\n'):
            stripped = line.strip()
            if stripped:
                session._known_lines.add(stripped)
        session.send(q)

        # 等待回答
        start = time.time()
        result_lines = []
        while time.time() - start < 45:
            time.sleep(1)
            new = session.get_new_lines()
            if new:
                result_lines.extend(new)
            if session.is_at_prompt() and result_lines:
                # 再等 2 秒确认稳定
                time.sleep(2)
                extra = session.get_new_lines()
                if extra:
                    result_lines.extend(extra)
                break

        result = "\n".join(result_lines)
        elapsed = time.time() - start
        print(f"  回答 ({elapsed:.1f}s): {result[:200]}")

        check("收到回答", len(result) > 0, f"结果为空")
        check("回答包含 '2'", "2" in result, f"结果: {result[:100]}")

    finally:
        session.close()
        # 确认会话已清理
        time.sleep(0.5)
        output = _tmux("has-session", "-t", session.session_name)
        # has-session 失败说明会话已关闭
        check("会话已清理", session.session_name not in _tmux("list-sessions"))


def test_tmux_session_followup():
    """测试追问（上下文保持）"""
    print("\n" + "=" * 60)
    print("测试 6: 追问（上下文保持）")
    print("=" * 60)

    session = ClaudeTmuxSession(task_id=99998)
    try:
        if not session.ready:
            check("会话就绪", False, "跳过")
            return

        # 第一轮
        session.mark()
        q1 = "请只回答一个数字，不要解释，不要用工具：1+1等于几？"
        for line in q1.split('\n'):
            if line.strip():
                session._known_lines.add(line.strip())
        session.send(q1)

        start = time.time()
        r1_lines = []
        while time.time() - start < 45:
            time.sleep(1)
            new = session.get_new_lines()
            if new:
                r1_lines.extend(new)
            if session.is_at_prompt() and r1_lines:
                time.sleep(2)
                extra = session.get_new_lines()
                if extra:
                    r1_lines.extend(extra)
                break

        r1 = "\n".join(r1_lines)
        print(f"  第一轮回答: {r1[:100]}")
        check("第一轮有回答", len(r1) > 0)

        # 第二轮：追问
        session.mark()
        q2 = "把刚才的结果乘以5，也只回答数字，不要用工具"
        for line in q2.split('\n'):
            if line.strip():
                session._known_lines.add(line.strip())
        session.send(q2)

        start = time.time()
        r2_lines = []
        while time.time() - start < 45:
            time.sleep(1)
            new = session.get_new_lines()
            if new:
                r2_lines.extend(new)
            if session.is_at_prompt() and r2_lines:
                time.sleep(2)
                extra = session.get_new_lines()
                if extra:
                    r2_lines.extend(extra)
                break

        r2 = "\n".join(r2_lines)
        print(f"  追问回答: {r2[:100]}")
        check("追问有回答", len(r2) > 0)
        check("上下文保持（含 '10'）", "10" in r2, f"结果: {r2[:100]}")

    finally:
        session.close()


def test_tmux_session_interrupt():
    """测试中断 + 继续对话"""
    print("\n" + "=" * 60)
    print("测试 7: 中断（Ctrl+C）+ 继续对话")
    print("=" * 60)

    session = ClaudeTmuxSession(task_id=99997)
    try:
        if not session.ready:
            check("会话就绪", False, "跳过")
            return

        # 发送长任务
        session.mark()
        q = "从1数到100，每个数字单独一行，不要用工具"
        for line in q.split('\n'):
            if line.strip():
                session._known_lines.add(line.strip())
        session.send(q)
        time.sleep(5)

        # 中断
        session.send_ctrl_c()
        time.sleep(3)

        # 检查是否回到提示符
        at_prompt = session._wait_for_prompt(timeout=10)
        check("中断后回到提示符", at_prompt)

        # 继续对话
        session.mark()
        q2 = "你好，只回答两个字：你好。不要用工具。"
        for line in q2.split('\n'):
            if line.strip():
                session._known_lines.add(line.strip())
        session.send(q2)

        start = time.time()
        r_lines = []
        while time.time() - start < 30:
            time.sleep(1)
            new = session.get_new_lines()
            if new:
                r_lines.extend(new)
            if session.is_at_prompt() and r_lines:
                time.sleep(2)
                extra = session.get_new_lines()
                if extra:
                    r_lines.extend(extra)
                break

        r = "\n".join(r_lines)
        print(f"  中断后回答: {r[:100]}")
        check("中断后能继续对话", len(r) > 0)

    finally:
        session.close()


def test_tmux_session_haiku():
    """测试 model=haiku 的会话"""
    print("\n" + "=" * 60)
    print("测试 8: model=haiku 会话")
    print("=" * 60)

    session = ClaudeTmuxSession(task_id=99996, model="haiku")
    try:
        check("haiku 会话名包含 model", "haiku" in session.session_name)
        check("model 属性正确", session.model == "haiku")
        check("haiku 会话就绪", session.ready)

        if not session.ready:
            return

        session.mark()
        q = "请只回答一个数字：2+3=？不要用工具。"
        for line in q.split('\n'):
            if line.strip():
                session._known_lines.add(line.strip())
        session.send(q)

        start = time.time()
        r_lines = []
        while time.time() - start < 30:
            time.sleep(1)
            new = session.get_new_lines()
            if new:
                r_lines.extend(new)
            if session.is_at_prompt() and r_lines:
                time.sleep(2)
                extra = session.get_new_lines()
                if extra:
                    r_lines.extend(extra)
                break

        r = "\n".join(r_lines)
        print(f"  haiku 回答: {r[:100]}")
        check("haiku 有回答", len(r) > 0)
        check("haiku 回答含 '5'", "5" in r, f"结果: {r[:100]}")

    finally:
        session.close()


# ==================== 主程序 ====================

if __name__ == "__main__":
    print("task_executor.py tmux 重构测试")
    print()

    # 单元测试（快速，无外部依赖）
    test_noise_filter()
    test_extract_content()
    test_prompt_detection()
    test_tmux_available()

    # 集成测试（需要 tmux + claude CLI）
    tests = [
        ("基本功能", test_tmux_session_basic),
        ("追问", test_tmux_session_followup),
        ("中断", test_tmux_session_interrupt),
        ("haiku 模型", test_tmux_session_haiku),
    ]

    # 支持选择性运行：python test_task_executor_tmux.py 5
    if len(sys.argv) > 1:
        idx = int(sys.argv[1]) - 5  # 集成测试从 5 开始
        if 0 <= idx < len(tests):
            tests = [tests[idx]]
        else:
            print(f"可选: 5-{4 + len(tests)}")
            sys.exit(1)

    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"  ❌ 异常: {e}")
            traceback.print_exc()
            FAIL += 1
        print()

    # 汇总
    print("=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过, {FAIL} 失败")
    if FAIL == 0:
        print("✅ 全部通过")
    else:
        print("❌ 有失败项")
    print("=" * 60)
    sys.exit(1 if FAIL else 0)
