#!/usr/bin/env python3
"""真实数据库知识图谱回放与质量基线脚本。

这个脚本用于演示和验证“真实业务数据进入知识图谱”的完整链路。

运行方式：
    python "docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py"

默认只执行写入期链路，方便验证入库和 Retrieval Document 质量。
如需恢复完整回放，设置环境变量：
    KG_REPLAY_WRITE_ONLY=0 python "docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py"

默认每次运行都会先清理本脚本写入的 KG 结果、Retrieval Document、质量评估快照和 Milvus scope。
如需临时保留旧数据，设置环境变量：
    KG_REPLAY_RESET=0 python "docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py"

默认不写入受控 seed，也不跑依赖 seed 的固定回放题；真实数据评估只使用 ft_news 投影记录。
如需跑受控 golden regression，设置环境变量：
    KG_REPLAY_INCLUDE_SEED=1 python "docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py"

每次运行默认输出两份排查文件：
    generated_real_replay_ai_diagnostic.md  给 AI/工程师快速定位问题的精简报告
    generated_real_replay_full.log          全量日志，包含控制台输出、Python 日志、LLM 请求/响应 trace

执行哪些步骤由 main() 控制。不想执行某个步骤，就把对应代码注释掉。

通俗版步骤说明：

- 预检查：
  配置日志，打印当前模型路由，检查 Milvus 和知识图谱服务是否可用。
  这一步只是确认环境能跑，不会写入业务知识。

- Step 0 清空当前知识图谱测试数据：
  按 adapter/target 清理当前 KG 事实表、Wiki、索引、编译记录和 Milvus 向量索引。
  当前阶段录入的数据主要来自本脚本的测试回放，规则调整后应该先清空旧脏数据，
  再按最新规则重新入图，避免旧数据影响检索和质量判断。

- Step 1 读取真实 ft_news 并写入知识图谱：
  从 ft_news 表读取真实新闻，把表数据转换成知识图谱能理解的标准输入，
  再让 LLM 从标题和正文里抽取实体、影响对象和影响关系，最后写入节点、
  关系和证据。这是当前最核心的真实数据入图步骤。

- Step 1.5 写入受控基线数据：
  写入一小批人工准备好的样例数据。它只是调试和对照用的样本。
  默认不执行，避免污染真实 ft_news 回放。需要受控 golden regression 时设置
  KG_REPLAY_INCLUDE_SEED=1。

- Step 2 执行真实 ft_* 增量刷新：
  执行更接近生产环境的一键增量刷新任务。它包含股票基础信息冷启动、
  ft_news 新闻入图和增量索引刷新。它和 Step 1 有重叠，所以通常不要
  和 Step 1 同时执行，除非你就是想对比两条链路。

- Step 3 重建 Wiki 和索引：
  从事实库重新生成 Wiki 页面、图邻接索引、证据切片和向量索引。
  这一步很慢，日常新增数据后一般不需要执行。只有改了 Wiki/索引逻辑、
  第一次冷启动、索引损坏或索引和事实库不一致时才需要跑。

- Step 4 回放自动路由质量基线：
  默认使用真实 ft_news 动态生成的问题测试检索效果，检查能不能搜到预期实体、关系和证据，
  同时检查不相关噪声是否被 Query Anchor 和候选裁判挡住。可以理解成
  “拿确定有来源的考题检查知识图谱是否能查对，而且不要把无关内容混进来”。
  如果 KG_REPLAY_INCLUDE_SEED=1，会额外加入受控固定题，作为回归对照。
  这一步不写入新的业务事实。
  默认会打印检索控制器和候选裁判发给 LLM 的 prompt/response，方便排查
  为什么它选择某个工具、为什么保留或丢弃某个候选。

- Step 5 回放 Agentic A-RAG 观察：
  还是问同一批问题，但让 LLM 自己决定使用哪些检索工具。
  这一步更慢，也会消耗 token，适合观察复杂检索效果，不适合日常入图。

- Step 6 查看样例投研上下文：
  打印几条查询的上下文片段，方便人工直接阅读检索结果。
  这是人工观察，不是自动质量门禁。

- Step 7 检查数据库落库：
  查询知识图谱相关表，打印数量和证据样例，用来确认数据确实写进去了。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from pprint import pprint
from typing import Any, Awaitable, Callable, TextIO, TypeVar, cast

from sqlalchemy import text


def _project_root() -> Path:
    root = Path(__file__).resolve()
    while root.name != "smart-fund-server" and root.parent != root:
        root = root.parent
    if root.name != "smart-fund-server":
        raise RuntimeError("cannot locate smart-fund-server project root")
    return root


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.dto.knowledge_dto import (  # noqa: E402
    KnowledgeBadCaseReplayCommand,
    KnowledgeCompileCommand,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildWikiCommand,
    KnowledgeResearchContextCommand,
    KnowledgeResearchContextBadCase,
)
from src.application.services.financial_news_projection import build_news_records_from_sources  # noqa: E402
from src.application.services.knowledge_service import (  # noqa: E402
    KnowledgeService,
    create_knowledge_service,
)
from src.application.services.knowledge_llm_config import kg_llm_config_summary  # noqa: E402
from src.domain.knowledge_adapters.financial.baseline_rules import (  # noqa: E402
    financial_baseline_normalization_rules,
)
from src.domain.knowledge.retrieval_eval import (  # noqa: E402
    RetrievalEvalRun,
    RetrievalLabel,
    aggregate_eval_metrics,
    build_preselect_eval_metrics,
    retrieval_query_hash,
)
from src.domain.knowledge.retrieval_document import RETRIEVAL_DOCUMENT_VERSION  # noqa: E402
from src.domain.knowledge.retrieval_document_quality import (  # noqa: E402
    build_retrieval_document_quality_report,
)
from src.infrastructure.config import settings  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.llm_proxy.service import get_llm_gateway_service  # noqa: E402
from src.infrastructure.persistence.repositories.knowledge_normalization_rule_repository import (  # noqa: E402
    KnowledgeNormalizationRuleRepository,
)
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridStore  # noqa: E402


BASELINE_TS = "2026-04-29T09:30:00+08:00"
SCRIPT_CREATED_BY = "docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py"
T = TypeVar("T")
RUN_STATE: dict[str, Any] = {
    "status": "running",
    "steps": [],
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
_FULL_LOG_HANDLE: TextIO | None = None
_ORIGINAL_STDOUT: TextIO | None = None
_ORIGINAL_STDERR: TextIO | None = None


class TeeStream:
    def __init__(self, primary: TextIO, secondary: TextIO):
        self.primary = primary
        self.secondary = secondary
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, text: str) -> int:
        self.primary.write(text)
        self.secondary.write(text)
        return len(text)

    def flush(self) -> None:
        self.primary.flush()
        self.secondary.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.primary, "isatty", lambda: False)())


@dataclass(frozen=True)
class ReplayConfig:
    target: str = "prod"
    adapter: str = "financial"
    dry_run: bool = False
    reset_before_replay: bool = True
    concurrency: int = 2
    stock_limit: int = 50
    news_limit: int = 5
    projection_news_limit: int = 100
    projection_order_by_created_at: bool = True
    projection_codes: tuple[str, ...] = ()
    dynamic_case_limit: int = 12
    codes: tuple[str, ...] = ("300750", "603305")
    max_chars: int = 8000
    strict_agentic: bool = False
    trace_retrieval_llm: bool = True
    trace_retrieval_llm_max_chars: int = 4000
    trace_retrieval_llm_max_items: int = 8
    trace_retrieval_llm_snippet_chars: int = 260
    trace_retrieval_llm_file: str = ""
    profile_retrieval: bool = True
    profile_retrieval_verbose: bool = False
    profile_retrieval_min_ms: int = 1000
    include_seed_baseline: bool = False
    full_log_enabled: bool = True
    full_log_file: str = ""
    llm_full_trace_file: str = ""
    ai_diagnostic_file: str = ""
    quality_eval_enabled: bool = True
    quality_eval_strategy_name: str = "real_replay_quality_baseline"
    quality_eval_strategy_version: str = "v1"
    quality_eval_k_values: tuple[int, ...] = (8, 12, 15)
    quality_snapshot_limit: int = 200
    write_only: bool = False
    fail_on_compile_failure: bool = False


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def print_run_mode(config: ReplayConfig) -> None:
    print(
        "[mode] "
        f"write_only={config.write_only} "
        f"reset_before_write={config.reset_before_replay} "
        f"target={config.target} adapter={config.adapter} "
        f"projection_news_limit={config.projection_news_limit} "
        f"dynamic_case_limit={config.dynamic_case_limit} "
        f"include_seed_baseline={config.include_seed_baseline} "
        f"fail_on_compile_failure={config.fail_on_compile_failure}"
    )


def _default_generated_path(filename: str) -> str:
    return str(PROJECT_ROOT / "docs/6. 使用说明/知识图谱" / filename)


def full_log_path(config: ReplayConfig) -> str:
    return config.full_log_file or _default_generated_path("generated_real_replay_full.log")


def llm_full_trace_path(config: ReplayConfig) -> str:
    return config.llm_full_trace_file or _default_generated_path("generated_real_replay_llm_full_trace.log")


def ai_diagnostic_path(config: ReplayConfig) -> str:
    return config.ai_diagnostic_file or _default_generated_path("generated_real_replay_ai_diagnostic.md")


def retrieval_trace_path(config: ReplayConfig) -> str:
    return config.trace_retrieval_llm_file or _default_generated_path("generated_retrieval_llm_trace.log")


def configure_full_run_log(config: ReplayConfig) -> None:
    global _FULL_LOG_HANDLE, _ORIGINAL_STDOUT, _ORIGINAL_STDERR
    if not config.full_log_enabled:
        os.environ.pop("LLM_PROXY_FULL_TRACE_FILE", None)
        print("[trace] full run log disabled")
        return
    path = Path(full_log_path(config))
    llm_trace_path = Path(llm_full_trace_path(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    llm_trace_path.parent.mkdir(parents=True, exist_ok=True)
    llm_trace_path.write_text("", encoding="utf-8")
    _FULL_LOG_HANDLE = path.open("w", encoding="utf-8", buffering=1)
    _ORIGINAL_STDOUT = sys.stdout
    _ORIGINAL_STDERR = sys.stderr
    sys.stdout = cast(TextIO, TeeStream(sys.stdout, _FULL_LOG_HANDLE))
    sys.stderr = cast(TextIO, TeeStream(sys.stderr, _FULL_LOG_HANDLE))
    os.environ["LLM_PROXY_FULL_TRACE_FILE"] = str(llm_trace_path)
    print(f"[trace] full run log enabled file={path}", flush=True)
    print(f"[trace] LLM proxy full trace enabled file={llm_trace_path}", flush=True)


def close_full_run_log() -> None:
    global _FULL_LOG_HANDLE, _ORIGINAL_STDOUT, _ORIGINAL_STDERR
    if _FULL_LOG_HANDLE is None:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        if _ORIGINAL_STDOUT is not None:
            sys.stdout = _ORIGINAL_STDOUT
        if _ORIGINAL_STDERR is not None:
            sys.stderr = _ORIGINAL_STDERR
        _FULL_LOG_HANDLE.close()
        _FULL_LOG_HANDLE = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("src.domain.knowledge.compiler").setLevel(logging.INFO)
    logging.getLogger("src.domain.knowledge.retrieval").setLevel(logging.INFO)
    logging.getLogger("src.domain.knowledge.retrieval.profile").setLevel(logging.INFO)
    logging.getLogger("src.application.services.knowledge_service").setLevel(logging.INFO)
    logging.getLogger("src.infrastructure.llm_proxy.service").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def configure_retrieval_llm_trace(config: ReplayConfig) -> None:
    if config.trace_retrieval_llm:
        trace_file = retrieval_trace_path(config)
        os.environ["KG_RETRIEVAL_LLM_TRACE"] = "1"
        os.environ["KG_RETRIEVAL_LLM_TRACE_MAX_CHARS"] = str(config.trace_retrieval_llm_max_chars)
        os.environ["KG_RETRIEVAL_LLM_TRACE_MAX_ITEMS"] = str(config.trace_retrieval_llm_max_items)
        os.environ["KG_RETRIEVAL_LLM_TRACE_SNIPPET_CHARS"] = str(
            config.trace_retrieval_llm_snippet_chars
        )
        os.environ["KG_RETRIEVAL_LLM_TRACE_FILE"] = trace_file
        Path(trace_file).parent.mkdir(parents=True, exist_ok=True)
        Path(trace_file).write_text("", encoding="utf-8")
        print(
            "[trace] KG retrieval LLM trace enabled "
            f"file={trace_file} max_chars={config.trace_retrieval_llm_max_chars} "
            f"max_items={config.trace_retrieval_llm_max_items} "
            f"snippet_chars={config.trace_retrieval_llm_snippet_chars}"
        )
        return
    os.environ.pop("KG_RETRIEVAL_LLM_TRACE", None)
    os.environ.pop("KG_RETRIEVAL_LLM_TRACE_FILE", None)
    print("[trace] KG retrieval LLM trace disabled")


def configure_retrieval_profile(config: ReplayConfig) -> None:
    if config.profile_retrieval:
        os.environ["KG_RETRIEVAL_PROFILE"] = "1"
        os.environ["KG_RETRIEVAL_PROFILE_MIN_MS"] = str(config.profile_retrieval_min_ms)
        os.environ.setdefault("KG_RETRIEVAL_PROFILE_LOG_LEVEL", "INFO")
        if config.profile_retrieval_verbose:
            os.environ["KG_RETRIEVAL_PROFILE_VERBOSE"] = "1"
        else:
            os.environ.pop("KG_RETRIEVAL_PROFILE_VERBOSE", None)
        print(
            "[trace] KG retrieval profile enabled "
            f"slow_only_ms={config.profile_retrieval_min_ms} "
            f"verbose={config.profile_retrieval_verbose}"
        )
        return
    os.environ.pop("KG_RETRIEVAL_PROFILE", None)
    os.environ.pop("KG_RETRIEVAL_PROFILE_VERBOSE", None)
    os.environ.pop("KG_RETRIEVAL_PROFILE_LOG_LEVEL", None)
    print("[trace] KG retrieval profile disabled")


def print_llm_config() -> None:
    gateway_health = get_llm_gateway_service().health()
    pprint(
        {
            "kg_llm": kg_llm_config_summary(),
            "llm_proxy": {
                "default_provider": gateway_health.get("default_provider"),
                "default_model": gateway_health.get("default_model"),
                "model_routes": gateway_health.get("model_routes"),
                "providers": gateway_health.get("providers"),
                "cache": gateway_health.get("cache"),
            },
        }
    )


def assert_kg_health_ok(health: Any) -> None:
    data = health.to_dict() if hasattr(health, "to_dict") else dict(health)
    if data.get("status") == "ok":
        return
    db_config = getattr(settings, "DB_CONFIG", {})
    db_target = {
        "host": db_config.get("host"),
        "port": db_config.get("port"),
        "dbname": db_config.get("dbname"),
        "user": db_config.get("user"),
    }
    raise RuntimeError(
        "\nKG service health check failed before replay.\n"
        f"database_target={db_target}\n"
        f"database_status={data.get('database')}\n"
        "This is an environment/database availability problem, not a retrieval quality result. "
        "If the error mentions pg_hba.conf, add this machine's client IP to PostgreSQL pg_hba.conf "
        "on the database server, or connect through an allowed network path. "
        "Otherwise start the configured PostgreSQL service or update DB_HOST/DB_PORT/DB_NAME in .env, then rerun."
    )


def ensure_financial_normalization_rules(target: str) -> None:
    """Ensure system baseline normalization rules exist; rule contents live in the adapter."""

    affected = KnowledgeNormalizationRuleRepository(target=target).ensure_active_rules(
        "financial",
        financial_baseline_normalization_rules(),
    )
    print(f"[rules] baseline financial normalization rules ready affected={affected}", flush=True)


def run_sync_step(name: str, func: Callable[[], T]) -> T:
    print(f"\n[step] START {name}", flush=True)
    started = time.perf_counter()
    try:
        result = func()
    except Exception as exc:
        duration = time.perf_counter() - started
        print(f"[step] FAILED {name} duration={duration:.1f}s", flush=True)
        RUN_STATE["steps"].append(
            {"name": name, "status": "failed", "duration_s": round(duration, 3), "error": repr(exc)}
        )
        raise
    duration = time.perf_counter() - started
    print(f"[step] DONE {name} duration={duration:.1f}s", flush=True)
    RUN_STATE["steps"].append({"name": name, "status": "done", "duration_s": round(duration, 3)})
    return result


async def run_step(name: str, awaitable_factory: Callable[[], Awaitable[T]]) -> T:
    print(f"\n[step] START {name}", flush=True)
    started = time.perf_counter()
    try:
        result = await awaitable_factory()
    except Exception as exc:
        duration = time.perf_counter() - started
        print(f"[step] FAILED {name} duration={duration:.1f}s", flush=True)
        RUN_STATE["steps"].append(
            {"name": name, "status": "failed", "duration_s": round(duration, 3), "error": repr(exc)}
        )
        raise
    duration = time.perf_counter() - started
    print(f"[step] DONE {name} duration={duration:.1f}s", flush=True)
    RUN_STATE["steps"].append({"name": name, "status": "done", "duration_s": round(duration, 3)})
    return result


def stock_entity(code: str, name: str, exchange: str = "SZ", confidence: float = 0.95) -> dict[str, Any]:
    return {
        "type": "stock",
        "exchange": exchange,
        "code": code,
        "name": name,
        "aliases": [code, f"{code}.{exchange}"],
        "confidence": confidence,
    }


def concept(name: str, *, taxonomy: str = "baseline", confidence: float = 0.86, **extra) -> dict[str, Any]:
    return {"type": "concept", "taxonomy": taxonomy, "name": name, "confidence": confidence, **extra}


def industry(name: str, *, taxonomy: str = "baseline", confidence: float = 0.86, **extra) -> dict[str, Any]:
    return {"type": "industry", "taxonomy": taxonomy, "name": name, "confidence": confidence, **extra}


def macro_indicator(code: str, name: str, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "type": "macro_indicator",
        "indicator_code": code,
        "name": name,
        "confidence": confidence,
    }


def baseline_records() -> list[dict[str, Any]]:
    return [
        {
            "source_type": "stock_basics",
            "source_id": "notebook_baseline:stock:300750",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:stock:300750",
                "exchange": "SZ",
                "code": "300750",
                "name": "宁德时代",
                "company_name": "宁德时代新能源科技股份有限公司",
                "aliases": ["CATL", "300750", "300750.SZ"],
                "status": "active",
            },
        },
        {
            "source_type": "stock_basics",
            "source_id": "notebook_baseline:stock:603305",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:stock:603305",
                "exchange": "SH",
                "code": "603305",
                "name": "旭升集团",
                "aliases": ["603305", "603305.SH"],
                "status": "active",
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:catl_overseas_capacity",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:catl_overseas_capacity",
                "document_id": "notebook_baseline:news:catl_overseas_capacity",
                "title": "宁德时代海外产能扩张带动储能供应链订单",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "宁德时代推进欧洲和东南亚海外产能扩张，储能电芯、快充电池和新能源车产业链订单预期改善。市场认为海外工厂投产有助于降低贸易壁垒，并改善供应链交付能力。",
                "mentioned_entities": [
                    stock_entity("300750", "宁德时代"),
                    concept("海外产能"),
                    concept("快充"),
                    industry("储能产业链"),
                ],
                "affected_entities": [
                    {**stock_entity("300750", "宁德时代"), "direction": "positive", "reason": "海外产能扩张改善交付能力"},
                    {**industry("储能产业链"), "direction": "positive", "reason": "储能电芯订单预期改善"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "快充和动力电池需求提升"},
                ],
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:ma_industry_rotation",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:ma_industry_rotation",
                "document_id": "notebook_baseline:news:ma_industry_rotation",
                "title": "并购重组政策活跃提升券商、半导体设备和新能源车风险偏好",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "资本市场并购重组审核提速，市场预期产业整合会带动券商投行业务、半导体设备国产替代和新能源车产业链估值修复。并购重组主题也可能提升中小市值公司的交易活跃度。",
                "mentioned_entities": [
                    concept("并购重组"),
                    industry("券商"),
                    industry("半导体设备"),
                    industry("新能源车产业链"),
                ],
                "affected_entities": [
                    {**industry("券商"), "direction": "positive", "reason": "投行业务弹性提升"},
                    {**industry("半导体设备"), "direction": "positive", "reason": "产业整合和国产替代预期加强"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "估值修复和整合预期"},
                    {**concept("中小市值"), "direction": "positive", "reason": "交易活跃度提升"},
                ],
            },
        },
        {
            "source_type": "policy_news",
            "source_id": "notebook_baseline:policy:low_rate_growth_assets",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:policy:low_rate_growth_assets",
                "document_id": "notebook_baseline:policy:low_rate_growth_assets",
                "title": "低利率环境和资本市场改革提升成长资产估值",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "低利率环境降低权益资产折现率，资本市场改革改善风险偏好。成长资产、科技创新、半导体设备和新能源车产业链可能受益，但高股息资产的相对吸引力可能下降。",
                "mentioned_entities": [
                    macro_indicator("low_rate_environment", "低利率环境"),
                    concept("资本市场改革"),
                    concept("成长资产"),
                ],
                "affected_entities": [
                    {**concept("成长资产"), "direction": "positive", "reason": "折现率下降提升估值"},
                    {**industry("半导体设备"), "direction": "positive", "reason": "成长风格风险偏好改善"},
                    {**industry("新能源车产业链"), "direction": "positive", "reason": "成长股估值弹性提升"},
                    {**concept("高股息资产"), "direction": "negative", "reason": "相对吸引力下降"},
                ],
            },
        },
        {
            "source_type": "news_articles",
            "source_id": "notebook_baseline:news:middle_east_assets",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:news:middle_east_assets",
                "document_id": "notebook_baseline:news:middle_east_assets",
                "title": "中东冲突升温推升原油和黄金避险需求",
                "published_at": BASELINE_TS,
                "source_name": "Notebook基线",
                "text": "中东冲突升温可能扰动能源运输通道，原油价格和黄金避险需求上升。航空运输行业面临燃油成本压力，化工产业链也会受到原油成本传导影响。",
                "mentioned_entities": [
                    concept("中东冲突"),
                    {"type": "commodity", "name": "原油", "confidence": 0.88},
                    {"type": "commodity", "name": "黄金", "confidence": 0.88},
                    industry("航空运输"),
                ],
                "affected_entities": [
                    {"type": "commodity", "name": "原油", "direction": "positive", "confidence": 0.88, "reason": "供应扰动和运输通道风险"},
                    {"type": "commodity", "name": "黄金", "direction": "positive", "confidence": 0.86, "reason": "避险需求上升"},
                    {**industry("航空运输"), "direction": "negative", "reason": "燃油成本压力上升"},
                    {**industry("化工产业链"), "direction": "negative", "reason": "原油成本向下游传导"},
                ],
            },
        },
        {
            "source_type": "derived_signal",
            "source_id": "notebook_baseline:signal:catl_flow",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:signal:catl_flow",
                "signal_type": "market_flow.stock_net_inflow",
                "observed_at": BASELINE_TS,
                "target_ref": stock_entity("300750", "宁德时代"),
                "title": "宁德时代资金净流入改善",
                "value": 1,
                "unit": "signal",
                "window": "1d",
                "confidence": 0.9,
                "raw_data": {"net_inflow_signal": "positive", "source": "notebook_baseline"},
            },
        },
        {
            "source_type": "derived_signal",
            "source_id": "notebook_baseline:signal:low_rate",
            "observed_at": BASELINE_TS,
            "payload": {
                "source_id": "notebook_baseline:signal:low_rate",
                "signal_type": "macro.low_rate_environment",
                "observed_at": BASELINE_TS,
                "target_ref": macro_indicator("low_rate_environment", "低利率环境"),
                "title": "低利率环境利好成长资产",
                "value": 1,
                "unit": "signal",
                "window": "latest",
                "confidence": 0.9,
                "raw_data": {"rate_signal": "low", "source": "notebook_baseline"},
            },
        },
    ]


def auto_bad_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "real_baseline_catl_recent_events",
            "宁德时代 300750 最近受哪些事件影响",
            ["宁德时代"],
            ["mentions"],
            1,
            forbidden_node_names=["俄罗斯", "波兰", "法国"],
            forbidden_topics=["中东冲突", "军演"],
        ),
        # Early replay debugging keeps only the first controlled bad case.
        # Uncomment these cases after the write/retrieval path is stable.
        # _case(
        #     "real_baseline_ma_industry_targets",
        #     "并购重组对哪些行业有影响",
        #     ["并购重组", "券商"],
        #     ["mentions", "affects"],
        #     2,
        #     forbidden_node_names=["宁德时代"],
        #     forbidden_topics=["固态电池", "军演"],
        # ),
        # _case(
        #     "real_baseline_low_rate_beneficiaries",
        #     "低利率环境利好什么资产和行业",
        #     ["低利率环境", "成长资产"],
        #     ["mentions", "affects"],
        #     2,
        #     forbidden_node_names=["俄罗斯", "波兰", "法国"],
        #     forbidden_topics=["军演"],
        # ),
        # _case(
        #     "real_baseline_middle_east_asset_transmission",
        #     "中东冲突影响哪些资产和行业",
        #     ["中东冲突", "原油", "黄金"],
        #     ["mentions", "affects"],
        #     3,
        #     forbidden_node_names=["宁德时代"],
        #     forbidden_topics=["固态电池"],
        # ),
        # _case(
        #     "real_baseline_semantic_paraphrase_overseas_factory",
        #     "海外工厂投产会带动哪些产业链机会",
        #     ["海外产能", "储能产业链"],
        #     ["mentions"],
        #     2,
        #     forbidden_node_names=["俄罗斯", "波兰", "法国"],
        #     forbidden_topics=["军演"],
        # ),
    ]


def real_news_cases_from(records: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for record in records:
        payload = record.get("payload", {}) or {}
        title = str(payload.get("title") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        safe_id = re.sub(r"[^0-9A-Za-z_]+", "_", source_id.replace(":", "_")).strip("_")
        cases.append(
            _case(
                f"real_ft_news_{safe_id}",
                f"{title} 这条新闻涉及哪些主体、行业或资产影响",
                [title],
                [],
                1,
                retrieval_mode="auto",
                min_matched_edges=0,
            )
        )
        if len(cases) >= limit:
            break
    return cases


def _case(
    case_id: str,
    query: str,
    expected_node_names: list[str],
    expected_relation_types: list[str],
    min_matched_nodes: int,
    *,
    forbidden_node_names: list[str] | None = None,
    forbidden_evidence_refs: list[str] | None = None,
    forbidden_topics: list[str] | None = None,
    retrieval_mode: str = "auto",
    min_matched_edges: int = 1,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "query": query,
        "expected_node_names": expected_node_names,
        "expected_relation_types": expected_relation_types,
        "expected_channels_used": ["search", "open"],
        "forbidden_node_names": forbidden_node_names or [],
        "forbidden_evidence_refs": forbidden_evidence_refs or [],
        "forbidden_topics": forbidden_topics or [],
        "min_hits": 2,
        "min_evidence_refs": 1,
        "min_matched_nodes": min_matched_nodes,
        "min_matched_edges": min_matched_edges,
        "max_forbidden_hits": 0,
        "retrieval_mode": retrieval_mode,
    }


def agentic_cases_from(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for case in cases:
        # Agentic A-RAG 的价值就是由 LLM 自主选工具，不能硬断言固定 channel。
        # 仍然检查核心节点/关系/最小召回量，避免假通过。
        relaxed = dict(case, retrieval_mode="agentic_arag")
        relaxed["expected_channels_used"] = []
        relaxed["forbidden_node_names"] = []
        relaxed["forbidden_evidence_refs"] = []
        relaxed["forbidden_topics"] = []
        result.append(relaxed)
    return result


def sample_queries() -> list[str]:
    return [
        "宁德时代 300750 最近受哪些事件影响",
        "并购重组对哪些行业有影响",
        "海外工厂投产会带动哪些产业链机会",
    ]


async def compile_seed(service: KnowledgeService, config: ReplayConfig) -> None:
    records = baseline_records()
    print(f"\n[seed] compile controlled records: {len(records)}")
    result = await service.compile_kg(
        KnowledgeCompileCommand(
            adapter_name=config.adapter,
            target=config.target,
            records=records,
            dry_run=config.dry_run,
            concurrency=config.concurrency,
        )
    )
    summary = print_compile_result_summary(result, label="seed")
    if result.failed_records:
        raise AssertionError({"message": "controlled baseline compile failed", "summary": summary})


async def compile_real_ft_news_projection(service: KnowledgeService, config: ReplayConfig) -> list[dict[str, Any]]:
    print("\n[ft_news] read real rows and project to KG source records")
    records = build_news_records_from_sources(
        target=config.target,
        codes=list(config.projection_codes),
        limit=config.projection_news_limit,
        order_by_created_at=config.projection_order_by_created_at,
    )
    print(f"[ft_news] projected records: {len(records)}")
    for record in records:
        payload = record.get("payload", {})
        preview = {
            "source_type": record.get("source_type"),
            "source_id": record.get("source_id"),
            "title": payload.get("title"),
            "raw_text_len": len(record.get("raw_text") or ""),
            "mentioned_entities": payload.get("mentioned_entities", [])[:5],
        }
        pprint(preview)
    if not records:
        raise AssertionError("no ft_news records projected")

    print("\n[ft_news] compile projected source records into KG")
    result = await service.compile_kg(
        KnowledgeCompileCommand(
            adapter_name=config.adapter,
            target=config.target,
            records=records,
            dry_run=config.dry_run,
            concurrency=config.concurrency,
        )
    )
    summary = print_compile_result_summary(result, label="ft_news")
    if result.failed_records:
        message = (
            "[ft_news] compile completed with partial failures. "
            f"failed_records={result.failed_records}; "
            "see failure_reason_counts/failure_sample above."
        )
        if config.fail_on_compile_failure:
            raise AssertionError({"message": message, "summary": summary})
        print(f"[warning] {message}", flush=True)
    return records


async def incremental_refresh(service: KnowledgeService, config: ReplayConfig) -> None:
    print("\n[incremental] refresh real ft_* sources")
    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target=config.target,
            codes=list(config.codes),
            stock_limit=config.stock_limit,
            news_limit=config.news_limit,
            dry_run=config.dry_run,
            concurrency=config.concurrency,
            rebuild_indexes=False,
        )
    )
    pprint(result.to_dict())
    failed_steps = [step for step in result.steps if step.get("failed_records", 0)]
    if failed_steps:
        raise AssertionError(failed_steps)


async def rebuild(service: KnowledgeService, config: ReplayConfig) -> None:
    if config.dry_run:
        raise RuntimeError("DRY_RUN=True cannot rebuild wiki/indexes")
    print("\n[rebuild] wiki")
    wiki = await service.rebuild_wiki_for(
        KnowledgeRebuildWikiCommand(adapter_name=config.adapter, target=config.target)
    )
    pprint(wiki.to_dict())
    if wiki.pages <= 0:
        raise AssertionError(wiki.to_dict())

    print("\n[rebuild] graph/evidence/Milvus indexes")
    indexes = await service.rebuild_indexes_for(
        KnowledgeRebuildIndexesCommand(
            adapter_name=config.adapter,
            target=config.target,
            index_types=["graph_adjacency", "evidence_chunks", "hybrid_chunks"],
        )
    )
    pprint(indexes.to_dict())
    if indexes.graph_adjacency <= 0 or indexes.evidence_chunks <= 0 or indexes.hybrid_chunks <= 0:
        raise AssertionError(indexes.to_dict())


async def replay(
    service: KnowledgeService,
    config: ReplayConfig,
    *,
    mode: str,
    cases: list[dict[str, Any]],
    fail_on_error: bool,
) -> dict[str, Any]:
    print(f"\n[replay] {mode}")
    results: list[dict[str, Any]] = []
    total = len(cases)
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case-{index}")
        query = str(case.get("query") or "")
        print(f"\n[replay] [{index}/{total}] START {case_id}: {query}", flush=True)
        started = time.perf_counter()
        result = await service.replay_research_context_bad_cases(
            KnowledgeBadCaseReplayCommand(
                adapter_name=config.adapter,
                target=config.target,
                cases=[KnowledgeResearchContextBadCase(**case)],
                graph_depth=3,
                graph_limit=30,
                wiki_limit=10,
                evidence_limit=30,
                max_chars=config.max_chars,
            )
        )
        single_data = result.to_dict()
        item = single_data["results"][0]
        results.append(item)
        duration = time.perf_counter() - started
        status = "PASS" if item["passed"] else "FAIL"
        print(
            f"[replay] [{index}/{total}] DONE {status} {case_id} "
            f"duration={duration:.1f}s route={_route_summary(item)} "
            f"channels={item.get('channels_used') or []} metrics={item.get('metrics') or {}} "
            f"retrieval_metrics={_compact_retrieval_metrics(item)}",
            flush=True,
        )
        _print_anchor_and_judge_summary(item)
        if not item["passed"]:
            pprint({
                "missing_node_names": item["missing_node_names"],
                "missing_relation_types": item["missing_relation_types"],
                "missing_channels_used": item["missing_channels_used"],
                "forbidden_node_names_hit": item.get("forbidden_node_names_hit") or [],
                "forbidden_evidence_refs_hit": item.get("forbidden_evidence_refs_hit") or [],
                "forbidden_topics_hit": item.get("forbidden_topics_hit") or [],
                "metric_failures": item["metric_failures"],
                "routing_decision": item.get("routing_decision") or {},
                "query_anchor": item.get("query_anchor") or {},
                "candidate_judgement_summary": item.get("candidate_judgement_summary") or {},
            })

    passed = sum(1 for item in results if item.get("passed"))
    data = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "metrics": replay_metrics(results),
        "results": results,
    }
    pprint({key: data[key] for key in ["total", "passed", "failed", "metrics"]})
    for item in data["results"]:
        if item["passed"]:
            continue
        print(f"\nFAILED {item['case_id']}: {item['query']}")
        pprint({
            "missing_node_names": item["missing_node_names"],
            "missing_relation_types": item["missing_relation_types"],
            "missing_channels_used": item["missing_channels_used"],
            "forbidden_node_names_hit": item.get("forbidden_node_names_hit") or [],
            "forbidden_evidence_refs_hit": item.get("forbidden_evidence_refs_hit") or [],
            "forbidden_topics_hit": item.get("forbidden_topics_hit") or [],
            "metric_failures": item["metric_failures"],
            "channels_used": item["channels_used"],
            "routing_decision": item.get("routing_decision") or {},
            "query_anchor": item.get("query_anchor") or {},
            "candidate_judgement_summary": item.get("candidate_judgement_summary") or {},
        })
    if data["failed"] and fail_on_error:
        raise AssertionError(data)
    if data["failed"]:
        print(
            "\nAgentic A-RAG replay has failures but is observational by default. "
            "Set config.strict_agentic=True if you want it to fail the script."
        )
    return data


def replay_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    channel_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    upgraded = 0
    totals = {
        "hits": 0,
        "evidence_refs": 0,
        "matched_nodes": 0,
        "matched_edges": 0,
        "forbidden_hits": 0,
    }
    context_precision_total = 0.0
    context_precision_count = 0
    for item in results:
        for channel in item.get("channels_used") or []:
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
        routing = item.get("routing_decision") or {}
        route = str(routing.get("final_mode") or item.get("retrieval_mode") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
        if routing.get("upgraded"):
            upgraded += 1
        metrics = item.get("metrics") or {}
        for name in totals:
            totals[name] += int(metrics.get(name) or 0)
        retrieval_metrics = item.get("retrieval_metrics") or {}
        if retrieval_metrics.get("context_precision") is not None:
            context_precision_total += float(retrieval_metrics["context_precision"])
            context_precision_count += 1
    return {
        "pass_rate": (passed / total) if total else 0.0,
        "channel_coverage": channel_counts,
        "route_coverage": route_counts,
        "upgraded": upgraded,
        "avg_hits": (totals["hits"] / total) if total else 0.0,
        "avg_evidence_refs": (totals["evidence_refs"] / total) if total else 0.0,
        "avg_matched_nodes": (totals["matched_nodes"] / total) if total else 0.0,
        "avg_matched_edges": (totals["matched_edges"] / total) if total else 0.0,
        "avg_forbidden_hits": (totals["forbidden_hits"] / total) if total else 0.0,
        "avg_context_precision": (
            context_precision_total / context_precision_count
            if context_precision_count
            else 0.0
        ),
    }


def _route_summary(item: dict[str, Any]) -> dict[str, Any]:
    routing = item.get("routing_decision") or {}
    return {
        "requested": item.get("retrieval_mode"),
        "initial": routing.get("initial_mode"),
        "final": routing.get("final_mode"),
        "upgraded": routing.get("upgraded"),
        "reason": routing.get("reason"),
    }


def _compact_retrieval_metrics(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item.get("retrieval_metrics") or {}
    keys = (
        "raw_hits",
        "accepted_hits",
        "rejected_hits",
        "context_precision",
        "anchor_coverage",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _print_anchor_and_judge_summary(item: dict[str, Any]) -> None:
    anchor = item.get("query_anchor") or {}
    judgement = item.get("candidate_judgement_summary") or {}
    compact_anchor = {
        "core_phrases": anchor.get("core_phrases") or [],
        "entities": anchor.get("entities") or [],
        "intent": anchor.get("intent") or "",
        "source_refs": anchor.get("source_refs") or [],
        "confidence": anchor.get("confidence"),
    }
    print("[replay] anchor/judge")
    pprint({"anchor": compact_anchor, "judge": judgement})


def write_case_file(config: ReplayConfig, cases: list[dict[str, Any]]) -> None:
    path = PROJECT_ROOT / "docs/6. 使用说明/知识图谱/generated_real_replay_bad_cases.json"
    path.write_text(
        json.dumps({"adapter_name": config.adapter, "target": config.target, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    RUN_STATE["case_file"] = str(path)
    RUN_STATE["case_count"] = len(cases)
    print(f"\n[cases] written: {path}")


def inspect_retrieval_documents(service: KnowledgeService, config: ReplayConfig) -> dict[str, Any]:
    repository = service.repository
    if repository is None:
        print("[retrieval-doc] skipped: repository unavailable")
        return {}
    documents = repository.list_retrieval_documents(config.adapter, target=config.target)
    versions = repository.list_retrieval_document_versions(
        config.adapter,
        target=config.target,
        limit=3,
    )
    by_fact_type: dict[str, int] = {}
    by_answer_type: dict[str, int] = {}
    for document in documents:
        by_fact_type[document.source_fact_type] = by_fact_type.get(document.source_fact_type, 0) + 1
        by_answer_type[document.answer_candidate_type] = (
            by_answer_type.get(document.answer_candidate_type, 0) + 1
        )
    summary = {
            "total": len(documents),
            "by_fact_type": by_fact_type,
            "by_answer_type": by_answer_type,
            "latest_versions": [
                {
                    "version_id": version.version_id,
                    "generation_version": version.generation_version,
                    "field_coverage": version.field_coverage,
                    "changed_fact_set": {
                        key: len(value) if isinstance(value, list) else value
                        for key, value in version.changed_fact_set.items()
                    },
                }
                for version in versions
            ],
            "sample": [
                {
                    "id": document.document_id,
                    "fact": f"{document.source_fact_type}:{document.source_fact_id}",
                    "title": document.title,
                    "answer_type": document.answer_candidate_type,
                    "key_phrases": document.key_phrases[:6],
                    "evidence_refs": document.evidence_refs[:3],
                }
                for document in documents[:8]
            ],
        }
    RUN_STATE["retrieval_documents"] = summary
    print("\n[retrieval-doc] current indexable documents")
    pprint(summary)
    return summary


def print_retrieval_document_quality_report(service: KnowledgeService, config: ReplayConfig) -> dict[str, Any]:
    repository = service.repository
    if repository is None:
        print("[retrieval-doc-quality] skipped: repository unavailable")
        return {}
    documents = repository.list_retrieval_documents(config.adapter, target=config.target)
    report = build_retrieval_document_quality_report(
        documents,
        expected_generation_version=RETRIEVAL_DOCUMENT_VERSION,
    )
    RUN_STATE["retrieval_document_quality"] = report
    print("\n[retrieval-doc-quality]")
    pprint(report)
    return report


def persist_retrieval_quality_eval(
    service: KnowledgeService,
    config: ReplayConfig,
    *,
    cases: list[dict[str, Any]],
    replay_data: dict[str, Any],
) -> dict[str, Any]:
    if not config.quality_eval_enabled:
        print("[quality-eval] disabled")
        return {}
    repository = service.repository
    if repository is None:
        print("[quality-eval] skipped: repository unavailable")
        return {}

    snapshots = repository.list_retrieval_trace_snapshots(
        adapter_name=config.adapter,
        target=config.target,
        limit=config.quality_snapshot_limit,
    )
    labels = _build_quality_labels(cases, snapshots=snapshots, replay_data=replay_data)
    for label in labels:
        repository.save_retrieval_label(label)

    run = RetrievalEvalRun(
        strategy_name=config.quality_eval_strategy_name,
        strategy_version=config.quality_eval_strategy_version,
        config={
            "adapter": config.adapter,
            "target": config.target,
            "k_values": list(config.quality_eval_k_values),
            "case_ids": [case.get("case_id") for case in cases],
            "replay_metrics": replay_data.get("metrics") or {},
        },
    )
    repository.save_retrieval_eval_run(run)
    metrics = build_preselect_eval_metrics(
        run_id=run.run_id,
        snapshots=snapshots,
        labels=labels,
        k_values=config.quality_eval_k_values,
    )
    upserted = repository.upsert_retrieval_eval_metrics(metrics)
    aggregate = aggregate_eval_metrics(metrics)
    aggregate.update(
        {
            "labels": len(labels),
            "snapshots_available": len(snapshots),
            "metrics_upserted": upserted,
        }
    )
    repository.finish_retrieval_eval_run(
        run.run_id,
        status="completed" if not replay_data.get("failed") else "completed_with_replay_failures",
        aggregate_metrics=aggregate,
    )
    summary = {
        "run_id": run.run_id,
        "strategy": f"{run.strategy_name}:{run.strategy_version}",
        "labels": len(labels),
        "snapshots": len(snapshots),
        "metrics": len(metrics),
        "upserted": upserted,
        "aggregate": aggregate,
        "metric_sample": [metric.model_dump(mode="json") for metric in metrics[:5]],
    }
    print("\n[quality-eval] saved retrieval quality evaluation")
    pprint(summary)
    return summary


def _build_quality_labels(
    cases: list[dict[str, Any]],
    *,
    snapshots: list[Any],
    replay_data: dict[str, Any],
) -> list[RetrievalLabel]:
    snapshot_by_query_hash = {
        snapshot.query_hash or retrieval_query_hash(snapshot.query): snapshot
        for snapshot in snapshots
    }
    result_by_case_id = {
        str(item.get("case_id") or ""): item
        for item in replay_data.get("results") or []
    }
    labels: list[RetrievalLabel] = []
    for case in cases:
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or "")
        snapshot = snapshot_by_query_hash.get(retrieval_query_hash(query))
        replay_item = result_by_case_id.get(case_id) or {}
        labels.append(
            RetrievalLabel(
                label_id=f"kg_rt_label:real_replay:{case_id}",
                snapshot_id=snapshot.snapshot_id if snapshot else None,
                case_id=case_id,
                query=query,
                expected_candidates=_expected_candidates_from_replay_case(case, replay_item, snapshot),
                expected_answers=_expected_answers_from_replay_case(case, replay_item, snapshot),
                expected_evidence_refs=case.get("expected_evidence_refs") or [],
                coverage_requirements={
                    "expected_node_names": case.get("expected_node_names") or [],
                    "expected_relation_types": case.get("expected_relation_types") or [],
                    "min_hits": case.get("min_hits") or 0,
                    "min_evidence_refs": case.get("min_evidence_refs") or 0,
                    "min_matched_nodes": case.get("min_matched_nodes") or 0,
                    "min_matched_edges": case.get("min_matched_edges") or 0,
                    "forbidden_node_names": case.get("forbidden_node_names") or [],
                    "forbidden_topics": case.get("forbidden_topics") or [],
                },
                failure_stage=None if replay_item.get("passed") else "replay",
                notes="Generated from real replay baseline; adjust with human labels when reviewing misses.",
                created_by=SCRIPT_CREATED_BY,
            )
        )
    return labels


def _expected_candidates_from_replay_case(
    case: dict[str, Any],
    replay_item: dict[str, Any],
    snapshot: Any | None,
) -> list[dict[str, Any]]:
    expected_names = [str(name) for name in case.get("expected_node_names") or [] if str(name)]
    candidates = _snapshot_candidates_matching_names(snapshot, expected_names)
    candidates.extend({"title": name, "role": "expected_node"} for name in expected_names)
    for title in replay_item.get("actual_hit_titles") or []:
        if any(expected in title for expected in expected_names):
            candidates.append({"id": title, "title": title, "role": "observed_expected_hit"})
    return _dedupe_candidate_dicts(candidates)


def _expected_answers_from_replay_case(
    case: dict[str, Any],
    replay_item: dict[str, Any],
    snapshot: Any | None,
) -> list[dict[str, Any]]:
    expected_names = [str(name) for name in case.get("expected_node_names") or [] if str(name)]
    answers = [
        {**candidate, "role": "answer"}
        for candidate in _snapshot_candidates_matching_names(snapshot, expected_names)
        if str(candidate.get("role") or "").lower() in {"answer", "expected_node", "observed_expected_hit", ""}
    ]
    for title in replay_item.get("actual_hit_titles") or []:
        if any(expected in title for expected in expected_names):
            answers.append({"id": title, "title": title, "role": "answer"})
    return _dedupe_candidate_dicts(answers)


def _snapshot_candidates_matching_names(snapshot: Any | None, expected_names: list[str]) -> list[dict[str, Any]]:
    if snapshot is None or not expected_names:
        return []
    candidates = _candidate_dicts_from_value(
        {
            "ranking": snapshot.ranking_snapshot,
            "package": snapshot.package_snapshot,
            "judge": snapshot.judge_snapshot,
            "context": snapshot.context_snapshot,
            "recall": snapshot.recall_snapshot,
        }
    )
    matched: list[dict[str, Any]] = []
    for candidate in candidates:
        text = "\n".join(str(candidate.get(key) or "") for key in ("id", "candidate_id", "hit_id", "title"))
        if any(name and name in text for name in expected_names):
            candidate_id = candidate.get("candidate_id") or candidate.get("id") or candidate.get("hit_id")
            matched.append(
                {
                    "id": candidate_id or candidate.get("title") or text[:120],
                    "title": candidate.get("title") or str(candidate_id or ""),
                    "role": candidate.get("role") or candidate.get("type") or "expected_node",
                }
            )
    return _dedupe_candidate_dicts(matched)


def _candidate_dicts_from_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        result: list[dict[str, Any]] = []
        if any(key in value for key in ("candidate_id", "id", "hit_id", "title")):
            result.append(value)
        for item in value.values():
            result.extend(_candidate_dicts_from_value(item))
        return result
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(_candidate_dicts_from_value(item))
        return result
    return []


def _dedupe_candidate_dicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.get("id") or candidate.get("candidate_id") or candidate.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


async def inspect_sample_contexts(service: KnowledgeService, config: ReplayConfig) -> None:
    print("\n[samples] auto routed research context")
    for query in sample_queries():
        context = await service.build_research_context_for(
            KnowledgeResearchContextCommand(
                adapter_name=config.adapter,
                target=config.target,
                query=query,
                retrieval_mode="auto",
                graph_depth=3,
                graph_limit=30,
                wiki_limit=10,
                evidence_limit=30,
                max_chars=config.max_chars,
            )
        )
        data = context.to_dict()
        print(f"\n# {query}")
        pprint(
            {
                "hits": len(data["hits"]),
                "matched_nodes": len(data["matched_nodes"]),
                "matched_edges": len(data["matched_edges"]),
                "evidence_refs": len(data["evidence_refs"]),
                "channels_used": data["retrieval_channels_used"],
                "milvus_enabled": data["milvus_enabled"],
                "agentic_enabled": data["agentic_enabled"],
                "query_anchor": data.get("query_anchor") or {},
                "routing_decision": data.get("routing_decision") or {},
                "retrieval_metrics": _compact_retrieval_metrics_from_trace(data.get("retrieval_trace") or {}),
            }
        )
        print("--- context_text preview ---")
        print(data["context_text"][:1500])


def _compact_retrieval_metrics_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (trace.get("retrieval_metrics") or {}).items()
        if key in {"raw_hits", "accepted_hits", "rejected_hits", "context_precision", "anchor_coverage"}
    }


def db_rows(target: str, sql: str, params: dict | None = None) -> list[dict]:
    with get_session(target) as session:
        rows = session.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in rows]


def _list_sample(values: Any, limit: int = 5) -> dict[str, Any]:
    if not isinstance(values, list):
        return {"count": 0, "sample": []}
    return {"count": len(values), "sample": values[:limit]}


def _compact_failure(failure: dict[str, Any]) -> dict[str, Any]:
    details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
    compact = {
        "source_type": failure.get("source_type"),
        "source_id": failure.get("source_id"),
        "reason": failure.get("reason"),
    }
    for key in (
        "source_ref",
        "target_ref",
        "source_type",
        "target_type",
        "source_resolved",
        "target_resolved",
        "source_node_id",
        "target_node_id",
        "evidence_refs",
    ):
        if key in details:
            compact[key] = details[key]
    issues = details.get("issues")
    if isinstance(issues, list) and issues:
        compact["issues"] = issues[:2]
    return compact


def _failure_endpoint_summary(failures: list[dict[str, Any]], *, limit: int = 80) -> dict[str, Any]:
    endpoints: dict[str, dict[str, Any]] = {}
    for failure in failures:
        details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
        for side in ("source", "target"):
            ref = details.get(f"{side}_ref")
            if not ref:
                continue
            key = str(ref)
            item = endpoints.setdefault(
                key,
                {
                    "ref": key,
                    "side": set(),
                    "node_id": details.get(f"{side}_node_id"),
                    "node_type": details.get(f"{side}_type"),
                    "resolved": details.get(f"{side}_resolved"),
                    "failure_count": 0,
                    "reasons": set(),
                    "source_ids": set(),
                },
            )
            item["side"].add(side)
            item["failure_count"] += 1
            item["reasons"].add(str(failure.get("reason") or "unknown"))
            source_id = failure.get("source_id")
            if source_id:
                item["source_ids"].add(str(source_id))

    values: list[dict[str, Any]] = []
    for item in endpoints.values():
        values.append(
            {
                "ref": item["ref"],
                "side": sorted(item["side"]),
                "node_id": item["node_id"],
                "node_type": item["node_type"],
                "resolved": item["resolved"],
                "failure_count": item["failure_count"],
                "reasons": sorted(item["reasons"]),
                "source_ids": sorted(item["source_ids"])[:8],
            }
        )
    values.sort(key=lambda item: (-item["failure_count"], item["ref"]))
    return {"count": len(values), "items": values[:limit]}


def _failure_reason_counts(failures: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failure in failures:
        reason = str(failure.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def compact_compile_result(result: Any, *, failure_limit: int = 12) -> dict[str, Any]:
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    failures = data.get("failures") if isinstance(data.get("failures"), list) else []
    index_refresh = data.get("index_refresh") if isinstance(data.get("index_refresh"), dict) else {}
    compact_index_refresh = {
        key: value
        for key, value in index_refresh.items()
        if key not in {"node_ids", "edge_ids", "evidence_ids"}
    }
    for key in ("node_ids", "edge_ids", "evidence_ids"):
        if key in index_refresh:
            compact_index_refresh[key] = _list_sample(index_refresh.get(key))
    return {
        "adapter_name": data.get("adapter_name"),
        "run_id": data.get("run_id"),
        "nodes": data.get("nodes"),
        "edges": data.get("edges"),
        "evidence": data.get("evidence"),
        "failed_records": data.get("failed_records"),
        "ids": {
            "node_ids": _list_sample(data.get("node_ids")),
            "edge_ids": _list_sample(data.get("edge_ids")),
            "evidence_ids": _list_sample(data.get("evidence_ids")),
        },
        "index_refresh": compact_index_refresh,
        "failure_reason_counts": _failure_reason_counts(failures),
        "failure_endpoints": _failure_endpoint_summary(failures),
        "failure_sample": [_compact_failure(failure) for failure in failures[:failure_limit]],
        "warnings_count": len(data.get("warnings") or []),
        "dry_run": data.get("dry_run"),
    }


def print_compile_result_summary(result: Any, *, label: str) -> dict[str, Any]:
    summary = compact_compile_result(result)
    print(f"\n[{label}] compile result summary")
    pprint(summary)
    RUN_STATE.setdefault("compile_results", []).append({"label": label, "summary": summary})
    return summary


def append_trace_file_to_full_log(config: ReplayConfig) -> None:
    if not config.full_log_enabled:
        return
    full_path = Path(full_log_path(config))
    if not full_path.exists():
        return
    if _FULL_LOG_HANDLE is not None:
        _FULL_LOG_HANDLE.flush()
    with full_path.open("a", encoding="utf-8") as handle:
        llm_trace_path = Path(llm_full_trace_path(config))
        if llm_trace_path.exists() and full_path != llm_trace_path:
            handle.write("\n\n===== generated_real_replay_llm_full_trace.log =====\n")
            handle.write(llm_trace_path.read_text(encoding="utf-8", errors="replace"))
            handle.write("\n===== end generated_real_replay_llm_full_trace.log =====\n")

        if config.trace_retrieval_llm:
            trace_path = Path(retrieval_trace_path(config))
            if trace_path.exists() and full_path != trace_path:
                handle.write("\n\n===== generated_retrieval_llm_trace.log =====\n")
                handle.write(trace_path.read_text(encoding="utf-8", errors="replace"))
                handle.write("\n===== end generated_retrieval_llm_trace.log =====\n")


def write_ai_diagnostic_report(config: ReplayConfig | None = None) -> None:
    if config is None:
        config = RUN_STATE.get("config")
    if config is None:
        return
    path = Path(ai_diagnostic_path(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    report = _build_ai_diagnostic_report(config)
    path.write_text(report, encoding="utf-8")
    print(f"[diagnostic] AI diagnostic report written: {path}", flush=True)


def write_exception_traceback_to_full_log(error: BaseException | None) -> None:
    if error is None:
        return
    print("\n===== python exception traceback =====", file=sys.stderr, flush=True)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    print("===== end python exception traceback =====", file=sys.stderr, flush=True)


def _build_ai_diagnostic_report(config: ReplayConfig) -> str:
    replay_data = RUN_STATE.get("replay_data") or {}
    failed_cases = [
        {
            "case_id": item.get("case_id"),
            "query": item.get("query"),
            "metric_failures": item.get("metric_failures"),
            "missing_node_names": item.get("missing_node_names"),
            "missing_relation_types": item.get("missing_relation_types"),
            "channels_used": item.get("channels_used"),
            "candidate_judgement_summary": item.get("candidate_judgement_summary"),
        }
        for item in replay_data.get("results", [])
        if not item.get("passed")
    ][:8]
    lines = [
        "# KG Real Replay AI Diagnostic",
        "",
        f"- status: {RUN_STATE.get('status', 'unknown')}",
        f"- started_at: {RUN_STATE.get('started_at')}",
        f"- finished_at: {RUN_STATE.get('finished_at')}",
        f"- script: {SCRIPT_CREATED_BY}",
        f"- full_log: {full_log_path(config)}",
        f"- llm_full_trace: {llm_full_trace_path(config)}",
        f"- retrieval_llm_trace: {retrieval_trace_path(config)}",
        f"- case_file: {RUN_STATE.get('case_file')}",
        "",
        "## Config",
        "",
        _json_block(
            {
                "target": config.target,
                "adapter": config.adapter,
                "write_only": config.write_only,
                "reset_before_replay": config.reset_before_replay,
                "projection_news_limit": config.projection_news_limit,
                "projection_order_by_created_at": config.projection_order_by_created_at,
                "dynamic_case_limit": config.dynamic_case_limit,
                "include_seed_baseline": config.include_seed_baseline,
                "fail_on_compile_failure": config.fail_on_compile_failure,
                "concurrency": config.concurrency,
                "trace_retrieval_llm": config.trace_retrieval_llm,
                "profile_retrieval": config.profile_retrieval,
            }
        ),
        "",
        "## Step Timeline",
        "",
        _json_block(RUN_STATE.get("steps") or []),
        "",
        "## Data Summary",
        "",
        _json_block(
            {
                "real_news_records": RUN_STATE.get("real_news_records"),
                "case_count": RUN_STATE.get("case_count"),
                "database_counts": RUN_STATE.get("database_counts"),
                "compile_results": RUN_STATE.get("compile_results") or [],
                "retrieval_documents": _compact_retrieval_doc_summary(
                    RUN_STATE.get("retrieval_documents") or {}
                ),
                "retrieval_document_quality": RUN_STATE.get("retrieval_document_quality"),
            }
        ),
        "",
        "## Replay Summary",
        "",
        _json_block(
            {
                "summary": {
                    key: replay_data.get(key)
                    for key in ("total", "passed", "failed", "metrics")
                    if key in replay_data
                },
                "failed_cases": failed_cases,
            }
        ),
        "",
        "## Quality Eval",
        "",
        _json_block(RUN_STATE.get("quality_eval_summary") or {}),
    ]
    if RUN_STATE.get("error"):
        lines.extend(["", "## Error", "", _json_block(RUN_STATE["error"])])
    lines.extend(
        [
            "",
            "## Debug Pointers",
            "",
            "- 优先看 full_log 中 `[llm_call] START/DONE/FAILED` 判断是否卡在真实 LLM 请求。",
            "- 若 replay failed，优先看 `generated_retrieval_llm_trace.log` 的 `agentic_case_summary`、`ranker_preselect`、`candidate_judge`。",
            "- 若写入慢，优先看 full_log 中 `financial_news_extraction` 的 source_id 和耗时。",
        ]
    )
    return "\n".join(lines) + "\n"


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n```"


def _compact_retrieval_doc_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    return {
        "total": summary.get("total"),
        "by_fact_type": summary.get("by_fact_type"),
        "by_answer_type": summary.get("by_answer_type"),
        "latest_versions": summary.get("latest_versions"),
    }


def reset_kg_generated_data(config: ReplayConfig) -> dict[str, int | str]:
    """Delete generated KG data for one adapter/target before replaying latest rules."""

    target = config.target
    adapter = config.adapter
    statements = [
        ("kg_retrieval_documents", "delete from kg_retrieval_documents where adapter_name = :adapter and target = :target"),
        (
            "kg_retrieval_document_versions",
            "delete from kg_retrieval_document_versions where adapter_name = :adapter and target = :target",
        ),
        (
            "kg_retrieval_trace_snapshots",
            "delete from kg_retrieval_trace_snapshots where adapter_name = :adapter and target = :target",
        ),
        (
            "kg_retrieval_eval_metrics",
            """
            delete from kg_retrieval_eval_metrics
            where run_id in (
                select run_id from kg_retrieval_eval_runs
                where strategy_name = :quality_strategy
            )
            """,
        ),
        (
            "kg_retrieval_eval_runs",
            "delete from kg_retrieval_eval_runs where strategy_name = :quality_strategy",
        ),
        (
            "kg_retrieval_labels",
            "delete from kg_retrieval_labels where created_by = :created_by",
        ),
        (
            "kg_review_items",
            """
            delete from kg_review_items
            where object_id in (select edge_id from kg_edges where adapter_name = :adapter)
               or object_id in (select evidence_id from kg_evidence where adapter_name = :adapter)
               or object_id in (select node_id from kg_nodes where adapter_name = :adapter)
            """,
        ),
        (
            "kg_edge_evidence",
            """
            delete from kg_edge_evidence
            where edge_id in (select edge_id from kg_edges where adapter_name = :adapter)
               or evidence_id in (select evidence_id from kg_evidence where adapter_name = :adapter)
            """,
        ),
        ("kg_graph_adjacency", "delete from kg_graph_adjacency where adapter_name = :adapter"),
        ("kg_evidence_chunks", "delete from kg_evidence_chunks where adapter_name = :adapter"),
        ("kg_wiki_pages", "delete from kg_wiki_pages where adapter_name = :adapter"),
        ("kg_edges", "delete from kg_edges where adapter_name = :adapter"),
        ("kg_evidence", "delete from kg_evidence where adapter_name = :adapter"),
        ("kg_nodes", "delete from kg_nodes where adapter_name = :adapter"),
        ("kg_compilation_runs", "delete from kg_compilation_runs where adapter_name = :adapter"),
        ("kg_versions", "delete from kg_versions where adapter_name = :adapter"),
    ]
    deleted: dict[str, int | str] = {}
    with get_session(target) as session:
        for table_name, sql in statements:
            if not _table_exists(session, table_name):
                deleted[table_name] = "missing"
                continue
            result = session.execute(
                text(sql),
                {
                    "adapter": adapter,
                    "target": target,
                    "quality_strategy": config.quality_eval_strategy_name,
                    "created_by": SCRIPT_CREATED_BY,
                },
            )
            deleted[table_name] = result.rowcount if result.rowcount is not None else -1

    if settings.MILVUS_ENABLED:
        milvus_store = MilvusHybridStore()
        try:
            milvus_store.delete_scope(adapter_name=adapter, target=target)
        finally:
            milvus_store.close()
        deleted["milvus_scope"] = "deleted"
    else:
        deleted["milvus_scope"] = "skipped"
    pprint(deleted)
    return deleted


def _table_exists(session: Any, table_name: str) -> bool:
    return bool(session.execute(text("select to_regclass(:table_name)"), {"table_name": table_name}).scalar())


def print_database_checks(config: ReplayConfig) -> None:
    target = config.target
    adapter = config.adapter
    sql = """
    select 'kg_nodes' table_name, count(*) count from kg_nodes where adapter_name = :adapter
    union all
    select 'kg_edges' table_name, count(*) count from kg_edges where adapter_name = :adapter
    union all
    select 'kg_evidence' table_name, count(*) count from kg_evidence where adapter_name = :adapter
    union all
    select 'kg_wiki_pages' table_name, count(*) count from kg_wiki_pages where adapter_name = :adapter
    union all
    select 'kg_evidence_chunks' table_name, count(*) count from kg_evidence_chunks where adapter_name = :adapter
    union all
    select 'kg_retrieval_documents' table_name, count(*) count from kg_retrieval_documents where adapter_name = :adapter
    union all
    select 'kg_retrieval_trace_snapshots' table_name, count(*) count from kg_retrieval_trace_snapshots where adapter_name = :adapter
    """
    print("\n[counts]")
    counts = db_rows(target, sql, {"adapter": adapter})
    RUN_STATE["database_counts"] = counts
    pprint(counts)

    if config.include_seed_baseline:
        baseline_evidence = db_rows(
            target,
            """
            select source_type, source_id, left(coalesce(content, ''), 220) as content_preview
            from kg_evidence
            where adapter_name = :adapter and source_id like 'notebook_baseline:%'
            order by source_id
            """,
            {"adapter": adapter},
        )
        print("\n[baseline evidence]")
        pprint(baseline_evidence)
        expected = len(baseline_records())
        if len(baseline_evidence) < expected:
            raise AssertionError(
                f"baseline evidence count too small: expected>={expected} actual={len(baseline_evidence)}"
            )
    else:
        print("\n[baseline evidence] skipped; KG_REPLAY_INCLUDE_SEED=0")

    ft_news_evidence = db_rows(
        target,
        """
        select source_type, source_id, left(coalesce(content, ''), 220) as content_preview
        from kg_evidence
        where adapter_name = :adapter and source_id like 'ft_news:%'
        order by source_id desc
        limit 10
        """,
        {"adapter": adapter},
    )
    print("\n[ft_news evidence]")
    pprint(ft_news_evidence)


async def main() -> None:
    config = ReplayConfig(
        target="prod",
        adapter="financial",
        dry_run=False,
        reset_before_replay=env_bool("KG_REPLAY_RESET", True),
        concurrency=2,
        stock_limit=50,
        news_limit=env_int("KG_REPLAY_NEWS_LIMIT", 5),
        projection_news_limit=env_int("KG_REPLAY_PROJECTION_NEWS_LIMIT", 100),
        projection_order_by_created_at=env_bool("KG_REPLAY_PROJECTION_CREATED_AT", True),
        projection_codes=(),
        dynamic_case_limit=env_int("KG_REPLAY_DYNAMIC_CASE_LIMIT", 12),
        codes=("300750", "603305"),
        max_chars=8000,
        strict_agentic=False,
        trace_retrieval_llm=True,
        trace_retrieval_llm_max_chars=4000,
        trace_retrieval_llm_max_items=8,
        trace_retrieval_llm_snippet_chars=260,
        profile_retrieval=True,
        profile_retrieval_verbose=False,
        profile_retrieval_min_ms=1000,
        include_seed_baseline=env_bool("KG_REPLAY_INCLUDE_SEED", False),
        full_log_enabled=env_bool("KG_REPLAY_FULL_LOG", True),
        full_log_file=os.getenv("KG_REPLAY_FULL_LOG_FILE", ""),
        llm_full_trace_file=os.getenv("KG_REPLAY_LLM_FULL_TRACE_FILE", ""),
        ai_diagnostic_file=os.getenv("KG_REPLAY_AI_DIAGNOSTIC_FILE", ""),
        quality_eval_enabled=True,
        quality_eval_strategy_name="real_replay_quality_baseline",
        quality_eval_strategy_version="v1",
        quality_eval_k_values=(8, 12, 15),
        quality_snapshot_limit=200,
        write_only=env_bool("KG_REPLAY_WRITE_ONLY", False),
        fail_on_compile_failure=env_bool("KG_REPLAY_FAIL_ON_COMPILE_FAILURE", False),
    )
    RUN_STATE["config"] = config

    configure_full_run_log(config)
    run_sync_step("configure logging", configure_logging)
    run_sync_step("print run mode", lambda: print_run_mode(config))
    run_sync_step("configure retrieval profile", lambda: configure_retrieval_profile(config))
    run_sync_step("configure retrieval LLM trace", lambda: configure_retrieval_llm_trace(config))
    run_sync_step("print LLM routing config", print_llm_config)

    if settings.MILVUS_ENABLED is not True:
        raise RuntimeError("Milvus must be enabled")
    def check_milvus_ready() -> None:
        milvus_store = MilvusHybridStore()
        try:
            milvus_store.ensure_ready()
        finally:
            milvus_store.close()

    run_sync_step("check Milvus", check_milvus_ready)

    service: KnowledgeService = cast(KnowledgeService, create_knowledge_service(target=config.target))
    health = await run_step("check KG service health", service.health)
    pprint(health.to_dict())
    assert_kg_health_ok(health)

    if config.reset_before_replay:
        run_sync_step(
            "Step 0 reset generated KG data before write",
            lambda: reset_kg_generated_data(config),
        )
    else:
        print("[mode] KG_REPLAY_RESET=0; keeping existing KG data before write.")
    run_sync_step(
        "Step 0.5 ensure system baseline normalization rules",
        lambda: ensure_financial_normalization_rules(config.target),
    )

    # Step 1: 读取真实 ft_news，投影成标准 KG source records，再写入知识图谱。
    real_news_records = await run_step("Step 1 compile real ft_news projection records", lambda: compile_real_ft_news_projection(service, config))
    RUN_STATE["real_news_records"] = len(real_news_records)

    real_news_cases = real_news_cases_from(real_news_records, limit=config.dynamic_case_limit)
    cases = [
        *(auto_bad_cases() if config.include_seed_baseline else []),
        *real_news_cases,
    ]
    run_sync_step("write bad case file", lambda: write_case_file(config, cases))

    # Step 1.5: 受控 golden regression。默认关闭，避免污染真实 ft_news 回放。
    if config.include_seed_baseline:
        await run_step("Step 1.5 compile controlled baseline records", lambda: compile_seed(service, config))
    else:
        print("[mode] KG_REPLAY_INCLUDE_SEED=0; Step 1.5 controlled baseline records skipped.")

    run_sync_step(
        "Step 1.6 inspect retrieval documents",
        lambda: inspect_retrieval_documents(service, config),
    )
    run_sync_step(
        "Step 1.7 retrieval document quality report",
        lambda: print_retrieval_document_quality_report(service, config),
    )
    run_sync_step(
        "Step 1.8 check database persistence",
        lambda: print_database_checks(config),
    )

    if config.write_only:
        print("\n[mode] write-only enabled; replay and quality-eval steps skipped.")
        print("[mode] set KG_REPLAY_WRITE_ONLY=0 to run Step 4+ replay.")
        RUN_STATE["status"] = "success"
        return

    # # Step 2: 可选，读取真实 ft_* 数据做完整增量刷新；不需要时直接注释下一行。
    # await run_step("Step 2 refresh real ft_* incremental sources", lambda: incremental_refresh(service, config))

    # # Step 3: 重建 Wiki、graph/evidence 索引和 Milvus hybrid 索引。
    # await run_step("Step 3 rebuild wiki and indexes", lambda: rebuild(service, config))

    # Step 4: auto 路由质量门禁，失败会直接抛错。
    if not cases:
        raise RuntimeError(
            "No replay cases generated. Increase KG_REPLAY_DYNAMIC_CASE_LIMIT or enable "
            "KG_REPLAY_INCLUDE_SEED=1 for controlled baseline cases."
        )
    replay_data = await run_step(
        "Step 4 replay auto-routed quality baseline",
        lambda: replay(service, config, mode="auto", cases=cases, fail_on_error=False),
    )
    RUN_STATE["replay_data"] = replay_data
    quality_eval_summary = run_sync_step(
        "Step 4.5 persist retrieval quality evaluation",
        lambda: persist_retrieval_quality_eval(
            service,
            config,
            cases=cases,
            replay_data=replay_data,
        ),
    )
    RUN_STATE["quality_eval_summary"] = quality_eval_summary
    if replay_data.get("failed"):
        raise AssertionError(replay_data)
    RUN_STATE["status"] = "success"

    # # Step 5: Agentic A-RAG 观测；不需要时直接注释整个 await replay(...) 块。
    # await run_step(
    #     "Step 5 replay Agentic A-RAG observation",
    #     lambda: replay(
    #         service,
    #         config,
    #         mode="agentic_arag",
    #         cases=agentic_cases_from(cases),
    #         fail_on_error=config.strict_agentic,
    #     ),
    # )

    # # Step 6: 抽样查看研究上下文；不需要时直接注释下一行。
    # await run_step("Step 6 inspect sample research contexts", lambda: inspect_sample_contexts(service, config))

    # print("\nOK")


def _finalize_outputs(error: BaseException | None = None) -> None:
    config = RUN_STATE.get("config")
    RUN_STATE["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if error is not None:
        RUN_STATE["status"] = "failed"
        RUN_STATE["error"] = {
            "type": error.__class__.__name__,
            "message": str(error),
            "traceback": traceback.format_exception(type(error), error, error.__traceback__)[-20:],
        }
    elif RUN_STATE.get("status") == "running":
        RUN_STATE["status"] = "success"

    try:
        write_exception_traceback_to_full_log(error)
        if isinstance(config, ReplayConfig):
            append_trace_file_to_full_log(config)
            write_ai_diagnostic_report(config)
    finally:
        close_full_run_log()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:
        _finalize_outputs(exc)
        raise
    else:
        _finalize_outputs()
