# Changelog

All notable changes to aibridge are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Anthropic-native `POST /v1/messages`** — aibridge now speaks the
  Anthropic Messages API in addition to OpenAI's chat/completions. Clients
  like Claude Code, Cline, and anything else that targets Anthropic
  natively can point straight at an aibridge daemon without a separate
  adapter. Supports both streaming (full
  `message_start→content_block_start→ping→content_block_delta…→content_block_stop→message_delta→message_stop`
  sequence) and non-streaming responses. Accepts `x-api-key` as well as
  `Authorization: Bearer <key>` headers. Validation errors come back in
  Anthropic's `{type:"error", error:{type,message}}` envelope.
- **Session health monitor** (`aibridge health`) — cron-friendly upstream
  auth probe. Hits each provider's cheapest signed-in endpoint
  (`/api/user/me` for Monica, `/rest/user/settings` for Perplexity) via the
  running daemon, reports whether the saved browser session is still valid,
  and exits non-zero when any provider needs `aibridge login`. `--json`
  flag for machine-readable output.
- **`GET /healthz?deep=1`** — deep health variant on the provider daemon.
  Returns 200 + `{ok:true,...}` for a healthy upstream, 503 + a `hint`
  field when the cookie is expired or the session is otherwise broken.
  Requires the provider API key.
- **`Provider.health_check()`** hook + new `HealthReport` dataclass for
  future providers to implement.
- **Conversation continuity** — follow-up turns now land in the same thread
  on the provider side instead of spawning a brand-new chat on every call.
  - Perplexity: captures `backend_uuid` and `context_uuid` from each response
    and replays them as `last_backend_uuid` / `frontend_context_uuid` on the
    next request of the same thread. The server-side thread on perplexity.ai
    is now extended instead of recreated on every turn (verified end-to-end:
    follow-ups correctly recall AI-invented content from the same session and
    a fresh `X-Session-Id` gets a clean thread).
  - Monica: `conversation_id` seed now accepts the same thread identity so
    clients with stable session ids stay on one thread even when the first
    user message is re-sent.
  - New request header `X-Session-Id` (also accepts `X-Conversation-Id`) lets
    clients explicitly pin a thread. When absent, aibridge falls back to a
    hash of the conversation prefix so retries of the same chat collapse
    onto the same thread.

## [0.2.0] — 2026-05-11

### ⚠️ Breaking changes

- The HTTP server now **requires an API key** on every request (via
  `Authorization: Bearer <key>` or `X-API-Key`). Requests without the right
  key get a `401`. Clients configured with a dummy key like `aibridge-local`
  will stop working.
- A per-provider key is auto-generated on first start. View it with
  `aibridge key show` and wire it into your client (or 9router).
- 9router credentials created by `aibridge register-9router` now use the real
  per-provider key. Re-run `aibridge register-9router <provider>` after
  upgrading to refresh them.

### Added

- **Batch operations:** `aibridge start|stop|restart|status all` now targets
  every provider in one command.
- **`aibridge key` subcommand** for API key management:
  `show`, `set`, `rotate`, `test`.
- **`aibridge update`** — self-upgrade from GitHub (no manual reinstall needed).
- **`aibridge --version`** — print the installed version.
- **`aibridge doctor`** — quick health check (daemon, auth, upstream reachability).
- `aibridge start <provider>` output now includes the URL and API key.
- `aibridge list` now shows each provider's key preview and port.
- Stop/start is now launchd-aware: calling `stop` on a service installed
  with `install-service` properly boots it out (no more zombie respawns);
  `start` re-bootstraps the launchd plist if one is installed.
- If a stale process is holding a provider's port, `start` now cleans it up
  before retrying (port-based fallback kill).

### Fixed

- Fixed a race where a manually-started daemon and a launchd-managed daemon
  would both try to bind the same port (`Errno 48: address already in use`).
- Fixed 9router registration creating credentials with a placeholder API key
  (`aibridge-local`), which now fails against the newly-authenticated server.

## [0.1.0] — 2026-05-11

### Added

- Initial release.
- Monica.im provider (Claude Sonnet 4.5/4.6, Claude Opus 4.1, GPT-5, GPT-5.5,
  GPT-4o, Gemini 2.5 Pro, Gemini 3.1 Pro).
- Perplexity provider (34 model aliases across Sonar 2, GPT-5 family,
  Claude Sonnet/Opus, Gemini 3 family, Kimi K2.5/K2.6, Grok 4/4.1,
  Nemotron 3 Super — all with Thinking variants where available).
- OpenAI-compatible HTTP server with `/v1/chat/completions`, `/v1/models`,
  `/healthz` endpoints; streaming + non-streaming responses.
- CLI: `login`, `serve`, `start`, `stop`, `restart`, `status`, `logs`,
  `install-service`, `uninstall-service`, `register-9router`,
  `unregister-9router`, `list-9router`, `set-9router-key`, `set-9router-url`,
  `list`, `logout`, `test`.
- OS autostart via launchd (macOS) and systemd `--user` (Linux).
- Cross-platform pip-installable package (`pip install`-compatible).
- `aibridge setup` bootstrap to fetch the Camoufox browser on first run.
- Dock icon hidden on macOS for background daemons.
- 9router auto-integration: one command registers both the provider-node
  and the credential.
