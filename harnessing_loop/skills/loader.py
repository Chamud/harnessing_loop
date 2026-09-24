"""Skills: procedures in text, loaded on demand.

Layout: `<skills_dir>/<name>/SKILL.md` with frontmatter:

    ---
    name: verify
    description: Check the output against the request before finishing.
    when_to_use: before calling finish on any multi-file change
    ---
    (the procedure)

Only name, description and when_to_use are in the system prompt. The full
text is returned by the `skill` tool when invoked, and the model can write
new skills into the workspace skills dir with write_file.

スキル。テキストで書かれた手順を、必要なときに読み込む。

配置は `<skills_dir>/<name>/SKILL.md` で、上のようなフロントマターを付ける。

システムプロンプトに入るのは name、description、when_to_use だけである。全文は
`skill` ツールが呼ばれたときに返される。モデルは write_file でワークスペースの
skills ディレクトリに新しいスキルを書くこともできる。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..persistence.memory import parse_frontmatter
from ..tools.base import Tool, ToolContext, ToolResult

BUNDLED_DIR = Path(__file__).parent / "bundled"


@dataclass
class Skill:
    name: str
    description: str
    when_to_use: str
    path: Path

    def text(self) -> str:
        raw = self.path.read_text(encoding="utf-8", errors="replace")
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) == 3 else raw.strip()


class SkillSet:
    def __init__(self, dirs: list[Path] | None = None, *, include_bundled: bool = True):
        self.dirs = [Path(d) for d in (dirs or [])]
        if include_bundled:
            self.dirs.insert(0, BUNDLED_DIR)
        self.invoked: dict[str, str] = {}

    def discover(self) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        for d in self.dirs:
            if not d.is_dir():
                continue
            for sk in sorted(d.iterdir()):
                p = sk / "SKILL.md"
                if not p.is_file():
                    continue
                fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
                name = fm.get("name", sk.name)
                found[name] = Skill(name, fm.get("description", ""), fm.get("when_to_use", ""), p)
        return found

    def get(self, name: str) -> Skill | None:
        return self.discover().get(name)

    def listing_text(self) -> str:
        skills = self.discover()
        if not skills:
            return ""
        lines = ["Skills available through the skill tool:"]
        for s in skills.values():
            when = f" Use when: {s.when_to_use}" if s.when_to_use else ""
            lines.append(f"- {s.name}: {s.description}{when}")
        return "\n".join(lines)


class SkillTool(Tool):
    name = "skill"
    description = "Load a skill: a written procedure for a kind of task. Returns the full instructions."
    input_schema = {"type": "object", "properties": {"name": {"type": "string", "minLength": 1}}, "required": ["name"], "additionalProperties": False}
    category = "plan"
    read_only = True

    def __init__(self, skills: SkillSet):
        self.skills = skills

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        s = self.skills.get(input["name"])
        if s is None:
            return ToolResult.error(f"No skill named {input['name']!r}. Known: {', '.join(self.skills.discover()) or 'none'}")
        text = s.text()
        self.skills.invoked[s.name] = text
        return ToolResult.ok(f"<skill name=\"{s.name}\">\n{text}\n</skill>", skill_used=s.name)
