#!/usr/bin/env python3
"""
起点读书 RPC API 封装模块

通过 ADB forward 的 TCP 端口（127.0.0.1:12345）与手机端 LocalSocket 通信。

用法（作为模块）:
    from qidian_api import QidianAPI
    api = QidianAPI()
    result = api.search("斗破苍穹")
    chapters = api.get_chapter_list("1209977")
    content = api.fetch_chapter("1209977", "23373921")

用法（CLI）:
    python qidian_api.py ping
    python qidian_api.py search 斗破苍穹
    python qidian_api.py chapter-list 1209977
    python qidian_api.py fetch 1209977 23373921
    python qidian_api.py book-detail 1209977
    python qidian_api.py review-summary 1209977 23373921
    python qidian_api.py paragraph-comments 1209977 23373921 5
    python qidian_api.py all-comments 1209977 23373921
"""

import json
import socket
import subprocess
import sys
import time
from typing import Optional


class RpcError(Exception):
    """RPC 调用失败"""
    pass


class QidianAPI:
    """起点读书 RPC API 客户端"""

    RPC_HOST = "127.0.0.1"
    RPC_PORT = 12345
    ADB_CMD = ["/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe", "-s", "3B15BJ00GZL00000"]
    PKG = "com.qidian.QDReader"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._last_restart_time = 0  # 防止频繁重启

    def _rpc(self, cmd: str, timeout: Optional[int] = None, **kwargs) -> dict:
        """底层 RPC 调用，直接 TCP 连接 ADB forward 端口"""
        request = {"cmd": cmd, **kwargs}
        t = timeout or self.timeout
        request_bytes = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(t)
        try:
            sock.connect((self.RPC_HOST, self.RPC_PORT))
            sock.sendall(request_bytes)

            buf = b""
            deadline = time.time() + t
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sock.settimeout(min(remaining, 5))
                try:
                    chunk = sock.recv(262144)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf:
                        break
                except socket.timeout:
                    continue

            line = buf.split(b"\n")[0].decode("utf-8", errors="replace").strip()
            if not line:
                raise RpcError("RPC 无响应")
            return json.loads(line)
        except socket.timeout:
            raise RpcError(f"RPC 超时 ({t}s)")
        except ConnectionRefusedError:
            raise RpcError("RPC 连接被拒绝，请检查 ADB forward 和 App 是否运行")
        except json.JSONDecodeError as e:
            raise RpcError(f"JSON 解析失败: {e}, raw={line[:500]}")
        finally:
            sock.close()

    def _rpc_ok(self, cmd: str, timeout: Optional[int] = None, **kwargs) -> dict:
        """调用 RPC 并检查 success 字段，失败时抛出异常"""
        resp = self._rpc(cmd, timeout=timeout, **kwargs)
        if not resp.get("success"):
            raise RpcError(resp.get("error", "未知错误"))
        return resp

    # ==================== App 控制 ====================

    def restart_app(self, max_wait: int = 30, min_interval: int = 30) -> bool:
        """
        重启 App 并等待 RPC 就绪。

        Args:
            max_wait: 最大等待秒数
            min_interval: 两次重启最小间隔（秒），防止频繁重启

        Returns:
            True=RPC 就绪, False=超时
        """
        now = time.time()
        since_last = now - self._last_restart_time
        if since_last < min_interval:
            wait = min_interval - since_last
            print(f"  [自动恢复] 距上次重启仅 {since_last:.0f}s，等待 {wait:.0f}s...")
            time.sleep(wait)

        self._last_restart_time = time.time()
        print(f"  [自动恢复] 重启 App...")

        # 强制停止
        try:
            subprocess.run(
                self.ADB_CMD + ["shell", "su", "-c", f"am force-stop {self.PKG}"],
                capture_output=True, timeout=10
            )
        except Exception:
            pass
        time.sleep(1)

        # 启动
        try:
            subprocess.run(
                self.ADB_CMD + ["shell", "monkey", "-p", self.PKG,
                                "-c", "android.intent.category.LAUNCHER", "1"],
                capture_output=True, timeout=10
            )
        except Exception:
            pass

        # 轮询等待 RPC 就绪
        for i in range(1, max_wait + 1):
            time.sleep(1)
            try:
                subprocess.run(
                    self.ADB_CMD + ["forward", f"tcp:{self.RPC_PORT}", "localabstract:qdhook_rpc"],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass
            try:
                self.ping()
                print(f"  [自动恢复] RPC 就绪 ({i}s)")
                return True
            except Exception:
                if i % 5 == 0:
                    print(f"  [自动恢复] 等待中... ({i}s)")

        print(f"  [自动恢复] 超时 ({max_wait}s)")
        return False

    # ==================== 系统命令 ====================

    def ping(self) -> str:
        """测试连接，返回 'pong'"""
        resp = self._rpc_ok("ping")
        return resp.get("data", "")

    def get_status(self) -> dict:
        """获取 Hook 状态"""
        resp = self._rpc_ok("getStatus")
        return resp.get("data", {})

    def test_chinese(self) -> dict:
        """测试中文编码"""
        return self._rpc("testChinese")

    # ==================== 诊断命令 ====================

    def get_logs(self, limit: int = 200, since: int = 0, tag: str = "") -> dict:
        """获取探测日志"""
        return self._rpc_ok("getLogs", limit=limit, since=since, tag=tag)

    def get_recent_decrypted(self, limit: int = 10) -> dict:
        """获取最近解密的内容"""
        return self._rpc_ok("getRecentDecrypted", limit=limit)

    def get_call_records(self, limit: int = 50) -> dict:
        """获取方法调用记录"""
        return self._rpc_ok("getCallRecords", limit=limit)

    def get_entries(self) -> dict:
        """获取已发现的入口方法"""
        return self._rpc_ok("getEntries")

    def clear_records(self) -> dict:
        """清空调用记录"""
        return self._rpc_ok("clearRecords")

    def dump_chapter_item(self) -> dict:
        """调试：dump 保存的 ChapterItem 字段"""
        return self._rpc_ok("dumpChapterItem")

    def probe_bll_v(self) -> dict:
        """探测 bll.v 实例"""
        return self._rpc_ok("probeBllV")

    # ==================== 搜索与书籍信息 ====================

    def search(self, keyword: str, page: int = 1) -> dict:
        """
        搜索书籍

        返回: RPC 完整响应（含 keyword, page, httpCode, response）
        response 字段包含起点 API 原始 JSON
        """
        resp = self._rpc_ok("search", timeout=15, keyword=keyword, page=page)
        return resp

    def get_search_template(self) -> dict:
        """查看已捕获的搜索请求模板"""
        resp = self._rpc_ok("getSearchTemplate")
        return resp.get("data", {})

    def book_detail(self, book_id: str) -> dict:
        """
        获取书籍详情

        返回: RPC 完整响应（含 bookId, httpCode, response）
        response 字段包含起点 API 原始 JSON
        """
        resp = self._rpc_ok("bookDetail", timeout=15, bookId=book_id)
        return resp

    def get_chapter_list(self, book_id: str) -> dict:
        """
        获取章节列表

        返回: RPC 完整响应（含 bookId, httpCode, response）
        response.Data 有两种格式——Vs[].Cs[]（卷结构）和 Chapters[]（扁平列表）
        """
        resp = self._rpc_ok("getChapterList", timeout=15, bookId=book_id)
        return resp

    # ==================== 章节内容 ====================

    def fetch_chapter(self, book_id: str, chapter_id: str, retries: int = 2) -> dict:
        """
        主动解密获取章节内容（通过 bll.v.L→K→R 调用链）

        返回格式: {"success": true, "data": {"bookId": "...", "chapterId": "...", "content": "..."}}
        遇到 "未购买" 或 "网络开小差" 等临时错误会自动重试。
        """
        last_err = None
        for attempt in range(1, retries + 2):  # retries=2 → 最多3次
            try:
                resp = self._rpc_ok("fetchChapter", timeout=30, bookId=book_id, chapterId=chapter_id)
                return resp.get("data", {})
            except RpcError as e:
                last_err = e
                err_msg = str(e)
                # 临时性错误才重试
                if any(kw in err_msg for kw in ("未购买", "网络开小差", "parse failed")):
                    if attempt <= retries:
                        time.sleep(2 * attempt)  # 递增等待: 2s, 4s
                        continue
                raise  # 非临时错误直接抛出
        raise last_err

    def test_fetch(self) -> dict:
        """测试获取（硬编码的书籍 1209977 / 章节 23373921）"""
        resp = self._rpc_ok("testFetch", timeout=30)
        return resp.get("data", {})

    def test_no_template(self, book_id: str, chapter_id: str) -> dict:
        """测试完全免 UI 路径：不使用 chapterItems 模板"""
        resp = self._rpc_ok("testNoTemplate", timeout=30, bookId=book_id, chapterId=chapter_id)
        return resp.get("data", {})

    def get_chapter_content(self, book_id: str, chapter_id: str) -> dict:
        """
        通过 API 获取章节内容（返回加密数据，需在端侧解密）

        返回: RPC 完整响应（含 httpCode, response）
        """
        resp = self._rpc_ok("getChapterContent", timeout=15, bookId=book_id, chapterId=chapter_id)
        return resp

    # ==================== 段评 ====================

    def get_chapter_review_summary(self, book_id: str, chapter_id: str) -> dict:
        """
        获取段评摘要

        返回: RPC 完整响应，response 包含 ParagraphId, CommentCount 列表
        """
        resp = self._rpc_ok("getChapterReviewSummary", timeout=15,
                            bookId=book_id, chapterId=chapter_id)
        return resp

    def get_paragraph_comments(self, book_id: str, chapter_id: str,
                               paragraph_id: int, page: int = 1,
                               page_size: int = 20, type: int = 0) -> dict:
        """
        获取指定段落的评论

        Args:
            type: 评论类型 0=全部, 1=配图, 2=配音
            paragraph_id: 段落 ID，-1 表示章评
        """
        resp = self._rpc_ok("getParagraphComments", timeout=15,
                            bookId=book_id, chapterId=chapter_id,
                            paragraphId=paragraph_id, page=page,
                            pageSize=page_size, type=type)
        return resp

    def get_comment_summary(self, book_id: str, chapter_id: str) -> dict:
        """
        轻量评论摘要（1 次 API 请求）

        返回: {"totalComments": N, "paragraphs": [{"paragraphId": P, "commentCount": C}, ...]}
        """
        resp = self._rpc_ok("getCommentSummary", timeout=15,
                            bookId=book_id, chapterId=chapter_id)
        return resp

    def get_all_paragraph_comments(self, book_id: str, chapter_id: str,
                                   page_size: int = 20, type: int = 0,
                                   include_chapter_comments: bool = False,
                                   fetch_replies: bool = True,
                                   only_paragraphs: list[int] = None,
                                   existing_replies: dict = None,
                                   max_comments_per_paragraph: int = 0) -> dict:
        """
        获取章节所有段评（自动翻页）

        Args:
            type: 评论类型 0=全部, 1=配图, 2=配音
            include_chapter_comments: 是否包含章评（ParagraphId=-1），默认不包含
            fetch_replies: 是否获取评论回复，默认开启
            only_paragraphs: 仅获取指定段落 ID 列表的评论（增量模式）
            existing_replies: 旧数据中评论的回复数映射 {str(评论Id): ReviewCount}，用于跳过未变化的回复
            max_comments_per_paragraph: 每个段落最多获取前 N 条评论（0=不限制），回复不计入
        """
        kwargs = dict(
            bookId=book_id, chapterId=chapter_id,
            pageSize=page_size, type=type,
            includeChapterComments=include_chapter_comments,
            fetchReplies=fetch_replies,
        )
        if only_paragraphs is not None:
            kwargs["onlyParagraphs"] = only_paragraphs
        if existing_replies is not None:
            kwargs["existingReplies"] = existing_replies
        if max_comments_per_paragraph > 0:
            kwargs["maxCommentsPerParagraph"] = max_comments_per_paragraph
        resp = self._rpc_ok("getAllParagraphComments", timeout=300, **kwargs)
        return resp

    # ==================== 便捷方法 ====================

    def fetch_chapter_text(self, book_id: str, chapter_id: str) -> str:
        """获取章节纯文本内容（便捷方法）"""
        data = self.fetch_chapter(book_id, chapter_id)
        return data.get("content", "")

    def search_books(self, keyword: str, max_pages: int = 1) -> list:
        """
        搜索书籍，返回 CardList 中的 Body 列表（便捷方法）

        Args:
            keyword: 搜索关键词
            max_pages: 最大搜索页数

        Returns:
            搜索结果 Body 列表
        """
        all_items = []
        for page in range(1, max_pages + 1):
            resp = self.search(keyword, page=page)
            api_data = resp.get("response", {})
            card_list = _deep_get(api_data, "Data", "CardList") or []
            items = []
            for card in card_list:
                for body_item in card.get("Body", []):
                    # 新版 API: 书籍数据嵌套在 ItemData 中
                    items.append(body_item.get("ItemData", body_item))
            if not items:
                break
            all_items.extend(items)
        return all_items

    def get_all_chapter_ids(self, book_id: str) -> list:
        """
        获取书籍所有章节 ID 列表（便捷方法）

        自动处理两种 API 格式：卷结构 和 扁平列表
        返回: [{"id": "...", "name": "...", "vip": bool, "words": int}, ...]
        """
        resp = self.get_chapter_list(book_id)
        raw = resp.get("response", {})

        chapters = []

        def _parse_chapter(ch):
            cid = ch.get("C", ch.get("Id", ch.get("id", "")))
            return {
                "id": str(cid),
                "name": ch.get("N", ch.get("n", "")),
                "vip": ch.get("V", 0) == 1,
                "words": ch.get("W", 0),
            }

        # 格式1: Data.Vs[].Cs[]（卷结构）
        vs = _deep_get(raw, "Data", "Vs")
        if vs:
            for vol in vs:
                for ch in vol.get("Cs", []):
                    chapters.append(_parse_chapter(ch))
            if chapters:
                return chapters

        # 格式2: Data.Chapters[]（扁平列表）
        ch_list = _deep_get(raw, "Data", "Chapters")
        if ch_list:
            for ch in ch_list:
                chapters.append(_parse_chapter(ch))
            return chapters

        return chapters


def _deep_get(d: dict, *keys):
    """安全的嵌套字典访问"""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
        if d is None:
            return None
    return d


# ==================== CLI ====================

def _cli():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="起点读书 RPC API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s ping
  %(prog)s status
  %(prog)s search 斗破苍穹
  %(prog)s search 斗破苍穹 --page 2
  %(prog)s book-detail 1209977
  %(prog)s chapter-list 1209977
  %(prog)s fetch 1209977 23373921
  %(prog)s fetch-text 1209977 23373921
  %(prog)s review-summary 1209977 23373921
  %(prog)s paragraph-comments 1209977 23373921 5
  %(prog)s all-comments 1209977 23373921
  %(prog)s raw '{"cmd":"ping"}'
""")
    parser.add_argument("--timeout", type=int, default=30, help="RPC 超时（秒）")

    sub = parser.add_subparsers(dest="command", help="子命令")

    # ping
    sub.add_parser("ping", help="测试连接")

    # status
    sub.add_parser("status", help="获取 Hook 状态")

    # search
    p = sub.add_parser("search", help="搜索书籍")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--page", type=int, default=1, help="页码")

    # book-detail
    p = sub.add_parser("book-detail", help="获取书籍详情")
    p.add_argument("book_id", help="书籍 ID")

    # chapter-list
    p = sub.add_parser("chapter-list", help="获取章节列表")
    p.add_argument("book_id", help="书籍 ID")

    # fetch
    p = sub.add_parser("fetch", help="解密获取章节内容")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")

    # fetch-text
    p = sub.add_parser("fetch-text", help="获取章节纯文本")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")

    # test-fetch
    sub.add_parser("test-fetch", help="测试获取（硬编码参数）")

    # review-summary
    p = sub.add_parser("review-summary", help="获取段评摘要")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")

    # paragraph-comments
    p = sub.add_parser("paragraph-comments", help="获取段落评论")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")
    p.add_argument("paragraph_id", type=int, help="段落 ID（-1 表示章评）")
    p.add_argument("--page", type=int, default=1, help="页码")
    p.add_argument("--page-size", type=int, default=20, help="每页数量")
    p.add_argument("--type", type=int, default=0, help="评论类型: 0=全部, 1=配图, 2=配音")

    # comment-summary
    p = sub.add_parser("comment-summary", help="获取评论摘要（轻量）")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")

    # all-comments
    p = sub.add_parser("all-comments", help="获取章节所有段评")
    p.add_argument("book_id", help="书籍 ID")
    p.add_argument("chapter_id", help="章节 ID")
    p.add_argument("--page-size", type=int, default=20, help="每页数量")
    p.add_argument("--type", type=int, default=0, help="评论类型: 0=全部, 1=配图, 2=配音")
    p.add_argument("--include-chapter-comments", action="store_true", help="包含章评")
    p.add_argument("--no-replies", action="store_true", help="不获取评论回复")

    # logs
    p = sub.add_parser("logs", help="获取探测日志")
    p.add_argument("--limit", type=int, default=50, help="日志条数")

    # decrypted
    p = sub.add_parser("decrypted", help="获取最近解密内容")
    p.add_argument("--limit", type=int, default=10, help="条数")

    # probe
    sub.add_parser("probe", help="探测 bll.v 实例")

    # dump
    sub.add_parser("dump", help="dump ChapterItem 字段")

    # raw
    p = sub.add_parser("raw", help="发送原始 RPC JSON")
    p.add_argument("json_str", help='JSON 字符串，如 \'{"cmd":"ping"}\'')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    api = QidianAPI(timeout=args.timeout)

    try:
        result = _dispatch(api, args)
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except RpcError as e:
        print(f"RPC 错误: {e}", file=sys.stderr)
        sys.exit(1)


def _dispatch(api: QidianAPI, args) -> object:
    """根据子命令分发调用"""
    cmd = args.command

    if cmd == "ping":
        return api.ping()
    elif cmd == "status":
        return api.get_status()
    elif cmd == "search":
        return api.search(args.keyword, page=args.page)
    elif cmd == "book-detail":
        return api.book_detail(args.book_id)
    elif cmd == "chapter-list":
        return api.get_chapter_list(args.book_id)
    elif cmd == "fetch":
        return api.fetch_chapter(args.book_id, args.chapter_id)
    elif cmd == "fetch-text":
        text = api.fetch_chapter_text(args.book_id, args.chapter_id)
        return text if text else "(空内容)"
    elif cmd == "test-fetch":
        return api.test_fetch()
    elif cmd == "review-summary":
        return api.get_chapter_review_summary(args.book_id, args.chapter_id)
    elif cmd == "paragraph-comments":
        return api.get_paragraph_comments(
            args.book_id, args.chapter_id, args.paragraph_id,
            page=args.page, page_size=args.page_size, type=args.type)
    elif cmd == "comment-summary":
        return api.get_comment_summary(args.book_id, args.chapter_id)
    elif cmd == "all-comments":
        return api.get_all_paragraph_comments(
            args.book_id, args.chapter_id, page_size=args.page_size,
            type=args.type, include_chapter_comments=args.include_chapter_comments,
            fetch_replies=not args.no_replies)
    elif cmd == "logs":
        return api.get_logs(limit=args.limit)
    elif cmd == "decrypted":
        return api.get_recent_decrypted(limit=args.limit)
    elif cmd == "probe":
        return api.probe_bll_v()
    elif cmd == "dump":
        return api.dump_chapter_item()
    elif cmd == "raw":
        cmd_dict = json.loads(args.json_str)
        return api._rpc(**cmd_dict)
    else:
        return {"error": f"未知命令: {cmd}"}


if __name__ == "__main__":
    _cli()
