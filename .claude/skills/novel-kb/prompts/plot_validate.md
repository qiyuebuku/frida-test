你是专业的小说剧情分析师。你的任务是同时验证故事弧边界和伏笔追踪的准确性。

---

## 一、弧边界验证

### 当前弧划分方案

{arc_boundaries}

### 全书章节索引（精简版，仅编号+标题）

{full_index}

### 边界附近章节的完整摘要

以下是每个弧边界前后各 2 章的完整摘要，请仔细检查：

{boundary_summaries}

### 弧边界验证要点

请逐一检查每个弧边界：

1. **弧的起点是否合理？** 该章是否确实是一个新故事单元的开始（新冲突、场景转换、时间跳跃等）？
2. **弧的终点是否合理？** 该章是否确实是一个故事单元的结束（冲突解决、阶段总结等）？
3. **边界是否应该前移或后移几章？** 比如实际的弧结束可能在前 2 章而不是当前标记的章节。

---

## 二、伏笔验证

### 当前伏笔汇总

#### 已回收伏笔
{resolved_foreshadowing}

#### 未回收伏笔（高重要性）
{unresolved_high}

#### 未回收伏笔（中/低重要性）
{unresolved_low}

### 各段扫描中的原始伏笔数据

{segment_foreshadowing}

### 需要验证的章节摘要

以下是需要回查的章节完整摘要（包含所有标记为"未回收"的伏笔相关章节以及后续可能回收的章节）：

{validation_summaries}

### 伏笔验证要点

请检查以下内容：

1. **误判为"未回收"的伏笔**：检查后续章节是否实际已回收了某些标记为"未回收"的伏笔
2. **遗漏的伏笔**：检查原始段扫描数据中是否有被全局融合遗漏的重要伏笔
3. **错误的章节标注**：伏笔的埋设/回收章节标注是否准确
4. **重要性评级**：是否有伏笔的重要性被低估或高估

---

## 输出格式

请直接输出一个合法的 JSON 对象，不要添加任何 markdown 代码块标记或额外解释：

{
  "arc_validations": {
    "validations": [
      {
        "arc": 1,
        "original_start": "chXXXX",
        "original_end": "chXXXX",
        "validated_start": "chXXXX",
        "validated_end": "chXXXX",
        "start_adjusted": false,
        "end_adjusted": false,
        "start_reason": "简述验证理由",
        "end_reason": "简述验证理由"
      }
    ],
    "issues": [
      "任何发现的问题（如两个弧之间有遗漏章节、弧名称不合适等）"
    ],
    "suggested_arc_names": [
      {"arc": 1, "original_name": "原名", "suggested_name": "建议名（如果需要调整）"}
    ]
  },
  "foreshadow_validations": {
    "corrections": [
      {
        "type": "reclassify",
        "from": "unresolved",
        "to": "resolved",
        "planted_ch": "chXXXX",
        "resolved_ch": "chXXXX",
        "content": "描述",
        "reason": "为什么重新分类"
      }
    ],
    "additions": [
      {
        "planted_ch": "chXXXX",
        "content": "描述",
        "importance": "high|medium|low",
        "resolved": false,
        "resolved_ch": null,
        "reason": "为什么需要补充"
      }
    ],
    "removals": [
      {
        "planted_ch": "chXXXX",
        "content": "描述",
        "reason": "为什么移除（如：不是真正的伏笔，只是普通叙述）"
      }
    ],
    "importance_changes": [
      {
        "planted_ch": "chXXXX",
        "content": "描述",
        "original_importance": "low",
        "new_importance": "high",
        "reason": "为什么调整重要性"
      }
    ]
  }
}
