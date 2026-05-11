# aibridge

Bridge antara AI provider web (Monica, Perplexity) dan tool coding yang expect
OpenAI/Anthropic-compatible API — tanpa resmi API key, pake session kamu sendiri
lewat browser anti-detect (Camoufox).

**Status:** early-stage, text-only.

## What it does

Tool kamu (Trae, Cline, Continue, Claude Code, atau apa pun yang ngomong
OpenAI/Anthropic API format) → aibridge → Camoufox browser yang udah login →
provider.

```
┌──────────┐   OpenAI API    ┌──────────┐   browser fetch   ┌─────────────┐
│   Trae   │  ─────────────▶ │ aibridge │  ───────────────▶ │  monica.im  │
└──────────┘                 └──────────┘                   │ perplexity  │
                                   ▲                        └─────────────┘
                             Camoufox (logged-in)
```

Provider yang sudah ter-support:

- **monica** — Claude Sonnet 4.6, GPT-5/5.5, Gemini 3.1 Pro Thinking, dkk
- **perplexity** — answer + sources (WIP)

## Install

### macOS / Linux

```bash
git clone <this-repo> aibridge && cd aibridge
./install.sh
```

### Windows (PowerShell)

```powershell
git clone <this-repo> aibridge; cd aibridge
.\install.ps1
```

Script ini:

1. Bikin venv `.venv/` (butuh Python >= 3.10)
2. Install `camoufox[geoip]`, `playwright`, dan deps lain
3. Fetch Camoufox browser binary (~200MB)
4. Siapin config dir di `~/.aibridge/`

## Usage

### 1. Login (sekali aja per provider)

```bash
aibridge login monica
aibridge login perplexity
```

Camoufox kebuka, login manual, tab aibridge otomatis detect + save session.

### 2. Jalanin server

```bash
aibridge serve monica         # default port 18788
aibridge serve perplexity     # default port 18789
```

Atau semua sekaligus:

```bash
aibridge serve --all
```

### 3. Hubungin ke tool

Endpoint OpenAI-compatible:

```
http://localhost:18788/v1/chat/completions    # monica
http://localhost:18789/v1/chat/completions    # perplexity
```

API key: `aibridge-local` (tidak di-verify, tapi tool biasanya wajib isi)

#### 9router integration

Tambahin provider-node di dashboard 9router → type `openai-compatible`, prefix
`monica` / `perplexity`, base URL sesuai port di atas.

#### Langsung di Trae / Cline / etc

```
Endpoint: http://localhost:18788/v1
API Key:  aibridge-local
Model:    claude-sonnet-4-6
```

## Commands

```
aibridge login <provider>          open Camoufox, login manual, save session
aibridge serve <provider> [--port] start OpenAI-compatible HTTP server
aibridge serve --all               serve all logged-in providers
aibridge test <provider>           smoke test: kirim "ping", ekspektasi reply
aibridge list                      list provider & status (logged-in?)
aibridge logout <provider>         clear saved session
```

## Config

Semua disimpan di `~/.aibridge/`:

```
~/.aibridge/
├── sessions/          # storage_state.json per provider
│   ├── monica.json
│   └── perplexity.json
├── config.toml        # port, model mapping, dll
└── logs/              # stdout/stderr per serve
```

## License

MIT.
