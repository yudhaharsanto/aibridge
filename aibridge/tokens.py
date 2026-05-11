"""Per-provider API key management.

Each provider has a client-facing API key that incoming HTTP requests must
present as `Authorization: Bearer <key>`. Keys live in
`~/.aibridge/tokens.json` with 0600 permissions.

Keys are auto-generated on first use. Users can rotate them via
`aibridge config set-key <provider> <new-key>` or `aibridge config rotate
<provider>` for a random one.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .config import home


TOKENS_FILE = "tokens.json"


def _tokens_path() -> Path:
    return home() / TOKENS_FILE


def _load_all() -> dict[str, str]:
    p = _tokens_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict[str, str]) -> None:
    p = _tokens_path()
    p.write_text(json.dumps(data, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _generate() -> str:
    """Generate a random token like 'aibridge-<32-hex-chars>'."""
    return "aibridge-" + secrets.token_hex(16)


def get(provider: str) -> str:
    """Return the token for `provider`, generating one if missing."""
    data = _load_all()
    if provider in data and data[provider]:
        return data[provider]
    tok = _generate()
    data[provider] = tok
    _save_all(data)
    return tok


def set_key(provider: str, token: str) -> None:
    """Overwrite the token for a provider."""
    data = _load_all()
    data[provider] = token.strip()
    _save_all(data)


def unset(provider: str) -> None:
    """Remove the token (next access will regenerate)."""
    data = _load_all()
    if provider in data:
        del data[provider]
        _save_all(data)


def rotate(provider: str) -> str:
    """Generate and store a new random token."""
    tok = _generate()
    set_key(provider, tok)
    return tok


def list_all() -> dict[str, str]:
    """Return all known provider tokens."""
    return _load_all()
