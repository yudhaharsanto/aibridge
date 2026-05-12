"""Perplexity provider — direct POST ke /rest/sse/perplexity_ask.

Pola Monica-style: Camoufox cuma jadi tab persistent buat inherit cookies +
TLS fingerprint. Semua request fire lewat page-context fetch, bukan DOM scraping.

Response SSE dengan event `message` tiap incremental state. Answer text ada di
`blocks[].markdown_block.answer` (intended_usage=`ask_text`). Final event punya
`final_sse_message: true`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, AsyncIterator

from ..browser import BrowserSession
from ..config import PROVIDERS, session_path
from . import ChatRequest, register

log = logging.getLogger("aibridge.perplexity")

API_URL = "https://www.perplexity.ai/rest/sse/perplexity_ask"
MODELS_URL = (
    "https://www.perplexity.ai/rest/models/config"
    "?config_schema=v1&version=2.18&source=default"
)

# OpenAI-style alias → internal `model_preference` slug di Perplexity.
# Exposed models (Pro plan). Setiap family dapet both thinking & non-thinking
# variant biar user bisa pilih sesuai use case (speed vs depth).
MODEL_MAP: dict[str, str] = {
    # --- Perplexity in-house ---
    "sonar-2":                    "experimental",
    "auto":                       "pplx_pro",
    "best":                       "turbo",
    # --- OpenAI family ---
    "gpt-5":                      "gpt5",
    "gpt-5-thinking":             "gpt5_thinking",
    "gpt-5.1":                    "gpt51",
    "gpt-5.1-thinking":           "gpt51_thinking",
    "gpt-5.2":                    "gpt52",
    "gpt-5.2-thinking":           "gpt52_thinking",
    "gpt-5.4":                    "gpt54",
    "gpt-5.4-thinking":           "gpt54_thinking",
    "gpt-5.5":                    "gpt55",
    "gpt-5.5-thinking":           "gpt55_thinking",
    # --- Claude family ---
    "claude-sonnet-4.5":          "claude45sonnet",
    "claude-sonnet-4.5-thinking": "claude45sonnetthinking",
    "claude-sonnet-4.6":          "claude46sonnet",
    "claude-sonnet-4.6-thinking": "claude46sonnetthinking",
    "claude-opus-4.6":            "claude46opus",
    "claude-opus-4.6-thinking":   "claude46opusthinking",
    "claude-opus-4.7":            "claude47opus",
    "claude-opus-4.7-thinking":   "claude47opusthinking",
    # --- Gemini family ---
    "gemini-3-pro":               "gemini30pro",
    "gemini-3-flash":             "gemini30flash",
    "gemini-3-flash-thinking":    "gemini30flash_high",
    "gemini-3.1-pro":             "gemini31pro_low",
    "gemini-3.1-pro-thinking":    "gemini31pro_high",
    # --- Kimi family ---
    "kimi-k2.5-thinking":         "kimik25thinking",
    "kimi-k2.6":                  "kimik26instant",
    "kimi-k2.6-thinking":         "kimik26thinking",
    # --- Grok family ---
    "grok-4":                     "grok4nonthinking",
    "grok-4-thinking":            "grok4",
    "grok-4.1":                   "grok41nonreasoning",
    "grok-4.1-thinking":          "grok41reasoning",
    # --- NVIDIA ---
    "nemotron-3-super":           "nv_nemotron_3_super",
}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "Referer": "https://www.perplexity.ai/",
    "Origin": "https://www.perplexity.ai",
}


def _last_user_message(messages: list[dict]) -> str:
    """Perplexity = search engine, bukan chat LLM.

    System prompt + history di-collapse jadi query tunggal tidak makes sense
    (bales template \"Mode aktif\"). Ambil user message terakhir aja.
    """
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            s = str(c).strip()
            if s:
                return s
    raise ValueError("no user message")


def _parse_sse_events(raw: str):
    """Yield (event_name, data_obj) pairs dari raw SSE buffer."""
    cur_event = None
    cur_data = None
    for line in raw.splitlines():
        if line.startswith("event:"):
            cur_event = line[6:].strip()
        elif line.startswith("data:"):
            try:
                cur_data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                cur_data = None
        elif not line:
            if cur_event and cur_data is not None:
                yield cur_event, cur_data
            cur_event, cur_data = None, None
    if cur_event and cur_data is not None:
        yield cur_event, cur_data


def _extract_answer_text(event_data: dict) -> str | None:
    """
    Ekstrak answer markdown dari event. Prefer intended_usage='ask_text'
    (final answer), fallback ke 'ask_text_0_markdown' (incremental stream).
    """
    blocks = event_data.get("blocks") or []
    best = None
    for b in blocks:
        iu = b.get("intended_usage", "")
        mb = b.get("markdown_block")
        if not mb:
            continue
        ans = mb.get("answer")
        if not ans or not isinstance(ans, str):
            continue
        if iu == "ask_text":
            return ans
        if iu.startswith("ask_text") and best is None:
            best = ans
    return best


def _extract_sources(event_data: dict) -> list[dict]:
    for b in event_data.get("blocks") or []:
        wr = b.get("web_result_block")
        if wr:
            results = wr.get("web_results") or []
            out = []
            for r in results:
                url = r.get("url") or r.get("link")
                title = r.get("name") or r.get("title") or url
                if url:
                    out.append({"title": title, "url": url})
            return out[:10]
    return []


@register("perplexity")
class PerplexityProvider:
    name = "perplexity"
    models = list(MODEL_MAP.keys())

    def __init__(self, *, headless: bool = True) -> None:
        self._session: BrowserSession | None = None
        self.headless = headless

    async def start(self) -> None:
        info = PROVIDERS[self.name]
        self._session = BrowserSession(
            session_path(self.name),
            info.url,
            headless=self.headless,
        )
        await self._session.start()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def chat(self, req: ChatRequest) -> AsyncIterator[str]:
        if self._session is None:
            raise RuntimeError("provider not started")

        query = _last_user_message(req.messages)
        model_pref = MODEL_MAP.get(req.model, req.model)

        # `frontend_uuid` is per-turn (the request id); re-generate each call.
        # `frontend_context_uuid` threads turns together on Perplexity's
        # side — derive it deterministically from the conversation identity
        # so follow-ups land in the same chat instead of spawning new ones.
        fuuid = str(uuid.uuid4())
        context_uuid = req.thread_uuid()
        payload = {
            "query_str": query,
            "params": {
                "attachments": [],
                "language": "en-US",
                "timezone": "Asia/Jakarta",
                "search_focus": "internet",
                "sources": ["web"],
                "frontend_uuid": fuuid,
                "mode": "copilot",
                "model_preference": model_pref,
                "is_related_query": False,
                "is_sponsored": False,
                "frontend_context_uuid": context_uuid,
                "prompt_source": "user",
                "query_source": "home",
                "is_incognito": False,
                "dsl_query": query,
                "source": "default",
                "client_search_results_cache_key": fuuid,
            },
        }

        # Full raw stream buffer, tapi kita derive delta dari answer field
        # dengan tracking length yang udah di-yield.
        raw = ""
        emitted = 0
        final_data: dict | None = None
        sent_any = False

        async for chunk in self._session.stream(
            API_URL,
            method="POST",
            headers=HEADERS,
            json_body=payload,
            timeout_ms=180_000,
            poll_ms=300,
        ):
            raw += chunk
            # Re-parse full buffer biar gak repot handle partial events.
            # Cari answer terbaru tiap chunk masuk.
            latest_answer = ""
            for ev, data in _parse_sse_events(raw):
                if ev == "message" and data:
                    ans = _extract_answer_text(data)
                    if ans:
                        latest_answer = ans
                    if data.get("final_sse_message"):
                        final_data = data
            if len(latest_answer) > emitted:
                delta = latest_answer[emitted:]
                emitted = len(latest_answer)
                if delta:
                    sent_any = True
                    yield delta
            if final_data:
                break

        # Append sources setelah answer kalau ada.
        if final_data is None:
            # Try parse once more in case stream ended without explicit final.
            for _, data in _parse_sse_events(raw):
                if data.get("final_sse_message") or data.get("status") == "COMPLETED":
                    final_data = data
        if final_data:
            sources = _extract_sources(final_data)
            if sources:
                yield "\n\n**Sources:**\n"
                for s in sources:
                    title = s.get("title") or s.get("url")
                    yield f"- [{title}]({s['url']})\n"

        if not sent_any:
            # Hard failure protection: emit raw snippet biar bisa debug.
            log.warning("perplexity returned no answer. raw snippet: %s", raw[:500])
            raise RuntimeError("perplexity returned no answer text")
