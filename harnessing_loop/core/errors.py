"""Error types used across the package.

Rule: a tool never raises into the loop. Failures inside a tool become an
error result the model can read. The exceptions below are for the harness
itself (model transport, configuration, persistence), not for tool logic.
"""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for harness-level failures."""


class ConfigError(HarnessError):
    """A profile or runtime setting is invalid."""


class ModelError(HarnessError):
    """The model transport failed after retries."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class PromptTooLong(ModelError):
    """The request exceeded the model's context window."""

    def __init__(self, message: str = "prompt too long"):
        super().__init__(message, retryable=False, status=400)


class Aborted(HarnessError):
    """The run was cancelled through the control file or a signal."""


class SandboxUnavailable(HarnessError):
    """The requested sandbox backend cannot run on this machine."""


TOOL_ERROR_OPEN = "<tool_error>"
TOOL_ERROR_CLOSE = "</tool_error>"


def tool_error_text(message: str) -> str:
    """Wrap an error so the model can tell it apart from normal output."""
    return f"{TOOL_ERROR_OPEN}{message}{TOOL_ERROR_CLOSE}"
