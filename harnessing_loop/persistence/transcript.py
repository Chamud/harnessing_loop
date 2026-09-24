"""The transcript: one JSON record per line, append-only.

Each message record carries its own id and its parent's id, so the file is
a chain. A compaction writes a boundary record whose parent is the last
message before compaction; loading for resume starts at the last boundary,
because everything before it is represented by the summary.

Other record kinds (`run_start`, `run_end`, `note`) carry metadata and are
skipped when rebuilding messages.

トランスクリプト。1行に1つの JSON レコード、追記のみ。

各メッセージのレコードは自分の id と親の id を持つので、ファイルは鎖になる。コンパクション
は、その直前のメッセージを親とする境界レコードを書く。再開のための読み込みは最後の境界から
始まる。それより前はすべて要約で代表されているからである。

他の種類のレコード（`run_start`、`run_end`、`note`）はメタデータを運び、メッセージを
組み直すときは読み飛ばされる。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from ..core.messages import Message, message_from_record, message_to_record, normalize_for_api

MAX_READ_BYTES = 200 * 1024 * 1024


class Transcript:
    def __init__(self, path: Path, session_id: str):
        self.path = Path(path)
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_id: str | None = None

    # ---- writing ------------------------------------------------------------------
    # ---- 書き込み ------------------------------------------------------------------
    def _write(self, record: dict[str, Any]) -> None:
        record.setdefault("ts", time.time())
        record.setdefault("session", self.session_id)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def append(self, message: Message) -> None:
        if message.parent_id is None:
            message.parent_id = self._last_id
        rec = {"type": "message", **message_to_record(message)}
        self._write(rec)
        self._last_id = message.id

    def meta(self, kind: str, **data: Any) -> None:
        self._write({"type": kind, **data})

    def boundary(self, boundary_message: Message) -> None:
        boundary_message.parent_id = self._last_id
        rec = {"type": "message", **message_to_record(boundary_message)}
        rec["kind"] = "compact_boundary"
        self._write(rec)
        self._last_id = boundary_message.id

    # ---- reading ---------------------------------------------------------------------
    # ---- 読み取り ---------------------------------------------------------------------
    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        if self.path.stat().st_size > MAX_READ_BYTES:
            raise RuntimeError(f"transcript larger than {MAX_READ_BYTES // (1024*1024)} MB; refusing to load")
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn last line after a crash
                    # クラッシュ後に途中で切れた最終行

    def load_all(self) -> list[Message]:
        by_id: dict[str, Message] = {}
        order: list[str] = []
        for rec in self.records():
            if rec.get("type") != "message":
                continue
            m = message_from_record(rec)
            by_id[m.id] = m
            order.append(m.id)
        if not order:
            return []
        # Walk back from the last message through parent ids; this drops
        # records from abandoned branches if a session was ever forked.
        # 最後のメッセージから親の id をたどって遡る。セッションが分岐していた場合、
        # 捨てられた枝のレコードはこれで落ちる。
        chain: list[Message] = []
        cur: str | None = order[-1]
        seen: set[str] = set()
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            chain.append(by_id[cur])
            cur = by_id[cur].parent_id
        chain.reverse()
        self._last_id = order[-1]
        return chain

    def load_live(self) -> list[Message]:
        """Messages from the last compaction boundary onward, repaired for the API.

        最後のコンパクション境界以降のメッセージ。API 向けに修復済み。
        """
        chain = self.load_all()
        start = 0
        for i, m in enumerate(chain):
            if m.kind == "compact_boundary":
                start = i
        live = chain[start:]
        return normalize_for_api(live)

    def last_run_meta(self) -> dict[str, Any] | None:
        last = None
        for rec in self.records():
            if rec.get("type") in ("run_start", "run_end"):
                last = rec
        return last
