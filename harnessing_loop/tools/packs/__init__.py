"""Tool packs. A profile names the packs it wants.

    files      read_file, write_file, edit_file, list_dir, glob_files, grep_files
    exec       shell, run_python                      (needs a sandbox)
    planning   todo_write, set_phase, notes_append, finish
    web        web_fetch, web_search
    agents     subagent
    tasks      run_background, task_output, task_stop (needs a sandbox)
    meta       tool_search, define_tool

ツールパック。プロファイルは、欲しいパックを名前で指定する。

上の表は、パック名とそのパックが作るツールの対応である。`exec` と `tasks` は
サンドボックスを必要とする。
"""

from __future__ import annotations

from typing import Callable

from ..base import Tool
from . import agents, exec as exec_pack, files, meta, planning, tasks, web

PACKS: dict[str, Callable[[], list[Tool]]] = {
    "files": files.make,
    "exec": exec_pack.make,
    "planning": planning.make,
    "web": web.make,
    "agents": agents.make,
    "tasks": tasks.make,
    "meta": meta.make,
}


def make_pack(name: str) -> list[Tool]:
    if name not in PACKS:
        raise KeyError(f"unknown tool pack {name!r}; known: {sorted(PACKS)}")
    return PACKS[name]()
