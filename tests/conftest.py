"""Shared fixtures. Every test runs on the fake model: zero API spend."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessing_loop.core.deps import Deps  # noqa: E402
from harnessing_loop.core.events import EventBus  # noqa: E402
from harnessing_loop.llm.fake import FakeModel  # noqa: E402
from harnessing_loop.profiles.base import Profile  # noqa: E402


def make_profile(**overrides: Any) -> Profile:
    base: dict[str, Any] = {
        "name": "test",
        "model": "fake",
        "system_prompt": "You are a test agent.",
        "tool_packs": ["files", "planning"],
        "permissions": {"mode": "accept_edits"},
        "limits": {"max_turns": 20},
        "transcript": True,
        "checkpoints": True,
    }
    base.update(overrides)
    return Profile.from_dict(base)


def make_runtime(tmp_path: Path, model: FakeModel | list | None = None, *, events: list | None = None, **overrides: Any):
    profile = make_profile(**overrides)
    fake = model if isinstance(model, FakeModel) else FakeModel(model or [])
    bus = EventBus()
    if events is not None:
        bus.subscribe(events.append)
    rt = profile.build(tmp_path / "ws", model=fake, events=bus, deps=Deps.deterministic())
    return rt, fake


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "ws"
    d.mkdir()
    return d


@pytest.fixture
def sandboxed() -> dict[str, Any]:
    """Profile overrides that enable exec tools on the local backend."""
    return {
        "tool_packs": ["files", "exec", "planning", "meta"],
        "sandbox": {"backend": "local", "allow_unsafe_local": True, "timeout_s": 30},
        "permissions": {"mode": "bypass"},
    }
