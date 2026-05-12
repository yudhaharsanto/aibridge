# aibridge

[![GitHub](https://img.shields.io/github/v/tag/yudhaharsanto/aibridge?label=release&sort=semver)](https://github.com/yudhaharsanto/aibridge/releases)

Bridge web-only AI providers (Monica, Perplexity) to an OpenAI / Anthropic
compatible HTTP API — no official API keys required, just your own browser
session through an anti-detect browser (Camoufox).

aibridge works with **any client that speaks the OpenAI API**, and pairs
especially well with [9router](https://9router.com) as a unified gateway.
Some examples of clients it's been tested with:

- [Trae](https://trae.ai)
- [Cline](https://github.com/cline/cline) (VSCode extension)
- [Continue.dev](https://continue.dev)
- [Roo Code](https://github.com/RooCodeInc/Roo-Code)
- [Aider](https://aider.chat)
- [Claude Code](https://claude.ai/code) (via 9router)
- [LibreChat](https://librechat.ai)
- [Open WebUI](https://openwebui.com)
- [Cursor](https://cursor.com) (via 9router)
- Your own scripts / curl / any OpenAI SDK

```
┌──────────┐   OpenAI API    ┌──────────┐   browser fetch   ┌─────────────┐
│  client  │  ─────────────▶ │ aibridge │  ───────────────▶ │  monica.im  │
└──────────┘                 └──────────┘                   │ perplexity  │
                                   ▲                        └─────────────┘
                             Camoufox (logged-in)
```

## Features

- **Cross-platform**: macOS, Linux, Windows
- **Anti-detect browser** via [Camoufox](https://camoufox.com) (Firefox-based)
- **Daemon mode** with `start` / `stop` / `status` / `logs` commands
- **OS autostart** integration (launchd on macOS, systemd user units on Linux)
- **Background process** with no dock icon on macOS
- **9router auto-integration**: one command registers the provider and its
  credentials in your local 9router instance
- **Multiple providers** behind a single OpenAI-compatible endpoint

### Supported providers

| Provider   | Models                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------- |
| monica     | Claude Sonnet 4.5/4.6, Claude Opus 4.1, GPT-5, GPT-5.5, GPT-4o, Gemini 2.5 Pro, Gemini 3.1 Pro    |
| perplexity | Sonar 2, GPT-5/5.1/5.2/5.4/5.5 (± Thinking), Claude Sonnet/Opus 4.5-4.7 (± Thinking), Gemini 3/3.1 (± Thinking), Kimi K2.5/2.6 (± Thinking), Grok 4/4.1 (± Thinking), Nemotron 3 Super |

A Perplexity Pro subscription is required to use the full model list.

## Install

### One-liner (recommended)

The installer bootstraps everything it needs — including Python itself — via
[uv](https://docs.astral.sh/uv/), then installs aibridge as an isolated tool
and runs `aibridge setup`. No pre-existing Python required.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/yudhaharsanto/aibridge/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
iwr -useb https://raw.githubusercontent.com/yudhaharsanto/aibridge/main/install.ps1 | iex
```

Environment overrides for the installer:

| Variable               | Purpose                                     | Default |
| ---------------------- | ------------------------------------------- | ------- |
| `AIBRIDGE_REF`         | Git ref (branch/tag/sha) to install         | `main`  |
| `AIBRIDGE_REPO`        | Override repo URL                           | official repo |
| `AIBRIDGE_SKIP_SETUP`  | Set to `1` to skip the `aibridge setup` run | unset   |

Example — install a specific tag without running setup yet:

```bash
curl -fsSL https://raw.githubusercontent.com/yudhaharsanto/aibridge/main/install.sh \
  | AIBRIDGE_REF=v0.2.0 AIBRIDGE_SKIP_SETUP=1 sh
```

### Manual install

If you already have Python 3.10+ and prefer to install by hand:

```bash
# via uv (recommended)
uv tool install git+https://github.com/yudhaharsanto/aibridge.git

# via pipx
pipx install git+https://github.com/yudhaharsanto/aibridge.git

# via pip (once published to PyPI)
pip install aibridge
```

Then run the one-time setup:

```bash
aibridge setup
```

Requirements:

- Python 3.10 or later (auto-installed by the one-liner via uv)
- One-time ~200 MB download of the Camoufox browser binary during `aibridge setup`

## Quick start

```bash
# 1. One-time setup: fetch browser, create config dirs
aibridge setup

# 2. Log in to the providers you want (opens a browser window)
aibridge login monica
aibridge login perplexity

# 3. Start the bridges as background daemons
aibridge start all          # or: aibridge start monica

# 4. (optional) Install autostart so services boot at login
aibridge install-service monica
aibridge install-service perplexity

# 5. (optional) Register everything in 9router
aibridge register-9router monica
aibridge register-9router perplexity
```

Each provider gets its own local HTTP endpoint + API key. View them with:

```bash
aibridge list          # summary (URL/port/key preview)
aibridge key show      # full URL + key for every provider
```

## Using it

Once the daemons are running, each provider exposes an OpenAI-compatible HTTP
server on `127.0.0.1`:

- Monica: `http://127.0.0.1:18788/v1`
- Perplexity: `http://127.0.0.1:18790/v1`

Each provider is protected by its own **API key**. Find the current key with
`aibridge key show`. Pass it as `Authorization: Bearer <key>` or as the
`X-API-Key` header:

```bash
curl http://127.0.0.1:18788/v1/chat/completions \
  -H "Authorization: Bearer $(aibridge key show monica | awk '/key:/{print $2}')" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Managing API keys

Keys are generated automatically the first time a provider starts and stored
in `~/.aibridge/tokens.json` (chmod 600).

```bash
aibridge key show                  # show every provider's url + key
aibridge key show monica           # show one provider
aibridge key set monica my-key     # set a custom key
aibridge key rotate monica         # generate a fresh random key
aibridge key test monica           # validate the stored key against the running daemon
```

After changing a key: `aibridge restart <provider>` to apply.

### Integrating with 9router

[9router](https://9router.com) is a local gateway that aggregates multiple
OpenAI-compatible providers behind a single endpoint. It's the easiest way to
expose aibridge to every client at once — once registered in 9router, any
tool that can point at `http://localhost:20128/v1` will see all your models.

```bash
aibridge register-9router monica
aibridge register-9router perplexity
```

Then from any client:

```bash
curl http://localhost:20128/v1/chat/completions \
  -H 'Authorization: Bearer <your-9router-key>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "monica/claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Integrating with an OpenAI-compatible client

Most coding assistants (Trae, Cline, Continue, Roo Code, Aider, Cursor,
LibreChat, Open WebUI, etc.) accept a custom OpenAI-compatible endpoint.
Point them at aibridge directly, or at 9router if you use it:

```
Base URL:  http://127.0.0.1:18788/v1          (direct to aibridge)
           http://localhost:20128/v1          (via 9router)
API key:   run 'aibridge key show <provider>' to see it
Model:     claude-sonnet-4-6                  (or monica/claude-sonnet-4-6 via 9router)
```

Check each client's docs for where to configure a "custom provider" or
"OpenAI-compatible endpoint" — the option is usually under **Settings →
Providers / Models**.

## CLI reference

```
aibridge setup                       fetch Camoufox browser (one-time)

aibridge login <provider>            open a browser, log in, save the session
aibridge logout <provider>           delete the saved session

aibridge start <provider|all>        start background daemon(s)
aibridge stop <provider|all>         stop daemon(s)
aibridge restart <provider|all>      restart daemon(s)
aibridge status <provider|all>       daemon status
aibridge logs <provider> [-f] [-n N] view / tail the daemon log
aibridge serve <provider>            run in the foreground (useful for debugging)

aibridge test <provider>             send a ping message end-to-end

aibridge key show [provider|all]     show API key(s) for the local HTTP server
aibridge key set <provider> <key>    set a custom API key
aibridge key rotate <provider>       generate a new random API key
aibridge key test <provider>         verify the stored key against the running daemon

aibridge install-service <provider>  register OS autostart
                                     (launchd on macOS, systemd --user on Linux)
aibridge uninstall-service <provider>

aibridge register-9router <provider> create / update provider-node + connection
aibridge unregister-9router <provider>
aibridge list-9router                list provider-nodes registered in 9router
aibridge set-9router-key <key>       save your 9router API key locally
aibridge set-9router-url <url>       override the default 9router base URL

aibridge list                        show all providers and their status
```

## Configuration

All local state lives under `~/.aibridge/`:

```
~/.aibridge/
├── sessions/          # Playwright storage_state per provider (cookies, etc.)
│   ├── monica.json
│   └── perplexity.json
├── run/               # PID files for running daemons
├── logs/              # Daemon stdout/stderr logs
├── tokens.json        # Per-provider client API keys (chmod 600)
├── .9router-key       # Saved 9router API key (chmod 600)
└── .9router-url       # Saved 9router base URL (optional)
```

Environment overrides:

| Variable                   | Purpose                                    |
| -------------------------- | ------------------------------------------ |
| `AIBRIDGE_HOME`            | Override the base config directory          |
| `AIBRIDGE_9ROUTER_KEY`     | Override the stored 9router API key         |
| `AIBRIDGE_9ROUTER_URL`     | Override the 9router base URL               |
| `AIBRIDGE_SHOW_DOCK`       | Set to `1` to show the dock icon on macOS   |
| `MONICA_PROXY_PORT`        | Override the default Monica port (18788)    |

## Upgrading

```bash
aibridge check-update   # see if a new version is out
aibridge update         # upgrade to latest main
aibridge restart all    # apply changes to running daemons
```

`aibridge update` runs `pip install --upgrade git+https://github.com/yudhaharsanto/aibridge.git`
using the same Python interpreter aibridge was installed with — it works for
both `pip` and `pipx` installs.

When upgrading across a breaking change (e.g. 0.1.x → 0.2.x), check the
[CHANGELOG](CHANGELOG.md) for migration notes.

### 0.1.x → 0.2.x migration

0.2.0 introduced mandatory API key auth on the HTTP server. Anyone upgrading
from 0.1.x needs to do this once:

```bash
aibridge update
aibridge restart all

# See the auto-generated per-provider keys:
aibridge key show

# If you use 9router, refresh its credentials so they carry the real key:
aibridge register-9router monica
aibridge register-9router perplexity
```

## Troubleshooting

```bash
aibridge doctor         # check environment + each provider, suggests fixes
aibridge status all     # daemon status only
aibridge logs monica    # recent log
aibridge logs monica -f # follow log (like tail -f)
```

Common issues:

| symptom                           | fix                                             |
| --------------------------------- | ----------------------------------------------- |
| `401 invalid api key`             | use the key from `aibridge key show <provider>` |
| Daemon crashes on start           | `aibridge logs <provider>` to see the error     |
| Port already in use               | `aibridge stop <provider>` then start again     |
| Session expired / "please log in" | `aibridge login <provider>` to refresh          |
| Browser fails to launch           | `aibridge setup` to re-fetch Camoufox           |

If a daemon was installed with `install-service`, launchd / systemd will
automatically restart it on crash. `aibridge start` and `aibridge stop` are
launchd-aware and will coordinate with the service manager so you don't end
up with two daemons racing on the same port.

## Uninstall

```bash
aibridge stop monica
aibridge stop perplexity
aibridge uninstall-service monica
aibridge uninstall-service perplexity
pip uninstall aibridge

# Optional: remove local state (sessions, logs, keys)
rm -rf ~/.aibridge
```

## How it works

Every provider runs its own persistent Camoufox browser context. On `login`,
the browser opens, the user signs in manually, and Playwright saves the full
`storage_state` (cookies + localStorage) to disk.

On `start`, aibridge boots that browser context headless. HTTP requests flow
through `page.evaluate()` using the browser's native `fetch`, so they inherit
the session cookies, TLS fingerprint, and User-Agent of the logged-in user.
Responses are streamed back to Python via a polled buffer (works around a
Firefox + Playwright `expose_binding` quirk).

No DOM scraping is involved: aibridge replays the same REST / SSE endpoints
the Monica and Perplexity web apps use internally. Those endpoints are
reverse-engineered and translated to the OpenAI chat-completions shape.

## Contributing

Issues and PRs welcome. The scraper selectors and payloads are reverse
engineered from a running browser session, so they can break when the
upstream site ships a redesign — pull requests updating them are especially
appreciated.

## License

MIT.
