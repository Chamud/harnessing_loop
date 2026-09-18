"""Redaction of tool output before the model sees it.

Two jobs:
- replace absolute workspace and home paths with short placeholders so the
  model reasons about relative paths and the transcript stays portable
- mask secret-shaped values: cloud access keys, bearer tokens, private key
  blocks, and KEY=value pairs whose key looks like a secret
"""

from __future__ import annotations

import re
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)[A-Z0-9_]*)\s*[=:]\s*[^\s,;]+"),
]


class Redactor:
    def __init__(self, workspace: Path | None = None, extra_patterns: list[str] | None = None):
        self.workspace = workspace.resolve() if workspace else None
        self.home = Path.home().resolve()
        self.extra = [re.compile(p) for p in (extra_patterns or [])]

    def paths(self, text: str) -> str:
        if not text:
            return text
        if self.workspace:
            for form in (str(self.workspace), self.workspace.as_posix()):
                text = text.replace(form, "<workspace>")
        for form in (str(self.home), self.home.as_posix()):
            text = text.replace(form, "~")
        return text

    def secrets(self, text: str) -> str:
        if not text:
            return text
        for pat in _SECRET_PATTERNS + self.extra:
            text = pat.sub(lambda m: _mask(m.group(0)), text)
        return text

    def __call__(self, text: str) -> str:
        return self.secrets(self.paths(text))


def _mask(s: str) -> str:
    if "=" in s or ":" in s:
        head = re.split(r"[=:]", s, 1)[0]
        return f"{head}=<redacted>"
    return "<redacted>"
