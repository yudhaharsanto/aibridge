"""Monica provider — drive monica.im lewat Camoufox session."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import string
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from ..browser import BrowserSession
from ..config import PROVIDERS, session_path
from . import ChatRequest, HealthReport, register

log = logging.getLogger("aibridge.monica")

API_URL = "https://api.monica.im/api/custom_bot/chat"
PRESIGN_URL = "https://api.monica.im/api/file_object/pre_sign_list_by_module"
REGISTER_URL = "https://api.monica.im/api/files/batch_create_llm_file"
POLL_URL = "https://api.monica.im/api/files/batch_get_file"

# Cap per-image payload to keep a single request reasonable. Monica's UI
# accepts much larger but aibridge is meant as a lightweight bridge and we
# don't want to tie up the browser for a minute uploading one turn.
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MiB

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
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in (None, "text")
        ).strip()
    return str(content or "").strip()


def _extract_images(content: Any) -> list[dict[str, Any]]:
    """Pull image blocks out of an OpenAI-shaped content array.

    Each returned dict has: ``url`` (raw URL or ``data:`` URI), ``mime``
    (best-effort, defaults to ``image/png``).
    """
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") != "image_url":
            continue
        iu = p.get("image_url")
        url: str | None = None
        if isinstance(iu, str):
            url = iu
        elif isinstance(iu, dict):
            url = iu.get("url") if isinstance(iu.get("url"), str) else None
        if not url:
            continue
        mime = "image/png"
        if url.startswith("data:"):
            head = url.split(",", 1)[0]
            if ":" in head and ";" in head:
                mime = head.split(":", 1)[1].split(";", 1)[0] or mime
        else:
            low = url.lower()
            for ext, m in ((".png", "image/png"), (".jpg", "image/jpeg"),
                           (".jpeg", "image/jpeg"), (".webp", "image/webp"),
                           (".gif", "image/gif")):
                if low.endswith(ext) or f"{ext}?" in low:
                    mime = m
                    break
        out.append({"url": url, "mime": mime})
    return out


def _mime_to_ext(mime: str) -> str:
    table = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
    }
    return table.get(mime.lower(), "png")


def _nanoid(n: int = 21) -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alphabet, k=n))


def _build_payload(
    model: str,
    messages: list[dict],
    *,
    thread_seed: str | None = None,
    attached_files: list[dict[str, Any]] | None = None,
) -> dict:
    chat_model = MODEL_MAP.get(model, model.replace("-", "_"))
    attached_files = attached_files or []

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
            # Attach files only to the final user turn — that's the one being
            # sent. Earlier turns already happened and their files would be
            # re-submitted for no reason.
            if is_last and attached_files:
                # file_with_text data block mirrors the traffic captured from
                # monica.im: each file carries not just its CDN URL but also
                # file_uid / file_chunks / file_tokens that batch_get_file
                # yields once indexing completes. Dropping those keys makes
                # the server ignore the attachment silently.
                data_block: dict[str, Any] = {
                    "type": "file_with_text",
                    "content": text,
                    "chat_model": chat_model,
                    "file_infos": [
                        {
                            "use_full_text": True,
                            "file_type": f.get("file_type", "png"),
                            "file_ext": f.get("file_ext", f.get("file_type", "png")),
                            "file_name": f.get("file_name", "image.png"),
                            "file_size": int(f.get("file_size", 0)),
                            "file_url": f["file_url"],
                            "file_uid": f.get("file_uid", ""),
                            "file_chunks": int(f.get("file_chunks", 0)),
                            "file_tokens": int(f.get("file_tokens", 0)),
                        }
                        for f in attached_files
                    ],
                    "max_token": 0,
                    "is_incognito": False,
                }
            else:
                data_block = {
                    "type": "text",
                    "content": text,
                    "quote_content": "",
                    "chat_model": chat_model,
                    "max_token": 0,
                    "is_incognito": False,
                }
            items.append({
                "item_id": mid,
                "conversation_id": conv_id,
                "item_type": "question",
                "summary": text[:200],
                "parent_item_id": prev_id,
                "data": data_block,
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

    async def health_check(self) -> HealthReport:
        """Probe monica auth with a cheap GET /api/user/me.

        Monica wraps responses in {code, msg, ...}. A valid session returns
        code=0. Expired / missing cookies surface as HTTP 401/403 or a
        non-zero code.
        """
        if self._session is None:
            return HealthReport(self.name, False, None, "not started", "aibridge start monica")
        url = "https://api.monica.im/api/user/me"
        try:
            status, _hdrs, text = await self._session.fetch(
                url,
                method="GET",
                headers={"Accept": "application/json"},
                timeout_ms=10_000,
            )
        except Exception as e:  # noqa: BLE001
            return HealthReport(
                self.name, False, None, f"probe failed: {e}",
                "aibridge restart monica",
            )
        if status in (401, 403):
            return HealthReport(
                self.name, False, status,
                "session expired (unauthenticated)",
                "aibridge login monica",
            )
        if status >= 500:
            return HealthReport(
                self.name, False, status,
                f"upstream {status}", "retry later",
            )
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {}
        code = data.get("code")
        if status == 200 and code == 0:
            user = data.get("user") or {}
            name = user.get("name") or user.get("email") or "ok"
            return HealthReport(self.name, True, status, f"signed in as {name}")
        return HealthReport(
            self.name, False, status,
            f"unexpected response: code={code!r} msg={data.get('msg')!r}",
            "aibridge login monica",
        )

    async def chat(self, req: ChatRequest) -> AsyncIterator[str]:
        if self._session is None:
            raise RuntimeError("provider not started")

        # Pull any image_url parts out of the last user turn and pre-upload
        # them to monica so we can reference them in the chat payload.
        attached_files: list[dict[str, Any]] = []
        last_user_idx = None
        for i in range(len(req.messages) - 1, -1, -1):
            if req.messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is not None:
            images = _extract_images(req.messages[last_user_idx].get("content"))
            for img in images:
                try:
                    info = await self._upload_image(img["url"], img["mime"])
                    attached_files.append(info)
                    log.info("monica: uploaded image %s (%d bytes)",
                             info["file_name"], info["file_size"])
                except Exception as e:  # noqa: BLE001
                    log.warning("monica: image upload failed, dropping: %s", e)

        payload = _build_payload(
            req.model,
            req.messages,
            thread_seed=req.thread_seed(),
            attached_files=attached_files,
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

    # ------------------------------------------------------------------
    # Image upload plumbing
    # ------------------------------------------------------------------
    async def _upload_image(self, url: str, mime: str) -> dict[str, Any]:
        """Resolve an image URL/data-URI into Monica file_info dict.

        3-step flow (reverse-engineered from monica.im web app):
          1. POST /api/file_object/pre_sign_list_by_module
             -> pre_sign_url_list / object_url_list / cdn_url_list
          2. PUT bytes to pre_sign_url_list[0]
          3. POST /api/files/batch_create_llm_file to register the asset
             (returns file_uid; not strictly required for the chat payload,
             but lets Monica's vision pipeline parse the image before the
             chat endpoint is hit).

        The ``cdn_url_list[0]`` value is what the chat payload's
        ``file_infos[].file_url`` expects.
        """
        assert self._session is not None
        data, file_size = await self._fetch_bytes(url, mime)
        if file_size > MAX_IMAGE_BYTES:
            raise ValueError(
                f"image too large ({file_size} bytes > {MAX_IMAGE_BYTES} cap)"
            )
        ext = _mime_to_ext(mime)
        file_name = f"image_{_nanoid(8)}.{ext}"

        # Step 1: presign
        presign_body = {
            "filename_list": [file_name],
            "module": "chat_bot",
            "location": "files",
            "obj_id": _nanoid(),
        }
        status, _hdrs, text = await self._session.fetch(
            PRESIGN_URL,
            method="POST",
            headers=MONICA_HEADERS,
            json_body=presign_body,
            timeout_ms=20_000,
        )
        if status != 200:
            raise RuntimeError(f"presign failed: HTTP {status} {text[:200]}")
        presign = json.loads(text).get("data") or {}
        put_url = (presign.get("pre_sign_url_list") or [None])[0]
        object_url = (presign.get("object_url_list") or [None])[0]
        cdn_url = (presign.get("cdn_url_list") or [None])[0]
        if not (put_url and object_url and cdn_url):
            raise RuntimeError(f"presign response missing urls: {text[:200]}")

        # Derive a fallback object_url from the PUT URL when the API didn't
        # include object_url_list (older responses only exposed pre_sign_url).
        if not object_url:
            u = urlparse(put_url)
            object_url = f"https://monica-private.s3.us-east-1.amazonaws.com{u.path}"

        # Step 2: PUT bytes via page.evaluate so the request inherits the
        # browser's TLS + cookies (some CDNs reject bare curl-style
        # User-Agents).
        await self._put_bytes(put_url, data, mime)

        # Step 3: register the asset so monica's vision pipeline parses it.
        # The chat endpoint won't surface the image to the model until the
        # file reaches index_state == 3 (indexed).
        register_body = {
            "data": [{
                "url": "",
                "parse": True,
                "file_name": file_name,
                "file_size": file_size,
                "file_type": ext,
                "object_url": object_url,
                "embedding": False,
            }]
        }
        status, _hdrs, text = await self._session.fetch(
            REGISTER_URL,
            method="POST",
            headers=MONICA_HEADERS,
            json_body=register_body,
            timeout_ms=20_000,
        )
        if status != 200:
            raise RuntimeError(f"register failed: HTTP {status} {text[:200]}")
        reg_data = json.loads(text).get("data") or {}
        items = reg_data.get("items") or []
        if not items or not items[0].get("file_uid"):
            raise RuntimeError(f"register response missing file_uid: {text[:200]}")
        file_uid = items[0]["file_uid"]

        # Poll batch_get_file until indexing finishes and we know the final
        # file_uid / file_chunks / file_tokens values that the chat payload
        # needs.
        index_info = await self._await_indexed(file_uid)

        return {
            "file_name": file_name,
            "file_size": file_size,
            "file_type": ext,
            "file_ext": ext,
            "file_url": cdn_url,
            "object_url": object_url,
            "file_uid": file_uid,
            "file_chunks": int(index_info.get("file_chunks", 0)),
            "file_tokens": int(index_info.get("file_tokens", 0)),
        }

    async def _fetch_bytes(self, url: str, mime: str) -> tuple[str, int]:
        """Return (base64_bytes, size_bytes) for a data URI or remote URL."""
        if url.startswith("data:"):
            try:
                b64 = url.split(",", 1)[1]
            except IndexError as e:
                raise ValueError("malformed data URI") from e
            raw = base64.b64decode(b64, validate=False)
            return b64, len(raw)
        # Remote URL: fetch in browser context so CORS/cookies work.
        assert self._session is not None
        page = await self._session.page()
        js = """async ({url}) => {
          const r = await fetch(url, {credentials: 'omit'});
          if (!r.ok) return {error: 'HTTP ' + r.status};
          const buf = new Uint8Array(await r.arrayBuffer());
          let s = '';
          const chunk = 0x8000;
          for (let i = 0; i < buf.length; i += chunk) {
            s += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
          }
          return {b64: btoa(s), size: buf.length};
        }"""
        res = await page.evaluate(js, {"url": url})
        if res.get("error"):
            raise RuntimeError(f"fetch image failed: {res['error']}")
        return res["b64"], int(res["size"])

    async def _put_bytes(self, put_url: str, b64: str, mime: str) -> None:
        """PUT base64-decoded bytes to the presign URL via the browser."""
        assert self._session is not None
        page = await self._session.page()
        js = """async ({url, b64, mime}) => {
          const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const r = await fetch(url, {method: 'PUT', body: bin, headers: {'Content-Type': mime}});
          const text = (await r.text()).slice(0, 300);
          return {status: r.status, text};
        }"""
        res = await page.evaluate(js, {"url": put_url, "b64": b64, "mime": mime})
        if int(res.get("status", 0)) != 200:
            raise RuntimeError(f"PUT failed: {res.get('status')} {res.get('text')}")

    async def _await_indexed(
        self, file_uid: str, *, timeout_s: float = 30.0, interval_s: float = 0.8
    ) -> dict[str, Any]:
        """Poll batch_get_file until the asset reaches index_state == 3.

        Returns the final item dict (with file_chunks / file_tokens / etc.)
        when the file is ready, or a partial one on parse_error / timeout so
        the chat can still go through without vision.
        """
        assert self._session is not None
        deadline = asyncio.get_event_loop().time() + timeout_s
        last_item: dict[str, Any] = {}
        while asyncio.get_event_loop().time() < deadline:
            try:
                status, _h, text = await self._session.fetch(
                    POLL_URL,
                    method="POST",
                    headers=MONICA_HEADERS,
                    json_body={"file_uids": [file_uid]},
                    timeout_ms=10_000,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("monica: index poll failed: %s", e)
                await asyncio.sleep(interval_s)
                continue
            if status != 200:
                await asyncio.sleep(interval_s)
                continue
            items = (json.loads(text).get("data") or {}).get("items") or []
            item = items[0] if items else {}
            last_item = item
            state = item.get("index_state")
            if state == 3:
                log.info(
                    "monica: file %s indexed (chunks=%s tokens=%s)",
                    file_uid, item.get("file_chunks"), item.get("file_tokens"),
                )
                return item
            if state == 2:
                desc = item.get("index_desc", "")
                log.warning(
                    "monica: file %s parse_error (%s) — chat will proceed without vision",
                    file_uid, desc,
                )
                return item
            await asyncio.sleep(interval_s)
        log.warning(
            "monica: file %s not indexed after %.1fs (last_state=%s) — continuing",
            file_uid, timeout_s, last_item.get("index_state"),
        )
        return last_item
