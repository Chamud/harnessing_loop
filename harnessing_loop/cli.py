"""Command line entry point.

    hloop run "prompt" --profile coder --workspace ./work --model bedrock:...
    hloop resume --profile coder --workspace ./work
    hloop control --workspace ./work --cancel | --pause | --resume | --send "text"
    hloop profiles

コマンドラインの入口。上にあるのは使い方の例である。

`hloop run` は新しい実行を始め、`hloop resume` はワークスペースの前回の実行を
続ける。`hloop control` は動いているループを操作し、`hloop profiles` は同梱の
プロファイルを一覧する。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.events import print_subscriber
from .core.loop import Loop
from .persistence.control import set_flag
from .profiles.base import list_profiles, load_profile


def _ask(tool: str, input: dict, reason: str) -> bool:
    print(f"\n[permission] {tool} {input}\n  reason: {reason}\n  allow? [y/N] ", end="", flush=True)
    try:
        return sys.stdin.readline().strip().lower() in ("y", "yes")
    except (EOFError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hloop")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="start a new run")
    run.add_argument("prompt")
    run.add_argument("--profile", default="chat")
    run.add_argument("--workspace", default="./work")
    run.add_argument("--model", default=None)
    run.add_argument("--quiet", action="store_true")

    res = sub.add_parser("resume", help="continue the last run in a workspace")
    res.add_argument("--profile", default="chat")
    res.add_argument("--workspace", default="./work")
    res.add_argument("--model", default=None)
    res.add_argument("--quiet", action="store_true")

    ctl = sub.add_parser("control", help="steer a running loop")
    ctl.add_argument("--workspace", default="./work")
    ctl.add_argument("--cancel", action="store_true")
    ctl.add_argument("--pause", action="store_true")
    ctl.add_argument("--resume", action="store_true")
    ctl.add_argument("--send", default=None)

    sub.add_parser("profiles", help="list bundled profiles")

    args = ap.parse_args(argv)

    if args.cmd == "profiles":
        for p in list_profiles():
            print(p)
        return 0

    if args.cmd == "control":
        flags = {}
        if args.cancel:
            flags["cancel"] = True
        if args.pause:
            flags["pause"] = True
        if args.resume:
            flags["pause"] = False
        if args.send:
            flags["inbox"] = [args.send]
        print(set_flag(Path(args.workspace), **flags))
        return 0

    profile = load_profile(args.profile)
    runtime = profile.build(args.workspace, model=args.model, ask_handler=_ask if sys.stdin.isatty() else None)
    if not args.quiet:
        runtime.events.subscribe(print_subscriber)
    if args.cmd == "run":
        result = Loop(runtime).run(args.prompt)
    else:
        result = Loop.resume(runtime).run(None)
    return 0 if result.reason == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
