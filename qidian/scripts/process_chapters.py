#!/usr/bin/env python3
"""
起点读书章节数据处理工具

支持两种模式：
1. json 清洗模式：将原始 JSON 清洗为结构化数据
2. markdown 转换模式：将清洗后的 JSON 转换为 Markdown

使用示例：
    python process_chapters.py --mode json --input 原始目录 --output 清洗后目录
    python process_chapters.py --mode markdown --input 清洗后目录 --output markdown目录
    python process_chapters.py --mode markdown --min-agree 10  # 只保留点赞≥10的评论
    python process_chapters.py --mode json --test  # 测试单个章节
"""
import argparse
import json
import os
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

  # 测试模式
  python process_chapters.py --mode json --test
  python process_chapters.py --mode markdown --test --min-agree 5
        """
    )

    parser.add_argument("--mode", choices=["json", "markdown"], required=True,
                        help="处理模式: json=清洗JSON, markdown=转换为Markdown")
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

    input_dir = args.input or (default_input_json if args.mode == "json" else default_output_json)
    output_dir = args.output or (default_output_json if args.mode == "json" else default_output_md)

    print(f"模式: {args.mode.upper()}")
    print(f"输入: {input_dir}")
    print(f"输出: {output_dir}")
    print()

    if args.mode == "json":
        process_json_mode(input_dir, output_dir, args.test)
    else:
        process_markdown_mode(input_dir, output_dir, args.test, args.min_agree)


if __name__ == "__main__":
    main()
