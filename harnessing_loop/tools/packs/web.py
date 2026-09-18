"""Web tools. They run in the harness process, never in the sandbox, and
never execute anything they fetch.

- domain allowlist from the profile (`web.allowed_domains`, "*" for any)
- private and link-local addresses are refused, so a fetched page cannot be
  used to reach internal services or cloud metadata endpoints
- HTML is reduced to text and capped
"""

from __future__ import annotations

import fnmatch
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

from ..base import Tool, ToolContext, ToolResult

MAX_FETCH_CHARS = 40_000
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+\n|\n{3,}")


def _allowed(host: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(host.lower(), p.lower()) for p in patterns)


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_link_local or ip.is_loopback or ip.is_reserved or ip.is_multicast:
            return True
    return False


def html_to_text(raw: str) -> str:
    raw = _TAG.sub(" ", raw)
    raw = re.sub(r"</(p|div|br|li|h\d|tr)>", "\n", raw, flags=re.I)
    text = html.unescape(_TAGS.sub(" ", raw))
    text = re.sub(r"[ \t]+", " ", text)
    text = _WS.sub("\n\n", text)
    return text.strip()


class WebFetch(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its text content. Only allowed domains can be fetched."
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 100}},
        "required": ["url"],
        "additionalProperties": False,
    }
    category = "web"
    read_only = True
    concurrency_safe = True

    def permission_subjects(self, input: dict[str, Any]) -> Iterable[str]:
        return [urllib.parse.urlparse(input.get("url", "")).hostname or ""]

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        u = urllib.parse.urlparse(input["url"])
        if u.scheme not in ("http", "https") or not u.hostname:
            return "Only http and https URLs are supported."
        patterns = ctx.extra.get("allowed_domains") or []
        if not _allowed(u.hostname, patterns):
            return f"Domain {u.hostname} is not in the allowed list."
        if _is_private(u.hostname):
            return "Private, loopback and link-local addresses cannot be fetched."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        req = urllib.request.Request(input["url"], headers={"User-Agent": "harnessing_loop/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read(5 * 1024 * 1024).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return ToolResult.error(f"HTTP {exc.code} for {input['url']}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ToolResult.error(f"Fetch failed: {exc}")
        text = html_to_text(raw) if "html" in ctype.lower() else raw
        limit = int(input.get("max_chars", MAX_FETCH_CHARS))
        if len(text) > limit:
            text = text[:limit] + f"\n... [truncated at {limit:,} chars]"
        return ToolResult.ok(text or "(empty response)", fetched=True)


class WebSearch(Tool):
    name = "web_search"
    description = "Search the web. Returns titles, URLs and snippets."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}, "max_results": {"type": "integer", "minimum": 1, "maximum": 20}},
        "required": ["query"],
        "additionalProperties": False,
    }
    category = "web"
    read_only = True
    concurrency_safe = True

    def validate(self, input: dict[str, Any], ctx: ToolContext) -> str | None:
        if not callable(ctx.extra.get("search_fn")):
            return "No search provider is configured. Set extra['search_fn'] in the profile or runtime."
        return None

    def call(self, input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        fn = ctx.extra["search_fn"]
        rows = fn(input["query"], int(input.get("max_results", 8)))
        if not rows:
            return ToolResult.ok("No results.")
        out = [f"- {r.get('title', '')}\n  {r.get('url', '')}\n  {r.get('snippet', '')}" for r in rows]
        return ToolResult.ok("\n".join(out))


def make() -> list[Tool]:
    return [WebFetch(), WebSearch()]
