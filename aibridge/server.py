"""HTTP server OpenAI-compatible. Per-provider, single-port."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from aiohttp import web

from . import tokens
from .providers import ChatRequest, available, get_provider_class

log = logging.getLogger("aibridge.server")


def _cors(resp: web.Response) -> web.Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "*"
    return resp


async def _options(_request: web.Request) -> web.Response:
    return _cors(web.Response(status=204))


def _mk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _completion_chunk(cid: str, model: str, delta: str, finish: str | None = None) -> dict:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": delta} if delta else {},
            "finish_reason": finish,
        }],
    }


def _completion_full(cid: str, model: str, content: str) -> dict:
    return {
        "id": cid,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def build_app(provider_name: str, *, headless: bool = True) -> web.Application:
    cls = get_provider_class(provider_name)
    provider = cls(headless=headless)
    await provider.start()

    expected_token = tokens.get(provider.name)

    def _auth_ok(request: web.Request) -> bool:
        """Bearer auth. Empty / missing is rejected when token is set."""
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token == expected_token:
                return True
        # Also accept X-API-Key for tools that don't send Authorization header.
        if request.headers.get("X-API-Key", "").strip() == expected_token:
            return True
        return False

    async def handle_models(request: web.Request) -> web.Response:
        if not _auth_ok(request):
            return _cors(web.json_response({"error": "invalid api key"}, status=401))
        data = [
            {"id": m, "object": "model", "owned_by": provider.name}
            for m in provider.models
        ]
        return _cors(web.json_response({"object": "list", "data": data}))

    async def handle_chat(request: web.Request) -> web.StreamResponse:
        if not _auth_ok(request):
            return _cors(web.json_response({"error": "invalid api key"}, status=401))
        try:
            body = await request.json()
        except Exception:
            return _cors(web.json_response({"error": "invalid json"}, status=400))

        req = ChatRequest.from_openai(body)
        if not req.model:
            return _cors(web.json_response({"error": "model required"}, status=400))

        cid = _mk_id()

        if req.stream:
            resp = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
            _cors(resp)
            await resp.prepare(request)
            try:
                async for delta in provider.chat(req):
                    payload = _completion_chunk(cid, req.model, delta)
                    await resp.write(f"data: {json.dumps(payload)}\n\n".encode())
                done = _completion_chunk(cid, req.model, "", finish="stop")
                await resp.write(f"data: {json.dumps(done)}\n\n".encode())
                await resp.write(b"data: [DONE]\n\n")
            except Exception as e:
                log.exception("stream error: %s", e)
                err = {"error": str(e)}
                await resp.write(f"data: {json.dumps(err)}\n\n".encode())
            await resp.write_eof()
            return resp

        # non-streaming: kumpulin semua delta
        acc = []
        try:
            async for delta in provider.chat(req):
                acc.append(delta)
        except Exception as e:
            log.exception("chat error: %s", e)
            return _cors(web.json_response({"error": str(e)}, status=500))

        return _cors(web.json_response(_completion_full(cid, req.model, "".join(acc))))

    async def handle_health(_request: web.Request) -> web.Response:
        return _cors(web.json_response({
            "ok": True,
            "provider": provider.name,
            "models": provider.models,
        }))

    app = web.Application()
    app.router.add_route("OPTIONS", "/{tail:.*}", _options)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/chat/completions", handle_chat)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/", handle_health)

    async def _cleanup(_app):
        await provider.close()

    app.on_cleanup.append(_cleanup)
    return app


async def run_server(provider_name: str, port: int, *, headless: bool = True) -> None:
    app = await build_app(provider_name, headless=headless)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info(
        "aibridge %s serving on http://127.0.0.1:%d (models=%s)",
        provider_name, port, ", ".join(get_provider_class(provider_name).models
                                       if hasattr(get_provider_class(provider_name), "models")
                                       else []),
    )
    # serve forever
    try:
        import asyncio
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
