#!/usr/bin/env python3
"""基金交易数据库层 - 建表/缓存/信号/交易/持仓 CRUD"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.json")

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "jettask",
    "user": "jettask",
    "password": "123456",
}

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS ft_cache (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    data JSONB NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(fund_code, data_type)
);

CREATE TABLE IF NOT EXISTS ft_market_cache (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) NOT NULL UNIQUE,
    data JSONB NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ft_signals (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL,
    strategy VARCHAR(50) DEFAULT 'default',
    action VARCHAR(10),
    confidence VARCHAR(10),
    indicators JSONB,
    signal_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ft_decisions (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL,
    fund_name VARCHAR(100),
    action VARCHAR(10) NOT NULL,
    amount NUMERIC(12,2),
    sell_pct NUMERIC(5,2),
    reason TEXT,
    confidence VARCHAR(10),
    market_view TEXT,
    risk_notes TEXT,
    referenced_lesson_ids INT[],
    decision_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 增量DDL：为已有 ft_decisions 表添加新字段
DO $$ BEGIN
    ALTER TABLE ft_decisions ADD COLUMN IF NOT EXISTS referenced_lesson_ids INT[];
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS ft_trades (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL,
    fund_name VARCHAR(100),
    action VARCHAR(10) NOT NULL,
    amount NUMERIC(12,2),
    shares NUMERIC(14,4),
    order_no VARCHAR(50),
    reason TEXT,
    api_response JSONB,
    trade_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ft_positions (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL UNIQUE,
    fund_name VARCHAR(100),
    total_cost NUMERIC(12,2) DEFAULT 0,
    shares NUMERIC(14,4) DEFAULT 0,
    avg_cost NUMERIC(10,4) DEFAULT 0,
    current_nav NUMERIC(10,4) DEFAULT 0,
    market_value NUMERIC(12,2) DEFAULT 0,
    profit_pct NUMERIC(8,4) DEFAULT 0,
    first_buy_date DATE DEFAULT CURRENT_DATE,
    add_count INT DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 增量DDL：为已有表添加新字段
DO $$ BEGIN
    ALTER TABLE ft_positions ADD COLUMN IF NOT EXISTS first_buy_date DATE DEFAULT CURRENT_DATE;
    ALTER TABLE ft_positions ADD COLUMN IF NOT EXISTS add_count INT DEFAULT 0;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS ft_run_log (
    id SERIAL PRIMARY KEY,
    run_date DATE DEFAULT CURRENT_DATE,
    decisions_count INT DEFAULT 0,
    trades_count INT DEFAULT 0,
    summary TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ft_reviews (
    id SERIAL PRIMARY KEY,
    decision_id INT REFERENCES ft_decisions(id),
    fund_code VARCHAR(10) NOT NULL,
    decision_date DATE NOT NULL,
    decision_action VARCHAR(10),
    decision_reason TEXT,
    nav_at_decision NUMERIC(10,4),
    nav_t1 NUMERIC(10,4),
    nav_t2 NUMERIC(10,4),
    change_t1_pct NUMERIC(8,4),
    change_t2_pct NUMERIC(8,4),
    outcome VARCHAR(10) DEFAULT 'pending',
    review_notes TEXT,
    lesson_extracted BOOLEAN DEFAULT FALSE,
    reviewed_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ft_lessons (
    id SERIAL PRIMARY KEY,
    category VARCHAR(20) NOT NULL,
    trigger_pattern TEXT,
    expected_outcome TEXT,
    actual_outcome TEXT,
    lesson_text TEXT NOT NULL,
    confidence VARCHAR(10) DEFAULT 'low',
    status VARCHAR(10) DEFAULT 'active',
    verify_count INT DEFAULT 1,
    success_count INT DEFAULT 0,
    related_sectors TEXT[],
    tags JSONB,
    source_review_ids INT[],
    superseded_by INT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 增量DDL：为已有 ft_lessons 表添加新字段
DO $$ BEGIN
    ALTER TABLE ft_lessons ADD COLUMN IF NOT EXISTS status VARCHAR(10) DEFAULT 'active';
    ALTER TABLE ft_lessons ADD COLUMN IF NOT EXISTS superseded_by INT;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_ft_cache_lookup ON ft_cache(fund_code, data_type);
CREATE INDEX IF NOT EXISTS idx_ft_market_cache_type ON ft_market_cache(data_type);
CREATE INDEX IF NOT EXISTS idx_ft_signals_date ON ft_signals(fund_code, signal_date);
CREATE INDEX IF NOT EXISTS idx_ft_decisions_date ON ft_decisions(decision_date);
CREATE INDEX IF NOT EXISTS idx_ft_trades_date ON ft_trades(fund_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_ft_reviews_date ON ft_reviews(decision_date);
CREATE INDEX IF NOT EXISTS idx_ft_reviews_outcome ON ft_reviews(outcome);
CREATE INDEX IF NOT EXISTS idx_ft_lessons_category ON ft_lessons(category);
CREATE INDEX IF NOT EXISTS idx_ft_lessons_status ON ft_lessons(status);

CREATE TABLE IF NOT EXISTS ft_alipay_positions (
    id SERIAL PRIMARY KEY,
    fund_name VARCHAR(100) NOT NULL,
    fund_code VARCHAR(10),
    current_value NUMERIC(12,2) NOT NULL,
    total_cost NUMERIC(12,2),
    total_profit NUMERIC(12,2),
    profit_rate NUMERIC(8,2),
    daily_pnl NUMERIC(12,2),
    category VARCHAR(20),
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(fund_name, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_ft_alipay_date ON ft_alipay_positions(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ft_alipay_name ON ft_alipay_positions(fund_name);

CREATE TABLE IF NOT EXISTS ft_alipay_decisions (
    id SERIAL PRIMARY KEY,
    fund_name VARCHAR(100) NOT NULL,
    fund_code VARCHAR(10),
    action VARCHAR(10) NOT NULL,
    amount NUMERIC(12,2),
    sell_pct NUMERIC(5,2),
    reason TEXT,
    confidence VARCHAR(10),
    market_view TEXT,
    decision_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ft_alipay_decisions_date ON ft_alipay_decisions(decision_date);

-- 待执行决策表（交易截止后的决策，等待次日执行）
CREATE TABLE IF NOT EXISTS ft_pending_decisions (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL,
    fund_name VARCHAR(100),
    action VARCHAR(10) NOT NULL,  -- buy/sell/clear
    amount NUMERIC(12,2),          -- 买入金额
    sell_pct NUMERIC(5,2),         -- 卖出比例
    reason TEXT,
    confidence VARCHAR(10),
    market_view TEXT,              -- 决策时的市场观点
    market_phase VARCHAR(20),      -- 决策时的市场阶段
    risk_notes TEXT,
    decision_date DATE DEFAULT CURRENT_DATE,  -- 决策日期
    status VARCHAR(20) DEFAULT 'pending',     -- pending/executed/cancelled
    executed_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    cancel_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ft_pending_status ON ft_pending_decisions(status);
CREATE INDEX IF NOT EXISTS idx_ft_pending_date ON ft_pending_decisions(decision_date);

-- 基金买入限额表（记录每次买入时获取的限额信息）
CREATE TABLE IF NOT EXISTS ft_fund_limits (
    id SERIAL PRIMARY KEY,
    fund_code VARCHAR(10) NOT NULL UNIQUE,
    fund_name VARCHAR(100),
    min_buy NUMERIC(12,2) DEFAULT 0,           -- 最小买入金额
    max_buy NUMERIC(18,2) DEFAULT 0,           -- 最大买入金额（当日可买）
    daily_limit NUMERIC(18,2) DEFAULT 0,       -- 每日申购限额（基金本身的限制）
    is_suspended BOOLEAN DEFAULT FALSE,        -- 是否暂停申购
    suspend_reason TEXT,                       -- 暂停原因
    last_checked_at TIMESTAMP DEFAULT NOW(),   -- 最后检查时间
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ft_fund_limits_code ON ft_fund_limits(fund_code);
"""


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def init_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
        conn.commit()
    print("✓ 13 张表创建完成 (ft_cache, ft_market_cache, ft_signals, ft_decisions, ft_trades, ft_positions, ft_run_log, ft_reviews, ft_lessons, ft_alipay_positions, ft_alipay_decisions, ft_pending_decisions, ft_fund_limits)")


# ==================== 缓存 ====================

def get_cache(fund_code, data_type):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT data FROM ft_cache WHERE fund_code=%s AND data_type=%s AND expires_at > NOW()",
                (fund_code, data_type),
            )
            row = cur.fetchone()
            return row["data"] if row else None


def set_cache(fund_code, data_type, data, ttl_seconds=14400):
    expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_cache (fund_code, data_type, data, expires_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (fund_code, data_type)
                   DO UPDATE SET data=%s, expires_at=%s, created_at=NOW()""",
                (fund_code, data_type, json.dumps(data, ensure_ascii=False), expires_at,
                 json.dumps(data, ensure_ascii=False), expires_at),
            )
        conn.commit()


def get_market_cache(data_type):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT data FROM ft_market_cache WHERE data_type=%s AND expires_at > NOW()",
                (data_type,),
            )
            row = cur.fetchone()
            return row["data"] if row else None


def set_market_cache(data_type, data, ttl_seconds=1800):
    expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_market_cache (data_type, data, expires_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (data_type)
                   DO UPDATE SET data=%s, expires_at=%s, created_at=NOW()""",
                (data_type, json.dumps(data, ensure_ascii=False), expires_at,
                 json.dumps(data, ensure_ascii=False), expires_at),
            )
        conn.commit()


# ==================== 信号 ====================

def save_signal(fund_code, strategy, action, confidence, indicators):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_signals (fund_code, strategy, action, confidence, indicators)
                   VALUES (%s, %s, %s, %s, %s)""",
                (fund_code, strategy, action, confidence, json.dumps(indicators, ensure_ascii=False)),
            )
        conn.commit()


def get_latest_signals(fund_code=None, days=1):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if fund_code:
                cur.execute(
                    "SELECT * FROM ft_signals WHERE fund_code=%s AND signal_date >= CURRENT_DATE - %s ORDER BY created_at DESC",
                    (fund_code, days),
                )
            else:
                cur.execute(
                    "SELECT * FROM ft_signals WHERE signal_date >= CURRENT_DATE - %s ORDER BY created_at DESC",
                    (days,),
                )
            return cur.fetchall()


# ==================== 决策 ====================

def save_decision(fund_code, fund_name, action, reason, confidence,
                   market_view, amount=None, sell_pct=None, risk_notes=None, referenced_lesson_ids=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_decisions (fund_code, fund_name, action, amount, sell_pct, reason,
                   confidence, market_view, risk_notes, referenced_lesson_ids)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (fund_code, fund_name, action, amount, sell_pct, reason, confidence,
                 market_view, risk_notes, referenced_lesson_ids or []),
            )
        conn.commit()


def get_today_decisions():
    """获取今日已做出的决策（含交易执行状态）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT d.*, t.order_no, t.trade_date as executed_at
                   FROM ft_decisions d
                   LEFT JOIN ft_trades t ON t.fund_code = d.fund_code AND t.trade_date = d.decision_date
                   WHERE d.decision_date = CURRENT_DATE
                   ORDER BY d.created_at DESC""",
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, (datetime,)):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


def get_watch_streaks():
    """获取每只基金连续被标记为 watch 的天数（从最近一次非 watch 决策算起）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 对每只基金，查最近连续 watch 的天数
            cur.execute(
                """WITH ranked AS (
                    SELECT fund_code, action, decision_date,
                           ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY decision_date DESC) as rn
                    FROM ft_decisions
                    WHERE decision_date >= CURRENT_DATE - 30
                )
                SELECT fund_code,
                       COUNT(*) as streak
                FROM ranked
                WHERE action = 'watch'
                  AND rn <= (
                    SELECT COALESCE(MIN(r2.rn), 999)
                    FROM ranked r2
                    WHERE r2.fund_code = ranked.fund_code AND r2.action != 'watch'
                  ) - 1
                GROUP BY fund_code"""
            )
            return {row["fund_code"]: row["streak"] for row in cur.fetchall()}


def get_recent_decisions(days=5, exclude_today=False):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            date_filter = "decision_date < CURRENT_DATE AND decision_date >= CURRENT_DATE - %s" if exclude_today else "decision_date >= CURRENT_DATE - %s"
            cur.execute(
                f"SELECT * FROM ft_decisions WHERE {date_filter} ORDER BY created_at DESC",
                (days,),
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, (datetime,)):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


# ==================== 交易 ====================

def save_trade(fund_code, fund_name, action, amount, shares=None, order_no=None, reason=None, api_response=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_trades (fund_code, fund_name, action, amount, shares, order_no, reason, api_response)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (fund_code, fund_name, action, amount, shares, order_no, reason,
                 json.dumps(api_response, ensure_ascii=False) if api_response else None),
            )
        conn.commit()


def get_recent_trades(fund_code=None, days=1):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if fund_code:
                cur.execute(
                    "SELECT * FROM ft_trades WHERE fund_code=%s AND trade_date >= CURRENT_DATE - %s ORDER BY created_at DESC",
                    (fund_code, days),
                )
            else:
                cur.execute(
                    "SELECT * FROM ft_trades WHERE trade_date >= CURRENT_DATE - %s ORDER BY created_at DESC",
                    (days,),
                )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, (datetime,)):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


# ==================== 持仓 ====================

def get_positions():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ft_positions ORDER BY market_value DESC")
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, (datetime,)):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


def get_trades(days=30, limit=100):
    """查询本地交易记录"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM ft_trades
                WHERE trade_date >= CURRENT_DATE - %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (days, limit))
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, (datetime,)):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


def update_position(fund_code, fund_name=None, total_cost=None, shares=None,
                    avg_cost=None, current_nav=None, market_value=None, profit_pct=None):
    fields = []
    values = []
    if fund_name is not None:
        fields.append("fund_name=%s")
        values.append(fund_name)
    if total_cost is not None:
        fields.append("total_cost=%s")
        values.append(total_cost)
    if shares is not None:
        fields.append("shares=%s")
        values.append(shares)
    if avg_cost is not None:
        fields.append("avg_cost=%s")
        values.append(avg_cost)
    if current_nav is not None:
        fields.append("current_nav=%s")
        values.append(current_nav)
    if market_value is not None:
        fields.append("market_value=%s")
        values.append(market_value)
    if profit_pct is not None:
        fields.append("profit_pct=%s")
        values.append(profit_pct)
    if not fields:
        return
    fields.append("updated_at=NOW()")
    values.append(fund_code)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE ft_positions SET {', '.join(fields)} WHERE fund_code=%s",
                values,
            )
            if cur.rowcount == 0:
                cur.execute(
                    """INSERT INTO ft_positions (fund_code, fund_name, total_cost, shares, avg_cost, current_nav, market_value, profit_pct, first_buy_date, add_count)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, 0)""",
                    (fund_code, fund_name or '', total_cost or 0, shares or 0,
                     avg_cost or 0, current_nav or 0, market_value or 0, profit_pct or 0),
                )
        conn.commit()


def delete_position(fund_code):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ft_positions WHERE fund_code=%s", (fund_code,))
        conn.commit()


# ==================== 支付宝持仓 ====================

def save_alipay_snapshot(holdings, snapshot_date=None):
    """保存支付宝持仓快照（整批替换当日数据）

    Args:
        holdings: list of dict, 每个 dict 包含:
            fund_name, fund_code(可选), current_value, total_cost(可选),
            total_profit(可选), profit_rate(可选), daily_pnl(可选), category(可选)
        snapshot_date: 快照日期，默认今天
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 删除当日已有数据（整批替换）
            if snapshot_date:
                cur.execute("DELETE FROM ft_alipay_positions WHERE snapshot_date=%s", (snapshot_date,))
            else:
                cur.execute("DELETE FROM ft_alipay_positions WHERE snapshot_date=CURRENT_DATE")

            for h in holdings:
                cur.execute(
                    """INSERT INTO ft_alipay_positions
                       (fund_name, fund_code, current_value, total_cost, total_profit,
                        profit_rate, daily_pnl, category, snapshot_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (h["fund_name"], h.get("fund_code"), h["current_value"],
                     h.get("total_cost"), h.get("total_profit"),
                     h.get("profit_rate"), h.get("daily_pnl"),
                     h.get("category"), snapshot_date or datetime.now().strftime("%Y-%m-%d")),
                )
        conn.commit()
    return len(holdings)


def get_alipay_positions(snapshot_date=None):
    """获取支付宝持仓（默认最新快照）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if snapshot_date:
                cur.execute(
                    "SELECT * FROM ft_alipay_positions WHERE snapshot_date=%s ORDER BY current_value DESC",
                    (snapshot_date,),
                )
            else:
                # 获取最新快照日期
                cur.execute("SELECT MAX(snapshot_date) FROM ft_alipay_positions")
                row = cur.fetchone()
                max_date = row["max"] if row else None
                if not max_date:
                    return []
                cur.execute(
                    "SELECT * FROM ft_alipay_positions WHERE snapshot_date=%s ORDER BY current_value DESC",
                    (max_date,),
                )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                    elif hasattr(v, "days"):
                        r[k] = str(v)
            return rows


def get_alipay_history(fund_name, days=30):
    """获取某只支付宝基金的历史持仓变化"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT snapshot_date, current_value, total_profit, profit_rate, daily_pnl
                   FROM ft_alipay_positions
                   WHERE fund_name=%s AND snapshot_date >= CURRENT_DATE - %s
                   ORDER BY snapshot_date""",
                (fund_name, days),
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                    elif hasattr(v, "days"):
                        r[k] = str(v)
            return rows


def get_alipay_snapshot_dates(limit=10):
    """获取所有快照日期"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT snapshot_date FROM ft_alipay_positions ORDER BY snapshot_date DESC LIMIT %s",
                (limit,),
            )
            return [r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]) for r in cur.fetchall()]


def save_alipay_decision(fund_name, fund_code, action, amount=None, sell_pct=None,
                         reason=None, confidence=None, market_view=None):
    """保存支付宝基金操作建议记录"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_alipay_decisions
                   (fund_name, fund_code, action, amount, sell_pct, reason, confidence, market_view)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (fund_name, fund_code, action, amount, sell_pct, reason, confidence, market_view),
            )
            decision_id = cur.fetchone()[0]
        conn.commit()
    return decision_id


def get_alipay_recent_decisions(days=7):
    """获取最近的支付宝操作建议"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM ft_alipay_decisions
                   WHERE decision_date >= CURRENT_DATE - %s
                   ORDER BY decision_date DESC, created_at DESC""",
                (days,),
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
            return rows


# ==================== 支付宝基金代码映射 ====================

def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    # 尾部换行
    with open(CONFIG_PATH, "a") as f:
        f.write("\n")


def load_alipay_fund_map():
    config = _load_config()
    return config.get("alipay_fund_map", {})


def save_alipay_fund_map(fund_map):
    config = _load_config()
    config["alipay_fund_map"] = fund_map
    _save_config(config)


def alipay_map_add(name, code):
    fund_map = load_alipay_fund_map()
    fund_map[name] = code
    save_alipay_fund_map(fund_map)
    return {"ok": True, "name": name, "code": code, "total": len(fund_map)}


def alipay_map_batch(batch_dict):
    fund_map = load_alipay_fund_map()
    fund_map.update(batch_dict)
    save_alipay_fund_map(fund_map)
    return {"ok": True, "added": len(batch_dict), "total": len(fund_map)}


def alipay_resolve(names):
    """批量解析基金名称为代码"""
    fund_map = load_alipay_fund_map()
    resolved = {}
    unresolved = []
    for name in names:
        code = fund_map.get(name)
        if not code:
            name_clean = name.replace(" ", "").replace("（", "(").replace("）", ")")
            for map_name, map_code in fund_map.items():
                map_clean = map_name.replace(" ", "").replace("（", "(").replace("）", ")")
                if name_clean == map_clean or name_clean in map_clean or map_clean in name_clean:
                    code = map_code
                    break
        if code:
            resolved[name] = code
        else:
            unresolved.append(name)
    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
    }


# ==================== 待执行决策（延迟交易） ====================

def save_pending_decision(fund_code, fund_name, action, reason, confidence,
                          market_view, market_phase=None, amount=None, sell_pct=None, risk_notes=None):
    """保存待执行的决策（交易截止后的决策，等待次日执行）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 检查是否已存在同一基金的 pending 决策，如果有则更新
            cur.execute(
                "SELECT id FROM ft_pending_decisions WHERE fund_code=%s AND status='pending'",
                (fund_code,),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """UPDATE ft_pending_decisions SET
                       action=%s, amount=%s, sell_pct=%s, reason=%s, confidence=%s,
                       market_view=%s, market_phase=%s, risk_notes=%s, decision_date=CURRENT_DATE, created_at=NOW()
                       WHERE id=%s""",
                    (action, amount, sell_pct, reason, confidence, market_view, market_phase, risk_notes, existing[0]),
                )
                decision_id = existing[0]
            else:
                cur.execute(
                    """INSERT INTO ft_pending_decisions
                       (fund_code, fund_name, action, amount, sell_pct, reason, confidence, market_view, market_phase, risk_notes)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (fund_code, fund_name, action, amount, sell_pct, reason, confidence, market_view, market_phase, risk_notes),
                )
                decision_id = cur.fetchone()[0]
        conn.commit()
    return decision_id


def get_pending_decisions():
    """获取所有待执行的决策"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM ft_pending_decisions WHERE status='pending' ORDER BY decision_date, created_at"""
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
            return rows


def execute_pending_decision(pending_id):
    """标记待执行决策为已执行"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_pending_decisions SET status='executed', executed_at=NOW() WHERE id=%s",
                (pending_id,),
            )
        conn.commit()


def cancel_pending_decision(pending_id, cancel_reason=None):
    """撤销待执行决策"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_pending_decisions SET status='cancelled', cancelled_at=NOW(), cancel_reason=%s WHERE id=%s",
                (cancel_reason, pending_id),
            )
        conn.commit()


def cancel_all_pending_for_fund(fund_code, cancel_reason=None):
    """撤销某基金的所有待执行决策"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_pending_decisions SET status='cancelled', cancelled_at=NOW(), cancel_reason=%s WHERE fund_code=%s AND status='pending'",
                (cancel_reason, fund_code),
            )
            count = cur.rowcount
        conn.commit()
    return count


def get_pending_decisions_summary():
    """获取待执行决策汇总（按基金分组）"""
    pending = get_pending_decisions()
    if not pending:
        return {"count": 0, "total_buy_amount": 0, "decisions": []}
    total_buy = sum(float(d.get("amount") or 0) for d in pending if d.get("action") == "buy")
    return {
        "count": len(pending),
        "total_buy_amount": total_buy,
        "decisions": pending,
    }


# ==================== 运行日志 ====================

def log_run(decisions_count, trades_count, summary):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_run_log (decisions_count, trades_count, summary)
                   VALUES (%s, %s, %s)""",
                (decisions_count, trades_count, summary),
            )
        conn.commit()


# ==================== 工具函数 ====================

def get_today_buy_total():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM ft_trades WHERE action='buy' AND trade_date=CURRENT_DATE"
            )
            return float(cur.fetchone()[0])


def get_last_trade_date(fund_code):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT trade_date FROM ft_trades WHERE fund_code=%s ORDER BY trade_date DESC LIMIT 1",
                (fund_code,),
            )
            row = cur.fetchone()
            return row["trade_date"] if row else None


def get_last_trade_action(fund_code):
    """获取某基金最近一次交易的操作类型和日期"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT action, trade_date FROM ft_trades WHERE fund_code=%s ORDER BY created_at DESC LIMIT 1",
                (fund_code,),
            )
            return cur.fetchone()


def get_hold_days(fund_code):
    """获取某基金的持有天数（从 first_buy_date 到今天）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CURRENT_DATE - first_buy_date FROM ft_positions WHERE fund_code=%s",
                (fund_code,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def get_recent_run_losses(days=5):
    """获取最近 N 天的运行日志，用于熔断检测"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ft_run_log WHERE run_date >= CURRENT_DATE - %s ORDER BY run_date DESC",
                (days,),
            )
            return cur.fetchall()


def increment_add_count(fund_code):
    """加仓次数 +1"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_positions SET add_count = add_count + 1 WHERE fund_code=%s",
                (fund_code,),
            )
        conn.commit()


# ==================== 决策复盘 ====================

def create_reviews_from_decisions(decision_date=None):
    """从某天的决策记录中创建待复盘条目（跳过已有的）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            date_filter = "d.decision_date = %s" if decision_date else "d.decision_date = CURRENT_DATE - 1"
            params = (decision_date,) if decision_date else ()
            cur.execute(
                f"""SELECT d.id, d.fund_code, d.decision_date, d.action, d.reason
                    FROM ft_decisions d
                    LEFT JOIN ft_reviews r ON r.decision_id = d.id
                    WHERE {date_filter} AND d.action IN ('buy','sell','clear','hold','watch') AND r.id IS NULL""",
                params,
            )
            pending = cur.fetchall()
            created = 0
            for row in pending:
                cur.execute(
                    """INSERT INTO ft_reviews (decision_id, fund_code, decision_date, decision_action, decision_reason)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (row["id"], row["fund_code"], row["decision_date"], row["action"], row["reason"]),
                )
                created += 1
        conn.commit()
    return created


def get_pending_reviews(days_back=3):
    """获取待复盘的决策（outcome='pending' 且决策日距今至少 1 天）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT r.*, d.fund_name, d.amount, d.sell_pct, d.confidence, d.market_view
                   FROM ft_reviews r
                   JOIN ft_decisions d ON r.decision_id = d.id
                   WHERE r.outcome = 'pending'
                     AND r.decision_date <= CURRENT_DATE - 1
                     AND r.decision_date >= CURRENT_DATE - %s
                   ORDER BY r.decision_date""",
                (days_back,),
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                    elif hasattr(v, 'days'):
                        r[k] = str(v)
            return rows


def update_review(review_id, nav_at_decision=None, nav_t1=None, nav_t2=None,
                  change_t1_pct=None, change_t2_pct=None, outcome=None, review_notes=None):
    """更新复盘记录（填入净值变化和结论）"""
    fields = []
    values = []
    for fname, fval in [
        ("nav_at_decision", nav_at_decision), ("nav_t1", nav_t1), ("nav_t2", nav_t2),
        ("change_t1_pct", change_t1_pct), ("change_t2_pct", change_t2_pct),
        ("outcome", outcome), ("review_notes", review_notes),
    ]:
        if fval is not None:
            fields.append(f"{fname}=%s")
            values.append(fval)
    if not fields:
        return
    fields.append("reviewed_at=NOW()")
    values.append(review_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE ft_reviews SET {', '.join(fields)} WHERE id=%s", values)
        conn.commit()


def mark_lesson_extracted(review_id):
    """标记复盘记录已提取经验"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ft_reviews SET lesson_extracted=TRUE WHERE id=%s", (review_id,))
        conn.commit()


def get_review_stats(days=30):
    """获取复盘统计：正确/错误/平局数量"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT outcome, COUNT(*) as cnt
                   FROM ft_reviews
                   WHERE outcome != 'pending' AND decision_date >= CURRENT_DATE - %s
                   GROUP BY outcome""",
                (days,),
            )
            return {row["outcome"]: row["cnt"] for row in cur.fetchall()}


# ==================== 经验知识库 ====================

def save_lesson(category, trigger_pattern, expected_outcome, actual_outcome,
                lesson_text, confidence="low", related_sectors=None, tags=None, source_review_ids=None):
    """保存一条经验教训"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_lessons
                   (category, trigger_pattern, expected_outcome, actual_outcome,
                    lesson_text, confidence, related_sectors, tags, source_review_ids)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (category, trigger_pattern, expected_outcome, actual_outcome,
                 lesson_text, confidence,
                 related_sectors or [],
                 json.dumps(tags, ensure_ascii=False) if tags else None,
                 source_review_ids or []),
            )
            lesson_id = cur.fetchone()[0]
        conn.commit()
    return lesson_id


def get_lessons(category=None, min_confidence=None, include_deprecated=False, limit=20):
    """获取经验教训列表（默认只返回 active 状态的）"""
    conditions = []
    params = []
    if not include_deprecated:
        conditions.append("status = 'active'")
    if category:
        conditions.append("category=%s")
        params.append(category)
    if min_confidence:
        conf_order = {"low": 0, "medium": 1, "high": 2}
        min_val = conf_order.get(min_confidence, 0)
        conditions.append(
            "CASE confidence WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END >= %s"
        )
        params.append(min_val)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""SELECT * FROM ft_lessons {where}
                    ORDER BY
                      CASE confidence WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END DESC,
                      verify_count DESC
                    LIMIT %s""",
                params,
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
            return rows


def update_lesson_confidence(lesson_id, success):
    """更新经验的可信度（验证次数 +1，成功则 success_count +1，失败率高自动废弃）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ft_lessons SET verify_count = verify_count + 1 WHERE id=%s",
                (lesson_id,),
            )
            if success:
                cur.execute(
                    "UPDATE ft_lessons SET success_count = success_count + 1 WHERE id=%s",
                    (lesson_id,),
                )
            # 自动升级/降级 confidence + 自动废弃
            cur.execute("SELECT verify_count, success_count FROM ft_lessons WHERE id=%s", (lesson_id,))
            row = cur.fetchone()
            if row:
                vc, sc = row
                rate = sc / vc if vc > 0 else 0
                # 验证 5 次以上且成功率 < 30% → 自动废弃
                if vc >= 5 and rate < 0.3:
                    cur.execute(
                        "UPDATE ft_lessons SET confidence='low', status='deprecated', updated_at=NOW() WHERE id=%s",
                        (lesson_id,),
                    )
                elif vc >= 5 and rate >= 0.7:
                    new_conf = "high"
                    cur.execute("UPDATE ft_lessons SET confidence=%s, updated_at=NOW() WHERE id=%s", (new_conf, lesson_id))
                elif vc >= 3 and rate >= 0.5:
                    new_conf = "medium"
                    cur.execute("UPDATE ft_lessons SET confidence=%s, updated_at=NOW() WHERE id=%s", (new_conf, lesson_id))
                else:
                    cur.execute("UPDATE ft_lessons SET confidence='low', updated_at=NOW() WHERE id=%s", (lesson_id,))
        conn.commit()


def find_similar_lessons(trigger_pattern, category=None):
    """根据触发模式模糊搜索相似经验（只搜 active 的）"""
    conditions = ["trigger_pattern ILIKE %s", "status = 'active'"]
    params = [f"%{trigger_pattern}%"]
    if category:
        conditions.append("category=%s")
        params.append(category)
    where = " AND ".join(conditions)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""SELECT * FROM ft_lessons WHERE {where}
                    ORDER BY verify_count DESC LIMIT 10""",
                params,
            )
            rows = cur.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
            return rows


def deprecate_lesson(lesson_id, reason=None):
    """手动废弃一条经验（标记为 deprecated）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            tags_update = ""
            params = [lesson_id]
            if reason:
                tags_update = ", tags = COALESCE(tags, '{}'::jsonb) || %s::jsonb"
                params = [json.dumps({"deprecate_reason": reason}, ensure_ascii=False), lesson_id]
            cur.execute(
                f"UPDATE ft_lessons SET status='deprecated', updated_at=NOW(){tags_update} WHERE id=%s",
                params,
            )
        conn.commit()


def revise_lesson(old_lesson_id, new_lesson_text, new_trigger_pattern=None,
                  new_expected_outcome=None, reason=None):
    """修正一条经验：将旧经验标记为 revised，创建新版本并继承元信息"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 读取旧经验
            cur.execute("SELECT * FROM ft_lessons WHERE id=%s", (old_lesson_id,))
            old = cur.fetchone()
            if not old:
                return None

            # 创建新版本，继承 category、related_sectors、source_review_ids
            # confidence 重置为 low，verify/success 从 1/0 开始
            tags = old.get("tags") or {}
            if isinstance(tags, str):
                tags = json.loads(tags)
            tags["revised_from"] = old_lesson_id
            if reason:
                tags["revision_reason"] = reason

            cur.execute(
                """INSERT INTO ft_lessons
                   (category, trigger_pattern, expected_outcome, actual_outcome,
                    lesson_text, confidence, status, related_sectors, tags, source_review_ids)
                   VALUES (%s, %s, %s, %s, %s, 'low', 'active', %s, %s, %s)
                   RETURNING id""",
                (old["category"],
                 new_trigger_pattern or old["trigger_pattern"],
                 new_expected_outcome or old["expected_outcome"],
                 old["actual_outcome"],
                 new_lesson_text,
                 old["related_sectors"] or [],
                 json.dumps(tags, ensure_ascii=False),
                 old["source_review_ids"] or []),
            )
            new_id = cur.fetchone()["id"]

            # 标记旧经验为 revised，记录被谁取代
            cur.execute(
                "UPDATE ft_lessons SET status='revised', superseded_by=%s, updated_at=NOW() WHERE id=%s",
                (new_id, old_lesson_id),
            )
        conn.commit()
    return new_id


# ==================== 基金限额 ====================

def save_fund_limit(fund_code, fund_name=None, min_buy=0, max_buy=0,
                   daily_limit=0, is_suspended=False, suspend_reason=None):
    """保存或更新基金买入限额信息"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_fund_limits
                   (fund_code, fund_name, min_buy, max_buy, daily_limit,
                    is_suspended, suspend_reason, last_checked_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                   ON CONFLICT (fund_code) DO UPDATE SET
                       fund_name = COALESCE(EXCLUDED.fund_name, ft_fund_limits.fund_name),
                       min_buy = EXCLUDED.min_buy,
                       max_buy = EXCLUDED.max_buy,
                       daily_limit = CASE
                           WHEN EXCLUDED.daily_limit > 0 THEN EXCLUDED.daily_limit
                           ELSE ft_fund_limits.daily_limit
                       END,
                       is_suspended = EXCLUDED.is_suspended,
                       suspend_reason = EXCLUDED.suspend_reason,
                       last_checked_at = NOW(),
                       updated_at = NOW()""",
                (fund_code, fund_name, min_buy, max_buy, daily_limit,
                 is_suspended, suspend_reason),
            )
        conn.commit()


def get_fund_limit(fund_code):
    """获取单个基金的限额信息"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ft_fund_limits WHERE fund_code = %s",
                (fund_code,),
            )
            return cur.fetchone()


def get_fund_limits(fund_codes=None, min_buy_lte=None, include_suspended=False):
    """批量获取基金限额信息

    Args:
        fund_codes: 基金代码列表，None 表示查全部
        min_buy_lte: 最小买入金额小于等于此值
        include_suspended: 是否包含暂停申购的基金
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conditions = []
            params = []

            if fund_codes:
                conditions.append("fund_code = ANY(%s)")
                params.append(fund_codes)

            if min_buy_lte is not None:
                conditions.append("min_buy <= %s")
                params.append(min_buy_lte)

            if not include_suspended:
                conditions.append("(is_suspended = FALSE OR is_suspended IS NULL)")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            cur.execute(
                f"""SELECT * FROM ft_fund_limits
                    WHERE {where_clause}
                    ORDER BY min_buy ASC, max_buy DESC""",
                params,
            )
            return cur.fetchall()


def get_buyable_funds(target_amount, exclude_codes=None):
    """获取可以凑够目标金额的基金列表

    Args:
        target_amount: 目标总金额
        exclude_codes: 排除的基金代码列表

    Returns:
        list: [{"fund_code", "fund_name", "min_buy", "max_buy", "suggested_amount"}, ...]
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            exclude_clause = ""
            params = []

            if exclude_codes:
                exclude_clause = "AND fund_code != ALL(%s)"
                params.append(exclude_codes)

            cur.execute(
                f"""SELECT fund_code, fund_name, min_buy, max_buy, daily_limit,
                           last_checked_at
                    FROM ft_fund_limits
                    WHERE (is_suspended = FALSE OR is_suspended IS NULL)
                      AND max_buy > 0
                      {exclude_clause}
                    ORDER BY max_buy DESC, min_buy ASC""",
                params,
            )
            funds = cur.fetchall()

            # 计算建议金额
            result = []
            remaining = target_amount
            for f in funds:
                if remaining <= 0:
                    break
                max_buy = float(f["max_buy"]) if f["max_buy"] else 0
                min_buy = float(f["min_buy"]) if f["min_buy"] else 0

                if max_buy <= 0:
                    continue

                suggested = min(remaining, max_buy)
                if suggested < min_buy:
                    continue

                result.append({
                    "fund_code": f["fund_code"],
                    "fund_name": f["fund_name"],
                    "min_buy": min_buy,
                    "max_buy": max_buy,
                    "suggested_amount": suggested,
                    "last_checked_at": f["last_checked_at"],
                })
                remaining -= suggested

            return result, remaining


def mark_fund_suspended(fund_code, reason=None):
    """标记基金暂停申购"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ft_fund_limits (fund_code, is_suspended, suspend_reason, last_checked_at)
                   VALUES (%s, TRUE, %s, NOW())
                   ON CONFLICT (fund_code) DO UPDATE SET
                       is_suspended = TRUE,
                       suspend_reason = EXCLUDED.suspend_reason,
                       last_checked_at = NOW(),
                       updated_at = NOW()""",
                (fund_code, reason),
            )
        conn.commit()


def get_fund_limits_summary():
    """获取限额统计摘要"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE is_suspended = TRUE) as suspended,
                       COUNT(*) FILTER (WHERE min_buy <= 10) as min_buy_10,
                       COUNT(*) FILTER (WHERE min_buy <= 100) as min_buy_100,
                       COUNT(*) FILTER (WHERE max_buy >= 1000) as max_buy_1000,
                       SUM(max_buy) FILTER (WHERE is_suspended = FALSE OR is_suspended IS NULL) as total_available
                   FROM ft_fund_limits"""
            )
            return cur.fetchone()


# ==================== CLI ====================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python fund_db.py <command>")
        print("  init              - 创建数据库表")
        print("  log-run           - 记录运行日志 (stdin JSON)")
        print("  create-reviews    - 从昨日决策创建待复盘记录 [可选: 日期 YYYY-MM-DD]")
        print("  pending-reviews   - 获取待复盘记录 [可选: 回溯天数, 默认3]")
        print("  review-stats      - 复盘统计 [可选: 天数, 默认30]")
        print("  lessons           - 查看经验知识库 [可选: 类别]")
        print("  alipay-save       - 保存支付宝持仓快照 (stdin JSON)")
        print("  alipay-positions  - 查看支付宝持仓 [可选: 日期]")
        print("  alipay-history    - 查看某基金历史 <基金名> [天数]")
        print("  alipay-dates      - 查看所有快照日期")
        print("  alipay-decisions  - 查看支付宝操作建议 [可选: 天数]")
        print("  alipay-map        - 添加基金名称→代码映射 <名称> <代码> 或 --batch '<JSON>'")
        print("  alipay-list-map   - 查看所有名称→代码映射")
        print("  alipay-resolve    - 批量解析基金名称 '<JSON数组>'")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "init":
        init_tables()
    elif cmd == "log-run":
        data = json.loads(sys.stdin.read())
        log_run(data.get("decisions_count", 0), data.get("trades_count", 0), data.get("summary", ""))
        print("✓ 运行日志已记录")
    elif cmd == "create-reviews":
        date_arg = sys.argv[2] if len(sys.argv) >= 3 else None
        count = create_reviews_from_decisions(date_arg)
        print(json.dumps({"created": count, "message": f"创建 {count} 条待复盘记录"}, ensure_ascii=False))
    elif cmd == "pending-reviews":
        days = int(sys.argv[2]) if len(sys.argv) >= 3 else 3
        reviews = get_pending_reviews(days)
        print(json.dumps(reviews, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "review-stats":
        days = int(sys.argv[2]) if len(sys.argv) >= 3 else 30
        stats = get_review_stats(days)
        print(json.dumps(stats, ensure_ascii=False, separators=(",",":")))
    elif cmd == "lessons":
        category = sys.argv[2] if len(sys.argv) >= 3 else None
        lessons = get_lessons(category=category)
        print(json.dumps(lessons, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "alipay-save":
        data = json.loads(sys.stdin.read())
        holdings = data if isinstance(data, list) else data.get("holdings", [])
        snapshot_date = data.get("snapshot_date") if isinstance(data, dict) else None
        count = save_alipay_snapshot(holdings, snapshot_date)
        print(json.dumps({"saved": count, "message": f"保存 {count} 条支付宝持仓记录"}, ensure_ascii=False))
    elif cmd == "alipay-positions":
        date_arg = sys.argv[2] if len(sys.argv) >= 3 else None
        positions = get_alipay_positions(date_arg)
        print(json.dumps(positions, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "alipay-history":
        if len(sys.argv) < 3:
            print("用法: fund_db.py alipay-history <基金名称> [天数]")
            sys.exit(1)
        fund_name = sys.argv[2]
        days = int(sys.argv[3]) if len(sys.argv) >= 4 else 30
        history = get_alipay_history(fund_name, days)
        print(json.dumps(history, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "alipay-dates":
        dates = get_alipay_snapshot_dates()
        print(json.dumps(dates, ensure_ascii=False))
    elif cmd == "alipay-decisions":
        days = int(sys.argv[2]) if len(sys.argv) >= 3 else 7
        decisions = get_alipay_recent_decisions(days)
        print(json.dumps(decisions, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "alipay-map":
        if len(sys.argv) < 3:
            print("用法: fund_db.py alipay-map <基金名称> <基金代码>")
            print("      fund_db.py alipay-map --batch '<JSON>'")
            sys.exit(1)
        if sys.argv[2] == "--batch":
            batch = json.loads(sys.argv[3])
            result = alipay_map_batch(batch)
        else:
            result = alipay_map_add(sys.argv[2], sys.argv[3])
        print(json.dumps(result, ensure_ascii=False))
    elif cmd == "alipay-list-map":
        fund_map = load_alipay_fund_map()
        print(json.dumps(fund_map, ensure_ascii=False, indent=2) if fund_map else '{"message": "映射为空"}')
    elif cmd == "alipay-resolve":
        if len(sys.argv) < 3:
            print('用法: fund_db.py alipay-resolve \'["名称1", "名称2", ...]\'')
            sys.exit(1)
        names = json.loads(sys.argv[2])
        result = alipay_resolve(names)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif cmd == "pending":
        # 获取待执行决策
        summary = get_pending_decisions_summary()
        print(json.dumps(summary, ensure_ascii=False, default=str, separators=(",",":")))
    elif cmd == "pending-cancel":
        # 撤销待执行决策
        if len(sys.argv) < 3:
            print("用法: fund_db.py pending-cancel <pending_id> [撤销原因]")
            print("      fund_db.py pending-cancel --fund <基金代码> [撤销原因]")
            sys.exit(1)
        if sys.argv[2] == "--fund":
            fund_code = sys.argv[3]
            reason = sys.argv[4] if len(sys.argv) >= 5 else None
            count = cancel_all_pending_for_fund(fund_code, reason)
            print(json.dumps({"cancelled": count, "fund_code": fund_code}, ensure_ascii=False))
        else:
            pending_id = int(sys.argv[2])
            reason = sys.argv[3] if len(sys.argv) >= 4 else None
            cancel_pending_decision(pending_id, reason)
            print(json.dumps({"cancelled": pending_id, "reason": reason}, ensure_ascii=False))
    elif cmd == "pending-execute":
        # 标记为已执行
        if len(sys.argv) < 3:
            print("用法: fund_db.py pending-execute <pending_id>")
            sys.exit(1)
        pending_id = int(sys.argv[2])
        execute_pending_decision(pending_id)
        print(json.dumps({"executed": pending_id}, ensure_ascii=False))
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
