"""The control file: how an operator talks to a running loop.

`.harness/control.json` holds flags the loop checks every turn:

    {"cancel": false, "pause": false, "inbox": ["message for the agent"]}

Writes are atomic (temp file + replace), so a reader never sees a torn
file. Any process with access to the workspace can steer the run.

制御ファイル。オペレータが動いているループに話しかける手段である。

`.harness/control.json` は、ループが毎ターン確認するフラグを持つ。形は上の例のとおりである。

書き込みは原子的（一時ファイル + 置き換え）なので、読む側が壊れかけのファイルを見ることは
ない。ワークスペースにアクセスできるプロセスなら、どれでも実行を操舵できる。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT = {"cancel": False, "pause": False, "inbox": []}


def control_path(workspace: Path) -> Path:
    return Path(workspace) / ".harness" / "control.json"


def read_control(workspace: Path) -> dict[str, Any]:
    p = control_path(workspace)
    if not p.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    out = dict(DEFAULT)
    out.update({k: v for k, v in data.items() if k in DEFAULT})
    if not isinstance(out["inbox"], list):
        out["inbox"] = []
    return out


def write_control(workspace: Path, data: dict[str, Any]) -> None:
    p = control_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="control.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def set_flag(workspace: Path, **flags: Any) -> dict[str, Any]:
    data = read_control(workspace)
    for k, v in flags.items():
        if k == "inbox":
            data["inbox"] = list(data["inbox"]) + (v if isinstance(v, list) else [v])
        elif k in DEFAULT:
            data[k] = v
    write_control(workspace, data)
    return data
