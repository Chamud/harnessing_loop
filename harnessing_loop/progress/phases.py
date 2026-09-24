"""Phases: the named stages of a job, in the order they happen.

`Phases` is an ordered list of names and nothing more. The order carries the
meaning: a name's position is what "forward" refers to. The type holds no run
state and enforces no rule. Its helpers answer the questions the rules ask —
where a name sits (`index`), which names are still ahead (`after`), which name
ends the run (`last`), and whether a name belongs at all (`in`).

The rules themselves live where they can see the run:

- `set_phase` in `tools/packs/planning.py` refuses a move to an earlier phase
- `Gates.check_finish` refuses `finish` anywhere but the last phase
- `infer_phase` in `evidence.py` moves the phase forward once the evidence
  mapped to it appears

A profile lists its phases under `phases:`, and `Profile.build` passes them to
`Gates` as a plain list. This type is for callers that want the lookups without
building a `Gates`.

フェーズ。仕事の各段階に与える名前を、起きる順に並べたもの。

`Phases` は名前の順序付きリストであり、それ以上のものではない。意味を担うのは順序で
ある。名前の位置こそが「前」の意味を決める。この型は実行状態を持たず、規則を強制も
しない。備える補助関数は、規則が問う事柄に答えるためのものである。すなわち、名前が
どこにあるか（`index`）、まだ先に残っているのはどれか（`after`）、実行を終えるのは
どの名前か（`last`）、そしてその名前がそもそも含まれるか（`in`）である。

規則そのものは、実行を見られる場所に置かれている。

- `tools/packs/planning.py` の `set_phase` は、前のフェーズへ戻る移動を拒否する
- `Gates.check_finish` は、最後のフェーズ以外での `finish` を拒否する
- `evidence.py` の `infer_phase` は、対応づけられた証跡が現れた時点でフェーズを進める

プロファイルは `phases:` の下にフェーズを並べ、`Profile.build` はそれを素のリストと
して `Gates` に渡す。この型は、`Gates` を組み立てずに参照だけしたい呼び出し側のため
にある。
"""

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
