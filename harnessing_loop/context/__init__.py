"""Context management: what the model sees each turn and how it is kept small."""

from .tokens import conversation_tokens, estimate_tokens
from .compact import should_compact, is_blocking, compact
from .microcompact import microcompact
from .rebuild import build_post_compact
from .attachments import AttachmentState, build_attachments
from .system_prompt import assemble_static_prompt, dynamic_prompt, load_instruction_files

__all__ = [
    "conversation_tokens",
    "estimate_tokens",
    "should_compact",
    "is_blocking",
    "compact",
    "microcompact",
    "build_post_compact",
    "AttachmentState",
    "build_attachments",
    "assemble_static_prompt",
    "dynamic_prompt",
    "load_instruction_files",
]
