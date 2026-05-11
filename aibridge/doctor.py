"""`aibridge doctor` — quick health / troubleshooting report.

Checks each provider for:
  1. Session file present
  2. Daemon running
  3. Port listening
  4. HTTP 200 on /v1/models with the stored API key
  5. Camoufox installation
  6. Python version

Prints a concise pass/fail table.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .config import PROVIDERS, home, log_path, session_path


def _check_mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _check_python() -> tuple[bool, str]:
    if sys.version_info < (3, 10):
        return False, f"Python {sys.version_info.major}.{sys.version_info.minor} (need ≥ 3.10)"
    return True, f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _check_camoufox() -> tuple[bool, str]:
    try:
        import camoufox  # noqa: F401
    except ImportError:
        return False, "not installed  (run: aibridge setup)"
    # Try to locate the browser binary.
    try:
        from camoufox import utils  # type: ignore
        # different camoufox versions expose different helpers; best-effort
        if hasattr(utils, "installed_verstr"):
            return True, utils.installed_verstr()
    except Exception:
        pass
    return True, "installed"


def _check_9router() -> tuple[bool, str]:
    url = os.environ.get("AIBRIDGE_9ROUTER_URL", "http://localhost:20128")
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
            if r.status == 200:
                return True, f"reachable at {url}"
    except urllib.error.URLError:
        pass
    except Exception:
        pass
    # Not fatal: 9router might not even be installed.
    return False, f"not reachable at {url}  (optional)"


def _check_provider(name: str) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []

    # Session
    spath = session_path(name)
    rows.append(("session", spath.exists(),
                 f"{spath}" if spath.exists() else "missing  (run: aibridge login)"))

    # Daemon / port
    from .daemon import _pids_on_port, _read_pid, _alive
    info = PROVIDERS[name]
    port = info.default_port
    pid = _read_pid(name)
    port_pids = _pids_on_port(port)

    running = bool(pid and _alive(pid)) or bool(port_pids)
    detail = ""
    if running:
        effective_pid = pid if (pid and _alive(pid)) else port_pids[0]
        detail = f"pid {effective_pid} on :{port}"
    else:
        detail = f"stopped  (run: aibridge start {name})"
    rows.append(("daemon", running, detail))

    # HTTP auth check
    if running:
        from . import tokens
        token = tokens.get(name)
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                rows.append(("api key", r.status == 200,
                             f"HTTP {r.status} at /v1/models"))
        except urllib.error.HTTPError as e:
            rows.append(("api key", False, f"HTTP {e.code}"))
        except urllib.error.URLError as e:
            rows.append(("api key", False, f"connection failed: {e.reason}"))
    else:
        rows.append(("api key", False, "skipped  (daemon not running)"))

    # Log file tail hint
    log = log_path(name)
    if log.exists():
        rows.append(("log", True, f"{log} ({log.stat().st_size} bytes)"))
    else:
        rows.append(("log", False, "no log yet"))

    return rows


def run() -> int:
    print(f"aibridge v{__version__}  ({platform.system()} {platform.machine()})")
    print(f"home: {home()}")
    print()

    print("=== environment ===")
    for label, (ok, detail) in [
        ("python", _check_python()),
        ("camoufox", _check_camoufox()),
        ("9router", _check_9router()),
    ]:
        print(f"  [{_check_mark(ok)}] {label:<10} {detail}")
    print()

    any_issue = False
    for name in PROVIDERS:
        print(f"=== {name} ===")
        rows = _check_provider(name)
        for label, ok, detail in rows:
            print(f"  [{_check_mark(ok)}] {label:<10} {detail}")
            if not ok and label in ("session", "daemon", "api key"):
                any_issue = True
        print()

    if any_issue:
        print("==> issues detected. common fixes:")
        print("    - missing session → aibridge login <provider>")
        print("    - daemon stopped  → aibridge start <provider>")
        print("    - api key failing → aibridge restart <provider>")
        print("    - stuck/broken    → aibridge stop <provider> && aibridge start <provider>")
        return 1

    print("==> all good.")
    return 0
