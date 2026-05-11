"""aibridge config — paths, defaults, per-provider metadata."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def home() -> Path:
    """Konfig dir lintas-OS di bawah ~/.aibridge."""
    root = Path(os.environ.get("AIBRIDGE_HOME", Path.home() / ".aibridge"))
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def session_path(provider: str) -> Path:
    return home() / "sessions" / f"{provider}.json"


def log_path(provider: str) -> Path:
    return home() / "logs" / f"{provider}.log"


@dataclass
class ProviderInfo:
    """Static metadata untuk satu provider."""
    name: str
    url: str                 # landing URL (opened pas login)
    default_port: int
    # selector/probe buat nge-verify session udah login dari DOM setelah user
    # click tombol di UI login.
    logged_in_probe: str     # JS expression yang return True kalau logged in


PROVIDERS: dict[str, ProviderInfo] = {
    "monica": ProviderInfo(
        name="monica",
        url="https://monica.im/home/chat/Monica/monica",
        default_port=18788,
        logged_in_probe=(
            "document.cookie.split(';').some(c => c.trim().startsWith('session_id='))"
        ),
    ),
    "perplexity": ProviderInfo(
        name="perplexity",
        url="https://www.perplexity.ai/",
        default_port=18790,
        # Perplexity nunjukin sidebar "Home / Discover / Library" hanya kalau
        # user logged in.
        logged_in_probe=(
            "!!document.querySelector('a[href*=\"/account\"]')"
            " || !!document.querySelector('[data-testid=\"user-menu\"]')"
            " || document.cookie.includes('next-auth.session-token')"
        ),
    ),
}


def need_camoufox_installed() -> None:
    """Cek camoufox ter-install, kalau nggak exit dengan pesan jelas."""
    try:
        import camoufox  # noqa: F401
    except ImportError:
        print(
            "error: package 'camoufox' not installed. run ./install.sh first.",
            file=sys.stderr,
        )
        sys.exit(1)
