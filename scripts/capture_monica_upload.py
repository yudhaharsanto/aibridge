#!/usr/bin/env python3
"""Headful Monica upload capture.

Launches Camoufox non-headless using the existing monica session, logs every
XHR/fetch to console (with multipart / JSON body preview), and holds the
browser open until you press Ctrl+C. While it's running:

  1. Navigate into a real chat with a bot that accepts uploads
     (e.g. https://monica.im/home or pick any bot).
  2. Click the paperclip / attach icon and upload a small image.
  3. Watch the console: every POST the app fires will be printed, with
     URL, content-type, body length, and a 200-char body preview.
  4. Copy the request(s) you see into the chat with Jihyo so she can
     implement it in aibridge/providers/monica.py.

Ctrl+C to exit. The session file is re-saved on clean exit.
"""
from __future__ import annotations

import asyncio
import sys
import signal
from pathlib import Path

# allow running straight out of the source tree
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aibridge.browser import BrowserSession  # noqa: E402
from aibridge.config import PROVIDERS, session_path  # noqa: E402


def _short(s: str | None, n: int = 240) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


async def main() -> None:
    info = PROVIDERS["monica"]
    s = BrowserSession(
        session_path("monica"),
        info.url,
        headless=False,   # show the browser so you can click
        humanize=False,
    )
    await s.start()
    page = await s.page()

    seen: set[str] = set()

    def on_request(req):
        try:
            if req.resource_type not in ("xhr", "fetch"):
                return
            method = req.method
            url = req.url
            hdrs = dict(req.headers or {})
            ctype = (hdrs.get("content-type") or "").lower()
            body = None
            try:
                body = req.post_data
            except Exception:
                body = None
            blen = len(body) if body else 0

            interesting = (
                method != "GET"
                and (
                    "multipart" in ctype
                    or "form-data" in ctype
                    or "octet-stream" in ctype
                    or any(k in url.lower() for k in (
                        "upload", "pre_sign", "presign", "oss", "s3",
                        "attach", "asset", "media", "image", "/file", "put_url",
                        "/chat", "custom_bot",
                    ))
                )
            )
            # Noise filter: skip rapid repeating probe endpoints
            if any(n in url for n in ("batch_get_file", "get_image_gallery")):
                interesting = False
            if interesting:
                key = f"{method} {url}"
                first = key not in seen
                seen.add(key)
                tag = "★ NEW" if first else "  rpt"
                print(f"\n{tag} {method} {url}")
                print(f"      content-type: {ctype or '(none)'}")
                print(f"      body-length:  {blen}")
                if body and "multipart" not in ctype:
                    print(f"      body-preview: {_short(body, 4000)}")
        except Exception as e:
            print(f"[on_request error] {e}")

    async def on_response(resp):
        try:
            req = resp.request
            url = req.url
            if req.resource_type not in ("xhr", "fetch"):
                return
            if req.method == "GET":
                return
            hdrs = dict(req.headers or {})
            ctype = (hdrs.get("content-type") or "").lower()
            if any(n in url for n in ("batch_get_file", "get_image_gallery")):
                return
            if not (
                "multipart" in ctype
                or "form-data" in ctype
                or "octet-stream" in ctype
                or any(k in url.lower() for k in (
                    "upload", "pre_sign", "presign", "oss", "s3",
                    "attach", "asset", "media", "image", "/file", "put_url",
                    "/chat", "custom_bot",
                ))
            ):
                return
            text = ""
            try:
                text = await resp.text()
            except Exception:
                pass
            print(f"      ↩ {resp.status}  {_short(text, 400)}")
        except Exception as e:
            print(f"[on_response error] {e}")

    page.on("request", on_request)
    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    # Open the chat landing so you can navigate to a bot manually.
    try:
        await page.goto("https://monica.im/home", wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"(goto warn: {e})")

    print("\n" + "=" * 72)
    print("Browser is OPEN. Now do this:")
    print("  1. Click a bot / open a chat where you can upload an image")
    print("  2. Click the paperclip icon, pick a small image, send it")
    print("  3. Watch THIS console for POST requests (filtered to uploads)")
    print("  4. Press Ctrl+C here when you're done to save the session")
    print("=" * 72 + "\n")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # platforms without signal support: fall back to waiting on close
            pass

    # Keep-alive loop. Exit when user presses Ctrl+C or closes the browser.
    while not stop.is_set():
        try:
            if page.is_closed():
                print("(page closed, exiting)")
                break
        except Exception:
            break
        await asyncio.sleep(1)

    print("shutting down, saving session...")
    await s.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
