"""Meta tools: the model works on its own tool set.

tool_search   loads deferred tool schemas on demand, so a profile can carry
              many tools without paying for all of them every turn
define_tool   lets the model write a new tool. The code is stored under
              `tools/` in the workspace and always runs through the sandbox,
              so a model-made tool can do exactly what run_python can do and
              nothing more. Definitions persist per workspace.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from ..base import Tool, ToolContext, ToolResult, validate_schema

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RESERVED = {"finish", "subagent", "define_tool", "tool_search", "shell", "run_python", "read_file", "write_file", "edit_file"}


class ToolSearch(Tool):
    name = "tool_search"
    description = (
        "Load deferred tools. Query forms: 'select:name1,name2' to load by name, "
        "or keywords to search names and descriptions."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}, "max_results": {"type": "integer", "minimum": 1, "maximum": 20}},
        "required": ["query"],
        "additionalProperties": False,
    }
    category = "meta"
    read_only = True

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        reg = ctx.registry
        q = input["query"].strip()
        deferred = reg.deferred_names()
        if q.startswith("select:"):
            names = [n.strip() for n in q[len("select:") :].split(",") if n.strip()]
            missing = [n for n in names if n not in reg]
            loaded = reg.load_deferred(names)
            msg = f"Loaded: {', '.join(loaded) or 'none'}."
            if missing:
                msg += f" Unknown: {', '.join(missing)}."
            return ToolResult.ok(msg + "\n" + _schemas_text(reg, loaded))
        terms = [t.lower() for t in re.split(r"\s+", q) if t]
        scored = []
        for n in deferred:
            t = reg.get(n)
            hay = f"{n} {t.description} {t.search_hint}".lower()
            score = sum(2 if term in n else (1 if term in hay else 0) for term in terms)
            if score:
                scored.append((score, n))
        scored.sort(reverse=True)
        picked = [n for _, n in scored[: int(input.get("max_results", 5))]]
        if not picked:
            return ToolResult.ok(f"No deferred tools matched. Deferred tools: {', '.join(deferred) or 'none'}")
        reg.load_deferred(picked)
        return ToolResult.ok(f"Loaded: {', '.join(picked)}.\n" + _schemas_text(reg, picked))


def _schemas_text(reg: Any, names: list[str]) -> str:
    return "\n".join(json.dumps(reg.get(n).schema(), ensure_ascii=False) for n in names if n in reg)


class DynamicTool(Tool):
    """A tool whose body is a Python file in the workspace, run in the sandbox."""

    category = "exec"

    def __init__(self, name: str, description: str, input_schema: dict[str, Any], rel_path: str):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.rel_path = rel_path

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [f"dynamic:{self.name}"]

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.sandbox is None:
            return ToolResult.error("No sandbox configured; dynamic tools cannot run.")
        path = ctx.sandbox.path_inside(self.rel_path)
        code = (
            "import json, sys, runpy\n"
            f"ns = runpy.run_path({path!r})\n"
            "out = ns['run'](json.loads(sys.stdin.read() or '{}'))\n"
            "print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str))\n"
        )
        res = ctx.sandbox.run_python(code, stdin=json.dumps(input, ensure_ascii=False))
        body = (res.stdout or "").rstrip()
        if not res.ok:
            return ToolResult.error(f"{self.name} failed (exit {res.exit_code}):\n{body}\n{res.stderr}")
        return ToolResult.ok(body or "(no output)")


def dynamic_tools_dir(workspace: Path) -> Path:
    d = workspace / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_dynamic_tools(registry: Any, workspace: Path) -> list[str]:
    """Register tools defined in earlier runs of this workspace."""
    loaded = []
    d = workspace / "tools"
    if not d.is_dir():
        return loaded
    for meta_path in sorted(d.glob("*.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            py = d / f"{meta['name']}.py"
            if not py.exists():
                continue
            registry.add(DynamicTool(meta["name"], meta["description"], meta["input_schema"], f"tools/{meta['name']}.py"), dynamic=True)
            loaded.append(meta["name"])
        except (OSError, KeyError, json.JSONDecodeError):
            continue
    return loaded


class DefineTool(Tool):
    name = "define_tool"
    description = (
        "Create a new tool from Python code. The code must define `run(input: dict) -> str | dict`. "
        "It runs in the sandbox with the same limits as run_python. The tool is available from the next turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "snake_case, 3-40 chars"},
            "description": {"type": "string", "minLength": 10},
            "input_schema": {"type": "object"},
            "code": {"type": "string", "minLength": 10},
        },
        "required": ["name", "description", "input_schema", "code"],
        "additionalProperties": False,
    }
    category = "meta"

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [input.get("name", "")]

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        return [f"tools/{input.get('name', '')}.py"]

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        n = input["name"]
        if not _NAME.match(n):
            return "Tool name must be snake_case, 3 to 40 characters, starting with a letter."
        if n in RESERVED or (n in ctx.registry and n not in ctx.registry.dynamic):
            return f"Name {n!r} is reserved or already taken by a built-in tool."
        if "def run(" not in input["code"]:
            return "Code must define a function `run(input)`."
        err = validate_schema({"type": "object", "properties": {"type": {"type": "string"}}}, input["input_schema"])
        if err or input["input_schema"].get("type") != "object":
            return "input_schema must be a JSON schema with type 'object'."
        if ctx.sandbox is None:
            return "No sandbox configured; dynamic tools cannot run."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        d = dynamic_tools_dir(ctx.workspace)
        n = input["name"]
        (d / f"{n}.py").write_text(input["code"], encoding="utf-8")
        meta = {"name": n, "description": input["description"], "input_schema": input["input_schema"]}
        (d / f"{n}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        ctx.registry.add(DynamicTool(n, input["description"], input["input_schema"], f"tools/{n}.py"), dynamic=True)
        if n not in ctx.state.dynamic_tools:
            ctx.state.dynamic_tools.append(n)
        return ToolResult.ok(f"Defined tool {n}. It is available from your next turn. Source: tools/{n}.py", tool_defined=n)


def make() -> list[Tool]:
    return [ToolSearch(), DefineTool()]
