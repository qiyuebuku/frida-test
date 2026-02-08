#!/usr/bin/env python3
"""
批量采集起点章节正文 + 评论（含章评）

完整流程:
  1. 搜索书籍       batch_fetch.py search 关键词
  2. 查看章节目录    batch_fetch.py list 搜索序号
  3. 采集数据       batch_fetch.py fetch 搜索序号 --last 5

示例:
  batch_fetch.py search 大奉打更人
  batch_fetch.py list 1                          # 查看第1本书的章节
  batch_fetch.py list 1 --last 10                # 只看最后10章
  batch_fetch.py fetch 1 --last 5                # 采集最后5章
  batch_fetch.py fetch 1 --range 100-110         # 采集第100~110章
  batch_fetch.py fetch 1 --chapters 1,5,10-15    # 采集指定章节
  batch_fetch.py fetch 1                         # 采集全部
  batch_fetch.py fetch 1 --last 3 --no-comments  # 只要正文
  batch_fetch.py fetch 1 --range 15-500 --force  # 强制重采所有章节
  batch_fetch.py fetch 1 --last 5              # 采集评论（默认含回复）
  batch_fetch.py fetch 1 --last 5 --no-replies  # 不采集回复（更快）
  batch_fetch.py fetch 1 --update-comments       # 增量更新已有评论
"""

import argparse
import glob as globmod
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qidian_api import QidianAPI, RpcError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, ".last_search.json")
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")


# ==================== 工具函数 ====================

def parse_indices(spec: str) -> list[int]:
    """解析序号表达式: '1,5,10-15,20' → [1,5,10,11,12,13,14,15,20]"""
    result = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def save_search_cache(books: list):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=2)


def load_search_cache() -> list:
    if not os.path.exists(CACHE_FILE):
        return []
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_book(api: QidianAPI, book_ref: str) -> dict | None:
    """
    解析书籍引用:
      - 纯数字且 > 100 → 直接当 bookId
      - 纯数字且 <= 100 → 搜索缓存中的序号
    返回 {"bookId": "...", "bookName": "..."}
    """
    if book_ref.isdigit():
        num = int(book_ref)
        if num <= 100:
            cache = load_search_cache()
            if not cache:
                print("没有搜索缓存，请先执行 search 子命令")
                return None
            if num < 1 or num > len(cache):
                print(f"序号超出范围，缓存中共 {len(cache)} 条结果")
                return None
            book = cache[num - 1]
            print(f"选中: [{num}] {book['bookName']} (id={book['bookId']})")
            return book
        else:
            # 直接传 bookId，通过 API 获取书名
            try:
                detail = api.book_detail(book_ref)
                base = detail.get("response", {}).get("Data", {}).get("BaseBookInfo", {})
                name = base.get("BookName", "")
            except Exception:
                name = ""
            return {"bookId": book_ref, "bookName": name}
    # 非数字 → 当书名搜索，取第一个结果
    print(f"搜索: {book_ref}")
    results = _do_search(api, book_ref)
    if results:
        book = results[0]
        print(f"自动选中第1个: {book['bookName']} (id={book['bookId']})")
        return book
    return None


def select_chapters(all_chapters: list, args) -> list[tuple[int, dict]]:
    """根据参数选出章节，返回 [(1-based序号, chapter_dict), ...]"""
    total = len(all_chapters)

    if hasattr(args, 'chapters') and args.chapters:
        indices = parse_indices(args.chapters)
        return [(i, all_chapters[i - 1]) for i in indices if 1 <= i <= total]
    elif hasattr(args, 'range') and args.range:
        a, b = args.range.split("-", 1)
        a, b = max(1, int(a)), min(total, int(b))
        return [(i, all_chapters[i - 1]) for i in range(a, b + 1)]
    elif hasattr(args, 'last') and args.last:
        start = max(0, total - args.last)
        return [(start + i + 1, all_chapters[start + i]) for i in range(total - start)]
    else:
        return [(i + 1, ch) for i, ch in enumerate(all_chapters)]


# ==================== search 子命令 ====================

def _do_search(api: QidianAPI, keyword: str) -> list:
    """执行搜索并返回精简结果列表"""
    resp = api.search(keyword)
    api_data = resp.get("response", {})
    card_list = api_data.get("Data", {}).get("CardList", [])
    books = []
    seen = set()
    for card in card_list:
        for body_item in card.get("Body", []):
            # 新版 API: 书籍数据嵌套在 ItemData 中
            item = body_item.get("ItemData", body_item)
            bid = str(item.get("BookId", item.get("bid", "")))
            if not bid or bid in seen:
                continue
            seen.add(bid)
            books.append({
                "bookId": bid,
                "bookName": item.get("BookName", item.get("bN", "")),
                "author": item.get("AuthorName", item.get("aN", "")),
                "desc": item.get("Description", item.get("desc", ""))[:80],
                "category": item.get("CategoryName", item.get("CatName", item.get("cat", ""))),
                "words": item.get("WordsCount", 0),
                "status": item.get("ActionStatusString", ""),
            })
    return books


def cmd_search(args):
    api = QidianAPI(timeout=15)
    print(f"搜索: {args.keyword}")
    books = _do_search(api, args.keyword)

    if not books:
        print("未找到结果")
        return

    save_search_cache(books)
    print(f"找到 {len(books)} 本书:\n")
    for i, b in enumerate(books, 1):
        wc = b.get('words', 0)
        wc_str = f"{wc // 10000}万字" if wc >= 10000 else f"{wc}字"
        print(f"  {i:>2d}. {b['bookName']}  [{b.get('status', '')}]  {wc_str}")
        print(f"      作者: {b['author']}  分类: {b['category']}")
        if b['desc']:
            print(f"      {b['desc']}")
    print(f"\n结果已缓存，后续使用序号: list 1 / fetch 1 --last 5")


# ==================== list 子命令 ====================

def cmd_list(args):
    api = QidianAPI(timeout=60)
    book = resolve_book(api, args.book)
    if not book:
        return

    book_id = book["bookId"]
    print(f"获取章节列表...")
    all_chapters = api.get_all_chapter_ids(book_id)
    total = len(all_chapters)
    vip_count = sum(1 for c in all_chapters if c.get("vip"))
    print(f"共 {total} 章 (免费 {total - vip_count}, VIP {vip_count})\n")

    if not all_chapters:
        return

    selected = select_chapters(all_chapters, args)
    for seq, ch in selected:
        vip = "[VIP]" if ch.get("vip") else "[免费]"
        print(f"  #{seq:>4d} {vip} {ch['name']} ({ch.get('words', 0)}字)")


def _find_existing_file(out_dir: str, seq: int, ch: dict) -> str | None:
    """查找章节已有的输出文件路径，不存在返回 None。"""
    safe_name = ch["name"].replace("/", "_").replace("\\", "_").replace(":", "_")[:50]
    fpath = os.path.join(out_dir, f"{seq:04d}_{ch['id']}_{safe_name}.json")
    if os.path.exists(fpath):
        return fpath
    pattern = os.path.join(out_dir, f"{seq:04d}_{ch['id']}_*.json")
    matches = globmod.glob(pattern)
    return matches[0] if matches else None


def _has_valid_content(data: dict) -> bool:
    """检查已有数据中正文是否有效。"""
    if data.get("contentError"):
        return False
    content = data.get("content", "")
    return bool(content and content.strip().replace("\u3000", ""))


def _has_valid_comments(data: dict) -> bool:
    """检查已有数据中评论是否有效。"""
    if data.get("commentsError"):
        return False
    return data.get("comments") is not None


def _find_changed_paragraphs(summary: dict, old_comments: dict) -> list[int]:
    """对比摘要和旧数据，返回 CommentCount 有变化的段落 ID 列表。"""
    old_map = {}
    for p in (old_comments.get("paragraphs") or []):
        old_map[p.get("paragraphId")] = p.get("fetchedCount", 0)

    changed = []
    for p in (summary.get("paragraphs") or []):
        pid = p.get("paragraphId")
        new_count = p.get("commentCount", 0)
        old_count = old_map.get(pid, 0)
        if new_count != old_count:
            changed.append(pid)
    return changed


def _build_replies_map(old_comments: dict, changed_pids: list[int]) -> dict:
    """从变化段落的旧评论中提取 {str(评论Id): ReviewCount} 映射。"""
    replies_map = {}
    pid_set = set(changed_pids)
    for p in (old_comments.get("paragraphs") or []):
        if p.get("paragraphId") not in pid_set:
            continue
        for comment in (p.get("comments") or []):
            cid = comment.get("Id")
            review_count = comment.get("ReviewCount", 0)
            if cid is not None and review_count > 0:
                replies_map[str(cid)] = review_count
    return replies_map


def _build_old_replies_index(old_comments: dict, changed_pids: list[int]) -> dict:
    """构建旧数据中评论 ID → replies 的索引（仅变化段落）。"""
    index = {}
    pid_set = set(changed_pids)
    for p in (old_comments.get("paragraphs") or []):
        if p.get("paragraphId") not in pid_set:
            continue
        for comment in (p.get("comments") or []):
            cid = comment.get("Id")
            if cid is not None and "replies" in comment:
                index[cid] = comment["replies"]
    return index


def get_comments_in_batches(api: QidianAPI, book_id: str, chapter_id: str,
                             fetch_replies: bool = False, batch_size: int = 15,
                             batch_delay: int = 3) -> dict:
    """
    分批获取章节所有评论，避免单次请求时间过长或 App 压力过大。

    Args:
        api: QidianAPI 实例
        book_id: 书籍 ID
        chapter_id: 章节 ID
        fetch_replies: 是否获取评论回复
        batch_size: 每批处理的段落数量（默认 20）

    Returns:
        合并后的完整评论数据
    """
    try:
        # Step 1: 获取评论摘要，获取所有有评论的段落
        t0 = time.time()
        summary = api.get_comment_summary(book_id, chapter_id)
        paragraphs = summary.get("paragraphs", [])

        # 过滤出有评论的段落
        paragraph_ids = [p["paragraphId"] for p in paragraphs if p.get("commentCount", 0) > 0]

        if not paragraph_ids:
            return {
                "totalComments": 0,
                "totalParagraphs": 0,
                "paragraphs": [],
                "batches": 0
            }

        # Step 2: 分批处理段落
        all_paragraphs = []
        total_comments = 0
        batch_count = 0

        print(f"    评论分批获取: 共 {len(paragraph_ids)} 个段落，每批 {batch_size} 个")

        for i in range(0, len(paragraph_ids), batch_size):
            batch_num = i // batch_size + 1
            batch_ids = paragraph_ids[i:i + batch_size]
            batch_count = batch_num

            batch_start = time.time()
            print(f"    批次 {batch_count}/{(len(paragraph_ids) + batch_size - 1) // batch_size}: 段落 {len(batch_ids)} 个")

            # 批次间延迟（让 App "喘口气"）
            if i > 0:
                print(f"      等待 {batch_delay} 秒后继续...")
                time.sleep(batch_delay)

            try:
                batch_result = api.get_all_paragraph_comments(
                    book_id, chapter_id,
                    page_size=20,
                    include_chapter_comments=True,
                    fetch_replies=fetch_replies,
                    only_paragraphs=batch_ids
                )

                batch_paragraphs = batch_result.get("paragraphs", [])
                batch_comments = sum(p.get("fetchedCount", 0) for p in batch_paragraphs)
                batch_time = time.time() - batch_start

                print(f"      完成: {batch_comments} 条评论, {batch_time:.1f}s")

                all_paragraphs.extend(batch_paragraphs)
                total_comments = batch_result.get("totalComments", 0)

            except Exception as e:
                print(f"      批次 {batch_count} 失败: {e}")
                # 继续下一批，不要让整章失败

        total_time = time.time() - t0
        print(f"    评论采集完成: {total_comments} 条, {batch_count} 批, {total_time:.1f}s")

        return {
            "totalComments": total_comments,
            "totalParagraphs": len(all_paragraphs),
            "paragraphs": all_paragraphs,
            "batches": batch_count
        }

    except Exception as e:
        raise RpcError(f"分批获取评论失败: {e}")


def _merge_comments(old_comments: dict, new_data: dict, changed_pids: list[int]) -> dict:
    """合并旧数据和增量新数据。

    - 未变化段落: 从 old_comments 保持原样
    - 变化段落: 用 new_data 替换，但评论中无 replies 字段的从旧数据恢复
    """
    pid_set = set(changed_pids)

    # 旧回复索引（用于恢复跳过的 replies）
    old_replies_idx = _build_old_replies_index(old_comments, changed_pids)

    # 新数据中的段落按 paragraphId 索引
    new_para_map = {}
    for p in (new_data.get("paragraphs") or []):
        new_para_map[p.get("paragraphId")] = p

    merged_paragraphs = []
    merged_total = 0

    # 保留所有旧段落中未变化的
    for p in (old_comments.get("paragraphs") or []):
        pid = p.get("paragraphId")
        if pid not in pid_set:
            merged_paragraphs.append(p)
            merged_total += p.get("fetchedCount", 0)

    # 添加变化段落的新数据
    for pid in changed_pids:
        new_p = new_para_map.get(pid)
        if new_p is None:
            continue
        # 恢复跳过的 replies
        for comment in (new_p.get("comments") or []):
            cid = comment.get("Id")
            if cid is not None and "replies" not in comment and cid in old_replies_idx:
                comment["replies"] = old_replies_idx[cid]
        merged_paragraphs.append(new_p)
        merged_total += new_p.get("fetchedCount", 0)

    result = dict(old_comments)
    result["paragraphs"] = merged_paragraphs
    result["totalParagraphs"] = len(merged_paragraphs)
    result["totalComments"] = merged_total
    # summaryTotalComments 由调用方设置
    return result


def load_existing(out_dir: str, seq: int, ch: dict) -> dict | None:
    """加载已有的章节数据文件，失败返回 None。"""
    fpath = _find_existing_file(out_dir, seq, ch)
    if not fpath:
        return None
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ==================== fetch 子命令 ====================

def fetch_one(api: QidianAPI, book_id: str, ch: dict, idx: int, total: int,
              fetch_content: bool, fetch_comments: bool,
              existing: dict | None = None,
              update_comments: bool = False,
              fetch_replies: bool = False) -> dict:
    """采集单章。existing 非空时，已有有效数据的部分直接复用。"""
    cid = ch["id"]
    cname = ch["name"]
    vip_tag = "[VIP]" if ch.get("vip") else "[免费]"
    print(f"\n[{idx}/{total}] {vip_tag} {cname} (id={cid}, {ch.get('words', 0)}字)")

    result = {
        "bookId": book_id,
        "chapterId": cid,
        "chapterName": cname,
        "vip": ch.get("vip", False),
        "words": ch.get("words", 0),
    }

    if fetch_content:
        if existing and _has_valid_content(existing):
            result["content"] = existing["content"]
            print(f"  正文: {len(existing['content'])} 字 (已有，跳过)")
        else:
            t0 = time.time()
            try:
                data = api.fetch_chapter(book_id, cid)
                content = data.get("content", "")
                result["content"] = content
                print(f"  正文: {len(content)} 字, method={data.get('method', '?')}, {time.time() - t0:.1f}s")
            except RpcError as e:
                result["content"] = ""
                result["contentError"] = str(e)
                print(f"  正文获取失败: {e}")

    if fetch_comments:
        if existing and _has_valid_comments(existing) and not update_comments:
            # 默认模式：已有评论直接复用
            result["comments"] = existing["comments"]
            cm = (existing["comments"] or {}).get("totalComments", 0)
            print(f"  评论: {cm} 条 (已有，跳过)")
        elif existing and _has_valid_comments(existing) and update_comments:
            # 增量检测模式：先获取摘要对比总评论数
            t0 = time.time()
            try:
                summary = api.get_comment_summary(book_id, cid)
                # 用 summaryTotalComments（上次摘要总数）对比，避免 fetchedCount 差异导致误判
                old_summary_total = (existing["comments"] or {}).get("summaryTotalComments")
                if old_summary_total is None:
                    # 兼容旧数据（没有 summaryTotalComments 字段），用 totalComments 代替
                    old_summary_total = (existing["comments"] or {}).get("totalComments", 0)
                new_total = summary.get("totalComments", 0)

                if new_total == old_summary_total:
                    # 第一级：总数一致，直接复用
                    result["comments"] = existing["comments"]
                    print(f"  评论: {new_total} 条 (无变化), {time.time() - t0:.1f}s")
                else:
                    # 第二级：找出变化的段落
                    changed_pids = _find_changed_paragraphs(summary, existing["comments"])
                    if not changed_pids:
                        # 摘要总数变了但逐段没变（可能是章评计数差异），全量更新
                        resp = api.get_all_paragraph_comments(
                            book_id, cid, page_size=20, include_chapter_comments=True,
                            fetch_replies=fetch_replies)
                        resp["summaryTotalComments"] = new_total
                        result["comments"] = resp
                        total_c = resp.get("totalComments", 0)
                        print(f"  评论: {old_summary_total}→{new_total} (全量更新), {time.time() - t0:.1f}s")
                    else:
                        # 第三级：构建回复映射，增量获取变化段落
                        existing_replies = _build_replies_map(existing["comments"], changed_pids) if fetch_replies else None
                        new_data = api.get_all_paragraph_comments(
                            book_id, cid, page_size=20, include_chapter_comments=True,
                            fetch_replies=fetch_replies,
                            only_paragraphs=changed_pids,
                            existing_replies=existing_replies)
                        merged = _merge_comments(existing["comments"], new_data, changed_pids)
                        merged["summaryTotalComments"] = new_total
                        result["comments"] = merged
                        print(f"  评论: {old_summary_total}→{new_total} (更新{len(changed_pids)}段), {time.time() - t0:.1f}s")
            except RpcError as e:
                result["comments"] = existing["comments"]
                print(f"  评论增量检测失败，保留旧数据: {e}")
        else:
            # 首次获取，使用分批采集（避免单次请求超时）
            t0 = time.time()
            try:
                resp = get_comments_in_batches(
                    api, book_id, cid,
                    fetch_replies=True,  # 默认采集回复（HTTP 30ms 节流已足够保护 App）
                    batch_size=30,   # 每批 30 个段落
                    batch_delay=0    # 无需批次延迟（30ms 节流已足够）
                )
                # 记录摘要总数，用于后续增量对比
                summary = api.get_comment_summary(book_id, cid)
                resp["summaryTotalComments"] = summary.get("totalComments", 0)
                result["comments"] = resp
                total_c = resp.get("totalComments", 0)
                paras = resp.get("paragraphs", [])
                ch_cm = sum(p.get("fetchedCount", 0) for p in paras if p.get("paragraphId") == -1)
                print(f"  评论: {total_c} 条 (章评 {ch_cm}, 段评 {total_c - ch_cm}), {time.time() - t0:.1f}s")
            except RpcError as e:
                result["comments"] = {}
                result["commentsError"] = str(e)
                print(f"  评论获取失败: {e}")

    return result


def cmd_fetch(args):
    api = QidianAPI(timeout=60)
    book = resolve_book(api, args.book)
    if not book:
        return

    book_id = book["bookId"]
    book_name = book.get("bookName", "")

    print(f"获取章节列表...")
    all_chapters = api.get_all_chapter_ids(book_id)
    total_all = len(all_chapters)
    vip_count = sum(1 for c in all_chapters if c.get("vip"))
    print(f"共 {total_all} 章 (免费 {total_all - vip_count}, VIP {vip_count})")

    if not all_chapters:
        print("章节列表为空！")
        return

    selected = select_chapters(all_chapters, args)
    if not selected:
        print("未选中任何章节！")
        return

    print(f"已选中 {len(selected)} 章: #{selected[0][0]}~#{selected[-1][0]}")

    fetch_content = not args.no_content
    fetch_comments = not args.no_comments
    if not fetch_content and not fetch_comments:
        print("--no-content 和 --no-comments 同时指定，无需采集")
        return

    # 输出目录: output/<bookId>_<bookName>/
    dir_name = book_id
    if book_name:
        safe_book = book_name.replace("/", "_").replace("\\", "_").replace(":", "_")[:30]
        dir_name = f"{book_id}_{safe_book}"
    out_dir = os.path.join(args.output_dir, dir_name)
    os.makedirs(out_dir, exist_ok=True)

    # 保存书籍详情（只在首次采集时获取）
    detail_path = os.path.join(out_dir, "_book_detail.json")
    if not os.path.exists(detail_path):
        try:
            detail_resp = api.book_detail(book_id)
            detail_data = detail_resp.get("response", {}).get("Data", {})
            with open(detail_path, "w", encoding="utf-8") as f:
                json.dump(detail_data, f, ensure_ascii=False, indent=2)
            print(f"书籍详情已保存: _book_detail.json")
        except RpcError as e:
            print(f"书籍详情获取失败: {e}")

    results = []
    skipped = 0
    t_start = time.time()
    total_sel = len(selected)

    update_comments = args.update_comments

    for i, (seq, ch) in enumerate(selected):
        existing = None
        if not args.force:
            existing = load_existing(out_dir, seq, ch)
            if existing:
                content_ok = _has_valid_content(existing) if fetch_content else True
                comments_ok = _has_valid_comments(existing) if fetch_comments else True
                # --update-comments 时评论已有也不跳过，交给 fetch_one 增量检测
                if content_ok and (comments_ok and not update_comments or not fetch_comments):
                    vip_tag = "[VIP]" if ch.get("vip") else "[免费]"
                    c_len = len(existing.get("content", ""))
                    cm_cnt = (existing.get("comments") or {}).get("totalComments", 0)
                    print(f"  [{i+1}/{total_sel}] {vip_tag} {ch['name']} → 跳过 (正文 {c_len} 字, 评论 {cm_cnt} 条)")
                    skipped += 1
                    continue

        result = fetch_one(api, book_id, ch, i + 1, total_sel, fetch_content, fetch_comments,
                           existing=existing, update_comments=update_comments,
                           fetch_replies=not args.no_replies)
        result["index"] = seq
        results.append(result)

        # 请求间隔，避免触发服务器限流
        # HTTP 30ms 节流已保护 App，无需额外延迟
        delay = 0.5 if fetch_comments else 0.1
        if i < total_sel - 1:
            time.sleep(delay)

        safe_name = ch["name"].replace("/", "_").replace("\\", "_").replace(":", "_")[:50]
        fpath = os.path.join(out_dir, f"{seq:04d}_{ch['id']}_{safe_name}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    total_time = time.time() - t_start

    # 写索引文件（只含元数据，不含正文和评论内容）
    summary_entries = []
    for r in results:
        entry = {
            "index": r.get("index"),
            "bookId": r.get("bookId"),
            "chapterId": r.get("chapterId"),
            "chapterName": r.get("chapterName"),
            "vip": r.get("vip"),
            "words": r.get("words"),
            "contentLength": len(r.get("content", "")),
            "contentError": r.get("contentError"),
            "totalComments": (r.get("comments") or {}).get("totalComments", 0),
            "commentsError": r.get("commentsError"),
        }
        summary_entries.append(entry)
    summary_path = os.path.join(out_dir, "_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_entries, f, ensure_ascii=False, indent=2)

    # 摘要
    total_words = sum(len(r.get("content", "")) for r in results)
    total_comments = sum((r.get("comments") or {}).get("totalComments", 0) for r in results)
    failed_content = sum(1 for r in results if r.get("contentError"))
    failed_comments = sum(1 for r in results if r.get("commentsError"))

    print(f"\n{'=' * 60}")
    fetched_count = len(results)
    fix_info = f" (跳过 {skipped}, 采集 {fetched_count})" if skipped else ""
    print(f"采集完成 | {book_name or book_id} | 共 {total_sel} 章{fix_info} | 耗时 {total_time:.0f}s")
    print(f"{'=' * 60}")

    for r in results:
        content_len = len(r.get("content", ""))
        cm_count = (r.get("comments") or {}).get("totalComments", 0)
        vip = "[VIP]" if r.get("vip") else "[免费]"
        parts = []
        if fetch_content:
            parts.append(f"正文 {content_len} 字" if not r.get("contentError") else "正文 失败")
        if fetch_comments:
            parts.append(f"评论 {cm_count} 条" if not r.get("commentsError") else "评论 失败")
        print(f"  #{r.get('index', '?'):>4d} {vip} {r['chapterName']}: {', '.join(parts)}")

    print(f"\n总计: {total_words} 字正文, {total_comments} 条评论")
    if failed_content or failed_comments:
        print(f"失败: 正文 {failed_content} 章, 评论 {failed_comments} 章")
    print(f"输出: {out_dir}")


# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="起点读书批量采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
完整流程:
  %(prog)s search 大奉打更人         # 搜索，显示编号列表
  %(prog)s list 1                   # 查看第1本的章节目录
  %(prog)s list 1 --last 10         # 只看最后10章
  %(prog)s fetch 1 --last 5         # 采集最后5章（正文+根评论）
  %(prog)s fetch 1 --chapters 1-10  # 采集第1~10章
  %(prog)s fetch 1 --no-comments    # 只采集正文
  %(prog)s fetch 1 --no-replies    # 不采集评论回复（默认采集）
  %(prog)s fetch 1 --update-comments       # 增量更新已有评论
  %(prog)s fetch 1 --range 15-500 --force  # 强制重采所有（默认跳过已成功）
""")

    sub = parser.add_subparsers(dest="command")

    # search
    p = sub.add_parser("search", help="搜索书籍")
    p.add_argument("keyword", help="搜索关键词")

    # list — 章节筛选参数
    p = sub.add_parser("list", help="查看章节目录")
    p.add_argument("book", help="搜索结果序号 或 bookId")
    p.add_argument("--last", type=int, metavar="N", help="只看最后 N 章")
    p.add_argument("--range", metavar="A-B", help="只看第 A~B 章")
    p.add_argument("--chapters", metavar="SPEC", help="只看指定章节，如 1,5,10-15")

    # fetch — 章节筛选 + 采集选项
    p = sub.add_parser("fetch", help="批量采集")
    p.add_argument("book", help="搜索结果序号 或 bookId")
    p.add_argument("--last", type=int, metavar="N", help="采集最后 N 章")
    p.add_argument("--range", metavar="A-B", help="采集第 A~B 章（含两端）")
    p.add_argument("--chapters", metavar="SPEC", help="指定章节序号，如 1,5,10-15,20")
    p.add_argument("--no-content", action="store_true", help="不采集正文")
    p.add_argument("--no-comments", action="store_true", help="不采集评论")
    p.add_argument("--no-replies", action="store_true", help="不采集评论回复（默认采集）")
    p.add_argument("--force", action="store_true", help="强制重新采集所有章节（默认跳过已成功的）")
    p.add_argument("--update-comments", action="store_true", help="增量更新评论（检测新增评论并合并）")
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "search":
        cmd_search(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "fetch":
        cmd_fetch(args)


if __name__ == "__main__":
    main()
