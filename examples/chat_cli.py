"""A terminal chat agent in a few dozen lines.

    python examples/chat_cli.py --workspace ./work            # scripted fake model, runs offline
    python examples/chat_cli.py --workspace ./work --model bedrock:<model-id>
    python examples/chat_cli.py --workspace ./work --model <model-id>   # direct API client

Each line you type becomes a new user message on the same Loop, so the
conversation continues. The chat profile gives the model read-only file
tools and web fetch; nothing here can modify the workspace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harnessing_loop import Loop, load_profile  # noqa: E402
from harnessing_loop.core.events import print_subscriber  # noqa: E402
from harnessing_loop.llm.fake import FakeModel, tool  # noqa: E402


def demo_model() -> FakeModel:
    """Answers the first question by listing the workspace, then replies from a script."""
    return FakeModel([
        [tool("list_dir", path=".")],
        "That is what the workspace contains. Ask me about any of these files.",
        [tool("read_file", path="README.md")],
        "I read the README. It describes the workspace.",
        "I am a scripted stand-in. Pass --model to talk to a real model.",
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="./work")
    ap.add_argument("--model", default=None, help="model id; omit for the offline demo")
    args = ap.parse_args()

    profile = load_profile("chat")
    if args.model is None:
        readme = Path(args.workspace) / "README.md"
        if not readme.exists():
            readme.parent.mkdir(parents=True, exist_ok=True)
            readme.write_text("# Demo workspace\n\nThe offline chat demo reads this file.\n", encoding="utf-8")
    runtime = profile.build(args.workspace, model=args.model or demo_model())
    runtime.events.subscribe(print_subscriber)
    loop = Loop(runtime)

    print(f"chat on {runtime.workspace} (model: {getattr(runtime.model, 'model', '?')}). Empty line to quit.")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        result = loop.run(line)
        if result.reason != "completed":
            print(f"[{result.reason}] {result.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
