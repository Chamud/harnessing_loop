from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Phases:
    names: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.names)

    def __iter__(self):
        return iter(self.names)

    def __contains__(self, name: str) -> bool:
        return name in self.names

    def index(self, name: str) -> int:
        return self.names.index(name) if name in self.names else -1

    def after(self, name: str) -> list[str]:
        i = self.index(name)
        return self.names[i + 1 :] if i >= 0 else list(self.names)

    @property
    def last(self) -> str | None:
        return self.names[-1] if self.names else None
