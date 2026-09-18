"""A sandboxed coding agent on one task.

    python examples/coder_cli.py "add a --verbose flag to cli.py" --workspace ./work
    python examples/coder_cli.py "..." --workspace ./work --model <model-id> --docker

Without --model a scripted fake model performs a small end-to-end job so
you can watch the permission checks, the sandbox, the todo gate and the
finish gate without an API key.

With --docker the sandbox backend is switched to a container. Without it
the local backend is used, which the coder profile explicitly allows for
development (allow_unsafe_local).
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
    return FakeModel([
        [tool("todo_write", todos=[{"content": "write fizzbuzz.py", "status": "in_progress"}, {"content": "run it", "status": "pending"}])],
        [tool("write_file", path="fizzbuzz.py", content="for i in range(1, 16):\n    print('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else i)\n")],
        [tool("shell", command="python fizzbuzz.py")],
        [tool("finish", summary="too early")],  # refused: open todo items
        [tool("todo_write", todos=[{"content": "write fizzbuzz.py", "status": "completed"}, {"content": "run it", "status": "completed"}]),
         tool("notes_append", text="fizzbuzz.py prints 1..15 with the usual substitutions; verified by running it")],
        [tool("finish", summary="Wrote fizzbuzz.py and verified its output by running it.")],
    ])


def ask(tool_name: str, input: dict, reason: str) -> bool:
    print(f"\n[permission] {tool_name}: {reason}\n  {input}\n  allow? [y/N] ", end="", flush=True)
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="Write fizzbuzz.py for 1..15 and run it.")
    ap.add_argument("--workspace", default="./work")
    ap.add_argument("--model", default=None)
    ap.add_argument("--docker", action="store_true", help="use the container sandbox")
    args = ap.parse_args()

    profile = load_profile("coder")
    if args.docker:
        profile = profile.with_overrides(sandbox={**profile.sandbox, "backend": "docker", "allow_unsafe_local": False})
    runtime = profile.build(args.workspace, model=args.model or demo_model(), ask_handler=ask if sys.stdin.isatty() else None)
    runtime.events.subscribe(print_subscriber)
    result = Loop(runtime).run(args.prompt)
    print(f"\nresult: {result.reason} after {result.turns} turns")
    print(result.final_text)
    return 0 if result.reason == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
