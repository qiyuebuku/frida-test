你是专业的小说角色分析师。你的任务是对原始角色数据进行别名合并与分级。

## 原始角色数据

以下是从章节摘要和剧情层中 Python 预提取的角色数据（raw_census.json）：

{raw_census_json}

## 主线追踪（识别核心角色的参考）

{plot_lines_content}

## 任务

请完成以下工作：

### 1. 别名合并

识别同一角色的不同称呼，合并为一个条目。需要注意：
- 全名 vs 简称（如"李通崖" vs "通崖"）
- 姓名 vs 称号/绰号
- 不同关系下的称呼（"师父"在不同上下文可能指不同人）
- 改名/化名的情况

对于不确定的合并，标注 `"confidence": "low"`。

### 2. 角色分级

根据以下标准对角色分级：
- **core（核心角色）**：出场频次 ≥20 **或** 主线核心角色
- **important（重要角色）**：出场频次 5-19
- **minor（次要角色）**：出场频次 1-4

注意：由于当前数据只有少量章节，请根据角色在已有章节中的重要程度和主线关联度灵活判断。

### 3. 生成拼音文件名

为每个角色生成拼音格式的文件名（如"李木田" → "li_mutian"）。

## 输出格式

请直接输出一个合法的 JSON 对象，不要添加任何 markdown 代码块标记或额外解释：

{
  "characters": [
    {
      "canonical_name": "角色正名",
      "aliases": ["别名1", "别名2"],
      "identity": "身份简述",
      "classification": "core|important|minor",
      "first_appearance": "chXXXX",
      "last_appearance": "chXXXX",
      "appearance_count": 数字,
      "associated_plotlines": ["主线名称"],
      "file_name": "pinyin_name"
    }
  ],
  "alias_mapping": {
    "别名": "正名",
    ...
  },
  "classification_summary": {
    "core": ["角色A", "角色B"],
    "important": ["角色C"],
    "minor": ["角色D", "角色E"]
  }
}
