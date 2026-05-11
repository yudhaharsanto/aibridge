"""Camoufox browser lifecycle: launch, persistent page, in-browser fetch."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator

log = logging.getLogger("aibridge.browser")


class BrowserSession:
    """
    Wraps Camoufox (async Playwright Firefox). Maintains one persistent context
    per provider, loaded from `storage_state` (cookies, localStorage).

    Pola pemakaian:
        async with BrowserSession(storage_path, url, headless=True) as s:
            text = await s.fetch("/api/..", method="POST", json_body={...})
            async for chunk in s.stream("/api/..", method="POST", json_body={...}):
                ...
    """

    def __init__(
        self,
        storage_path: Path,
        landing_url: str,
        *,
        headless: bool = True,
        humanize: bool = False,
    ) -> None:
        self.storage_path = storage_path
        self.landing_url = landing_url
        self.headless = headless
        self.humanize = humanize
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self) -> None:
        from camoufox.async_api import AsyncCamoufox

        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "humanize": self.humanize,
            "geoip": False,
            "os": ("macos", "windows", "linux"),
            "locale": ["en-US", "en"],
            "i_know_what_im_doing": True,
        }

        self._cm = AsyncCamoufox(**kwargs)
        self._browser = await self._cm.__aenter__()

        ctx_kwargs: dict[str, Any] = {}
        if self.storage_path.exists():
            ctx_kwargs["storage_state"] = str(self.storage_path)
        self._context = await self._browser.new_context(**ctx_kwargs)

        self._page = await self._context.new_page()
        await self._page.goto(self.landing_url, wait_until="domcontentloaded")
        log.info("browser started, landing=%s", self.landing_url)

    async def close(self) -> None:
        try:
            if self._context is not None:
                # persist cookies/localStorage biar login awet
                try:
                    state = await self._context.storage_state()
                    self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                    self.storage_path.write_text(json.dumps(state))
                    log.info("storage_state saved -> %s", self.storage_path)
                except Exception as e:
                    log.warning("storage save failed: %s", e)
        finally:
            if getattr(self, "_cm", None):
                await self._cm.__aexit__(None, None, None)

    async def page(self):
        if self._page is None:
            await self.start()
        return self._page

    # -------- HTTP helpers executed inside browser context --------

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: Any | None = None,
        body: str | None = None,
        timeout_ms: int = 60_000,
    ) -> tuple[int, dict[str, str], str]:
        """
        Nge-fire fetch dari dalam browser (inherit cookies, TLS fingerprint,
        User-Agent yang sama). Return (status, headers, text).
        """
        page = await self.page()
        headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body)
            headers.setdefault("Content-Type", "application/json")

        script = """async ({url, method, headers, body, timeoutMs}) => {
          const ctl = new AbortController();
          const tid = setTimeout(() => ctl.abort(), timeoutMs);
          try {
            const r = await fetch(url, { method, headers, body, credentials: 'include', signal: ctl.signal });
            const text = await r.text();
            const hdrs = {};
            r.headers.forEach((v, k) => { hdrs[k] = v; });
            return { status: r.status, headers: hdrs, text };
          } finally { clearTimeout(tid); }
        }"""
        result = await page.evaluate(
            script,
            {
                "url": url,
                "method": method,
                "headers": headers,
                "body": body,
                "timeoutMs": timeout_ms,
            },
        )
        return result["status"], result["headers"], result["text"]

    async def stream(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        json_body: Any | None = None,
        body: str | None = None,
        timeout_ms: int = 300_000,
        poll_ms: int = 100,
    ) -> AsyncIterator[str]:
        """
        Streaming via polling: fire fetch + reader loop di browser, simpan
        chunk-chunk ke window.__aibridge_buf_*. Python polling variable itu
        tiap `poll_ms` untuk ambil new data.

        Lebih reliable daripada expose_binding di Firefox (yang ada quirk
        binding-after-goto).

        Yield raw decoded text chunks (bisa partial SSE lines).
        """
        page = await self.page()
        headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body)
            headers.setdefault("Content-Type", "application/json")

        tag = int(time.time() * 1000)
        buf_name = f"__aibridge_buf_{tag}"
        done_name = f"__aibridge_done_{tag}"
        err_name = f"__aibridge_err_{tag}"

        body_js = json.dumps(body) if body is not None else "undefined"
        # Kick off fetch + reader di browser (fire-and-forget).
        await page.evaluate(f"""
          window.{buf_name} = '';
          window.{done_name} = false;
          window.{err_name} = null;
          (async () => {{
            try {{
              const ctl = new AbortController();
              const tid = setTimeout(() => ctl.abort(), {timeout_ms});
              const r = await fetch({json.dumps(url)}, {{
                method: {json.dumps(method)},
                headers: {json.dumps(headers)},
                body: {body_js},
                credentials: 'include',
                signal: ctl.signal
              }});
              if (!r.ok) {{
                const t = await r.text();
                window.{err_name} = 'HTTP ' + r.status + ': ' + t.slice(0,400);
                window.{done_name} = true;
                clearTimeout(tid);
                return;
              }}
              const reader = r.body.getReader();
              const dec = new TextDecoder('utf-8');
              for (;;) {{
                const {{value, done}} = await reader.read();
                if (done) break;
                window.{buf_name} += dec.decode(value, {{stream: true}});
              }}
              window.{done_name} = true;
              clearTimeout(tid);
            }} catch (e) {{
              window.{err_name} = String(e);
              window.{done_name} = true;
            }}
          }})();
        """)

        sent = 0
        deadline = time.time() + (timeout_ms / 1000.0) + 5.0
        while time.time() < deadline:
            await asyncio.sleep(poll_ms / 1000.0)
            state = await page.evaluate(
                f"() => ({{buf: window.{buf_name} || '', done: !!window.{done_name}, err: window.{err_name}}})"
            )
            buf = state.get("buf") or ""
            err = state.get("err")
            done = bool(state.get("done"))

            if len(buf) > sent:
                delta = buf[sent:]
                sent = len(buf)
                yield delta

            if err:
                raise RuntimeError(f"stream error: {err}")
            if done:
                return

        raise TimeoutError("stream polling exceeded timeout")
