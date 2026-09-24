"""Safety: permissions, rules, hooks, guards, redaction.

This layer wraps every tool call the same way. Tools do not decide whether
they may run; this layer does.

安全性: 権限、ルール、フック、ガード、秘匿化。

この層はすべてのツール呼び出しを同じ形で包む。ツール自身が実行の可否を決めることは
なく、判断するのはこの層である。
"""

from .permissions import Decision, PermissionContext, PermissionMode, decide
from .rules import Rule, parse_rule, parse_rules
from .hooks import HookRegistry, HookResult, Events

__all__ = [
    "Decision",
    "PermissionContext",
    "PermissionMode",
    "decide",
    "Rule",
    "parse_rule",
    "parse_rules",
    "HookRegistry",
    "HookResult",
    "Events",
]
