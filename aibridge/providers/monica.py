"""Monica provider — drive monica.im lewat Camoufox session."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, AsyncIterator

from ..browser import BrowserSession
from ..config import PROVIDERS, session_path
from . import ChatRequest, register

log = logging.getLogger("aibridge.monica")

API_URL = "https://api.monica.im/api/custom_bot/chat"

# OpenAI model id -> monica internal chat_model slug
MODEL_MAP: dict[str, str] = {
    "claude-sonnet-4-6":  "claude_4_6_sonnet",
    "claude-sonnet-4-5":  "claude_4_5_sonnet",
    "claude-opus-4-1":    "claude_4_1_opus",
    "gpt-5":              "gpt_5",
    "gpt-5-5":            "gpt_5_5",
    "gpt-4o":             "gpt_4o",
    "gemini-2-5-pro":     "gemini_2_5_pro",
    "gemini-3-1-pro":     "gemini_3_1_pro_preview_think",
}


def _stable_uuid(seed: str) -> str:
    h = hashlib.sha256(seed.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _new_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def _flatten(content: Any) -> str:
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        ).strip()
    return str(content or "").strip()


def _build_payload(model: str, messages: list[dict], *, thread_seed: str | None = None) -> dict:
    chat_model = MODEL_MAP.get(model, model.replace("-", "_"))

    systems, turns = [], []
    for m in messages:
        role = m.get("role", "user")
        t = _flatten(m.get("content", ""))
        if not t:
            continue
        if role == "system":
            systems.append(t)
        else:
            turns.append({"role": role, "text": t})
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("last message must be user role")

    sys_prefix = (
        "[system instructions]\n" + "\n\n".join(systems) + "\n\n" if systems else ""
    )
    first_user = next((t["text"] for t in turns if t["role"] == "user"), "")
    # Prefer the caller-supplied thread_seed (client X-Session-Id or hash of
    # the conversation prefix) so follow-ups stay on the same monica thread.
    # Fall back to the legacy seed for single-turn one-shots without context.
    seed = thread_seed or f"{model}|{systems}|{first_user}"
    conv_id = f"conv:{_stable_uuid(seed)}"
    welcome_id = f"msg:{_stable_uuid('welcome|' + conv_id)}"

    items = [{
        "item_id": welcome_id,
        "conversation_id": conv_id,
        "item_type": "reply",
        "summary": "__RENDER_BOT_WELCOME_MSG__",
        "data": {"type": "text", "content": "__RENDER_BOT_WELCOME_MSG__"},
    }]

    prev_id = welcome_id
    applied_sys = False
    last_q_id = None
    last_idx = len(turns) - 1

    for i, m in enumerate(turns):
        text = m["text"]
        is_last = i == last_idx
        if m["role"] == "user":
            if not applied_sys and sys_prefix:
                text = sys_prefix + text
                applied_sys = True
            mid = _new_id("msg") if is_last else f"msg:{_stable_uuid(f'q|{conv_id}|{i}|{text}')}"
            if is_last:
                last_q_id = mid
            items.append({
                "item_id": mid,
                "conversation_id": conv_id,
                "item_type": "question",
                "summary": text[:200],
                "parent_item_id": prev_id,
                "data": {
                    "type": "text",
                    "content": text,
                    "quote_content": "",
                    "chat_model": chat_model,
                    "max_token": 0,
                    "is_incognito": False,
                },
            })
        else:
            mid = f"msg:{_stable_uuid(f'a|{conv_id}|{i}|{text}')}"
            items.append({
                "item_id": mid,
                "conversation_id": conv_id,
                "item_type": "reply",
                "summary": text[:200],
                "parent_item_id": prev_id,
                "data": {"type": "text", "content": text, "use_model": model},
            })
        prev_id = mid

    return {
        "task_uid": _new_id("task"),
        "bot_uid": "monica",
        "data": {
            "conversation_id": conv_id,
            "items": items,
            "pre_generated_reply_id": _new_id("msg"),
            "pre_parent_item_id": last_q_id,
            "origin": "https://monica.im/home/chat/Monica/monica",
            "origin_page_title": "Monica",
            "trigger_by": "auto",
            "use_model": model,
            "knowledge_source": "developer",
            "is_incognito": False,
            "use_new_memory": True,
            "use_memory_suggestion": True,
        },
        "language": "auto",
        "locale": "en",
        "task_type": "chat_with_custom_bot",
        "tool_data": {
            "sys_skill_list": [
                {"uid": "web_access",       "allow_user_modify": False, "enable": True,  "force_enable": False},
                {"uid": "draw_image",       "allow_user_modify": False, "enable": True,  "force_enable": False},
                {"uid": "code_interpreter", "allow_user_modify": False, "enable": True,  "force_enable": False},
                {"uid": "artifacts",        "allow_user_modify": False, "enable": True,  "force_enable": False},
            ],
        },
        "ai_resp_language": "auto",
    }


MONICA_HEADERS = {
    "Content-Type": "application/json",
    "X-Client-Type": "web",
    "X-Product-Name": "Monica",
    "X-Client-Locale": "en",
    "X-Client-Version": "5.4.3",
    "X-Time-Zone": "Asia/Jakarta;-420",
    "Origin": "https://monica.im",
    "Referer": "https://monica.im/",
    "Accept": "*/*",
}


@register("monica")
class MonicaProvider:
    name = "monica"
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
        payload = _build_payload(
            req.model,
            req.messages,
            thread_seed=req.thread_seed(),
        )

        async for chunk in self._session.stream(
            API_URL,
            method="POST",
            headers=MONICA_HEADERS,
            json_body=payload,
            timeout_ms=180_000,
        ):
            # chunk bisa berisi beberapa 'data: ...' SSE lines
            for line in chunk.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                text = obj.get("text", "")
                if text:
                    yield text
                if obj.get("finished"):
                    return
