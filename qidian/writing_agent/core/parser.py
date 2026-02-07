"""
小说数据解析器
解析起点小说 Markdown 格式数据（正文+评论）
"""

import re
from pathlib import Path
from typing import Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class Comment:
    """评论数据结构"""
    user: str
    location: str
    date: str
    likes: int
    content: str
    replies: List[Dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "user": self.user,
            "location": self.location,
            "date": self.date,
            "likes": self.likes,
            "content": self.content,
            "replies": self.replies
        }


@dataclass
class Paragraph:
    """段落数据结构"""
    index: int
    content: str
    comments: List[Comment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "content": self.content,
            "comments": [c.to_dict() for c in self.comments]
        }

    @property
    def quality_score(self) -> float:
        """基于评论计算段落质量分数"""
        total_likes = sum(c.likes for c in self.comments)
        total_replies = sum(len(c.replies) for c in self.comments)
        return total_likes * 0.5 + total_replies * 50


@dataclass
class Chapter:
    """章节数据结构"""
    chapter_id: str
    chapter_name: str
    word_count: int
    paragraphs: List[Paragraph] = field(default_factory=list)
    chapter_comments: List[Comment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "chapter_name": self.chapter_name,
            "word_count": self.word_count,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "chapter_comments": [c.to_dict() for c in self.chapter_comments]
        }


class NovelDataParser:
    """小说数据解析器"""

    def __init__(self):
        self.book_id = None
        self.book_name = None

    def parse_novel_directory(self, dir_path: str) -> Dict[str, Any]:
        """
        解析整本小说数据

        Args:
            dir_path: 小说目录路径

        Returns:
            完整的小说数据字典
        """
        dir_path = Path(dir_path)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {dir_path}")

        # 获取所有章节文件
        md_files = sorted(dir_path.glob("*.md"))
        chapters = []

        for md_file in md_files:
            # 跳过版权信息等非章节文件
            if any(x in md_file.name for x in ["版权", "感谢", "说明", "单章"]):
                continue

            chapter_data = self.parse_chapter_file(md_file)
            if chapter_data:
                chapters.append(chapter_data)

        # 提取书籍信息
        self.book_id, self.book_name = self._extract_book_info(dir_path.name)

        total_words = sum(ch['word_count'] for ch in chapters)

        return {
            "metadata": {
                "book_id": self.book_id,
                "book_name": self.book_name,
                "total_chapters": len(chapters),
                "total_words": total_words
            },
            "chapters": chapters
        }

    def parse_chapter_file(self, file_path: Path) -> Dict[str, Any]:
        """
        解析单个章节文件

        Args:
            file_path: 章节文件路径

        Returns:
            章节数据字典
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取元数据
        chapter_id, chapter_name, word_count = self._parse_chapter_metadata(content)

        # 提取章节评论
        chapter_comments = self._parse_chapter_comments(content)

        # 提取段落数据
        paragraphs = self._parse_paragraphs(content)

        return {
            "chapter_id": chapter_id,
            "chapter_name": chapter_name,
            "word_count": word_count,
            "paragraphs": paragraphs,
            "chapter_comments": chapter_comments
        }

    def _parse_chapter_metadata(self, content: str) -> tuple:
        """解析章节元数据"""
        # 提取章节ID和名称（从文件名或标题）
        title_match = re.search(r'# (.+)', content)
        if title_match:
            full_title = title_match.group(1)
        else:
            full_title = "未知章节"

        # 提取字数
        word_count_match = re.search(r'字数[：:]\s*(\d+)', content)
        word_count = int(word_count_match.group(1)) if word_count_match else 0

        # 提取章节ID
        chapter_id_match = re.search(r'章节ID[：:]\s*(\d+)', content)
        chapter_id = chapter_id_match.group(1) if chapter_id_match else "unknown"

        return chapter_id, full_title, word_count

    def _parse_chapter_comments(self, content: str) -> List[Dict]:
        """解析章评"""
        comments = []

        # 查找章评区域
        chapter_comment_section = re.search(
            r'## 📖 章节评论\s+(.+?)(?=## 第\d段|## \d+、|$)',
            content,
            re.DOTALL
        )

        if not chapter_comment_section:
            return comments

        comment_text = chapter_comment_section.group(1)
        comments = self._parse_comments_block(comment_text)

        return comments

    def _parse_paragraphs(self, content: str) -> List[Dict]:
        """解析所有段落"""
        paragraphs = []

        # 匹配段落模式
        paragraph_pattern = re.compile(
            r'## 第(\d+)段\s+(.+?)(?=## 第\d+段|## \d+、|$)',
            re.DOTALL
        )

        for match in paragraph_pattern.finditer(content):
            para_index = int(match.group(1))
            para_content = match.group(2)

            # 分离正文和评论
            para_text = self._extract_paragraph_text(para_content)
            para_comments = self._parse_paragraph_comments(para_content)

            paragraphs.append({
                "index": para_index,
                "content": para_text,
                "comments": para_comments
            })

        return paragraphs

    def _extract_paragraph_text(self, para_content: str) -> str:
        """提取段落纯正文"""
        # 移除评论部分
        lines = para_content.split('\n')
        text_lines = []

        for line in lines:
            line = line.strip()
            # 跳过评论标记
            if line.startswith('### 💬 本段评论'):
                break
            # 跳过空行和标记行
            if not line or line.startswith('##'):
                continue
            text_lines.append(line)

        return '\n'.join(text_lines).strip()

    def _parse_paragraph_comments(self, para_content: str) -> List[Dict]:
        """解析段评"""
        comments = []

        # 查找段评区域
        comment_section = re.search(
            r'### 💬 本段评论[（(](\d+)[）)]条\s+(.+?)(?=## 第\d段|## \d+、|$)',
            para_content,
            re.DOTALL
        )

        if not comment_section:
            return comments

        comment_text = comment_section.group(2)
        comments = self._parse_comments_block(comment_text)

        return comments

    def _parse_comments_block(self, comment_text: str) -> List[Dict]:
        """解析评论块"""
        comments = []

        # 匹配单条评论
        comment_pattern = re.compile(
            r'\*\*(.+?)\*\*[（(](.+?)[）)]·\s*(.+?)\s*·\s*👍\s*(\d+)(?:\s*💬\s*回复)?\s*\*\*(.+?)\*\*[：:]\s*「(.+?)」(.+?)(?=!\*\*(.+?)\*\*[（(])',
            re.DOTALL
        )

        for match in comment_pattern.finditer(comment_text):
            user = match.group(1).strip()
            location = match.group(2).strip()
            date = match.group(3).strip()
            likes = int(match.group(4))
            content = match.group(7).strip()

            # 清理内容
            content = re.sub(r'\s*🖼️\s*\[图片\].*$', '', content, flags=re.MULTILINE)
            content = re.sub(r'\s*\[fn=\d+\]', '', content)
            content = content.strip('> ')

            if not content:
                continue

            comment = Comment(
                user=user,
                location=location,
                date=date,
                likes=likes,
                content=content
            )

            # TODO: 解析回复关系
            # replies = self._parse_replies(...)

            comments.append(comment.to_dict())

        return comments

    def _extract_book_info(self, dir_name: str) -> tuple:
        """从目录名提取书籍信息"""
        # 目录名格式: ID_书名_markdown
        parts = dir_name.replace('_markdown', '').split('_', 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "unknown", dir_name


# 使用示例
if __name__ == "__main__":
    parser = NovelDataParser()

    # 解析单章测试
    test_file = Path("/home/yuyang/frida-test/qidian/scripts/output/1035420986_玄鉴仙族_markdown/0015_730944635_第一章 初入.md")

    if test_file.exists():
        chapter = parser.parse_chapter_file(test_file)
        print(f"解析章节: {chapter['chapter_name']}")
        print(f"段落数: {len(chapter['paragraphs'])}")
        print(f"评论数: {sum(len(p['comments']) for p in chapter['paragraphs'])}")

        # 打印第一段
        if chapter['paragraphs']:
            first_para = chapter['paragraphs'][0]
            print(f"\n第一段内容:\n{first_para['content'][:100]}...")
            if first_para['comments']:
                print(f"第一段评论数: {len(first_para['comments'])}")
