"""全局配置 — 所有配置集中管理，支持环境变量覆盖

优先级：环境变量 > .env 文件 > 此文件默认值
"""

import json
import os
from pathlib import Path

# 自动加载 .env 文件（项目根目录）
_env_file = Path(__file__).resolve().parents[3] / ".env"
_project_root = _env_file.parent
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:  # 不覆盖已设置的环境变量
            os.environ[key] = value


def _resolve_local_path_setting(value: str) -> str:
    """Resolve local file settings against the project root, not process cwd."""

    raw = str(value or "").strip()
    if not raw:
        return raw
    if "://" in raw or raw.startswith("unix:"):
        return raw
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    return str((_project_root / path).resolve())


# ==================== 数据库 ====================

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "jettask"),
    "user": os.getenv("DB_USER", "jettask"),
    "password": os.getenv("DB_PASSWORD", "123456"),
}

# PostgreSQL 连接 URL（供 jettask / SQLAlchemy 使用，独立数据库）
JETTASK_DB_NAME = os.getenv("JETTASK_DB_NAME", "jettask_queue")
PG_URL = os.getenv(
    "PG_URL",
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{JETTASK_DB_NAME}"
)

# ==================== Redis ====================

REDIS_URL = os.getenv("REDIS_URL", "redis://119.23.227.187:6379/0")

# ==================== 服务 ====================

SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8900"))

# 本服务的外部可访问地址（供客户端内部互调用）
SERVICE_BASE_URL = os.getenv("SERVICE_BASE_URL", "http://119.23.227.187:8900")

# ==================== OCR ====================

OCR_URL = os.getenv("OCR_URL", "http://119.23.227.187:8675/glmocr/parse")

# ==================== Embedding 服务 ====================

# Qwen3-Embedding-4B OpenAI-compatible HTTP 服务（vLLM，监听 0.0.0.0:8901）
# - 接口: /v1/embeddings
# - 同内网优先走 10.168.1.113:8901
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://10.168.1.113:8901")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "2560"))  # Qwen3-Embedding-4B 默认输出维度
EMBEDDING_REQUEST_DIMENSIONS = os.getenv("EMBEDDING_REQUEST_DIMENSIONS", "false").lower() == "true"
EMBEDDING_MIN_DIM = int(os.getenv("EMBEDDING_MIN_DIM", "32"))
EMBEDDING_MAX_DIM = int(os.getenv("EMBEDDING_MAX_DIM", "2560"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
EMBEDDING_TIMEOUT = float(os.getenv("EMBEDDING_TIMEOUT", "60"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "/models/Qwen3-Embedding-4B")
EMBEDDING_FILE_CACHE_ENABLED = (
    os.getenv("EMBEDDING_FILE_CACHE_ENABLED", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
_EMBEDDING_FILE_CACHE_DIR_RAW = os.getenv(
    "EMBEDDING_FILE_CACHE_DIR",
    str(_env_file.parent / "data" / "embedding_cache"),
)
EMBEDDING_FILE_CACHE_DIR = str(
    Path(_EMBEDDING_FILE_CACHE_DIR_RAW)
    if Path(_EMBEDDING_FILE_CACHE_DIR_RAW).is_absolute()
    else _env_file.parent / _EMBEDDING_FILE_CACHE_DIR_RAW
)

# ==================== Reranker 服务 ====================

# Listwise reranker 服务，用于 KG 检索候选语义重排。
# 该服务是检索质量链路的一部分；调用失败时由上层显式失败，不做静默降级。
RERANKER_URL = os.getenv("RERANKER_URL", "http://119.23.227.187:8860")
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "15"))
RERANKER_MAX_DOCUMENTS = int(os.getenv("RERANKER_MAX_DOCUMENTS", "100"))
RERANKER_DEFAULT_TOP_N = int(os.getenv("RERANKER_DEFAULT_TOP_N", "0"))

# ==================== Milvus Hybrid Retrieval ====================

# Milvus hybrid retrieval is a required part of the KG research context path.
# Do not gate it behind an environment flag: if pymilvus or the configured
# Milvus endpoint is unavailable, the service should fail loudly.
MILVUS_ENABLED = True
MILVUS_URI = _resolve_local_path_setting(os.getenv("MILVUS_URI", "./data/milvus/kg_vectors.db"))
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "kg_evidence_chunk_vectors")
MILVUS_CHUNK_COLLECTION = os.getenv("MILVUS_CHUNK_COLLECTION", "kg_evidence_chunks")
MILVUS_ENTITY_COLLECTION = os.getenv("MILVUS_ENTITY_COLLECTION", "kg_entity_cards")
MILVUS_RELATION_COLLECTION = os.getenv("MILVUS_RELATION_COLLECTION", "kg_relation_cards")
MILVUS_COMMUNITY_COLLECTION = os.getenv("MILVUS_COMMUNITY_COLLECTION", "kg_community_reports")
MILVUS_METRIC_TYPE = os.getenv("MILVUS_METRIC_TYPE", "COSINE")
MILVUS_RRF_K = int(os.getenv("MILVUS_RRF_K", "60"))
MILVUS_BATCH_SIZE = int(os.getenv("MILVUS_BATCH_SIZE", "128"))
MILVUS_SEMANTIC_CHUNK_TOPK = int(os.getenv("MILVUS_SEMANTIC_CHUNK_TOPK", "0"))
MILVUS_SEMANTIC_ENTITY_TOPK = int(os.getenv("MILVUS_SEMANTIC_ENTITY_TOPK", "15"))
MILVUS_SEMANTIC_RELATION_TOPK = int(os.getenv("MILVUS_SEMANTIC_RELATION_TOPK", "20"))
MILVUS_SEMANTIC_COMMUNITY_TOPK = int(os.getenv("MILVUS_SEMANTIC_COMMUNITY_TOPK", "8"))

# ==================== Claude / Skill ====================

SKILL_DIR = os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/.claude/skills/fund-trade")
SKILLS_DIR = os.getenv("SKILLS_DIR", "")  # SkillRegistry 根目录，空则用 main.py 上级目录

# Claude LLM 代理层
CLAUDE_PROXY_BACKEND = os.getenv("CLAUDE_PROXY_BACKEND", "tmux_pool")
CLAUDE_PROXY_CLI_BIN = os.getenv("CLAUDE_PROXY_CLI_BIN", "claude")
CLAUDE_PROXY_MODEL = os.getenv("CLAUDE_PROXY_MODEL", "sonnet")
CLAUDE_PROXY_TIMEOUT = float(os.getenv("CLAUDE_PROXY_TIMEOUT", "180"))
CLAUDE_PROXY_TMUX_READY_TIMEOUT = int(os.getenv("CLAUDE_PROXY_TMUX_READY_TIMEOUT", "45"))
CLAUDE_PROXY_TMUX_POOL_SIZE = int(os.getenv("CLAUDE_PROXY_TMUX_POOL_SIZE", "2"))
CLAUDE_PROXY_TMUX_CLEAR_TIMEOUT = int(os.getenv("CLAUDE_PROXY_TMUX_CLEAR_TIMEOUT", "20"))
CLAUDE_PROXY_TMUX_MAX_REQUESTS_PER_SESSION = int(
    os.getenv("CLAUDE_PROXY_TMUX_MAX_REQUESTS_PER_SESSION", "200")
)
CLAUDE_PROXY_TMUX_MAX_SESSION_AGE_SECONDS = int(
    os.getenv("CLAUDE_PROXY_TMUX_MAX_SESSION_AGE_SECONDS", "7200")
)
CLAUDE_PROXY_MAX_CONCURRENCY = int(os.getenv("CLAUDE_PROXY_MAX_CONCURRENCY", "2"))
CLAUDE_PROXY_MIN_INTERVAL_SECONDS = float(os.getenv("CLAUDE_PROXY_MIN_INTERVAL_SECONDS", "0"))
CLAUDE_PROXY_RATE_LIMIT_COOLDOWN_SECONDS = float(os.getenv("CLAUDE_PROXY_RATE_LIMIT_COOLDOWN_SECONDS", "0"))
CLAUDE_PROXY_CACHE_TTL_SECONDS = int(os.getenv("CLAUDE_PROXY_CACHE_TTL_SECONDS", "300"))
CLAUDE_PROXY_CACHE_MAX_SIZE = int(os.getenv("CLAUDE_PROXY_CACHE_MAX_SIZE", "256"))
CLAUDE_PROXY_SANDBOX_MODE = os.getenv("CLAUDE_PROXY_SANDBOX_MODE", "auto")  # auto/light/hard/off
CLAUDE_PROXY_SANDBOX_ROOT = os.getenv("CLAUDE_PROXY_SANDBOX_ROOT", "/tmp/smart-fund-claude-proxy")
CLAUDE_PROXY_FILE_CONTEXT_THRESHOLD_CHARS = int(
    os.getenv("CLAUDE_PROXY_FILE_CONTEXT_THRESHOLD_CHARS", "8000")
)
CLAUDE_PROXY_CLAUDE_CONFIG_JSON = os.getenv("CLAUDE_PROXY_CLAUDE_CONFIG_JSON", "")
CLAUDE_PROXY_MODEL_ALIASES_JSON = os.getenv("CLAUDE_PROXY_MODEL_ALIASES_JSON", "")


def _load_proxy_claude_config() -> dict:
    if not CLAUDE_PROXY_CLAUDE_CONFIG_JSON.strip():
        return {}
    try:
        data = json.loads(CLAUDE_PROXY_CLAUDE_CONFIG_JSON)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_proxy_model_aliases() -> dict[str, str]:
    if not CLAUDE_PROXY_MODEL_ALIASES_JSON.strip():
        return {}
    try:
        data = json.loads(CLAUDE_PROXY_MODEL_ALIASES_JSON)
        if not isinstance(data, dict):
            return {}
        aliases: dict[str, str] = {}
        for key, value in data.items():
            if not key or not value:
                continue
            aliases[str(key)] = str(value)
        return aliases
    except Exception:
        return {}


CLAUDE_PROXY_CLAUDE_CONFIG = _load_proxy_claude_config()
CLAUDE_PROXY_MODEL_ALIASES = _load_proxy_model_aliases()
CLAUDE_PROXY_CHILD_ENV = (
    CLAUDE_PROXY_CLAUDE_CONFIG.get("env", {})
    if isinstance(CLAUDE_PROXY_CLAUDE_CONFIG.get("env"), dict)
    else {}
)
CLAUDE_PROXY_CHILD_SETTINGS = {
    k: v
    for k, v in CLAUDE_PROXY_CLAUDE_CONFIG.items()
    if k != "env"
}

# 通用 LLM Proxy 网关。业务方只传 model，网关负责把模型路由到 provider。
LLM_PROXY_DEFAULT_PROVIDER = os.getenv("LLM_PROXY_DEFAULT_PROVIDER", "claude_tmux")
LLM_PROXY_DEFAULT_MODEL = os.getenv("LLM_PROXY_DEFAULT_MODEL", CLAUDE_PROXY_MODEL)
LLM_PROXY_TIMEOUT = float(os.getenv("LLM_PROXY_TIMEOUT", str(CLAUDE_PROXY_TIMEOUT)))
LLM_PROXY_MAX_CONCURRENCY = int(
    os.getenv("LLM_PROXY_MAX_CONCURRENCY", str(CLAUDE_PROXY_MAX_CONCURRENCY))
)
LLM_PROXY_CACHE_TTL_SECONDS = int(
    os.getenv("LLM_PROXY_CACHE_TTL_SECONDS", str(CLAUDE_PROXY_CACHE_TTL_SECONDS))
)
LLM_PROXY_CACHE_MAX_SIZE = int(
    os.getenv("LLM_PROXY_CACHE_MAX_SIZE", str(CLAUDE_PROXY_CACHE_MAX_SIZE))
)
LLM_PROXY_FILE_CACHE_ENABLED = (
    os.getenv("LLM_PROXY_FILE_CACHE_ENABLED", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
_LLM_PROXY_FILE_CACHE_DIR_RAW = os.getenv(
    "LLM_PROXY_FILE_CACHE_DIR",
    str(_env_file.parent / "data" / "llm_proxy_cache"),
)
LLM_PROXY_FILE_CACHE_DIR = str(
    Path(_LLM_PROXY_FILE_CACHE_DIR_RAW)
    if Path(_LLM_PROXY_FILE_CACHE_DIR_RAW).is_absolute()
    else _env_file.parent / _LLM_PROXY_FILE_CACHE_DIR_RAW
)
LLM_PROXY_MODEL_ROUTES_JSON = os.getenv("LLM_PROXY_MODEL_ROUTES_JSON", "")
LLM_PROXY_MODEL_ALIASES_JSON = os.getenv("LLM_PROXY_MODEL_ALIASES_JSON", "")


def _load_json_dict(value: str) -> dict:
    if not value.strip():
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_gateway_model_routes() -> dict[str, list[str]]:
    routes: dict[str, list[str]] = {
        CLAUDE_PROXY_MODEL: ["claude_tmux"],
        "sonnet": ["claude_tmux"],
        "opus": ["claude_tmux"],
        "glm-5.1": ["claude_tmux", "glm_http"],
        "deepseek-v4-flash": ["deepseek"],
        "deepseek-v4-pro": ["deepseek"],
    }
    data = _load_json_dict(LLM_PROXY_MODEL_ROUTES_JSON)
    for key, value in data.items():
        if isinstance(value, str):
            routes[str(key)] = [value]
        elif isinstance(value, list):
            routes[str(key)] = [str(item) for item in value if item]
    return routes


def _load_gateway_model_aliases() -> dict[str, str]:
    aliases = dict(CLAUDE_PROXY_MODEL_ALIASES)
    aliases.update(
        {
            "glm5.1": "glm-5.1",
            "GLM-5.1": "glm-5.1",
            "deepseek-flash": "deepseek-v4-flash",
            "deepseek-pro": "deepseek-v4-pro",
        }
    )
    data = _load_json_dict(LLM_PROXY_MODEL_ALIASES_JSON)
    for key, value in data.items():
        if key and value:
            aliases[str(key)] = str(value)
    return aliases


LLM_PROXY_MODEL_ROUTES = _load_gateway_model_routes()
LLM_PROXY_MODEL_ALIASES = _load_gateway_model_aliases()

# DeepSeek OpenAI-compatible provider
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_DEFAULT_MODEL = os.getenv("DEEPSEEK_DEFAULT_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", str(LLM_PROXY_TIMEOUT)))
DEEPSEEK_MAX_CONCURRENCY = int(os.getenv("DEEPSEEK_MAX_CONCURRENCY", "5"))
DEEPSEEK_RATE_LIMIT_COOLDOWN_SECONDS = float(
    os.getenv("DEEPSEEK_RATE_LIMIT_COOLDOWN_SECONDS", "60")
)
DEEPSEEK_THINKING_TYPE = os.getenv("DEEPSEEK_THINKING_TYPE", "")
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "")

# 知识图谱 LLM 任务模型方案。这里只选择模型名；模型到供应商的路由由 LLM Proxy 负责。
KG_LLM_PLAN = os.getenv("KG_LLM_PLAN", "deepseek_balanced")
KG_LLM_FORCE_MODEL = os.getenv("KG_LLM_FORCE_MODEL", "")
KG_LLM_PLANS_JSON = os.getenv("KG_LLM_PLANS_JSON", "")


def _load_kg_llm_plans() -> dict[str, dict[str, str]]:
    all_deepseek_flash = {
        "*": "deepseek-v4-flash",
        "financial_news_extraction": "deepseek-v4-flash",
        "financial_entity_normalization": "deepseek-v4-flash",
        "kg_retrieval_controller": "deepseek-v4-flash",
        "kg_candidate_judge": "deepseek-v4-flash",
        "kg_agentic_retrieval": "deepseek-v4-flash",
        "simple_extraction": "deepseek-v4-flash",
        "complex_extraction": "deepseek-v4-flash",
        "query_planning": "deepseek-v4-flash",
        "summarization": "deepseek-v4-flash",
        "kg_community_report": "deepseek-v4-flash",
        "kg_delta_finding": "deepseek-v4-flash",
        "kg_finding_evidence_validate": "deepseek-v4-flash",
        "quality_review": "deepseek-v4-flash",
    }
    plans: dict[str, dict[str, str]] = {
        "deepseek_cheap": dict(all_deepseek_flash),
        "deepseek_balanced": {
            **all_deepseek_flash,
            "financial_entity_normalization": "deepseek-v4-flash",
            "kg_retrieval_controller": "deepseek-v4-flash",
            "kg_candidate_judge": "deepseek-v4-flash",
            "kg_agentic_retrieval": "deepseek-v4-pro",
            "complex_extraction": "deepseek-v4-pro",
            "query_planning": "deepseek-v4-pro",
            "summarization": "deepseek-v4-pro",
            "kg_community_report": "deepseek-v4-pro",
            "kg_delta_finding": "deepseek-v4-flash",
            "kg_finding_evidence_validate": "deepseek-v4-flash",
            "quality_review": "deepseek-v4-pro",
        },
        "glm_subscription": {
            "*": "glm-5.1",
            "financial_news_extraction": "glm-5.1",
            "financial_entity_normalization": "glm-5.1",
            "kg_retrieval_controller": "glm-5.1",
            "kg_candidate_judge": "glm-5.1",
            "kg_agentic_retrieval": "glm-5.1",
            "simple_extraction": "glm-5.1",
            "complex_extraction": "glm-5.1",
            "query_planning": "glm-5.1",
            "summarization": "glm-5.1",
            "quality_review": "glm-5.1",
        },
    }
    data = _load_json_dict(KG_LLM_PLANS_JSON)
    for plan_name, mapping in data.items():
        if not isinstance(mapping, dict):
            continue
        plans[str(plan_name)] = {
            str(task): str(model)
            for task, model in mapping.items()
            if task and model
        }
    return plans


KG_LLM_PLANS = _load_kg_llm_plans()

# ==================== 交易 ====================

TRADE_BASE_URL = "https://trade.5ifund.com"
TRADE_DEVICE_ID = os.getenv("TRADE_DEVICE_ID", "7246091a5f126b63")
TRADE_DEVICE_SIGN = os.getenv("TRADE_DEVICE_SIGN", "2293a78f6581c12bbb334759458d4de3")

# ==================== jettask ====================

JETTASK_PREFIX = os.getenv("JETTASK_PREFIX", "fund_aggregator")
