你是专业的小说剧情分析师。你的任务是验证故事弧的边界划分是否准确。

## 当前弧划分方案

{arc_boundaries}

## 全书章节索引

{full_index}

## 边界附近章节的完整摘要

以下是每个弧边界前后各 5 章的完整摘要，请仔细检查：

{boundary_summaries}

## 验证任务

请逐一检查每个弧边界：

1. **弧的起点是否合理？** 该章是否确实是一个新故事单元的开始（新冲突、场景转换、时间跳跃等）？
2. **弧的终点是否合理？** 该章是否确实是一个故事单元的结束（冲突解决、阶段总结等）？
3. **边界是否应该前移或后移几章？** 比如实际的弧结束可能在前 2 章而不是当前标记的章节。

## 输出格式

请直接输出一个合法的 JSON 对象，不要添加任何 markdown 代码块标记或额外解释：

{
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
}
