"""First-run bootstrap: fetch the Camoufox browser binary and set up config dirs.

Exposed via `aibridge setup`. Idempotent: re-running only refreshes the
browser if Camoufox reports a newer version.
"""
from __future__ import annotations

import subprocess
import sys

from .config import home


def run() -> int:
    print("==> Preparing aibridge config directory...")
    root = home()
    print(f"    {root}")

    print("==> Checking Camoufox package...")
    try:
        import camoufox  # noqa: F401
    except ImportError:
        print(
            "error: the 'camoufox' package is not installed.\n"
            "       try:  pip install --upgrade aibridge",
            file=sys.stderr,
        )
        return 1

    print("==> Fetching Camoufox browser binary (~200 MB, one-time)...")
    # `python -m camoufox fetch` handles caching + incremental updates.
    rc = subprocess.call([sys.executable, "-m", "camoufox", "fetch"])
    if rc != 0:
        print(
            "warning: 'camoufox fetch' returned a non-zero exit code.\n"
            "         check the output above; you can re-run 'aibridge setup' later.",
            file=sys.stderr,
        )

    print()
    print("==> Done.")
    print("    Next: log in to a provider, e.g.")
    print("          aibridge login monica")
    print("          aibridge start monica")
    return 0
