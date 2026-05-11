"""Self-update logic: upgrade aibridge from GitHub in-place.

Runs `pip install --upgrade git+https://github.com/yudhaharsanto/aibridge.git`
against the interpreter that's running aibridge, then asks the user to
restart the daemons.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from urllib.error import URLError

from . import __version__


REPO = "yudhaharsanto/aibridge"
REPO_URL = f"https://github.com/{REPO}.git"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
COMMITS_API = f"https://api.github.com/repos/{REPO}/commits/main"


def _latest_tag() -> str | None:
    """Return the tag of the latest GitHub release, or None if no releases."""
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            return (data.get("tag_name") or "").lstrip("v") or None
    except (URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def _latest_commit() -> str | None:
    try:
        req = urllib.request.Request(
            COMMITS_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            sha = data.get("sha", "")
            return sha[:7] if sha else None
    except (URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def check() -> int:
    """Print the installed version and the latest available one."""
    print(f"==> installed: v{__version__}")

    tag = _latest_tag()
    commit = _latest_commit()
    if tag:
        print(f"==> latest release:    v{tag}")
        if tag != __version__:
            print(f"==> update available: 'aibridge update' to upgrade")
        else:
            print("==> up to date.")
    else:
        print("==> no tagged releases on GitHub yet.")

    if commit:
        print(f"==> latest commit (main): {commit}")

    return 0


def upgrade(*, source: str = "main") -> int:
    """Upgrade aibridge to the latest main branch (or a tag/ref)."""
    ref = f"git+{REPO_URL}@{source}"
    print(f"==> upgrading aibridge from {ref}")
    print(f"    using python: {sys.executable}")

    rc = subprocess.call(
        [sys.executable, "-m", "pip", "install", "--upgrade", ref]
    )
    if rc != 0:
        print("error: pip upgrade failed. see output above.", file=sys.stderr)
        return rc

    # Read the new version by re-invoking the installed aibridge.
    try:
        out = subprocess.check_output(
            [sys.executable, "-c", "import aibridge; print(aibridge.__version__)"],
            text=True,
            timeout=10,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        out = "unknown"

    print()
    print(f"==> upgrade complete. installed version: v{out}")
    print()
    print("    To apply changes to running daemons, restart them:")
    print("      aibridge restart all")
    return 0
