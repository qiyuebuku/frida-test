# 通用小说写作 Agent 技术方案

> 基于小说正文+评论数据学习作者风格，实现风格仿写与续写的通用AI系统

---

## 一、项目概述

### 1.1 核心目标

构建一个**通用的小说写作 Agent**，可以：

1. **输入任意小说数据** → 学习其写作风格
2. **基于学习结果** → 续写/仿写新小说
3. **利用评论数据** → 优化写作方向

### 1.2 关键特性

- **非特定小说仿写**：不是为《玄鉴仙族》而写，而是通用框架
- **离线处理**：可接受较长的处理等待时间
- **评论数据利用**：高赞评论作为写作参考但不被绑架
- **大方向可控**：提前定好剧情大纲，细节灵活调整

---

## 二、数据处理流程

### 2.1 数据结构分析

```
小说数据/
├── 正文内容
│   ├── 章节标题
│   ├── 段落内容
│   └── 字数统计
├── 评论数据
│   ├── 段评（按段落组织）
│   ├── 章评（章节整体评论）
│   ├── 点赞数
│   └── 回复关系
└── 元数据
    ├── 章节ID
    ├── 书籍ID
    └── 时间戳
```

### 2.2 数据解析模块

```python
class NovelParser:
    """小说数据解析器"""

    def parse_novel(self, markdown_files):
        """解析 Markdown 格式小说数据"""
        for file in markdown_files:
            yield {
                'chapter_id': self.extract_chapter_id(file),
                'title': self.extract_title(file),
                'content': self.extract_content(file),
                'comments': self.extract_comments(file),
            }

    def extract_comments(self, content):
        """提取评论数据并过滤（点赞≥阈值）"""
        comments = []
        for comment in content.find_all('comments'):
            if comment.likes >= THRESHOLD:
                comments.append({
                    'content': comment.text,
                    'likes': comment.likes,
                    'paragraph_id': comment.paragraph_id,
                })
        return comments
```

---

## 三、风格学习方法

### 3.1 写作风格维度

基于《玄鉴仙族》分析，提取以下风格维度：

| 维度 | 提取方法 | 示例特征 |
|------|---------|---------|
| **开篇技巧** | 分析前10章 | 梦境切入、危机开场 |
| **语言风格** | 词频+句式分析 | 短句为主、具象比喻 |
| **对话风格** | 对话提取+聚类 | 推动情节、塑造性格 |
| **节奏控制** | 段落长度+情感波动 | 紧张-舒缓波浪式 |
| **人物塑造** | 角色行为分析 | 通过行为展现性格 |
| **悬念设计** | 关键词识别+结构分析 | 分层悬念 |
| **幽默元素** | 梗识别+位置分析 | 紧张后插入幽默 |

### 3.2 风格提取流程

```
原始小说数据
    ↓
分章节处理（避免上下文爆炸）
    ↓
并行提取各维度特征
    ↓
生成风格指纹文件
    ↓
写入长期记忆
```

### 3.3 风格指纹文件结构

```json
{
  "novel_id": "1035420986",
  "novel_name": "玄鉴仙族",
  "style_fingerprint": {
    "opening_style": "梦境切入+危机开场",
    "language_pattern": {
      "avg_sentence_length": 15.2,
      "metaphor_ratio": 0.23,
      "description_ratio": 0.4
    },
    "dialogue_pattern": {
      "dialogue_to_narrative_ratio": 0.35,
      "dialect_usage": false
    },
    "pacing_pattern": {
      "tension-relaxation_cycle": "wave",
      "avg_paragraphs_per_twist": 10
    },
    "character_pattern": {
      "death_suddenness": "high",
      "character_development": "behavior_based"
    }
  }
}
```

---

## 四、评论数据利用

### 4.1 评论价值分析

| 评论类型 | 价值 | 权重 | 使用方式 |
|---------|------|------|---------|
| 情感共鸣 | 识别触动人心的情节 | 高 | 学习哪些写法有效 |
| 细节发现 | 发现作者未注意的伏笔 | 中 | 完善伏笔设计 |
| 设定考据 | 补充世界观知识 | 中 | 丰富设定 |
| 幽默玩梗 | 识别哪些梗有效 | 低 | 适度使用 |

### 4.2 评论处理策略

```python
class CommentAnalyzer:
    """评论分析器"""

    def analyze_comments(self, comments):
        """分析评论数据"""
        results = {
            'emotional_triggers': self.find_emotional_triggers(comments),
            'detail_discoveries': self.find_detail_discoveries(comments),
            'successful_patterns': self.find_successful_patterns(comments),
        }
        return results

    def find_emotional_triggers(self, comments):
        """找出情感触发点"""
        high_like_comments = [c for c in comments if c.likes > 1000]
        return {
            'death_scenes': self.count_pattern(high_like_comments, ['刀', '死', '便当']),
            'warm_scenes': self.count_pattern(high_like_comments, ['暖', '感动']),
            'surprise_scenes': self.count_pattern(high_like_comments, ['反转', '没想到']),
        }
```

### 4.3 利用原则

1. **参考但不盲从**：高赞评论反映读者喜好，但不能完全按期待写
2. **反期待策略**：有时候违背读者期待的反转更精彩
3. **平衡商业与艺术**：既要让读者满意，也要保持作品格调

---

## 五、Agent架构设计

### 5.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                     写作 Agent                            │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │  学习模式   │  │  创作模式   │  │  优化模式   │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
├─────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────┐  │
│  │           风格指纹数据库 (长期记忆)                 │  │
│  └───────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ 数据解析器  │  │ 风格提取器  │  │ 内容生成器  │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 5.2 工作流程

#### 学习模式

```
用户上传小说数据
    ↓
解析器解析数据（分章节处理）
    ↓
提取器并行提取风格特征
    ↓
生成风格指纹文件
    ↓
存入长期记忆
    ↓
返回：学习完成，可开始创作
```

#### 创作模式

```
用户输入：风格指纹ID + 剧情大纲
    ↓
加载风格指纹
    ↓
根据大纲生成章节（逐章生成）
    ↓
应用风格特征（句式、节奏、对话）
    ↓
输出章节内容
```

#### 优化模式

```
用户输入：已生成内容 + 评论反馈
    ↓
分析反馈（情感触发、细节发现）
    ↓
调整风格权重
    ↓
重新生成/修改内容
```

---

## 六、核心功能模块

### 6.1 开篇生成器

```python
class OpeningGenerator:
    """开篇生成器"""

    def generate_opening(self, style_fingerprint, story_outline):
        """生成第一章"""
        if style_fingerprint['opening_style'] == '梦境切入':
            return self.generate_dream_opening(story_outline)
        elif style_fingerprint['opening_style'] == '危机开场':
            return self.generate_crisis_opening(story_outline)
        # ...

    def generate_dream_opening(self, outline):
        """生成梦境切入式开篇"""
        return {
            'content': '主角做了一个很长的梦...',
            'hooks': ['前世暗示', '当前危机', '悬念'],
        }
```

### 6.2 对话生成器

```python
class DialogueGenerator:
    """对话生成器"""

    def generate_dialogue(self, characters, context, style):
        """生成对话"""
        dialogue = []
        for char in characters:
            line = self.generate_line(char, context)
            if self.should_include_dialogue(line, style):
                dialogue.append(line)
        return dialogue

    def should_include_dialogue(self, line, style):
        """判断是否包含该对话（过滤无意义对话）"""
        return (
            line.reveals_character() or
            line.advances_plot() or
            line.builds_tension()
        )
```

### 6.3 节奏控制器

```python
class PacingController:
    """节奏控制器"""

    def control_pacing(self, content, style_fingerprint):
        """控制节奏"""
        current_tension = self.analyze_tension(content)
        target_tension = style_fingerprint['pacing_pattern']['target']

        if current_tension > target_tension:
            return self.add_relief(content)  # 添加缓解内容
        elif current_tension < target_tension:
            return self.add_tension(content)  # 添加紧张内容
```

---

## 七、大方向与灵活性

### 7.1 大方向制定

用户需要提供：

1. **核心冲突**：故事的主要矛盾
2. **角色设定**：主要角色及其关系
3. **世界观基础**：基本设定（可后续完善）
4. **大致剧情走向**：起承转合框架
5. **预期结局**：开放式/确定式

### 7.2 灵活调整

- **细节处理**：可随时调整
- **情节分支**：根据需要选择不同路径
- **角色命运**：根据剧情需要决定（不强制存活）

---

## 八、连贯性保证

### 8.1 设定一致性

```python
class ConsistencyChecker:
    """连贯性检查器"""

    def check_consistency(self, new_content, existing_content):
        """检查新内容是否与已有内容一致"""
        checks = {
            'character_consistency': self.check_characters(new_content, existing_content),
            'world_consistency': self.check_world_rules(new_content, existing_content),
            'timeline_consistency': self.check_timeline(new_content, existing_content),
        }
        return all(checks.values())
```

### 8.2 伏笔管理

```python
class ForeshadowingTracker:
    """伏笔跟踪器"""

    def track_foreshadowing(self, content):
        """跟踪伏笔"""
        foreshadowing = {
            'planted': self.find_planted_foreshadowing(content),
            'revealed': self.find_revealed_foreshadowing(content),
            'unresolved': self.get_unresolved_foreshadowing(content),
        }
        return foreshadowing
```

---

## 九、爽点触发机制

### 9.1 爽点类型

| 爽点类型 | 触发方式 | 示例 |
|---------|---------|------|
| 期待爽点 | 努力后有回报 | 修炼突破、获得宝物 |
| 打脸爽点 | 轻视者被反杀 | 反派吃瘪 |
| 危机爽点 | 绝境翻盘 | 以弱胜强 |
| 群像爽点 | 家族成员各自成长 | 多线并进 |

### 9.2 爽点生成原则

1. **必须铺垫**：爽点不能凭空出现
2. **节奏控制**：不要连续爽点
3. **适度反转**：有时候"不爽"更有冲击力
4. **避免低俗**：不是龙傲天式的无脑爽

---

## 十、技术实现路线

### 10.1 阶段划分

**第一阶段（数据层）**
- [x] 数据解析器实现
- [ ] 评论数据提取与清洗
- [ ] 风格特征提取算法

**第二阶段（学习层）**
- [ ] 风格指纹生成
- [ ] 评论价值评估
- [ ] 长期记忆管理

**第三阶段（创作层）**
- [ ] 开篇生成器
- [ ] 对话生成器
- [ ] 节奏控制器
- [ ] 连贯性检查

**第四阶段（优化层）**
- [ ] 评论反馈分析
- [ ] 风格动态调整
- [ ] 迭代优化

### 10.2 技术栈

```
后端：
- Python 3.10+
- FastAPI (API服务)
- SQLAlchemy (数据库)

数据处理：
- pandas (数据处理)
- jieba (中文分词)
- scikit-learn (特征提取)

AI能力：
- Claude API (内容生成)
- LangChain (Agent框架)

存储：
- SQLite (本地存储)
- 文件系统 (长期记忆)
```

---

## 十一、风险与挑战

### 11.1 技术风险

| 风险 | 影响 | 应对 |
|------|------|------|
| 上下文限制 | 处理长小说 | 分章节处理+长期记忆 |
| 风格提取不准 | 仿写效果差 | 多维度特征+人工校验 |
| 连贯性难保证 | 内容矛盾 | 设定追踪+一致性检查 |

### 11.2 内容风险

| 风险 | 影响 | 应对 |
|------|------|------|
| 过度迎合读者 | 作品格调下降 | 评论数据参考但不盲从 |
| 仿写痕迹明显 | 缺乏原创性 | 风格融合+创新元素 |
| 剧情逻辑漏洞 | 可读性下降 | 严格的一致性检查 |

---

## 十二、总结

### 核心价值

1. **通用性**：不针对特定小说，可学习任意作品风格
2. **数据驱动**：利用评论数据优化写作方向
3. **可控性**：大方向可控，细节灵活
4. **可迭代**：根据反馈持续优化

### 与传统写作辅助的区别

| 传统辅助 | 本系统 |
|---------|--------|
| 模板化 | 风格学习 |
| 无评论利用 | 评论数据驱动 |
| 一次性生成 | 迭代优化 |
| 单一风格 | 多风格融合 |

---

*技术方案版本：v1.0*
*创建日期：2026-02-07*
