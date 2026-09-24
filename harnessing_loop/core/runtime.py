"""Everything a run needs, assembled once.

A `Runtime` is built by a profile, or by hand in a script. The loop reads
from it and never reaches for globals. Subagents get a `child()` with a
smaller registry and their own state.

1回の実行に必要なものを、一度だけ組み立てたもの。

`Runtime` はプロファイルが組み立てるか、スクリプト内で手で組み立てる。ループは
そこから読み、グローバルには手を伸ばさない。サブエージェントには、より小さな
レジストリと自分の状態を持つ `child()` が渡される。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .deps import Deps
from .events import EventBus
from .state import RunConfig, RunState


@dataclass
class Runtime:
    workspace: Path
    config: RunConfig
    model: Any  # ModelClient
    registry: Any  # tools.Registry
    system_prompt: str  # static, cached
    # 静的であり、キャッシュされる。
    permissions: Any  # safety.PermissionContext
    hooks: Any  # safety.HookRegistry
    sandbox: Any = None  # sandbox.Sandbox
    file_state: Any = None  # tools.file_state.FileStateCache
    redactor: Any = None
    events: EventBus | None = None
    gates: Any = None  # progress.gates.Gates
    checkpoints: Any = None  # persistence.checkpoints.Checkpoints
    transcript: Any = None  # persistence.transcript.Transcript
    memory: Any = None  # persistence.memory.MemoryDir
    skills: Any = None  # skills.loader.SkillSet
    deps: Deps = field(default_factory=Deps)
    dynamic_prompt: Callable[["Runtime", RunState], str] | None = None
    require_finish: bool = False
    profile_name: str = "custom"
    cache_ttl: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    depth: int = 0

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.events is None:
            self.events = EventBus(self.workspace / ".harness" / "events.jsonl")
        if self.file_state is None:
            from ..tools.file_state import FileStateCache

            self.file_state = FileStateCache()

    def new_state(self) -> RunState:
        return RunState(workspace=self.workspace)

    def child(self, *, registry: Any, system_prompt: str | None = None, max_turns: int | None = None) -> "Runtime":
        cfg = self.config
        if max_turns is not None:
            from dataclasses import replace

            cfg = replace(cfg, max_turns=max_turns)
        return Runtime(
            workspace=self.workspace,
            config=cfg,
            model=self.model,
            registry=registry,
            system_prompt=system_prompt or self.system_prompt,
            permissions=self.permissions,
            hooks=self.hooks,
            sandbox=self.sandbox,
            file_state=self.file_state.clone() if self.file_state else None,
            redactor=self.redactor,
            events=self.events,
            gates=None,
            checkpoints=self.checkpoints,
            transcript=None,
            memory=self.memory,
            skills=self.skills,
            deps=self.deps,
            dynamic_prompt=self.dynamic_prompt,
            require_finish=False,
            profile_name=f"{self.profile_name}/subagent",
            cache_ttl=self.cache_ttl,
            extra=dict(self.extra),
            depth=self.depth + 1,
        )
