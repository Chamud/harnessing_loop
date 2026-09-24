"""The tool protocol.

A tool is a class with a name, a description, a JSON schema for its input,
a few predicates, and a `call`. Defaults fail closed: a tool is neither
read-only nor safe to run in parallel until it says so.

`ToolContext` is everything a tool may touch: the workspace, the run state,
the sandbox, the file state cache, the event bus, the registry, and the
model client. Tools never import the loop.

A tool never raises to the loop. `call` returns `ToolResult.error(...)` for
anything the model should read and act on. Exceptions that escape are
caught by the pipeline and turned into error results anyway, but a tool
that plans its errors gives the model better text.

For small tools use the decorator:

    @tool("now", "Current date and time.", {"type": "object", "properties": {}}, read_only=True)
    def now(input, ctx):
        return ToolResult.ok(datetime.now().isoformat())

ツールのプロトコル。

ツールとは、名前、説明、入力の JSON スキーマ、いくつかの述語、そして `call` を
持つクラスである。既定値は安全側に倒れる。自ら宣言するまで、ツールは読み取り専用
でも並行実行安全でもない。

`ToolContext` はツールが触ってよいものすべてである。ワークスペース、実行状態、
サンドボックス、ファイル状態キャッシュ、イベントバス、レジストリ、そしてモデル
クライアント。ツールはループを import しない。

ツールはループに例外を投げない。モデルが読んで対処すべきものは、`call` が
`ToolResult.error(...)` として返す。抜けてきた例外もパイプラインが捕まえてエラー
結果に変えるが、エラーを設計したツールのほうがモデルに良い文面を渡せる。

小さなツールには、上に示したデコレータを使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.errors import tool_error_text
from ..safety.permissions import Decision


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    data: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)  # merged into RunState.evidence
    # RunState.evidence にマージされる。

    @classmethod
    def ok(cls, content: str, data: Any = None, **evidence: Any) -> "ToolResult":
        return cls(content=content, data=data, evidence=evidence)

    @classmethod
    def error(cls, message: str, data: Any = None) -> "ToolResult":
        return cls(content=tool_error_text(message), is_error=True, data=data)


@dataclass
class ToolContext:
    workspace: Path
    state: Any  # RunState
    # RunState（実行状態）。
    config: Any  # RunConfig
    # RunConfig（実行設定）。
    sandbox: Any = None  # Sandbox
    # Sandbox（サンドボックス）。
    file_state: Any = None  # FileStateCache
    # FileStateCache（ファイル状態キャッシュ）。
    events: Any = None  # EventBus
    # EventBus（イベントバス）。
    registry: Any = None  # Registry
    # Registry（レジストリ）。
    permissions: Any = None  # PermissionContext
    # PermissionContext（権限コンテキスト）。
    hooks: Any = None  # HookRegistry
    # HookRegistry（フックのレジストリ）。
    redactor: Any = None  # Redactor
    # Redactor（秘匿化）。
    model: Any = None  # ModelClient, for subagents and side queries
    # ModelClient。サブエージェントと副問い合わせのため。
    gates: Any = None  # progress.gates.Gates
    # progress.gates.Gates（ゲート）。
    checkpoints: Any = None  # persistence.checkpoints.Checkpoints
    # persistence.checkpoints.Checkpoints（チェックポイント）。
    deps: Any = None  # Deps
    # Deps（注入される依存物）。
    tasks: dict[str, Any] = field(default_factory=dict)  # background tasks
    # バックグラウンドタスク。
    extra: dict[str, Any] = field(default_factory=dict)
    depth: int = 0  # subagent nesting
    # サブエージェントの入れ子の深さ。

    @property
    def harness_dir(self) -> Path:
        d = self.workspace / ".harness"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve(self, path: str) -> Path:
        """Resolve a user path against the workspace. No escape check here; see guards.

        ユーザーの与えたパスをワークスペース基準で解決する。ここでは脱出の検査は
        しない。ガードを見ること。
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.workspace / p
        return p.resolve()

    def inside_workspace(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.workspace.resolve())
            return True
        except ValueError:
            return False


class Tool:
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    category: str = "other"  # read | edit | exec | plan | web | agent | meta | other
    # 分類。read | edit | exec | plan | web | agent | meta | other のいずれか。
    read_only: bool = False
    concurrency_safe: bool = False
    destructive: bool = False
    max_result_chars: int | None = None  # None -> config default; 0 -> never persist
    # None なら設定の既定値、0 ならディスクに退避しない。
    defer: bool = False  # only the name is sent until tool_search loads it
    # tool_search が読み込むまで、名前だけが送られる。
    search_hint: str = ""

    # ---- predicates ----------------------------------------------------------
    # ---- 述語 ----------------------------------------------------------------
    def is_read_only(self, input: dict[str, Any]) -> bool:
        return self.read_only

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return self.concurrency_safe

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        """Strings a permission rule pattern is matched against.

        権限ルールのパターンと照合される文字列。
        """
        return []

    def paths(self, input: dict[str, Any]) -> Iterable[str]:
        """Filesystem paths this call touches, for guards and rules.

        この呼び出しが触るファイルシステム上のパス。ガードとルールのために使う。
        """
        return []

    # ---- lifecycle -------------------------------------------------------------
    # ---- ライフサイクル --------------------------------------------------------
    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        """Return an error message, or None when the input is acceptable.

        エラーメッセージを返す。入力が受け入れられるなら None を返す。
        """
        return None

    def check_permissions(self, input: dict[str, Any], ctx: Any) -> Decision:
        return Decision.passthrough()

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    # ---- wire ----------------------------------------------------------------------
    # ---- 送信形式 ------------------------------------------------------------------
    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any] | None = None,
    *,
    read_only: bool = False,
    concurrency_safe: bool | None = None,
    category: str = "other",
    defer: bool = False,
    subjects: Callable[[dict[str, Any]], Iterable[str]] | None = None,
    paths: Callable[[dict[str, Any]], Iterable[str]] | None = None,
) -> Callable[[Callable[[dict[str, Any], ToolContext], ToolResult | str]], Tool]:
    """Turn a function into a Tool instance.

    関数を Tool のインスタンスに変える。
    """

    def deco(fn: Callable[[dict[str, Any], ToolContext], ToolResult | str]) -> Tool:
        class FnTool(Tool):
            pass

        FnTool.name = name
        FnTool.description = description
        FnTool.input_schema = input_schema or {"type": "object", "properties": {}}
        FnTool.read_only = read_only
        FnTool.concurrency_safe = read_only if concurrency_safe is None else concurrency_safe
        FnTool.category = category
        FnTool.defer = defer

        def _call(self: Tool, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
            out = fn(input, ctx)
            return out if isinstance(out, ToolResult) else ToolResult.ok(str(out))

        FnTool.call = _call  # type: ignore[assignment]
        if subjects:
            FnTool.permission_subjects = lambda self, i: subjects(i)  # type: ignore[assignment]
        if paths:
            FnTool.paths = lambda self, i: paths(i)  # type: ignore[assignment]
        FnTool.__name__ = f"Tool_{name}"
        return FnTool()

    return deco


# ---- minimal JSON schema validation --------------------------------------------
# ---- 最小限の JSON スキーマ検証 ------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate_schema(schema: dict[str, Any], value: Any, path: str = "input") -> str | None:
    """Check `value` against a small JSON schema subset. Return an error or None.

    JSON スキーマの小さな部分集合に対して `value` を検査する。エラーか None を返す。
    """
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for name in types:
            py = _TYPES.get(name)
            if py is None:
                continue
            if name == "integer" and isinstance(value, bool):
                continue
            if name == "number" and isinstance(value, bool):
                continue
            if isinstance(value, py):
                ok = True
                break
        if not ok:
            return f"{path}: expected {t}, got {type(value).__name__}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: must be one of {schema['enum']}"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                return f"{path}: missing required field '{req}'"
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    return f"{path}: unexpected field '{k}'"
        for k, sub in props.items():
            if k in value:
                err = validate_schema(sub, value[k], f"{path}.{k}")
                if err:
                    return err
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            err = validate_schema(schema["items"], item, f"{path}[{i}]")
            if err:
                return err
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path}: shorter than {schema['minLength']}"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path}: longer than {schema['maxLength']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: above maximum {schema['maximum']}"
    return None
