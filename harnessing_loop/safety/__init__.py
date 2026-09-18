"""Safety: permissions, rules, hooks, guards, redaction.

This layer wraps every tool call the same way. Tools do not decide whether
they may run; this layer does.
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
