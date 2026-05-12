"""HTTP server OpenAI-compatible. Per-provider, single-port."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from aiohttp import web

from . import tokens
from .anthropic import (
    anthropic_from_openai_body,
    anthropic_final_body,
    anthropic_event_lines_start,
    anthropic_event_lines_delta,
    anthropic_event_lines_stop,
    anthropic_stop_reason,
)
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

        req = ChatRequest.from_openai(
            body,
            session_id=(
                request.headers.get("X-Session-Id")
                or request.headers.get("X-Conversation-Id")
                or None
            ),
        )
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

    async def handle_anthropic_messages(request: web.Request) -> web.StreamResponse:
        """Anthropic-native POST /v1/messages.

        Translates the Anthropic request shape into our internal ChatRequest
        (share the same provider.chat() pipeline as /v1/chat/completions),
        then serializes the provider's incremental deltas back into Anthropic
        SSE events for streaming clients or a single `Message` object for
        non-streaming clients.
        """
        if not _auth_ok(request):
            return _cors(web.json_response({
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid api key"},
            }, status=401))
        try:
            body = await request.json()
        except Exception:
            return _cors(web.json_response({
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "invalid json"},
            }, status=400))

        try:
            openai_body = anthropic_from_openai_body(body)
        except ValueError as e:
            return _cors(web.json_response({
                "type": "error",
                "error": {"type": "invalid_request_error", "message": str(e)},
            }, status=400))

        req = ChatRequest.from_openai(
            openai_body,
            session_id=(
                request.headers.get("X-Session-Id")
                or request.headers.get("X-Conversation-Id")
                or None
            ),
        )
        if not req.model:
            return _cors(web.json_response({
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "model required"},
            }, status=400))

        mid = f"msg_{uuid.uuid4().hex[:24]}"

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
            total_out = 0
            try:
                for line in anthropic_event_lines_start(mid, req.model):
                    await resp.write(line.encode())
                async for delta in provider.chat(req):
                    if not delta:
                        continue
                    total_out += len(delta)
                    for line in anthropic_event_lines_delta(delta):
                        await resp.write(line.encode())
                for line in anthropic_event_lines_stop(
                    stop_reason=anthropic_stop_reason(None),
                    output_tokens=total_out,
                ):
                    await resp.write(line.encode())
            except Exception as e:
                log.exception("anthropic stream error: %s", e)
                err = {
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)},
                }
                await resp.write(
                    f"event: error\ndata: {json.dumps(err)}\n\n".encode()
                )
            await resp.write_eof()
            return resp

        # non-streaming: collect full text then wrap as Anthropic Message
        acc: list[str] = []
        try:
            async for delta in provider.chat(req):
                acc.append(delta)
        except Exception as e:
            log.exception("anthropic chat error: %s", e)
            return _cors(web.json_response({
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            }, status=500))

        text = "".join(acc)
        return _cors(web.json_response(
            anthropic_final_body(mid, req.model, text)
        ))

    async def handle_health(request: web.Request) -> web.Response:
        # Deep health probe upstream auth — only runs when explicitly asked
        # (`?deep=1`) so the default liveness check stays cheap and anonymous.
        if request.query.get("deep") in ("1", "true", "yes"):
            if not _auth_ok(request):
                return _cors(web.json_response({"error": "invalid api key"}, status=401))
            try:
                report = await provider.health_check()
            except Exception as e:  # noqa: BLE001
                log.exception("health_check error: %s", e)
                return _cors(web.json_response({
                    "ok": False,
                    "provider": provider.name,
                    "detail": f"probe crashed: {e}",
                }, status=500))
            payload = {
                "ok": report.ok,
                "provider": report.provider,
                "status": report.status,
                "detail": report.detail,
                "hint": report.hint,
                "models": provider.models,
            }
            # Service itself is still up, but return 503 when upstream auth
            # failed so orchestrators / cron jobs can alert on a simple curl.
            return _cors(web.json_response(payload, status=200 if report.ok else 503))

        return _cors(web.json_response({
            "ok": True,
            "provider": provider.name,
            "models": provider.models,
        }))

    app = web.Application()
    app.router.add_route("OPTIONS", "/{tail:.*}", _options)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/chat/completions", handle_chat)
    app.router.add_post("/v1/messages", handle_anthropic_messages)
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
