#!/usr/bin/env bash
# 将项目 .claude/skills/ 下的技能同步到 ~/.cc-switch/skills/
# 只同步包含 SKILL.md 的目录

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$SCRIPT_DIR"
DST_DIR="$HOME/.cc-switch/skills"

if [ ! -d "$DST_DIR" ]; then
    echo "❌ 目标目录不存在: $DST_DIR"
    exit 1
fi

changed=0

for skill_dir in "$SRC_DIR"/*/; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    [ -f "$skill_dir/SKILL.md" ] || continue

    dst_skill="$DST_DIR/$skill_name"
    mkdir -p "$dst_skill"

    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='*.pyc' \
        --exclude='node_modules' \
        "$skill_dir" "$dst_skill/"

    echo "  ✓ $skill_name"
    changed=$((changed + 1))
done

if [ $changed -eq 0 ]; then
    echo "无技能需要同步"
else
    echo "已同步 $changed 个技能 → $DST_DIR"
fi
