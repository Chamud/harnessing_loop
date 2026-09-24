"""Error types used across the package.

Rule: a tool never raises into the loop. Failures inside a tool become an
error result the model can read. The exceptions below are for the harness
itself (model transport, configuration, persistence), not for tool logic.

パッケージ全体で使う例外型。

原則: ツールはループへ例外を投げない。ツール内部の失敗は、モデルが読めるエラー
結果になる。以下の例外はハーネス自身のためのものであり（モデルの転送、設定、
永続化）、ツールの処理のためではない。
"""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for harness-level failures.

    ハーネス層の失敗の基底クラス。
    """


class ConfigError(HarnessError):
    """A profile or runtime setting is invalid.

    プロファイルまたはランタイムの設定が不正である。
    """


class ModelError(HarnessError):
    """The model transport failed after retries.

    再試行後もモデルの転送が失敗した。
    """

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class PromptTooLong(ModelError):
    """The request exceeded the model's context window.

    要求がモデルのコンテキストウィンドウを超えた。
    """

    def __init__(self, message: str = "prompt too long"):
        super().__init__(message, retryable=False, status=400)


class Aborted(HarnessError):
    """The run was cancelled through the control file or a signal.

    制御ファイルまたはシグナルによって実行が取り消された。
    """


class SandboxUnavailable(HarnessError):
    """The requested sandbox backend cannot run on this machine.

    要求されたサンドボックスのバックエンドはこのマシンでは動かせない。
    """


TOOL_ERROR_OPEN = "<tool_error>"
TOOL_ERROR_CLOSE = "</tool_error>"


def tool_error_text(message: str) -> str:
    """Wrap an error so the model can tell it apart from normal output.

    モデルが通常の出力と区別できるようにエラーを包む。
    """
    return f"{TOOL_ERROR_OPEN}{message}{TOOL_ERROR_CLOSE}"
