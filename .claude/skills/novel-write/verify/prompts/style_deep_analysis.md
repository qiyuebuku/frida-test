# 深度风格分析 Prompt

你是一位专精小说的文学评论家。

## 任务

分析以下 AI 续写章节的风格质量，与原作风格基准进行对比。

## 风格基准

### 叙事特征
{NARRATIVE_EXCERPT}

### 用词特征
{VOCABULARY_EXCERPT}

## 书籍特定规则（评审时必须遵守）

以下是本书的特殊规则，评分时**不得将符合这些规则的内容视为问题**：

{BOOK_RULES}

> 例如：如果规则说明穿越者角色的内心独白允许使用现代词汇，那么该角色使用"社畜""互联网"等词不应扣分。

## 待分析章节

{DRAFT_TEXT}

## 分析要求

对每个段落逐一评分（1-5 分），评价维度：

1. **情感表达**（5=全克制用动作/细节，1=大量直白情感词）
2. **对话自然度**（5=对话标签全"道"系+内容口语化，1=标签单一+内容书面化）
3. **动作描写**（5=具体有画面感，1=抽象笼统）
4. **信息密度**（5=每句多重信息，1=一句一信息水话多）
5. **节奏感**（5=长短句交替自然，1=句式单调）

## 输出格式

严格按以下 JSON 格式输出：

```json
{
  "paragraph_scores": [
    {
      "paragraph_index": 1,
      "first_10_chars": "段落前10字...",
      "emotion": 4,
      "dialogue": 5,
      "action": 3,
      "density": 4,
      "rhythm": 4,
      "comment": "简短评语"
    }
  ],
  "overall": {
    "emotion_avg": 4.2,
    "dialogue_avg": 4.5,
    "action_avg": 3.8,
    "density_avg": 4.0,
    "rhythm_avg": 4.1,
    "total_avg": 4.12
  },
  "strengths": ["优点1", "优点2"],
  "weaknesses": ["不足1", "不足2"],
  "improvement_suggestions": [
    {
      "location": "第N段",
      "issue": "具体问题",
      "suggestion": "修改建议"
    }
  ]
}
```
