#!/usr/bin/env python3
"""Probe Monica upload flow end-to-end programmatically.

Uses the authenticated BrowserSession to:
  1. POST /api/file_object/pre_sign_list_by_module  → presigned PUT URL
  2. PUT tiny PNG bytes to that URL
  3. POST /api/files/batch_create_llm_file          → register file, get file_uid
  4. POST /api/files/batch_get_file                 → poll until ready (full response)

The goal is to find where the CloudFront signed URL used in the chat payload
(`file_url` field) comes from, so we can construct it in monica.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aibridge.browser import BrowserSession  # noqa: E402
from aibridge.config import PROVIDERS, session_path  # noqa: E402

# tiny 1x1 red PNG (70 bytes)
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4n"
    "GP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)


def nanoid(n: int = 21) -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alphabet, k=n))


async def main() -> None:
    info = PROVIDERS["monica"]
    s = BrowserSession(session_path("monica"), info.url, headless=True)
    await s.start()
    try:
        page = await s.page()
        # Land so cookies are active
        try:
            await page.goto("https://monica.im/home", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)

        # --- Step 1: request presigned PUT URL ---
        obj_id = nanoid()
        payload_step1 = {
            "filename_list": ["probe.png"],
            "module": "chat_bot",
            "location": "files",
            "obj_id": obj_id,
        }
        st, hdrs, body = await s.fetch(
            "https://api.monica.im/api/file_object/pre_sign_list_by_module",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json_body=payload_step1,
            timeout_ms=15000,
        )
        print(f"\n=== STEP 1: pre_sign_list_by_module ({st}) ===")
        print(body)
        if st != 200:
            return
        r1 = json.loads(body)
        presign_url = r1["data"]["pre_sign_url_list"][0]

        # --- Step 2: PUT bytes (via browser context so it uses same IP/tls profile) ---
        put_js = """async ({url, b64}) => {
          const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const r = await fetch(url, {method: 'PUT', body: bin, headers: {'Content-Type': 'image/png'}});
          return {status: r.status, text: (await r.text()).slice(0,300)};
        }"""
        put_res = await page.evaluate(put_js, {"url": presign_url, "b64": PNG_B64})
        print(f"\n=== STEP 2: PUT to presign URL ===")
        print(put_res)

        # Derive S3-style object_url: presign URL path is the same, just host differs.
        # From capture: object_url = "https://monica-private.s3.us-east-1.amazonaws.com<path>"
        # private-us-east-1.monica.im -> monica-private.s3.us-east-1.amazonaws.com
        from urllib.parse import urlparse
        u = urlparse(presign_url)
        object_url = f"https://monica-private.s3.us-east-1.amazonaws.com{u.path}"
        print(f"\nderived object_url: {object_url}")

        # --- Step 3: register file ---
        payload_step3 = {
            "data": [{
                "url": "",
                "parse": True,
                "file_name": "probe.png",
                "file_size": 70,  # tiny png actual size
                "file_type": "png",
                "object_url": object_url,
                "embedding": False,
            }]
        }
        st, _, body = await s.fetch(
            "https://api.monica.im/api/files/batch_create_llm_file",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json_body=payload_step3,
            timeout_ms=15000,
        )
        print(f"\n=== STEP 3: batch_create_llm_file ({st}) ===")
        print(body)
        if st != 200:
            return
        r3 = json.loads(body)
        file_uid = r3["data"]["items"][0]["file_uid"]

        # --- Step 4: poll batch_get_file until indexed (FULL response) ---
        for i in range(10):
            st, _, body = await s.fetch(
                "https://api.monica.im/api/files/batch_get_file",
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json_body={"file_uids": [file_uid]},
                timeout_ms=15000,
            )
            print(f"\n=== STEP 4.{i}: batch_get_file ({st}) ===")
            print(body)
            if st == 200:
                parsed = json.loads(body)
                item = parsed["data"]["items"][0]
                if item.get("index_state") == 3:
                    print(f"\n✅ file ready. file_uid={file_uid}")
                    # Does the item now have a file_url / signed_url / cdn_url field?
                    print("\nitem keys:", list(item.keys()))
                    break
            await asyncio.sleep(2)
    finally:
        await s.close()


if __name__ == "__main__":
    asyncio.run(main())
