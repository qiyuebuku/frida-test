"""把 pg_dump --schema-only 输出按聚合根拆分到 schema/*.sql

被 schema/regenerate.sh 调用。

用法: python _split_dump.py <dump_file> <output_dir>
"""
import os
import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

DUMP = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(exist_ok=True)

GROUPS = {
    "01_collection.sql": [
        "ft_news", "ft_market_flow", "ft_market_cache",
        "ft_sentiment", "ft_macro_indicators", "ft_collection_state",
    ],
    "03_trading.sql": [
        "ft_pending_decisions", "ft_decisions", "ft_trades", "ft_positions",
        "ft_industry_mapping", "ft_industry_index", "ft_index_fund",
        "ft_fund_limits",
    ],
    "04_reflection.sql": ["ft_reviews", "ft_lessons"],
    # 不在重构范围,但 init_db.py 要建出来
    # 注意: 文件名前缀决定执行顺序,legacy 必须在 indexes(99) 之前
    "05_legacy.sql": [
        "ft_alipay_decisions", "ft_alipay_positions",
        "ft_cache", "ft_config", "ft_run_log", "ft_signals",
        "ft_raw_data", "ft_raw_data_202603", "ft_raw_data_202604",
        "ft_raw_data_202605", "ft_raw_data_202606",
    ],
}
TABLE_TO_FILE = {t: f for f, tables in GROUPS.items() for t in tables}

text = DUMP.read_text()
SECTION_HEADER = re.compile(r"^-- Name: (.+?); Type: (.+?); Schema: ", re.MULTILINE)
matches = list(SECTION_HEADER.finditer(text))

sections = []
for i, m in enumerate(matches):
    name = m.group(1).strip()
    typ = m.group(2).strip()
    start = text.rfind('\n--\n', 0, m.start())
    if start < 0:
        start = m.start()
    end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
    end_above = text.rfind('\n--\n', 0, end)
    if end_above > start:
        end = end_above
    sections.append((name, typ, text[start:end].strip()))

buckets = {f: [] for f in GROUPS}
# 索引/约束最后建,用 99 前缀确保排在所有 table 之后
buckets["99_indexes.sql"] = []
unknown = []


def find_table_in_body(body):
    for pat in (
        r"ALTER TABLE ONLY public\.(\w+)",
        r"ALTER TABLE public\.(\w+)",
        r"public\.(ft_\w+)",
    ):
        m = re.search(pat, body)
        if m:
            return m.group(1)
    return None


for name, typ, body in sections:
    if typ == "TABLE":
        f = TABLE_TO_FILE.get(name)
        (buckets[f] if f else unknown).append((name, typ, body))
    elif typ in ("SEQUENCE", "SEQUENCE OWNED BY"):
        base = re.sub(r"_id_seq$", "", name)
        f = TABLE_TO_FILE.get(base)
        (buckets[f] if f else unknown).append((name, typ, body))
    elif typ == "DEFAULT":
        tbl = find_table_in_body(body)
        f = TABLE_TO_FILE.get(tbl)
        (buckets[f] if f else unknown).append((name, typ, body))
    elif typ == "TABLE ATTACH":
        tbl = find_table_in_body(body)
        f = TABLE_TO_FILE.get(tbl)
        (buckets[f] if f else unknown).append((name, typ, body))
    elif typ in ("INDEX", "INDEX ATTACH", "CONSTRAINT", "FK CONSTRAINT"):
        buckets["05_indexes.sql"].append((name, typ, body))
    else:
        unknown.append((name, typ, body))

HEADER = """-- AUTO-GENERATED from pg_dump
-- 来源: jettask 生产库 (119.23.227.187:5432/jettask)
-- 重构方案: ../docs/3. 实施方案/3. 系统重构/06-ORM与DDD重构方案.md
--
-- 不要手动编辑;如需更新,跑 schema/regenerate.sh

SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET client_min_messages = warning;
SET row_security = off;
SET default_tablespace = '';
SET default_table_access_method = heap;
"""

for filename, items in buckets.items():
    if not items:
        continue
    out_path = OUT / filename
    parts = [HEADER]
    for name, typ, body in items:
        parts.append(f"\n-- {typ}: {name}\n{body}")
    out_path.write_text("\n".join(parts) + "\n")
    print(f"  ✅ {filename}: {len(items)} sections")

if unknown:
    print(f"\n⚠️  {len(unknown)} 个未分类:")
    for name, typ, _ in unknown[:10]:
        print(f"   {typ}: {name}")
else:
    print("\n✅ 所有 section 均已分类")
