"""Progress: phases, gates, evidence and stuck detection.

The model says what it is doing. This layer decides what has actually been
done, from files on disk and tool results, and refuses `finish` until the
evidence is there.
"""

from .phases import Phases
from .gates import Gates, evidence, file_exists, fresh_file, json_field
from .stuck import StuckDetector
from .evidence import infer_phase

__all__ = ["Phases", "Gates", "evidence", "file_exists", "fresh_file", "json_field", "StuckDetector", "infer_phase"]
