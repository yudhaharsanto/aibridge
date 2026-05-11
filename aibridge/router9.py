"""9router registration: auto-create/update provider-node entries.

Endpoint reverse-engineered:
  GET    /api/provider-nodes               -> list
  POST   /api/provider-nodes               -> create
  PUT    /api/provider-nodes/<id>          -> update
  DELETE /api/provider-nodes/<id>          -> remove

Auth via Bearer token in env AIBRIDGE_9ROUTER_KEY (falls back to prompt).
Base URL defaults to http://localhost:20128; override via AIBRIDGE_9ROUTER_URL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import PROVIDERS, home

DEFAULT_URL = "http://localhost:20128"
KEY_FILE = ".9router-key"
URL_FILE = ".9router-url"


# --------- config storage ---------

def _cfg_key_path() -> Path:
    return home() / KEY_FILE


def _cfg_url_path() -> Path:
    return home() / URL_FILE


def _read_key() -> str:
    env = os.environ.get("AIBRIDGE_9ROUTER_KEY", "").strip()
    if env:
        return env
    p = _cfg_key_path()
    if p.exists():
        return p.read_text().strip()
    return ""


def _read_url() -> str:
    env = os.environ.get("AIBRIDGE_9ROUTER_URL", "").strip()
    if env:
        return env.rstrip("/")
    p = _cfg_url_path()
    if p.exists():
        return p.read_text().strip().rstrip("/")
    return DEFAULT_URL


def _save_key(key: str) -> None:
    p = _cfg_key_path()
    p.write_text(key.strip())
    try:
        p.chmod(0o600)
    except Exception:
        pass


def _save_url(url: str) -> None:
    _cfg_url_path().write_text(url.rstrip("/"))


# --------- HTTP helpers ---------

def _req(method: str, path: str, *, body: dict | None = None, key: str, base: str) -> tuple[int, dict | list | None]:
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        url,
        method=method,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(text) if text else None
            except json.JSONDecodeError:
                return r.status, {"raw": text[:300]}
    except HTTPError as e:
        text = ""
        try:
            text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, {"error": text or str(e)}
    except URLError as e:
        return 0, {"error": f"connection error: {e.reason}"}


# --------- public operations ---------

def _fetch_nodes(key: str, base: str) -> list[dict]:
    code, body = _req("GET", "/api/provider-nodes", key=key, base=base)
    if code != 200 or not isinstance(body, dict):
        raise RuntimeError(f"GET provider-nodes failed: {code} {body}")
    return body.get("nodes", [])


def _find_node_by_prefix(nodes: list[dict], prefix: str) -> dict | None:
    for n in nodes:
        if n.get("prefix") == prefix:
            return n
    return None


def register(provider: str, *, port: int | None = None, prefix: str | None = None) -> int:
    """Ensure a provider-node + active credential exist for `provider`."""
    if provider not in PROVIDERS:
        print(f"error: unknown provider '{provider}'", file=sys.stderr)
        return 2

    key = _read_key()
    if not key:
        print("==> 9router API key not found.")
        print("    get it from http://localhost:20128/dashboard → Keys")
        try:
            key = input("    paste key: ").strip()
        except EOFError:
            key = ""
        if not key:
            print("error: no key provided", file=sys.stderr)
            return 2
        _save_key(key)
        print(f"==> saved to {_cfg_key_path()}")

    base = _read_url()
    info = PROVIDERS[provider]
    port = port or info.default_port
    prefix = prefix or provider
    local_base = f"http://127.0.0.1:{port}/v1"
    name = provider.capitalize()

    node_payload = {
        "type": "openai-compatible",
        "apiType": "chat",
        "name": name,
        "prefix": prefix,
        "baseUrl": local_base,
    }

    # ---- 1) upsert provider-node ----
    try:
        nodes = _fetch_nodes(key, base)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    existing = _find_node_by_prefix(nodes, prefix)
    if existing:
        node_id = existing["id"]
        code, body = _req(
            "PUT", f"/api/provider-nodes/{node_id}",
            body=node_payload, key=key, base=base,
        )
        if code != 200:
            print(f"error: update node failed ({code}): {body}", file=sys.stderr)
            return 1
        print(f"==> updated provider-node: id={node_id}, prefix={prefix}, baseUrl={local_base}")
    else:
        code, body = _req(
            "POST", "/api/provider-nodes",
            body=node_payload, key=key, base=base,
        )
        if code not in (200, 201):
            print(f"error: create node failed ({code}): {body}", file=sys.stderr)
            return 1
        node = body.get("node", {}) if isinstance(body, dict) else {}
        node_id = node.get("id")
        print(f"==> created provider-node: id={node_id}, prefix={prefix}, baseUrl={local_base}")

    # ---- 2) ensure active connection (credential) ----
    from . import tokens as _tokens
    local_api_key = _tokens.get(provider)

    conn_existing = _find_connection_by_node(key, base, node_id)
    conn_payload = {
        "provider": node_id,
        "apiKey": local_api_key,
        "name": name,
        "priority": 1,
        "providerSpecificData": {
            "prefix": prefix,
            "apiType": "chat",
            "baseUrl": local_base,
            "nodeName": name,
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        },
    }
    if conn_existing:
        conn_id = conn_existing["id"]
        code, body = _req(
            "PUT", f"/api/providers/{conn_id}",
            body=conn_payload, key=key, base=base,
        )
        if code != 200:
            print(f"warning: update connection returned {code}: {body}", file=sys.stderr)
        else:
            print(f"==> updated connection (credential): id={conn_id}")
    else:
        code, body = _req(
            "POST", "/api/providers",
            body=conn_payload, key=key, base=base,
        )
        if code not in (200, 201):
            print(f"error: create connection failed ({code}): {body}", file=sys.stderr)
            return 1
        conn_id = (body.get("connection") or {}).get("id") if isinstance(body, dict) else None
        print(f"==> created connection (credential): id={conn_id}")

    # ---- 3) pretty summary ----
    print()
    print("==> available models via 9router:")
    from .providers import get_provider_class
    cls = get_provider_class(provider)
    for m in cls.models:
        print(f"     - {prefix}/{m}")
    print()
    print("==> test with curl:")
    print(f"    curl {base}/v1/chat/completions \\")
    print(f"      -H 'Authorization: Bearer <9router-key>' \\")
    print(f"      -H 'Content-Type: application/json' \\")
    print(f"      -d '{{\"model\":\"{prefix}/{cls.models[0]}\",\"messages\":[{{\"role\":\"user\",\"content\":\"hi\"}}]}}'")
    return 0


def _find_connection_by_node(key: str, base: str, node_id: str) -> dict | None:
    code, body = _req("GET", "/api/providers", key=key, base=base)
    if code != 200 or not isinstance(body, dict):
        return None
    for c in body.get("connections", []):
        if c.get("provider") == node_id:
            return c
    return None


def unregister(provider: str, *, prefix: str | None = None) -> int:
    key = _read_key()
    if not key:
        print("error: no 9router key. set AIBRIDGE_9ROUTER_KEY or run register first.",
              file=sys.stderr)
        return 2
    base = _read_url()
    prefix = prefix or provider
    nodes = _fetch_nodes(key, base)
    existing = _find_node_by_prefix(nodes, prefix)
    if not existing:
        print(f"==> no node with prefix '{prefix}' found. nothing to do.")
        return 0
    node_id = existing["id"]
    code, body = _req("DELETE", f"/api/provider-nodes/{node_id}", key=key, base=base)
    if code != 200:
        print(f"error: delete failed ({code}): {body}", file=sys.stderr)
        return 1
    print(f"==> removed {prefix} (id={node_id})")
    return 0


def list_nodes() -> int:
    key = _read_key()
    if not key:
        print("error: no 9router key. set AIBRIDGE_9ROUTER_KEY or run register first.",
              file=sys.stderr)
        return 2
    base = _read_url()
    try:
        nodes = _fetch_nodes(key, base)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not nodes:
        print("(no provider-nodes registered)")
        return 0
    print(f"{'prefix':<15} {'type':<22} baseUrl")
    print("-" * 80)
    for n in nodes:
        print(f"{n.get('prefix',''):<15} {n.get('type',''):<22} {n.get('baseUrl','')}")
    return 0


def set_key(key: str) -> int:
    _save_key(key)
    print(f"==> saved key to {_cfg_key_path()}")
    return 0


def set_url(url: str) -> int:
    _save_url(url)
    print(f"==> saved url to {_cfg_url_path()}: {url}")
    return 0
