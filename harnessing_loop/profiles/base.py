"""Profiles: one YAML file is the whole difference between a chat agent and
an application.

    profile = load_profile("coder")                 # bundled
    profile = load_profile("./my_profile.yaml")     # your own
    runtime = profile.build(workspace="./work", model="bedrock:...")
    Loop(runtime).run("do the thing")

Everything a profile can set is documented in `template_app.yaml`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import yaml

from ..context.system_prompt import assemble_static_prompt
from ..core.deps import Deps
from ..core.errors import ConfigError
from ..core.events import EventBus
from ..core.runtime import Runtime
from ..core.state import RunConfig
from ..llm import make_client
from ..persistence.checkpoints import Checkpoints
from ..persistence.memory import MemoryDir
from ..persistence.transcript import Transcript
from ..progress.gates import Gates, evidence, file_exists, fresh_file, json_field, todos_done
from ..safety.guards import strip_dangerous_allow_rules
from ..safety.hooks import HookRegistry
from ..safety.permissions import MODES, PermissionContext
from ..safety.redact import Redactor
from ..safety.rules import parse_rules
from ..sandbox.policy import SandboxPolicy
from ..sandbox.startup_check import check_sandbox, make_sandbox
from ..skills.loader import SkillSet, SkillTool
from ..tools.packs import make_pack
from ..tools.packs.meta import load_dynamic_tools
from ..tools.registry import Registry

PROFILES_DIR = Path(__file__).parent


@dataclass
class Profile:
    name: str
    description: str = ""
    model: str = "fake"
    system_prompt: str = "You are a careful assistant."
    tool_packs: list[str] = field(default_factory=lambda: ["files"])
    exclude_tools: list[str] = field(default_factory=list)
    defer_tools: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=lambda: {"mode": "default"})
    sandbox: dict[str, Any] = field(default_factory=dict)
    web: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    phases: list[str] = field(default_factory=list)
    phase_evidence: dict[str, str] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    require_finish: bool = False
    hooks: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] = field(default_factory=dict)
    skills: dict[str, Any] = field(default_factory=dict)
    transcript: bool = True
    checkpoints: bool = True
    cache_ttl: str | None = None
    source: Path | None = None
    python_hooks: list[tuple[str, Callable, str | None]] = field(default_factory=list, repr=False)
    extra_tools: list[Any] = field(default_factory=list, repr=False)
    extra_gates: dict[str, Any] = field(default_factory=dict, repr=False)

    # ---- loading ------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any], source: Path | None = None) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown profile keys: {sorted(unknown)}")
        p = cls(**data)
        p.source = source
        if p.permissions.get("mode", "default") not in MODES:
            raise ConfigError(f"unknown permission mode {p.permissions.get('mode')!r}; use one of {MODES}")
        sp = p.system_prompt
        if isinstance(sp, str) and sp.endswith(".md") and source is not None and (source.parent / sp).exists():
            p.system_prompt = (source.parent / sp).read_text(encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path) -> "Profile":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, Path(path))

    def with_overrides(self, **kw: Any) -> "Profile":
        return replace(self, **kw)

    # ---- building ---------------------------------------------------------------------------
    def build(
        self,
        workspace: str | Path,
        *,
        model: Any = None,
        ask_handler: Callable[[str, dict[str, Any], str], bool] | None = None,
        events: EventBus | None = None,
        deps: Deps | None = None,
        extra: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Runtime:
        ws = Path(workspace).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        lim = self.limits
        ctxcfg = self.context
        config = RunConfig(
            model=self.model,
            max_turns=int(lim.get("max_turns", 200)),
            max_cost_usd=lim.get("max_cost_usd"),
            max_output_tokens=int(lim.get("max_output_tokens", 16000)),
            context_window_tokens=int(lim.get("context_window_tokens", 200_000)),
            thinking=str(lim.get("thinking", "adaptive")),
            thinking_budget_tokens=int(lim.get("thinking_budget_tokens", 8000)),
            temperature=lim.get("temperature"),
            compact_enabled=bool(ctxcfg.get("compact", True)),
            compact_buffer_tokens=int(ctxcfg.get("compact_buffer_tokens", 13_000)),
            blocking_buffer_tokens=int(ctxcfg.get("blocking_buffer_tokens", 3_000)),
            compact_max_output_tokens=int(ctxcfg.get("compact_max_output_tokens", 20_000)),
            compact_max_failures=int(ctxcfg.get("compact_max_failures", 3)),
            microcompact_keep_recent=int(ctxcfg.get("microcompact_keep_recent", 5)),
            microcompact_min_tokens=int(ctxcfg.get("microcompact_min_tokens", 40_000)),
            tool_result_max_chars=int(ctxcfg.get("tool_result_max_chars", 50_000)),
            tool_results_budget_per_message=int(ctxcfg.get("tool_results_budget_per_message", 200_000)),
            max_tool_concurrency=int(lim.get("max_tool_concurrency", 10)),
            stuck_repeat_limit=int(lim.get("stuck_repeat_limit", 3)),
            stuck_no_evidence_turns=int(lim.get("stuck_no_evidence_turns", 12)),
            allow_unsafe_local=bool(self.sandbox.get("allow_unsafe_local", False)),
        )

        # model
        client = model if model is not None and not isinstance(model, str) else make_client(model or self.model, cache_ttl=self.cache_ttl)

        # tools
        registry = Registry()
        for pack in self.tool_packs:
            for t in make_pack(pack):
                registry.add(t)
        for t in self.extra_tools:
            registry.add(t)
        for n in self.exclude_tools:
            registry.remove(n)
        for n in self.defer_tools:
            t = registry.get(n)
            if t is not None:
                t.defer = True
        load_dynamic_tools(registry, ws)

        # skills
        skills = None
        if self.skills.get("enabled", False) or self.skills.get("dirs"):
            dirs = [ws / d if not Path(d).is_absolute() else Path(d) for d in self.skills.get("dirs", ["skills"])]
            skills = SkillSet(dirs, include_bundled=bool(self.skills.get("bundled", True)))
            registry.add(SkillTool(skills))

        # permissions
        rules = parse_rules({k: self.permissions.get(k, []) for k in ("allow", "deny", "ask")})
        mode = self.permissions.get("mode", "default")
        dropped: list = []
        if self.permissions.get("strip_dangerous_allows", False):
            # Opt-in: remove allow rules that grant arbitrary code execution (python *, bash *, ...).
            # Useful when rules come from untrusted config. Off by default because an explicit
            # allow written by the operator is a decision, and the sandbox is the real boundary.
            rules, dropped = strip_dangerous_allow_rules(rules)
        removed = registry.apply_deny_rules(rules)
        permissions = PermissionContext(mode=mode, rules=rules, workspace=ws, ask_handler=ask_handler)

        # sandbox
        sandbox = None
        if self.sandbox:
            sb = self.sandbox
            policy = SandboxPolicy(
                env_allow=list(sb.get("env_allow", [])),
                env=dict(sb.get("env", {})),
                network=bool(sb.get("network", False)),
                allowed_domains=list(sb.get("allowed_domains", [])),
                timeout_s=float(sb.get("timeout_s", 120)),
                memory_mb=int(sb.get("memory_mb", 2048)),
                cpus=float(sb.get("cpus", 2.0)),
                pids=int(sb.get("pids", 256)),
                image=str(sb.get("image", "python:3.12-slim")),
            )
            backend_kwargs = {k: v for k, v in sb.items() if k in ("base_url", "token", "workspace_id", "docker_bin")}
            sandbox = make_sandbox(sb.get("backend", "local"), ws, policy, **backend_kwargs)
            check_sandbox(sandbox, has_exec_tools=registry.has_exec_tools(), allow_unsafe_local=config.allow_unsafe_local)
        elif registry.has_exec_tools():
            raise ConfigError(f"profile {self.name!r} has exec tools but no sandbox section")

        # hooks
        hooks = HookRegistry(workspace=str(ws))
        for h in self.hooks:
            hooks.command(h["event"], h["command"] if isinstance(h["command"], list) else [h["command"]], matcher=h.get("matcher"), timeout=float(h.get("timeout", 60)), name=h.get("name", ""))
        for event, fn, matcher in self.python_hooks:
            hooks.on(event, fn, matcher=matcher)

        # gates
        gates = None
        if self.phases or self.gates:
            gates = Gates(phases=list(self.phases), entry=_build_reqs_map(self.gates.get("entry", {})), finish=_build_reqs(self.gates.get("finish", [])))
            for phase, reqs in self.extra_gates.get("entry", {}).items():
                gates.entry.setdefault(phase, []).extend(reqs)
            gates.finish.extend(self.extra_gates.get("finish", []))

        # persistence
        sid = session_id or uuid.uuid4().hex[:12]
        transcript = Transcript(ws / ".harness" / "transcript.jsonl", sid) if self.transcript else None
        checkpoints = Checkpoints(ws) if self.checkpoints else None
        memory = None
        if self.memory.get("dir"):
            mdir = Path(self.memory["dir"]).expanduser()
            memory = MemoryDir(mdir if mdir.is_absolute() else ws / mdir)

        bus = events or EventBus(ws / ".harness" / "events.jsonl")
        system_prompt = assemble_static_prompt(self.system_prompt, workspace=ws, registry=registry, skills=skills)
        rt_extra: dict[str, Any] = {
            "allowed_domains": list(self.web.get("allowed_domains", [])),
            "phase_evidence": dict(self.phase_evidence),
            "removed_tools": removed,
            "dropped_allow_rules": [f"{r.tool}({r.pattern})" for r in dropped],
        }
        if extra:
            rt_extra.update(extra)
        return Runtime(
            workspace=ws,
            config=config,
            model=client,
            registry=registry,
            system_prompt=system_prompt,
            permissions=permissions,
            hooks=hooks,
            sandbox=sandbox,
            redactor=Redactor(ws),
            events=bus,
            gates=gates,
            checkpoints=checkpoints,
            transcript=transcript,
            memory=memory,
            skills=skills,
            deps=deps or Deps(),
            require_finish=self.require_finish,
            profile_name=self.name,
            cache_ttl=self.cache_ttl,
            extra=rt_extra,
        )


# ---- gate specs from YAML -------------------------------------------------------------------

def _build_req(spec: Any):
    if isinstance(spec, str):
        if spec == "todos_done":
            return todos_done
        if spec.startswith("evidence:"):
            return evidence(spec.split(":", 1)[1].strip())
        if spec.startswith("file_exists:"):
            return file_exists(spec.split(":", 1)[1].strip())
        raise ConfigError(f"unknown gate spec {spec!r}")
    if isinstance(spec, dict) and len(spec) == 1:
        kind, arg = next(iter(spec.items()))
        if kind == "evidence":
            return evidence(str(arg))
        if kind == "file_exists":
            return file_exists(str(arg))
        if kind == "fresh_file":
            return fresh_file(str(arg["path"]), str(arg["newer_than"]), arg.get("message"))
        if kind == "json_field":
            return json_field(str(arg["path"]), str(arg["key"]), arg.get("expected", True), arg.get("message"))
        if kind == "todos_done":
            return todos_done
    raise ConfigError(f"unknown gate spec {spec!r}")


def _build_reqs(specs: list[Any]) -> list:
    return [_build_req(s) for s in specs or []]


def _build_reqs_map(m: dict[str, list[Any]]) -> dict[str, list]:
    return {phase: _build_reqs(specs) for phase, specs in (m or {}).items()}


# ---- lookup -----------------------------------------------------------------------------------

def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def load_profile(name_or_path: str | Path) -> Profile:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return Profile.load(p)
    bundled = PROFILES_DIR / f"{name_or_path}.yaml"
    if bundled.exists():
        return Profile.load(bundled)
    raise ConfigError(f"no profile {name_or_path!r}; bundled: {list_profiles()}")
