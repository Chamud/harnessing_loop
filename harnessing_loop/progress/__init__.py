"""Progress: phases, gates, evidence and stuck detection.

The model says what it is doing. This layer decides what has actually been
done, from files on disk and tool results, and refuses `finish` until the
evidence is there.

進捗。フェーズ、ゲート、証跡、そしてスタック検出。

モデルは自分が何をしているかを言う。この層は、ディスク上のファイルとツール結果から、実際に
何が終わったのかを決める。そして証跡が揃うまで `finish` を拒否する。
"""

from .phases import Phases
from .gates import Gates, evidence, file_exists, fresh_file, json_field
from .stuck import StuckDetector
from .evidence import infer_phase

__all__ = ["Phases", "Gates", "evidence", "file_exists", "fresh_file", "json_field", "StuckDetector", "infer_phase"]
