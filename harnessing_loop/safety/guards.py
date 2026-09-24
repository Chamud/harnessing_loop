"""Checks that hold in every permission mode, including bypass.

- protected paths: the agent must never edit its own configuration, hook
  definitions, permission rules, or the version-control internals of the
  workspace. Editing those is how an agent widens its own permissions.
- dangerous prefixes: an allow rule such as `shell(python *)` allows
  arbitrary code and is therefore stripped when a profile asks for an
  automatic mode.

bypass を含むすべての権限モードで有効な検査。

- 保護パス: エージェントは自分の設定、フック定義、権限ルール、ワークスペースの
  バージョン管理の内部を決して編集してはならない。それらを編集させることが、
  エージェントが自分の権限を広げる手口になる。
- 危険な接頭辞: `shell(python *)` のような allow ルールは任意のコードを許して
  しまうため、プロファイルが自動モードを求めたときには取り除かれる。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .rules import Rule

PROTECTED_RELATIVE = (
    ".harness/",  # runtime config, hooks, rules
    # .harness/ はランタイムの設定、フック、ルールを保持する
    ".git/",
    ".hg/",
    ".svn/",
    ".env",
    ".envrc",
    "profile.yaml",
    "hooks.yaml",
)

PROTECTED_HOME = (
    ".ssh/",
    ".aws/",
    ".gnupg/",
    ".config/gcloud/",
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
)

CODE_EXEC_PREFIXES = (
    "python",
    "python3",
    "node",
    "deno",
    "ruby",
    "perl",
    "php",
    "bash",
    "sh",
    "zsh",
    "pwsh",
    "powershell",
    "eval",
    "exec",
    "xargs",
    "env",
    "sudo",
    "ssh",
    "npx",
    "bunx",
    "npm run",
    "pip install",
    "curl",
    "wget",
)


def _rel(path: Path, root: Path) -> str | None:
    try:
        return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix()).as_posix()
    except (ValueError, OSError):
        return None


def is_protected_path(path: str | Path, workspace: Path) -> str | None:
    """Return a reason if `path` must not be written, else None.
    `path` に書き込んではならない場合はその理由を返し、そうでなければ None を返す。
    """
    p = Path(path)
    rel = _rel(p, workspace)
    if rel is not None:
        for prefix in PROTECTED_RELATIVE:
            if rel == prefix.rstrip("/") or rel.startswith(prefix):
                return f"{rel} is harness or version-control configuration"
    home = Path.home()
    relh = _rel(p, home)
    if relh is not None:
        for prefix in PROTECTED_HOME:
            if relh == prefix.rstrip("/") or relh.startswith(prefix):
                return f"~/{relh} holds credentials or shell configuration"
    return None


def is_inside(path: str | Path, root: Path) -> bool:
    return _rel(Path(path), root) is not None


def rule_is_dangerous(rule: Rule) -> bool:
    """True if an allow rule effectively grants arbitrary code execution.
    allow ルールが実質的に任意コードの実行を許してしまう場合に True。
    """
    if rule.behavior != "allow" or rule.pattern is None:
        return False
    pat = rule.pattern.strip().lower()
    for prefix in CODE_EXEC_PREFIXES:
        if pat in (prefix, f"{prefix} *", f"{prefix}*", f"{prefix}:*"):
            return True
    return False


def strip_dangerous_allow_rules(rules: list[Rule]) -> tuple[list[Rule], list[Rule]]:
    kept, dropped = [], []
    for r in rules:
        (dropped if rule_is_dangerous(r) else kept).append(r)
    return kept, dropped
