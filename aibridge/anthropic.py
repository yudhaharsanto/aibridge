"""Anthropic Messages API ↔ internal OpenAI-shaped format translator.

aibridge speaks OpenAI shape internally (`ChatRequest` is a thin wrapper over
the OpenAI chat/completions body). This module bridges the Anthropic
Messages API so clients like Claude Code and Cline can point at aibridge
directly without a separate adapter.

Scope:
- `anthropic_from_openai_body(body)` — Anthropic request → OpenAI-shaped
  body, ready to feed to `ChatRequest.from_openai`.
- `anthropic_final_body(mid, model, text)` — wrap accumulated provider
  text as an Anthropic `Message` object for non-streaming responses.
- `anthropic_event_lines_start / _delta / _stop` — emit the SSE lines
  Anthropic's streaming format expects (`message_start`,
  `content_block_start`, `content_block_delta`, `content_block_stop`,
  `message_delta`, `message_stop`).

We intentionally ignore things the underlying web providers don't support
(tool_use, images inside input, top_k, etc.) — they would be dropped on
the floor by the provider anyway, and silently translating avoids breaking
clients that always send them.
"""
from __future__ import annotations

import json
from typing import Any, Iterable


def _flatten_anthropic_content(content: Any) -> str | list[dict[str, Any]]:
    """Anthropic `content` → internal (OpenAI-shaped) content.

    - String in → string out.
    - List in → returns a string when only text blocks are present, or a
      list of `{type: "text"|"image_url", ...}` dicts when images are mixed
      in. Tool-use / tool-result blocks are dropped silently because the
      underlying web providers can't honor them.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        images: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("text", "input_text"):
                t = block.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
            elif btype == "image":
                src = block.get("source") or {}
                src_type = src.get("type")
                if src_type == "base64":
                    media = src.get("media_type") or "image/png"
                    data = src.get("data") or ""
                    if isinstance(data, str) and data:
                        images.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{data}"},
                        })
                elif src_type == "url":
                    url = src.get("url")
                    if isinstance(url, str) and url:
                        images.append({
                            "type": "image_url",
                            "image_url": {"url": url},
                        })
            # tool_use / tool_result: skipped
        if images:
            out: list[dict[str, Any]] = []
            joined = "".join(text_parts)
            if joined:
                out.append({"type": "text", "text": joined})
            out.extend(images)
            return out
        return "".join(text_parts)
    # Unknown shape: best-effort stringify.
    return str(content)


def _flatten_anthropic_system(system: Any) -> str:
    """Anthropic `system` is either a string or a list of system blocks."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n\n".join(p for p in parts if p)
    return str(system)


def anthropic_from_openai_body(body: dict) -> dict:
    """Translate an Anthropic /v1/messages request body to OpenAI shape.

    Returns a dict suitable for `ChatRequest.from_openai`.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("model is required")

    raw_msgs = body.get("messages")
    if not isinstance(raw_msgs, list) or not raw_msgs:
        raise ValueError("messages must be a non-empty array")

    out_messages: list[dict[str, Any]] = []

    system_text = _flatten_anthropic_system(body.get("system"))
    if system_text:
        out_messages.append({"role": "system", "content": system_text})

    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            # Anthropic only has user/assistant at the `messages[].role` level.
            # Skip anything unknown rather than crash the request.
            continue
        flattened = _flatten_anthropic_content(m.get("content"))
        # Drop empties (str "" or []) but keep list-with-images even if the
        # text portion was blank.
        if isinstance(flattened, str):
            if not flattened:
                continue
        elif isinstance(flattened, list):
            if not flattened:
                continue
        out_messages.append({"role": role, "content": flattened})

    if not out_messages or not any(m["role"] == "user" for m in out_messages):
        raise ValueError("at least one user message with text content is required")

    openai_body: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": bool(body.get("stream")),
    }
    # Optional knobs — forward when present so downstream providers can honor
    # them if they choose to.
    if "max_tokens" in body:
        openai_body["max_tokens"] = body.get("max_tokens")
    if "temperature" in body:
        openai_body["temperature"] = body.get("temperature")
    return openai_body


def anthropic_stop_reason(finish: str | None) -> str:
    """Map an OpenAI finish_reason to Anthropic stop_reason."""
    if finish in (None, "stop"):
        return "end_turn"
    if finish == "length":
        return "max_tokens"
    if finish == "tool_calls":
        return "tool_use"
    return "end_turn"


def anthropic_final_body(mid: str, model: str, text: str,
                         *, stop_reason: str = "end_turn") -> dict:
    """Non-streaming Anthropic `Message` response payload."""
    return {
        "id": mid,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            # We don't have real token counts from the web backends, so we
            # expose rough character-length figures. Keeps clients that
            # display usage from blowing up.
            "input_tokens": 0,
            "output_tokens": len(text),
        },
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def anthropic_event_lines_start(mid: str, model: str) -> Iterable[str]:
    """Emit the opening events of an Anthropic streaming response.

    Order matches the official spec:
        message_start → content_block_start → ping
    """
    message_obj = {
        "id": mid,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    yield _sse("message_start", {"type": "message_start", "message": message_obj})
    yield _sse("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    yield _sse("ping", {"type": "ping"})


def anthropic_event_lines_delta(text: str) -> Iterable[str]:
    """Emit a single `content_block_delta` SSE event."""
    yield _sse("content_block_delta", {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    })


def anthropic_event_lines_stop(
    *, stop_reason: str = "end_turn",
    output_tokens: int = 0,
) -> Iterable[str]:
    """Emit the closing events of an Anthropic streaming response."""
    yield _sse("content_block_stop", {
        "type": "content_block_stop",
        "index": 0,
    })
    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})
