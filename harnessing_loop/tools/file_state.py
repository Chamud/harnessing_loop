"""What the model has read, and when.

Editing a file the model has not read produces guesses. Editing a file
that changed since the read produces silent damage. Both are refused.

- `record()` after every read (partial reads are marked)
- `check_before_edit()` before every edit or overwrite
- the cache is bounded; the oldest entries fall out first

モデルが何をいつ読んだか。

モデルが読んでいないファイルを編集すれば、それは当て推量になる。読んだあとに変わった
ファイルを編集すれば、黙って壊すことになる。どちらも拒否する。

- 読み取りのたびに `record()` を呼ぶ（部分的な読み取りには印を付ける）
- 編集や上書きのたびに、その前に `check_before_edit()` を呼ぶ
- キャッシュには上限がある。古い項目から先に落ちる
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileState:
    content: str
    mtime: float
    partial: bool = False


class FileStateCache:
    def __init__(self, max_entries: int = 100):
        self._items: OrderedDict[str, FileState] = OrderedDict()
        self.max_entries = max_entries

    @staticmethod
    def _key(path: Path | str) -> str:
        return os.path.normcase(str(Path(path).resolve()))

    def record(self, path: Path | str, content: str, *, partial: bool = False) -> None:
        key = self._key(path)
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            mtime = 0.0
        self._items.pop(key, None)
        self._items[key] = FileState(content=content, mtime=mtime, partial=partial)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, path: Path | str) -> FileState | None:
        return self._items.get(self._key(path))

    def forget(self, path: Path | str) -> None:
        self._items.pop(self._key(path), None)

    def clear(self) -> None:
        self._items.clear()

    def recent(self, n: int = 5) -> list[str]:
        return list(self._items.keys())[-n:]

    def check_before_edit(self, path: Path | str) -> str | None:
        """Return an error message if the file may not be edited yet.

        まだ編集してはならないファイルなら、エラーメッセージを返す。
        """
        p = Path(path)
        if not p.exists():
            return None  # creating a new file needs no prior read
            # 新規作成なら、事前の読み取りは要らない。
        st = self.get(p)
        if st is None or st.partial:
            return "File has not been read in full yet. Read it first, then edit."
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return None
        if mtime > st.mtime:
            try:
                if p.read_text(encoding="utf-8", errors="replace") == st.content:
                    return None  # touched but unchanged
                    # 時刻だけ変わって、内容は同じ。
            except OSError:
                pass
            return "File has been modified since it was read. Read it again before editing."
        return None

    def clone(self) -> "FileStateCache":
        c = FileStateCache(self.max_entries)
        c._items = OrderedDict(self._items)
        return c
