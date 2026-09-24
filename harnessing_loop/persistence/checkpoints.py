"""File checkpoints: a copy of every file before the agent changes it.

`backup()` is called by write and edit tools. `snapshot()` marks a turn.
`rewind(turn)` restores every file to how it was at the start of that
turn. The index is a JSON file; backups are plain copies with a sequence
number, bounded to the most recent `max_entries`.

ファイルのチェックポイント。エージェントが変更する前のすべてのファイルの写しである。

`backup()` は書き込みツールと編集ツールから呼ばれる。`snapshot()` はターンに印をつける。
`rewind(turn)` はすべてのファイルを、そのターンの開始時点の状態に戻す。索引は JSON ファイル、
バックアップは連番を付けた単純な写しで、最新の `max_entries` 件に限られる。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any


class Checkpoints:
    def __init__(self, workspace: Path, max_entries: int = 200):
        self.workspace = Path(workspace).resolve()
        self.dir = self.workspace / ".harness" / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self.max_entries = max_entries
        self.turn = 0
        self._index: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
        return []

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self._index, indent=1), encoding="utf-8")

    def snapshot(self, turn: int) -> None:
        self.turn = turn

    def backup(self, path: Path) -> str | None:
        p = Path(path).resolve()
        if not p.exists():
            rec = {"seq": len(self._index), "path": str(p), "backup": None, "turn": self.turn, "ts": time.time()}
        else:
            seq = len(self._index)
            dest = self.dir / f"{seq:06d}_{p.name}"
            try:
                shutil.copy2(p, dest)
            except OSError:
                return None
            rec = {"seq": seq, "path": str(p), "backup": str(dest), "turn": self.turn, "ts": time.time()}
        self._index.append(rec)
        if len(self._index) > self.max_entries:
            for old in self._index[: len(self._index) - self.max_entries]:
                if old.get("backup"):
                    Path(old["backup"]).unlink(missing_ok=True)
            self._index = self._index[-self.max_entries :]
        self._save()
        return rec.get("backup")

    def rewind(self, turn: int) -> list[str]:
        """Restore files to their state at the start of `turn`. Returns restored paths.

        ファイルを `turn` の開始時点の状態に戻す。戻したパスを返す。
        """
        restored: list[str] = []
        earliest: dict[str, dict[str, Any]] = {}
        for rec in self._index:
            if rec["turn"] >= turn and rec["path"] not in earliest:
                earliest[rec["path"]] = rec
        for path, rec in earliest.items():
            p = Path(path)
            if rec["backup"] is None:
                if p.exists():
                    p.unlink()
                    restored.append(path)
            else:
                shutil.copy2(rec["backup"], p)
                restored.append(path)
        self._index = [r for r in self._index if r["turn"] < turn]
        self._save()
        return restored

    def entries(self) -> list[dict[str, Any]]:
        return list(self._index)
