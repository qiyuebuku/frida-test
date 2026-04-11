"""AI 事件抽取 (P0)

依赖 event-extract skill (`/home/yuyang/frida-test/.claude/skills/event-extract/SKILL.md`)
通过 `claude -p` 非交互模式调用，把 SKILL.md 作为 system prompt 传入

流程：
1. 读取 ft_news where event_extracted=false（按发布时间倒序）
2. 预筛选：过滤明显无关的新闻
3. 批量调 claude -p 抽取（每批 3-5 条）
4. 解析 JSON 输出，验证 schema
5. 写入 ft_events（含 fingerprint 去重）
6. 标记 ft_news.event_extracted=true

目标表: ft_events
"""

import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

# event-extract skill 路径
# 优先环境变量 → 推算（src/domain/aggregation/event_extraction.py 上溯到 .claude/skills/event-extract/SKILL.md）
import os as _os
_default_skill = (
    Path(__file__).resolve().parents[4] / "event-extract" / "SKILL.md"
)
SKILL_PATH = Path(_os.environ.get("EVENT_EXTRACT_SKILL_PATH", str(_default_skill)))

# 每批最多处理多少条新闻（避免单次 prompt 过长）
BATCH_SIZE = 3

# 单次 task 最多处理多少条（避免一次跑太久）
MAX_NEWS_PER_RUN = 30

# claude -p 超时（秒）
CLAUDE_TIMEOUT = 180


# ==================== 预筛选规则 ====================

# 跳过的标题模式（明显的纯播报）
SKIP_TITLE_PATTERNS = [
    r"^\s*[A-Z]{1,5}\s*[\d\.,]+",          # "USD 7.05" 这类纯报价
    r"^\s*\d+月\d+日.*?[早中下]盘?[速.]?讯",
    r".*?涨幅.*?\d+%.*?(领涨|涨停)$",
    r"^\s*\[.*?\].*?[早中下]盘?[速.]?讯\s*$",
]

# 内容太短直接跳过（少于 N 字）
MIN_CONTENT_LENGTH = 30


def _should_extract(news: dict) -> tuple[bool, str]:
    """预筛选：判断这条新闻是否值得调 AI 抽取

    Returns: (应该抽取?, 跳过原因)
    """
    title = news.get("title", "") or ""
    content = news.get("content", "") or ""
    summary = news.get("summary", "") or ""

    # 1. 标题太短
    if len(title) < 5:
        return False, "标题过短"

    # 2. 标题匹配跳过模式
    for pattern in SKIP_TITLE_PATTERNS:
        if re.match(pattern, title):
            return False, f"标题匹配跳过模式 {pattern[:30]}"

    # 3. 内容 + 摘要都太短
    text_len = max(len(content), len(summary))
    if text_len < MIN_CONTENT_LENGTH:
        return False, f"内容过短({text_len}字)"

    return True, ""


# ==================== Claude 调用 ====================


def _read_skill_body() -> str:
    """读取 event-extract SKILL.md 作为 system prompt"""
    if not SKILL_PATH.exists():
        raise FileNotFoundError(f"event-extract SKILL.md 不存在: {SKILL_PATH}")
    return SKILL_PATH.read_text(encoding="utf-8")


def _format_news_batch(news_batch: list[dict]) -> str:
    """把一批新闻格式化为 prompt 输入"""
    parts = ["请从下面的新闻中抽取事件，输出 JSON 数组（每条新闻对应一个事件 JSON）。\n"]
    for i, n in enumerate(news_batch, 1):
        parts.append(f"[新闻 {i}]")
        parts.append(f"id: {n.get('id')}")
        parts.append(f"title: {n.get('title', '')}")
        parts.append(f"source: {n.get('source', '')}")
        parts.append(f"published_at: {n.get('published_at', '')}")
        # 优先使用 content（真实正文），降级到 summary
        text = n.get("content") or n.get("summary") or ""
        if text:
            parts.append(f"content: {text[:2000]}")  # 限长 2000 字
        parts.append("")
    return "\n".join(parts)


def _extract_json_from_output(output: str) -> list[dict]:
    """从 claude 输出中提取 JSON 数组或对象

    支持：
    - 直接的 JSON 数组 [{...}, {...}]
    - 单个 JSON 对象 {...}
    - 包裹在 markdown 代码块中的 JSON
    - 多个独立 JSON 对象（每行一个）
    """
    if not output:
        return []
    output = output.strip()

    # 去 markdown 代码块
    if output.startswith("```"):
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", output, re.DOTALL)
        if match:
            output = match.group(1).strip()

    # 尝试解析为 JSON 数组
    try:
        data = json.loads(output)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    # 提取所有 {...} 块（每个独立解析）
    results = []
    depth = 0
    start = -1
    for i, c in enumerate(output):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(output[start:i + 1])
                    results.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return results


def _call_claude_sync(prompt: str, skill_body: str) -> str:
    """同步调用 claude -p（在线程池中执行，避免 asyncio.create_subprocess_exec 的兼容性问题）

    注意：base.py 模块加载时会清除代理环境变量（避免 httpx 走代理影响 A 股数据源）。
    但 claude CLI 需要代理才能访问 Anthropic API（国内网络），所以这里还原代理。
    """
    import os
    from src.infrastructure.clients.base import ORIGINAL_PROXY_ENV

    cmd = [
        "claude", "-p", prompt,
        "--append-system-prompt", skill_body,
        "--dangerously-skip-permissions",
    ]
    # 给子进程注入代理环境变量
    env = {**os.environ, **ORIGINAL_PROXY_ENV}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=env,
        )
        if result.returncode != 0:
            err_short = (result.stderr or "")[:500]
            out_short = (result.stdout or "")[:300]
            logger.warning(
                f"claude -p 退出码 {result.returncode}, stderr={err_short!r}, stdout={out_short!r}"
            )
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning(f"claude -p 超时（{CLAUDE_TIMEOUT}s）")
        return ""
    except Exception as e:
        logger.warning(f"claude -p 调用失败: {e}")
        return ""


async def _call_claude_extract(news_batch: list[dict], skill_body: str) -> list[dict]:
    """调 claude -p 抽取一批新闻的事件

    Returns: 抽取出的事件列表（与新闻顺序对应，is_event=false 的也会包含）
    """
    import asyncio as _asyncio

    prompt = _format_news_batch(news_batch)
    # 在线程池执行同步子进程调用，避免阻塞事件循环
    output = await _asyncio.to_thread(_call_claude_sync, prompt, skill_body)

    if not output:
        return []

    events = _extract_json_from_output(output)
    if not events:
        logger.warning(f"claude 输出无法解析为 JSON: {output[:300]}")
    return events


# ==================== 数据库操作 ====================


def _query_unextracted_news(limit: int = MAX_NEWS_PER_RUN) -> list[dict]:
    """读取未被 AI 抽取过的新闻"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, summary, content, source, source_name,
                       source_reliability, category, url, published_at
                FROM ft_news
                WHERE event_extracted = FALSE
                ORDER BY published_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def _mark_extracted(news_ids: list[int]) -> int:
    """标记新闻为已抽取"""
    if not news_ids:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_news SET event_extracted = TRUE WHERE id = ANY(%s)",
                (news_ids,),
            )
            conn.commit()
            return cur.rowcount


def _save_event(
    event: dict,
    source_news_ids: list[int],
    event_time: datetime,
    embedding_blob: bytes | None = None,
    embedding_model: str = "",
) -> bool:
    """写入一个事件到 ft_events"""
    title = event.get("title") or ""
    event_type = event.get("event_type") or ""
    if not title or not event_type:
        return False

    fingerprint = hashlib.sha256(f"{title}|{event_type}".encode()).hexdigest()
    impact = event.get("impact") or {}
    entities = event.get("entities") or {}

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ft_events (
                        title, summary, event_type, event_subtype,
                        industries, companies, organizations, regions,
                        impact_direction, impact_strength, impact_scope, impact_duration,
                        sentiment, novelty, certainty,
                        source_news_ids, event_time, fingerprint,
                        embedding, embedding_model
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (fingerprint) DO NOTHING
                    """,
                    (
                        title,
                        event.get("summary") or "",
                        event_type,
                        event.get("event_subtype") or "",
                        json.dumps(entities.get("industries") or [], ensure_ascii=False),
                        json.dumps(entities.get("companies") or [], ensure_ascii=False),
                        json.dumps(entities.get("organizations") or [], ensure_ascii=False),
                        json.dumps(entities.get("regions") or [], ensure_ascii=False),
                        impact.get("direction") or "neutral",
                        float(impact.get("strength") or 0),
                        impact.get("scope") or "",
                        impact.get("duration") or "",
                        float(event.get("sentiment") or 0.5),
                        float(event.get("novelty") or 0.5),
                        float(event.get("certainty") or 0.5),
                        source_news_ids,
                        event_time,
                        fingerprint,
                        psycopg2.Binary(embedding_blob) if embedding_blob else None,
                        embedding_model[:32] if embedding_model else "",
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"写入 ft_events 失败: {e}")
        return False


# ==================== 聚合器 ====================


class EventExtractionAggregator(BaseAggregator):
    """AI 事件抽取聚合器

    本聚合器与其他聚合器不同：
    - 不从外部 API 拉数据，而是从 ft_news 读已入库的新闻
    - 不调 client，而是调 claude -p 命令
    - 输出到 ft_events
    """

    data_domain = "event_extraction"
    task_interval = 300  # 5 分钟

    def __init__(self):
        super().__init__()
        self.sources = []  # 不通过 SourceDef 框架
        self._skill_body = None

    def _get_skill_body(self) -> str:
        if self._skill_body is None:
            self._skill_body = _read_skill_body()
        return self._skill_body

    def _save(self, items: list[dict]) -> int:
        return 0  # 不走通用入库

    def _get_checkpoint(self, source_name: str):
        return None

    async def tick(self):
        """主流程：读未抽取新闻 → 预筛选 → 批量调 claude → 写 ft_events"""
        news_list = _query_unextracted_news(MAX_NEWS_PER_RUN)
        if not news_list:
            logger.info("[event_extraction] 无待抽取新闻")
            return

        logger.info(f"[event_extraction] 待处理 {len(news_list)} 条新闻")

        # 预筛选
        candidates = []
        skipped_ids = []
        for n in news_list:
            ok, reason = _should_extract(n)
            if ok:
                candidates.append(n)
            else:
                skipped_ids.append(n["id"])
                logger.debug(f"[event_extraction] 跳过 news#{n['id']}: {reason}")

        if skipped_ids:
            _mark_extracted(skipped_ids)
            logger.info(f"[event_extraction] 预筛选跳过 {len(skipped_ids)} 条")

        if not candidates:
            return

        # 批量抽取
        skill_body = self._get_skill_body()
        total_saved = 0
        total_processed = 0

        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            batch_ids = [n["id"] for n in batch]

            try:
                events = await _call_claude_extract(batch, skill_body)
            except Exception as e:
                logger.warning(f"[event_extraction] 批量调用失败: {e}")
                continue

            # 先收集本批所有有效事件，批量算 embedding
            valid_pairs: list[tuple[dict, dict]] = []  # (event, source_news)
            for j, event in enumerate(events):
                if not isinstance(event, dict):
                    continue
                if not event.get("is_event"):
                    continue
                if j < len(batch):
                    valid_pairs.append((event, batch[j]))

            # 批量调 embedding 服务（失败也不阻塞入库）
            embeddings_blob: list[bytes | None] = [None] * len(valid_pairs)
            if valid_pairs:
                try:
                    from src.infrastructure.clients.embedding import (
                        embed_texts, encode_embedding,
                    )
                    embed_texts_input = [
                        f"{ev.get('title', '')}。{ev.get('summary', '')}".strip("。")
                        for ev, _ in valid_pairs
                    ]
                    vecs = await embed_texts(embed_texts_input)
                    for k, vec in enumerate(vecs):
                        if vec:
                            embeddings_blob[k] = encode_embedding(vec)
                except Exception as e:
                    logger.warning(f"[event_extraction] embedding 调用失败: {e}")

            # 入库
            saved_in_batch = 0
            embedded_in_batch = 0
            for k, (event, source_news) in enumerate(valid_pairs):
                event_time = source_news.get("published_at") or datetime.now()
                blob = embeddings_blob[k]
                model_tag = "qwen3-emb-4b" if blob else ""
                if _save_event(event, [source_news["id"]], event_time, blob, model_tag):
                    saved_in_batch += 1
                    if blob:
                        embedded_in_batch += 1

            _mark_extracted(batch_ids)
            total_processed += len(batch)
            total_saved += saved_in_batch
            logger.info(
                f"[event_extraction] batch {i // BATCH_SIZE + 1}: "
                f"处理 {len(batch)} 条，抽取 {len(events)} 个事件，"
                f"入库 {saved_in_batch} 条（含 embedding {embedded_in_batch} 条）"
            )

        logger.info(
            f"[event_extraction] 完成：处理 {total_processed} 条，入库 {total_saved} 个事件"
        )
