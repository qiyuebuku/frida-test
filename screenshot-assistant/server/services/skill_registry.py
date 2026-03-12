import re
from pathlib import Path
from dataclasses import dataclass, field

import yaml


@dataclass
class CommandArg:
    name: str
    description: str
    required: bool = False


@dataclass
class PipelineStep:
    step: str
    handler: str
    description: str = ""
    prompt_template: str = ""


@dataclass
class CommandInfo:
    id: str
    name: str
    description: str = ""
    input: str = "none"  # none / screenshot / text / file
    capture_types: list[str] = field(default_factory=list)
    executor: str = "claude"  # pipeline / claude
    estimated_time: int = 60
    args: list[CommandArg] = field(default_factory=list)
    pipeline: list[PipelineStep] = field(default_factory=list)

    @property
    def floatable(self) -> bool:
        return self.input == "screenshot"


@dataclass
class SkillInfo:
    name: str
    display_name: str
    icon: str
    description: str
    category: str
    commands: list[CommandInfo]
    path: str

    @property
    def command_count(self) -> int:
        return len(self.commands)

    def get_command(self, command_id: str) -> CommandInfo | None:
        for cmd in self.commands:
            if cmd.id == command_id:
                return cmd
        return None

    def to_summary(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "description": self.description,
            "category": self.category,
            "command_count": self.command_count,
        }

    def to_detail(self) -> dict:
        return {
            **self.to_summary(),
            "commands": [
                {
                    "id": cmd.id,
                    "name": cmd.name,
                    "description": cmd.description,
                    "input": cmd.input,
                    "capture_types": cmd.capture_types,
                    "executor": cmd.executor,
                    "estimated_time": cmd.estimated_time,
                    "floatable": cmd.floatable,
                    "args": [
                        {"name": a.name, "description": a.description, "required": a.required}
                        for a in cmd.args
                    ],
                }
                for cmd in self.commands
            ],
        }


def _parse_frontmatter(file_path: Path) -> dict | None:
    """解析 SKILL.md 的 YAML frontmatter"""
    text = file_path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?\n)---", text, re.DOTALL)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None


def _parse_command(raw: dict) -> CommandInfo | None:
    """将 YAML 中的命令字典转为 CommandInfo"""
    if "id" not in raw or "name" not in raw:
        print(f"[SkillRegistry] skip command: missing id or name: {raw}", flush=True)
        return None
    args = [CommandArg(**a) for a in raw.get("args", [])]
    pipeline = [PipelineStep(**s) for s in raw.get("pipeline", [])]
    capture_types = raw.get("capture_types", [])
    # 兼容单数 capture_type
    if not capture_types and "capture_type" in raw:
        capture_types = [raw["capture_type"]]
    return CommandInfo(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        input=raw.get("input", "none"),
        capture_types=capture_types,
        executor=raw.get("executor", "claude"),
        estimated_time=raw.get("estimated_time", 60),
        args=args,
        pipeline=pipeline,
    )


class SkillRegistry:
    """扫描 skills 目录，解析 SKILL.md，提供 Skill 查询"""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: dict[str, SkillInfo] = {}
        self.scan()

    def scan(self):
        """扫描所有 SKILL.md，解析 frontmatter"""
        self.skills.clear()
        skills_path = Path(self.skills_dir)
        if not skills_path.exists():
            print(f"[SkillRegistry] skills dir not found: {self.skills_dir}", flush=True)
            return

        for skill_dir in sorted(skills_path.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta = _parse_frontmatter(skill_md)
            if not meta or "commands" not in meta:
                # 没有 commands 的 SKILL.md 跳过（可能是纯 Claude Skill）
                continue

            # 验证必填字段
            if "name" not in meta:
                print(f"[SkillRegistry] skip {skill_dir.name}: missing 'name' in frontmatter", flush=True)
                continue

            commands = [c for c in (_parse_command(cmd) for cmd in meta["commands"]) if c is not None]
            skill = SkillInfo(
                name=meta["name"],
                display_name=meta.get("display_name", meta["name"]),
                icon=meta.get("icon", "star"),
                description=meta.get("description", ""),
                category=meta.get("category", "other"),
                commands=commands,
                path=str(skill_dir),
            )
            self.skills[skill.name] = skill
            print(f"[SkillRegistry] loaded: {skill.name} ({skill.command_count} commands)", flush=True)

        print(f"[SkillRegistry] total: {len(self.skills)} skills", flush=True)

    def list_skills(self) -> list[SkillInfo]:
        return list(self.skills.values())

    def get_skill(self, name: str) -> SkillInfo | None:
        return self.skills.get(name)
