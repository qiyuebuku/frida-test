你是一个专业的小说风格分析师。你的任务是从原文中深度提取写作风格特征。

## 弧信息

{arc_info}

## 分析批次

{batch_label}

## 原文数据

以下是抽样的章节原文。部分章节标注了 **高赞段落**（读者用点赞投票选出的"最好写法"）。

{batch_text}

## 分析任务

请从以下 7 个维度分析原文写作风格，输出 JSON。

### 1. narrative_voice（叙事视角）
- 主视角（第几人称）
- 是否有视角切换
- 视角切换的时机和方式

### 2. scene_description（场景描写偏好）
- 环境描写的详略和手法
- 动作描写的风格
- 心理描写的方式
- 对比/反差手法的使用

### 3. emotion_expression（情感表达技巧）
- 是否克制（用动作/细节替代直白情感词）
- 常用的替代手法及原文例句
- 禁忌写法（原作中刻意避免的表达方式）

### 4. imagery（意象/修辞）
- 常用意象及象征含义
- 修辞手法偏好
- 典型例句

### 5. vocabulary（用词习惯）
- 古典/口语/书面的比例感
- 特征性口语表达
- 特征性古典用语
- 对话用语风格

### 6. writing_techniques（写作技巧）
- 信息密度手法（一句话承载多重信息的例子）
- 细节暗示手法（用具体细节暗示状态的例子）
- 视觉反差手法（对比色彩/明暗的例子）
- 其他特色技巧

### 7. reader_validated（读者验证分析）
如果本批章节中有标注 **高赞段落**，请分析：
- 这些段落用了什么具体写法
- 为什么能触发读者情绪（成功的机制是什么）
- 这种写法如何复用到续写中

如果本批没有高赞段落，返回空数组。

## 输出格式

严格输出以下 JSON 格式，不要有其他内容：

```json
{
    "batch": "{batch_label}",
    "chapters": ["ch0001", "ch0002"],
    "narrative_voice": {
        "pov": "第三人称有限",
        "pov_switches": true,
        "switch_pattern": "通过分场景切换视角角色",
        "examples": [
            {"chapter": "ch0001", "text": "原文片段", "note": "说明"}
        ]
    },
    "scene_description": {
        "style": "重细节暗示，轻直白描写",
        "techniques": [
            {"name": "视觉反差", "example": "原文片段", "chapter": "ch0001"}
        ],
        "environment_detail_level": "中等（点到为止，不铺陈）",
        "action_style": "简洁有力",
        "psychology_style": "克制型——用外在行为暗示内心"
    },
    "emotion_expression": {
        "style": "高度克制，以动作代替情感词",
        "preferred_techniques": [
            {"name": "动作替代", "example": "深深叹了口气", "chapter": "ch0002", "replaces": "感到无奈"}
        ],
        "forbidden_patterns": ["心中涌起一股...", "不由得感到..."],
        "indirect_ratio_note": "间接表达远多于直接表达"
    },
    "imagery": [
        {"image": "月光/月华", "frequency": "高", "usage": "修炼/力量象征", "example": "原文片段"}
    ],
    "vocabulary": {
        "formality": "古典口语混合",
        "colloquial_examples": ["睁着个眼", "睡得死沉"],
        "classical_examples": ["寅时", "卯时"],
        "dialogue_style": "简短有力，少有长段独白",
        "forbidden_modern_words": ["手机", "电脑"]
    },
    "writing_techniques": [
        {
            "name": "信息密度",
            "description": "一句话包含多重信息",
            "example": "他整整三天没睡好觉了，看着身边睡得死沉的妇人",
            "chapter": "ch0002",
            "analysis": "同时传达：时间跨度（三天）+ 人物状态（失眠）+ 环境（旁边有妇人）+ 妇人状态（睡熟）"
        }
    ],
    "reader_validated": [
        {
            "chapter": "ch0002",
            "paragraph": 3,
            "text": "段落原文",
            "likes": 32803,
            "why_successful": "分析成功原因",
            "techniques_used": ["视觉反差", "细节暗示", "口语化"],
            "reusable_pattern": "如何在续写中复用"
        }
    ]
}
```

## 注意事项

1. **每个维度必须有原文例句**，不能只有抽象描述
2. **例句标注章节出处**（如 ch0001），方便后续查证
3. **高赞段落分析要深入**：不只是"写得好"，要具体到用了什么手法、替代了什么常规写法
4. **关注跨章节的一致性**：如果多个章节都用了同一技巧，合并说明
5. **forbidden 类字段要具体**：不能只说"避免直白"，要列出具体的禁忌表达模式
6. 如果某个维度的证据不足（如只有 1-2 章数据），标注"样本有限"

## ⚠️ 输出方式

**使用 Write 工具直接将 JSON 写入文件**：

```
文件路径：{output_file}
内容：上述 JSON 对象（无需代码块标记，直接写纯 JSON）
```

完成后简短回复"已完成风格分析并保存"。
