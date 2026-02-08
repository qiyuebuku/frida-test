#!/usr/bin/env python3
"""
起点读书章节数据处理工具

支持三种模式：
1. json 清洗模式：将原始 JSON 清洗为结构化数据
2. markdown 转换模式：将清洗后的 JSON 转换为 Markdown
3. kb 知识库模式：输出纯正文 + 结构化评论到知识库目录

使用示例：
    python process_chapters.py --mode json --input 原始目录 --output 清洗后目录
    python process_chapters.py --mode markdown --input 清洗后目录 --output markdown目录
    python process_chapters.py --mode markdown --min-agree 10  # 只保留点赞≥10的评论
    python process_chapters.py --mode kb --min-agree 15  # 知识库模式
    python process_chapters.py --mode json --test  # 测试单个章节
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


# ==================== 配置 ====================

COMMENT_KEEP_FIELDS = {
    "Id", "Content", "CreateTime", "UserName", "UserId", "IpLocation",
    "AgreeAmount", "ParagraphId", "ImageDetail", "ImageMeaning", "ImgInfo",
    "RefferCommentId", "RootReviewId",
}

REPLIES_KEEP_FIELDS = {
    "Id", "Content", "CreateTime", "UserName", "UserId", "IpLocation",
    "AgreeAmount", "RefferCommentId", "RootReviewId",
    "ImageDetail", "ImageMeaning", "ImgInfo",
}


# ==================== JSON 清洗模式 ====================

def simplify_comment(comment: dict) -> dict:
    """简化单条评论，只保留必要字段"""
    # 过滤脏数据：UserId 为 0 的评论
    user_id = comment.get("UserId", 0)
    if user_id == 0:
        return None

    simplified = {k: comment.get(k) for k in COMMENT_KEEP_FIELDS if k in comment}
    if "replies" in comment and comment["replies"]:
        # 递归简化回复，同时过滤脏数据
        simplified["replies"] = []
        for reply in comment["replies"]:
            simplified_reply = simplify_reply(reply)
            if simplified_reply is not None:
                simplified["replies"].append(simplified_reply)
    return simplified


def simplify_reply(reply: dict) -> dict:
    """简化回复，只保留必要字段"""
    # 过滤脏数据：UserId 为 0 的回复
    user_id = reply.get("UserId", 0)
    if user_id == 0:
        return None

    return {k: reply.get(k) for k in REPLIES_KEEP_FIELDS if k in reply}


def format_timestamp(ts: int) -> str:
    """格式化时间戳为可读字符串"""
    if ts and ts > 0:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def clean_chapter_to_json(input_path: Path, output_dir: Path) -> dict:
    """清洗单个章节文件为 JSON 格式"""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 提取正文并按段落拆分
    content = data.get("content", "")
    paragraphs = [p.strip() for p in content.replace('\r\n', '\n').split('\n') if p.strip()]

    # 构建段落ID到评论的映射
    comments_map = {}
    comments_data = data.get("comments", {})

    if comments_data.get("success"):
        for para in comments_data.get("paragraphs", []):
            paragraph_id = para.get("paragraphId")
            simplified_comments = []
            for comment in para.get("comments", []):
                simple = simplify_comment(comment)
                if simple is not None:  # 跳过脏数据
                    if simple.get("CreateTime"):
                        simple["formattedTime"] = format_timestamp(simple["CreateTime"])
                    simplified_comments.append(simple)
            if simplified_comments:
                comments_map[paragraph_id] = simplified_comments

    # 构建段落+评论的组合列表
    items = []

    # 先处理章评
    chapter_review_comments = comments_map.get(-1, [])
    if chapter_review_comments:
        items.append({
            "index": 0,
            "text": data.get("chapterName", ""),
            "comments": chapter_review_comments,
            "isChapterReview": True
        })

    # 添加正文段落
    for idx, para_text in enumerate(paragraphs):
        items.append({
            "index": idx + 1,
            "text": para_text,
            "comments": []
        })

    # 匹配段评到段落
    for para_id, comments in comments_map.items():
        if para_id >= 1:
            offset = 1 if chapter_review_comments else 0
            target_index = (para_id - 1) + offset
            if target_index < len(items):
                items[target_index]["comments"].extend(comments)

    return {
        "bookId": data.get("bookId"),
        "chapterId": data.get("chapterId"),
        "chapterName": data.get("chapterName"),
        "vip": data.get("vip"),
        "words": data.get("words"),
        "items": items,
        "stats": {
            "totalParagraphs": len(items),
            "paragraphsWithComments": sum(1 for item in items if item["comments"]),
            "totalComments": sum(len(item["comments"]) for item in items),
        }
    }


# ==================== Markdown 转换模式 ====================

def format_time_md(ts: int) -> str:
    """格式化时间戳为 Markdown 友好格式"""
    if ts and ts > 0:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    return ""


def format_location_md(location: str) -> str:
    """格式化位置信息"""
    return location if location else "未知地区"


def comment_to_markdown(comment: dict, id_to_comment: dict = None, indent: int = 0, min_agree: int = 0, force_show: bool = False) -> str:
    """
    将评论转换为 Markdown 格式

    Args:
        comment: 评论数据
        id_to_comment: ID到评论的映射（用于显示回复关系）
        indent: 缩进级别（用于回复）
        min_agree: 最小点赞数阈值（低于此值的评论将被过滤）
        force_show: 是否强制显示（由外部控制，用于被引用的上下文评论）
    """
    # 如果不是强制显示且点赞数不够，直接返回空
    if not force_show and comment.get("AgreeAmount", 0) < min_agree:
        return ""

    prefix = "  " * indent
    user_name = comment.get("UserName", "").strip()
    if not user_name or user_name == "****":
        user_name = f"书友{str(comment.get('UserId', ''))[-6:]}"

    location = format_location_md(comment.get("IpLocation", ""))
    time_str = format_time_md(comment.get("CreateTime", 0))
    agree = comment.get("AgreeAmount", 0)
    content = comment.get("Content", "").strip()

    # 如果内容为空，不显示此评论
    if not content and not force_show:
        return ""

    # 如果是被强制显示的低赞评论，添加标记
    force_mark = " 💬 [被回复引用]" if force_show and agree < min_agree else ""

    # 回复关系
    reffer_id = comment.get("RefferCommentId", 0)
    reply_info = ""
    if reffer_id > 0 and id_to_comment and reffer_id in id_to_comment:
        parent = id_to_comment[reffer_id]
        parent_user = parent.get("UserName", "未知用户").strip()
        if not parent_user:
            parent_user = "某读者"
        parent_content = parent.get("Content", "")[:30]
        reply_info = f" 💬 回复 **{parent_user}**：「{parent_content}...」"

    # 图片
    image_info = ""
    if comment.get("ImageDetail"):
        image_info = f"\n{prefix}  🖼️ [图片]({comment.get('ImageDetail')})"

    md = f"""{prefix}**{user_name}**（{location}）· {time_str} · 👍 {agree}{force_mark}{reply_info}
{prefix}> {content}{image_info}
"""

    # 处理回复
    if "replies" in comment and comment["replies"]:
        for reply in comment["replies"]:
            md += comment_to_markdown(reply, id_to_comment, indent + 1, min_agree, False)

    return md


def has_high_agree_replies(comment: dict, min_agree: int) -> bool:
    """
    检查评论是否有高赞回复

    Args:
        comment: 评论数据
        min_agree: 最小点赞数阈值

    Returns:
        True 如果有至少一个回复的点赞数 ≥ min_agree
    """
    if "replies" not in comment:
        return False
    for reply in comment["replies"]:
        if reply.get("AgreeAmount", 0) >= min_agree:
            return True
        # 递归检查嵌套回复
        if has_high_agree_replies(reply, min_agree):
            return True
    return False


def chapter_to_markdown(json_data: dict, min_agree: int = 0) -> str:
    """
    将清洗后的 JSON 转换为 Markdown

    Args:
        json_data: 清洗后的 JSON 数据
        min_agree: 最小点赞数阈值，低于此值的评论将被过滤
    """
    chapter_name = json_data.get("chapterName", "")
    book_id = json_data.get("bookId", "")
    chapter_id = json_data.get("chapterId", "")
    vip = json_data.get("vip", False)
    words = json_data.get("words", 0)

    # 标题
    vip_tag = "【VIP】" if vip else ""
    header = f"# {chapter_name}\n\n"
    header += f"**书籍ID**: {book_id} | **章节ID**: {chapter_id} | **字数**: {words}字 {vip_tag}\n\n"
    header += "---\n\n"

    # 构建评论ID映射
    items = json_data.get("items", [])
    id_to_comment = {}
    for item in items:
        for comment in item.get("comments", []):
            comment_id = comment.get("Id")
            if comment_id:
                id_to_comment[comment_id] = comment

    # 遍历段落，生成 Markdown
    body = ""
    filtered_total_comments = 0

    for item in items:
        index = item.get("index", 0)
        text = item.get("text", "").strip()
        comments = item.get("comments", [])

        if item.get("isChapterReview"):
            body += f"## 📖 章节评论\n\n"
        else:
            body += f"## 第{index}段\n\n"

        body += f"{text}\n\n"

        # 第一步：收集需要显示的评论 ID
        show_comment_ids = set()
        for comment in comments:
            agree = comment.get("AgreeAmount", 0)
            # 点赞够高，直接显示
            if agree >= min_agree:
                show_comment_ids.add(comment.get("Id"))
            # 检查是否有高赞回复
            elif has_high_agree_replies(comment, min_agree):
                show_comment_ids.add(comment.get("Id"))

        # 第二步：收集被高赞回复引用的评论 ID
        for comment in comments:
            comment_id = comment.get("Id")
            if comment_id in show_comment_ids:
                reffer_id = comment.get("RefferCommentId", 0)
                if reffer_id > 0 and reffer_id not in show_comment_ids:
                    # 被回复的评论也需要显示（作为上下文）
                    show_comment_ids.add(reffer_id)

        # 第三步：生成 Markdown，只显示需要的评论
        filtered_comments = []
        for comment in comments:
            if comment.get("Id") in show_comment_ids:
                md = comment_to_markdown(comment, id_to_comment, min_agree=min_agree, force_show=True)
                if md.strip():
                    filtered_comments.append(md)

        if filtered_comments:
            if item.get("isChapterReview"):
                body += f"### 💬 章评内容（{len(filtered_comments)}条）\n\n"
            else:
                body += f"### 💬 本段评论（{len(filtered_comments)}条）\n\n"

            for md in filtered_comments:
                body += md

            body += "\n\n"

        filtered_total_comments += len(filtered_comments)

    # 统计信息
    header += f"**本章共 {len(items)} 个段落，{filtered_total_comments} 条评论**"
    if min_agree > 0:
        header += f"（过滤：点赞 ≥ {min_agree}）"
    header += "\n\n---\n\n"

    return header + body


# ==================== 目录处理 ====================

def process_json_mode(input_dir: str, output_dir: str, test_mode: bool = False):
    """JSON 清洗模式"""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if test_mode:
        # 测试模式
        test_file = input_path / "0719_782154202_第六百八十四章 天色既明.json"
        if not test_file.exists():
            test_file = list(input_path.glob("*.json"))[0]
        print(f"测试文件: {test_file.name}")
        cleaned = clean_chapter_to_json(test_file, output_path)
        output_file = output_path / test_file.name
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        print(f"输出: {output_file}")
        return

    # 全量处理
    json_files = sorted(input_path.glob("*.json"))
    print(f"找到 {len(json_files)} 个章节文件")

    stats = {"total": len(json_files), "processed": 0, "orig_size": 0, "out_size": 0}

    for json_file in json_files:
        # _book_detail.json 不是章节文件，原样复制
        if json_file.name == "_book_detail.json":
            import shutil
            shutil.copy2(json_file, output_path / json_file.name)
            print(f"原样复制: {json_file.name}")
            continue

        try:
            print(f"处理: {json_file.name}")
            cleaned = clean_chapter_to_json(json_file, output_path)
            output_file = output_path / json_file.name
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)

            stats["processed"] += 1
            stats["orig_size"] += cleaned["stats"].get("totalComments", 0)
            stats["out_size"] += output_file.stat().st_size

            if stats["processed"] % 100 == 0:
                print(f"  进度: {stats['processed']}/{stats['total']}")

        except Exception as e:
            print(f"  错误: {e}")

    print("\n" + "=" * 50)
    print(f"处理完成: {stats['processed']}/{stats['total']}")
    print(f"输出目录: {output_path.absolute()}")


def process_markdown_mode(input_dir: str, output_dir: str, test_mode: bool = False, min_agree: int = 0):
    """
    Markdown 转换模式

    Args:
        input_dir: 输入目录
        output_dir: 输出目录
        test_mode: 是否为测试模式
        min_agree: 最小点赞数阈值，低于此值的评论将被过滤
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if min_agree > 0:
        print(f"🔍 过滤条件: 只保留点赞数 ≥ {min_agree} 的评论")

    if test_mode:
        # 测试模式
        test_file = input_path / "0142_739173888_第一百二十六章布阵与婚约.json"
        if not test_file.exists():
            test_file = list(input_path.glob("*.json"))[0]
        print(f"测试文件: {test_file.name}")
        with open(test_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        markdown = chapter_to_markdown(data, min_agree=min_agree)
        output_file = output_path / f"{test_file.stem}.md"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown)
        print(f"输出: {output_file}")
        print(f"\n预览（前50行）:")
        print("\n".join(markdown.split("\n")[:50]))
        return

    # 全量处理
    json_files = sorted(input_path.glob("*.json"))
    print(f"找到 {len(json_files)} 个 JSON 文件")

    stats = {"total": len(json_files), "converted": 0, "total_size_kb": 0}

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            markdown = chapter_to_markdown(data, min_agree=min_agree)
            output_file = output_path / f"{json_file.stem}.md"
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(markdown)

            stats["converted"] += 1
            stats["total_size_kb"] += len(markdown.encode('utf-8')) / 1024

            if stats["converted"] % 100 == 0:
                print(f"  进度: {stats['converted']}/{stats['total']}")

        except Exception as e:
            print(f"  错误 {json_file.name}: {e}")

    print("\n" + "=" * 50)
    print(f"转换完成: {stats['converted']}/{stats['total']}")
    print(f"输出总大小: {stats['total_size_kb'] / 1024:.2f} MB")
    print(f"输出目录: {output_path.absolute()}")


# ==================== 知识库模式 ====================

# 正文章节名正则：匹配 "第X章 标题"、"第X章标题"（无空格）、"第X 标题"（缺章字）、"X章 标题"（缺第字）
CHAPTER_NAME_PATTERN = re.compile(
    r'^第.+章[\s\S]|^第[\d一二三四五六七八九十百千]+\s|^[\d一二三四五六七八九十百千]+章[\s\S]'
)


def is_chapter_file(data: dict) -> bool:
    """判断 cleaned JSON 是否为正文章节"""
    name = data.get("chapterName", "")
    return bool(CHAPTER_NAME_PATTERN.match(name))


def extract_text(data: dict, min_agree: int = 15) -> str:
    """从 cleaned JSON 提取纯正文 Markdown，有评论的段落标注评论数"""
    chapter_name = data.get("chapterName", "")
    lines = [f"# {chapter_name}", ""]

    for item in data.get("items", []):
        if item.get("isChapterReview"):
            continue
        text = item.get("text", "").strip()
        if not text:
            continue
        # 统计达到阈值的评论数
        comment_count = sum(1 for c in item.get("comments", []) if c.get("AgreeAmount", 0) >= min_agree)
        if comment_count > 0:
            lines.append(f"{text}({comment_count}评)")
        else:
            lines.append(text)

    return "\n".join(lines) + "\n"


def extract_comments(data: dict, min_agree: int = 15) -> str:
    """从 cleaned JSON 提取结构化评论 Markdown"""
    chapter_name = data.get("chapterName", "")
    lines = [f"# {chapter_name} · 评论", ""]

    items = data.get("items", [])

    # 构建评论ID映射（用于回复关系）
    id_to_comment = {}
    for item in items:
        for comment in item.get("comments", []):
            cid = comment.get("Id")
            if cid:
                id_to_comment[cid] = comment

    has_any_comment = False

    for item in items:
        comments = item.get("comments", [])
        if not comments:
            continue

        # 筛选需要显示的评论（复用 show_comment_ids 机制）
        show_comment_ids = set()
        for comment in comments:
            agree = comment.get("AgreeAmount", 0)
            if agree >= min_agree:
                show_comment_ids.add(comment.get("Id"))
            elif has_high_agree_replies(comment, min_agree):
                show_comment_ids.add(comment.get("Id"))

        # 收集被高赞回复引用的评论
        for comment in comments:
            if comment.get("Id") in show_comment_ids:
                reffer_id = comment.get("RefferCommentId", 0)
                if reffer_id > 0 and reffer_id not in show_comment_ids:
                    show_comment_ids.add(reffer_id)

        # 生成过滤后的评论列表
        filtered = []
        for comment in comments:
            if comment.get("Id") in show_comment_ids:
                md = comment_to_markdown(comment, id_to_comment, min_agree=min_agree, force_show=True)
                if md.strip():
                    filtered.append(md)

        if not filtered:
            continue

        has_any_comment = True

        if item.get("isChapterReview"):
            lines.append(f"## 章评（{len(filtered)}条）")
            lines.append("")
        else:
            index = item.get("index", 0)
            text = item.get("text", "").strip()
            lines.append(f"## 第{index}段评论（{len(filtered)}条）")
            lines.append("")
            # 引用原文（截取前100字）
            if text:
                preview = text[:100] + ("..." if len(text) > 100 else "")
                lines.append(f"> 原文：{preview}")
                lines.append("")

        for md in filtered:
            lines.append(md)

    if not has_any_comment:
        # 整章无评论，只保留标题
        return f"# {chapter_name} · 评论\n"

    # 压缩空行：去掉连续空行，节省 token
    result = "\n".join(lines)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    # 去掉所有空行，只保留段落间单换行
    result = re.sub(r'\n\n+', '\n', result)
    return result + "\n"


def extract_book_detail(data: dict) -> str:
    """从 _book_detail.json 提取书籍详情 Markdown"""
    base = data.get("BaseBookInfo", {})
    author = data.get("AuthorInfo", {})
    chapter_info = base.get("ChapterInfo", {})
    role_info = data.get("RoleInfo", {})

    lines = [f"# {base.get('BookName', '未知')}", ""]

    # 基本信息
    lines.append("## 基本信息")
    lines.append(f"- 作者：{author.get('Author', '')}（{author.get('AuthorLevel', '')}）")
    lines.append(f"- 分类：{base.get('CategoryName', '')} / {base.get('SubCategoryName', '')}")
    lines.append(f"- 状态：{base.get('BookStatus', '')}（{base.get('SignStatus', '')}）")
    lines.append(f"- 总字数：{base.get('WordsCnt', 0):,}")
    lines.append(f"- 总章数：{chapter_info.get('TotalChapterCount', 0)}")
    lines.append(f"- 月票数：{base.get('MonthTicketCount', 0):,}")
    lines.append(f"- 总推荐：{base.get('RecommendAll', 0):,}")
    lines.append(f"- 总粉丝：{data.get('BookCircleScope', {}).get('TotalFansCount', 0):,}")
    lines.append(f"- BookId：{base.get('BookId', '')}")

    # 简介
    desc = base.get("Description", "").replace("\r\n", "\n").strip()
    if desc:
        lines.append("")
        lines.append("## 简介")
        lines.append(desc)

    # 标签
    tags = [t.get("TagName", "") for t in base.get("BookUgcTag", []) if t.get("Type") == 1]
    if tags:
        lines.append("")
        lines.append("## 标签")
        lines.append("、".join(tags))

    # 角色榜
    roles = role_info.get("RoleList", [])
    if roles:
        lines.append("")
        lines.append(f"## 角色人气榜（共{role_info.get('TotalRoleCount', 0)}个角色）")
        for role in roles:
            name = role.get("RoleName", "")
            position = role.get("Position", "")
            likes = role.get("Likes", 0)
            role_tags = [t.get("TagName", "") for t in role.get("TagList", [])]
            tag_str = "、".join(role_tags) if role_tags else ""
            lines.append(f"- **{name}**（{position}）👍{likes:,} — {tag_str}")

    return "\n".join(lines) + "\n"


def process_kb_mode(input_dir: str, output_dir: str, min_agree: int = 15):
    """知识库模式：输出纯正文 + 结构化评论"""
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    text_dir = output_path / "text"
    comments_dir = output_path / "reader" / "comments"
    text_dir.mkdir(parents=True, exist_ok=True)
    comments_dir.mkdir(parents=True, exist_ok=True)

    # 0. 处理书籍详情
    book_detail_file = input_path / "_book_detail.json"
    if book_detail_file.exists():
        with open(book_detail_file, 'r', encoding='utf-8') as f:
            book_data = json.load(f)
        if book_data.get("BaseBookInfo"):
            detail_md = extract_book_detail(book_data)
            with open(output_path / "book_detail.md", 'w', encoding='utf-8') as f:
                f.write(detail_md)
            print(f"书籍详情: book_detail.md")
        else:
            print(f"警告: _book_detail.json 缺少 BaseBookInfo，跳过书籍详情")

    # 1. 扫描所有 JSON 文件（排除 _book_detail.json）
    json_files = sorted(f for f in input_path.glob("*.json") if f.name != "_book_detail.json")
    print(f"扫描到 {len(json_files)} 个 JSON 文件")

    # 2. 过滤正文章节
    chapter_files = []
    skipped = []
    for json_file in json_files:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if is_chapter_file(data):
            chapter_files.append((json_file, data))
        else:
            skipped.append((json_file.name, data.get("chapterName", "")))

    print(f"正文章节: {len(chapter_files)} 个")
    print(f"跳过非正文: {len(skipped)} 个")
    if skipped:
        for fname, cname in skipped[:5]:
            print(f"  跳过: {fname} ({cname})")
        if len(skipped) > 5:
            print(f"  ... 共 {len(skipped)} 个")

    # 3. 按文件名排序后顺序编号，生成输出
    stats = {"text_files": 0, "comments_files": 0, "total_text_bytes": 0, "total_comments_bytes": 0}

    for seq, (json_file, data) in enumerate(chapter_files, start=1):
        ch_id = f"ch{seq:04d}"

        # 生成纯正文
        text_content = extract_text(data, min_agree=min_agree)
        text_file = text_dir / f"{ch_id}.md"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(text_content)
        stats["text_files"] += 1
        stats["total_text_bytes"] += len(text_content.encode('utf-8'))

        # 生成结构化评论
        comments_content = extract_comments(data, min_agree=min_agree)
        comments_file = comments_dir / f"{ch_id}.md"
        with open(comments_file, 'w', encoding='utf-8') as f:
            f.write(comments_content)
        stats["comments_files"] += 1
        stats["total_comments_bytes"] += len(comments_content.encode('utf-8'))

        if seq % 100 == 0:
            print(f"  进度: {seq}/{len(chapter_files)}")

    print("\n" + "=" * 50)
    print(f"知识库生成完成:")
    print(f"  纯正文: {stats['text_files']} 个文件, {stats['total_text_bytes'] / 1024 / 1024:.2f} MB")
    print(f"  评论:   {stats['comments_files']} 个文件, {stats['total_comments_bytes'] / 1024 / 1024:.2f} MB")
    print(f"  输出目录: {output_path.absolute()}")


# ==================== 主函数 ====================

def main():
    parser = argparse.ArgumentParser(
        description="起点读书章节数据处理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # JSON 清洗模式
  python process_chapters.py --mode json --input 原始目录 --output 清洗后目录

  # Markdown 转换模式（包含所有评论）
  python process_chapters.py --mode markdown --input 清洗后目录 --output markdown目录

  # Markdown 转换模式（只保留点赞≥10的评论）
  python process_chapters.py --mode markdown --min-agree 10

  # 知识库模式（输出纯正文 + 结构化评论）
  python process_chapters.py --mode kb --min-agree 15

  # 测试模式
  python process_chapters.py --mode json --test
  python process_chapters.py --mode markdown --test --min-agree 5
        """
    )

    parser.add_argument("--mode", choices=["json", "markdown", "kb"], required=True,
                        help="处理模式: json=清洗JSON, markdown=转换为Markdown, kb=知识库模式")
    parser.add_argument("--input", help="输入目录（默认为内置目录）")
    parser.add_argument("--output", help="输出目录（默认为内置目录）")
    parser.add_argument("--test", action="store_true", help="测试模式：只处理单个文件")
    parser.add_argument("--min-agree", type=int, default=0,
                        help="Markdown模式：只保留点赞数≥此值的评论（默认0，不过滤）")

    args = parser.parse_args()

    # 默认目录
    default_input_json = "/home/yuyang/frida-test/qidian/scripts/output/1035420986_玄鉴仙族"
    default_output_json = "/home/yuyang/frida-test/qidian/scripts/output/1035420986_玄鉴仙族_cleaned"
    default_output_md = "/home/yuyang/frida-test/qidian/scripts/output/1035420986_玄鉴仙族_markdown"
    default_output_kb = "/home/yuyang/frida-test/qidian/novel_kb/玄鉴仙族"

    if args.mode == "json":
        input_dir = args.input or default_input_json
        output_dir = args.output or default_output_json
    elif args.mode == "markdown":
        input_dir = args.input or default_output_json
        output_dir = args.output or default_output_md
    else:  # kb
        input_dir = args.input or default_output_json
        output_dir = args.output or default_output_kb

    print(f"模式: {args.mode.upper()}")
    print(f"输入: {input_dir}")
    print(f"输出: {output_dir}")
    print()

    if args.mode == "json":
        process_json_mode(input_dir, output_dir, args.test)
    elif args.mode == "markdown":
        process_markdown_mode(input_dir, output_dir, args.test, args.min_agree)
    else:  # kb
        process_kb_mode(input_dir, output_dir, args.min_agree)


if __name__ == "__main__":
    main()
