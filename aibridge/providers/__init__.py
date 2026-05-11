"""Provider base class + registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


@dataclass
class ChatRequest:
    """Normalized chat request dari OpenAI format."""
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None

    @classmethod
    def from_openai(cls, body: dict) -> "ChatRequest":
        return cls(
            model=body.get("model") or "",
            messages=body.get("messages") or [],
            stream=bool(body.get("stream")),
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
        )


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
