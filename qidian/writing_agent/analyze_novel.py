#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析玄鉴仙族小说内容，提取人物关系、世界观设定、故事线等信息
"""
import os
import re
import json
from collections import defaultdict
from pathlib import Path

# 章节目录
CHAPTER_DIR = "/home/yuyang/frida-test/qidian/scripts/output/1035420986_玄鉴仙族_markdown"

# 修仙境界（按顺序）
REALM_LEVELS = [
    "胎息", "练气", "筑基", "紫府", "金丹", "元婴", "化神", "炼虚", "合体", "大乘", "渡劫"
]

class NovelAnalyzer:
    def __init__(self):
        self.characters = {}  # 人物信息
        self.relationships = defaultdict(set)  # 人物关系
        self.realms = {}  # 修仙境界信息
        self.locations = {}  # 地点信息
        self.events = []  # 重要事件
        self.timeline = []  # 时间线
        self.emotional_scenes = []  # 感人场景

    def extract_character_info(self, content, chapter_name, chapter_num):
        """提取人物信息"""
        # 提取李家人物
        li_family_patterns = [
            r'李([木田玄渊曦明昭宏达进承福景启文武]盛|云[莲萍]|[天元]南?|玄[鉴真]?|叶[盛平]?|长[生子]?|伯仲叔季|承[福禄]|万[钟楼]|景[和]?|启[祥]|文[斌]|武[杰])',
        ]

        # 查找"李XX"格式的人物
        li_pattern = r'李(\w{1,3})'
        for match in re.finditer(li_pattern, content):
            name = match.group(1)
            full_name = f"李{name}"

            # 记录人物
            if full_name not in self.characters:
                self.characters[full_name] = {
                    "name": full_name,
                    "family": "李家",
                    "appearances": [],
                    "status": "unknown",
                    "realm": None,
                    "relations": {}
                }

            # 记录出现章节
            if chapter_num not in self.characters[full_name]["appearances"]:
                self.characters[full_name]["appearances"].append(chapter_num)

    def extract_relationships(self, content, chapter_name, chapter_num):
        """提取人物关系"""
        # 父子关系
        father_son = r'(李\w{1,3})[之次](子|婿|孙)'
        for match in re.finditer(father_son, content):
            parent = match.group(1)
            relation = match.group(2)
            self.relationships[parent].add(f"{relation}关系")

        # 配偶关系
        spouse_patterns = [
            r'(李\w{1,3})(?:娶|嫁)(?:了?)([萧杨王赵]\w{1,3})',
            r'([萧杨王赵]\w{1,3})(?:嫁予?|嫁给)(李\w{1,3})',
        ]

        # 兄弟关系
        brother_patterns = [
            r'(李\w{1,3})(?:之弟|之兄)',
            r'兄长?(?:李\w{1,3})',
            r'弟弟?(?:李\w{1,3})',
        ]

    def extract_realm_info(self, content):
        """提取修仙境界信息"""
        realm_info = {}

        for realm in REALM_LEVELS:
            # 查找关于该境界的描述
            pattern = rf'{realm}(?:期?|境?|修士|修为|境界)'
            matches = re.finditer(pattern, content)
            for match in matches:
                context = content[max(0, match.start()-50):min(len(content), match.end()+50)]
                if realm not in realm_info:
                    realm_info[realm] = []
                realm_info[realm].append(context.strip())

        return realm_info

    def extract_locations(self, content):
        """提取地点信息"""
        locations = []

        # 常见地点模式
        location_patterns = [
            r'(望月湖|黎山|青云宗|玄天宗|金丹宗|紫府宗|万法山|黑风洞|白骨山)',
            r'(李家(?:堡|寨|村|镇))',
            r'(萧家(?:堡|寨|村|镇))',
        ]

        for pattern_group in location_patterns:
            for match in re.finditer(pattern_group, content):
                loc = match.group(1)
                if loc not in locations:
                    locations.append(loc)

        return locations

    def extract_deaths(self, content, chapter_name, chapter_num):
        """提取死亡事件"""
        death_patterns = [
            r'(李\w{1,3})(?:逝|亡|死|陨落|战死|被害|被杀)',
            r'(?:击杀|斩杀|杀害|杀死)(?:了?)(李\w{1,3})',
        ]

        for pattern in death_patterns:
            for match in re.finditer(pattern, content):
                character = match.group(1)
                # 获取上下文
                start = max(0, match.start()-100)
                end = min(len(content), match.end()+100)
                context = content[start:end]

                self.events.append({
                    "type": "death",
                    "character": character,
                    "chapter": chapter_name,
                    "chapter_num": chapter_num,
                    "context": context.strip()
                })

                # 更新人物状态
                if character in self.characters:
                    self.characters[character]["status"] = "deceased"
                    self.characters[character]["death_chapter"] = chapter_num

    def extract_emotional_scenes(self, content, chapter_name, chapter_num):
        """提取感人场景"""
        emotional_keywords = [
            "吾儿速来",
            "幸得一见",
            "候君多时",
            "父亲",
            "母亲",
            "勿念",
            "珍重",
            "来世",
        ]

        scenes = []
        for keyword in emotional_keywords:
            if keyword in content:
                # 找到包含关键词的段落
                paragraphs = content.split('\n\n')
                for i, para in enumerate(paragraphs):
                    if keyword in para:
                        scenes.append({
                            "keyword": keyword,
                            "content": para.strip(),
                            "chapter": chapter_name,
                            "chapter_num": chapter_num
                        })

        return scenes

    def analyze_chapter(self, filepath):
        """分析单个章节"""
        filename = os.path.basename(filepath)

        # 解析文件名获取章节号和名称
        # 格式: 0015_730944635_第一章 初入.md
        match = re.match(r'(\d+)_(\d+)_(.+)\.md', filename)
        if not match:
            return None

        chapter_num = int(match.group(1))
        chapter_id = match.group(2)
        chapter_name = match.group(3)

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # 提取各类信息
            self.extract_character_info(content, chapter_name, chapter_num)
            self.extract_relationships(content, chapter_name, chapter_num)
            self.extract_deaths(content, chapter_name, chapter_num)

            scenes = self.extract_emotional_scenes(content, chapter_name, chapter_num)
            self.emotional_scenes.extend(scenes)

            # 提取章节摘要（前500字）
            summary = content[:500].strip() if len(content) > 500 else content.strip()

            return {
                "num": chapter_num,
                "id": chapter_id,
                "name": chapter_name,
                "filepath": filepath,
                "summary": summary
            }

        except Exception as e:
            print(f"Error reading {filename}: {e}")
            return None

    def analyze_all(self):
        """分析所有章节"""
        chapters = []

        # 获取所有md文件并排序
        md_files = sorted(Path(CHAPTER_DIR).glob("*.md"))

        print(f"Found {len(md_files)} chapter files")

        for filepath in md_files:
            chapter_info = self.analyze_chapter(str(filepath))
            if chapter_info:
                chapters.append(chapter_info)

            if len(chapters) % 100 == 0:
                print(f"Processed {len(chapters)} chapters...")

        return chapters

    def generate_family_tree(self):
        """生成家族谱系"""
        # 按名字中辈分字分类
        generations = {
            "木田": 0,  # 第一代
            "玄": 1,    # 第二代
            "渊": 2,    # 第三代
            "曦": 3,    # 第四代
            "明": 4,    # 第五代
            "昭": 5,    # 第六代
            "宏": 6,    # 第七代
        }

        family_tree = defaultdict(list)

        for char_name, char_info in self.characters.items():
            # 判断辈分
            gen_char = None
            for gen in generations:
                if gen in char_name:
                    gen_char = gen
                    break

            if gen_char:
                family_tree[gen_char].append(char_info)

        return family_tree, generations

    def save_results(self, output_dir):
        """保存分析结果"""
        os.makedirs(output_dir, exist_ok=True)

        # 保存人物信息
        with open(os.path.join(output_dir, "characters.json"), 'w', encoding='utf-8') as f:
            json.dump(self.characters, f, ensure_ascii=False, indent=2)

        # 保存事件
        with open(os.path.join(output_dir, "events.json"), 'w', encoding='utf-8') as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

        # 保存感人场景
        with open(os.path.join(output_dir, "emotional_scenes.json"), 'w', encoding='utf-8') as f:
            json.dump(self.emotional_scenes, f, ensure_ascii=False, indent=2)

        print(f"Results saved to {output_dir}")


def main():
    analyzer = NovelAnalyzer()
    chapters = analyzer.analyze_all()

    print(f"\n分析完成!")
    print(f"总章节数: {len(chapters)}")
    print(f"识别人物: {len(analyzer.characters)}")
    print(f"死亡事件: {len(analyzer.events)}")
    print(f"感人场景: {len(analyzer.emotional_scenes)}")

    # 保存结果
    analyzer.save_results("/home/yuyang/frida-test/qidian/writing_agent")

    # 生成家族谱系
    family_tree, generations = analyzer.generate_family_tree()

    print("\n=== 家族辈分统计 ===")
    for gen in sorted(generations.keys(), key=lambda x: generations[x]):
        count = len(family_tree[gen])
        print(f"{gen}字辈: {count}人")


if __name__ == "__main__":
    main()
