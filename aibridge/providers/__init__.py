"""Provider base class + registry."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


def _stable_uuid(seed: str) -> str:
    """Deterministic UUID-shaped string from any seed string."""
    h = hashlib.sha256(seed.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


@dataclass
class ChatRequest:
    """Normalized chat request dari OpenAI format."""
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    # Stable identifier for the logical conversation so providers can
    # thread turns on their side (monica conv_id, perplexity context uuid, ...).
    # Supplied by the client via `X-Session-Id` header when available; the
    # server falls back to a hash of the non-final messages so the same
    # chat history maps to the same thread on retries.
    session_id: str | None = None

    @classmethod
    def from_openai(cls, body: dict, *, session_id: str | None = None) -> "ChatRequest":
        return cls(
            model=body.get("model") or "",
            messages=body.get("messages") or [],
            stream=bool(body.get("stream")),
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
            session_id=session_id,
        )

    def thread_seed(self) -> str:
        """Stable seed for provider thread ids.

        Prefers an explicit session_id from the client; otherwise hashes the
        conversation "prefix" (every message except the last user turn) so
        successive turns of the same chat collapse onto the same thread.
        """
        if self.session_id:
            return f"sid:{self.session_id}"

        def _flat(c: Any) -> str:
            if isinstance(c, list):
                return "".join(
                    (p.get("text") or "") for p in c if isinstance(p, dict)
                )
            return str(c or "")

        # Drop the last user turn so we get a stable "conversation identity"
        # regardless of which question the user asks next.
        msgs = list(self.messages)
        if msgs and msgs[-1].get("role") == "user":
            msgs = msgs[:-1]
        parts: list[str] = [self.model]
        for m in msgs:
            parts.append(f"{m.get('role','')}::{_flat(m.get('content',''))}")
        # Always include the very first user message so single-turn requests
        # still produce a stable id (prefix would otherwise be empty).
        for m in self.messages:
            if m.get("role") == "user":
                parts.append(f"first::{_flat(m.get('content',''))}")
                break
        return "hash:" + _stable_uuid("|".join(parts))

    def thread_uuid(self) -> str:
        """UUID-shaped derivative of thread_seed, safe to hand to providers."""
        return _stable_uuid(self.thread_seed())


class Provider(Protocol):
    """Semua provider implement interface ini."""

    name: str
    models: list[str]

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def chat(self, req: ChatRequest) -> AsyncIterator[str]:
        """Yield text deltas (incremental response)."""
        ...


_REGISTRY: dict[str, type] = {}


def register(name: str):
    def _wrap(cls):
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return _wrap


def get_provider_class(name: str):
    if name not in _REGISTRY:
        raise KeyError(f"unknown provider: {name}")
    return _REGISTRY[name]


def available() -> list[str]:
    return sorted(_REGISTRY.keys())
