"""aibridge CLI: login / serve / start / stop / status / logs / list / logout /
   install-service / uninstall-service / test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from ._platform import hide_dock_icon
from .config import PROVIDERS, session_path, need_camoufox_installed
from .providers import get_provider_class
# side-effect: register providers
from .providers import monica as _monica  # noqa: F401
from .providers import perplexity as _perplexity  # noqa: F401


def _setup_log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# -------------------- login --------------------

async def cmd_login(provider: str) -> int:
    if provider not in PROVIDERS:
        print(f"error: unknown provider '{provider}'. known: {', '.join(PROVIDERS)}",
              file=sys.stderr)
        return 2

    info = PROVIDERS[provider]
    spath = session_path(provider)
    print(f"==> opening Camoufox headed. login ke {info.url} di window yang kebuka.")
    print("    setelah login success, tutup window-nya (atau tekan Ctrl+C di sini).")

    from camoufox.async_api import AsyncCamoufox

    # Login flow: jangan pake humanize (virtual cursor bikin klik unresponsive),
    # force locale & timezone biar UI site konsisten (jangan random Arab/RTL).
    kwargs = {
        "headless": False,
        "humanize": False,
        "geoip": False,
        "os": ("macos", "windows", "linux"),
        "locale": ["en-US", "en"],
        "i_know_what_im_doing": True,
    }

    async with AsyncCamoufox(**kwargs) as browser:
        ctx_kwargs = {}
        if spath.exists():
            ctx_kwargs["storage_state"] = str(spath)
        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        await page.goto(info.url, wait_until="domcontentloaded")

        probe_js = f"() => {{ try {{ return !!({info.logged_in_probe}); }} catch(e) {{ return false; }} }}"
        print("==> waiting for login... (polling every 2s, close window to finish)")
        try:
            for _ in range(60 * 10):  # up to 10 menit
                try:
                    ok = await page.evaluate(probe_js)
                except Exception:
                    break
                if ok:
                    print("==> login detected. saving session...")
                    state = await ctx.storage_state()
                    spath.parent.mkdir(parents=True, exist_ok=True)
                    spath.write_text(json.dumps(state))
                    print(f"==> saved: {spath}")
                    await asyncio.sleep(2)
                    return 0
                await asyncio.sleep(2)
        except KeyboardInterrupt:
            pass

        try:
            state = await ctx.storage_state()
            spath.write_text(json.dumps(state))
            print(f"==> session saved (pre-login state): {spath}")
        except Exception as e:
            print(f"==> could not save session: {e}", file=sys.stderr)

    return 0


# -------------------- serve (foreground) --------------------

async def cmd_serve_fg(provider: str, port: int | None, headed: bool) -> int:
    if provider not in PROVIDERS:
        print(f"error: unknown provider '{provider}'.", file=sys.stderr)
        return 2
    if not session_path(provider).exists():
        print(f"warning: no saved session for '{provider}'. "
              f"run 'aibridge login {provider}' first.", file=sys.stderr)

    from .server import run_server
    port = port or PROVIDERS[provider].default_port
    await run_server(provider, port, headless=not headed)
    return 0


# -------------------- list --------------------

def cmd_list() -> int:
    from .daemon import _read_pid
    print(f"{'provider':<15} {'logged-in':<10} {'running':<16} {'models':<6} port")
    print("-" * 65)
    for name, info in PROVIDERS.items():
        logged_in = session_path(name).exists()
        cls = get_provider_class(name)
        pid = _read_pid(name)
        run_s = f"yes (pid {pid})" if pid else "no"
        print(f"{name:<15} {'yes' if logged_in else 'no':<10} "
              f"{run_s:<16} {len(cls.models):<6} {info.default_port}")
    return 0


def cmd_logout(provider: str) -> int:
    if provider not in PROVIDERS:
        print(f"error: unknown provider '{provider}'.", file=sys.stderr)
        return 2
    p = session_path(provider)
    if p.exists():
        p.unlink()
        print(f"==> cleared: {p}")
    else:
        print(f"==> nothing to clear for {provider}")
    return 0


async def cmd_test(provider: str) -> int:
    if provider not in PROVIDERS:
        print(f"error: unknown provider '{provider}'.", file=sys.stderr)
        return 2
    cls = get_provider_class(provider)
    inst = cls(headless=True)
    await inst.start()
    try:
        from .providers import ChatRequest
        req = ChatRequest(
            model=cls.models[0],
            messages=[{"role": "user", "content": "reply with just: OK"}],
            stream=False,
        )
        out = []
        async for c in inst.chat(req):
            out.append(c)
        reply = "".join(out).strip()
        print(f"==> reply: {reply[:200]}")
        return 0 if reply else 1
    finally:
        await inst.close()


# -------------------- entrypoint --------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("aibridge", description="bridge web-only AI to OpenAI API")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="download Camoufox browser and prepare config dirs")

    sp = sub.add_parser("login", help="open Camoufox and login to provider")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    sp = sub.add_parser("serve", help="run OpenAI-compatible server (foreground by default)")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--headed", action="store_true", help="show browser window")
    sp.add_argument("--foreground", "-F", action="store_true",
                    help="force foreground (default is foreground; used by daemon)")

    sp = sub.add_parser("start", help="start as background daemon")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--headed", action="store_true")

    sp = sub.add_parser("stop", help="stop background daemon")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    sp = sub.add_parser("restart", help="restart background daemon")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--headed", action="store_true")

    sp = sub.add_parser("status", help="check daemon status")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    sp = sub.add_parser("logs", help="tail logs")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("-n", type=int, default=80)

    sp = sub.add_parser("install-service", help="install OS autostart (launchd/systemd)")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--port", type=int, default=None)

    sp = sub.add_parser("uninstall-service", help="remove OS autostart")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    # 9router integration
    sp = sub.add_parser("register-9router",
                        help="register provider-node in 9router (auto setup)")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--prefix", type=str, default=None,
                    help="9router prefix (default = provider name)")

    sp = sub.add_parser("unregister-9router",
                        help="remove provider-node from 9router")
    sp.add_argument("provider", choices=sorted(PROVIDERS))
    sp.add_argument("--prefix", type=str, default=None)

    sub.add_parser("list-9router", help="list 9router provider-nodes")

    sp = sub.add_parser("set-9router-key", help="save 9router API key")
    sp.add_argument("key")

    sp = sub.add_parser("set-9router-url", help="save 9router base URL")
    sp.add_argument("url")

    sub.add_parser("list", help="list providers, login & daemon status")

    sp = sub.add_parser("logout", help="remove saved session")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    sp = sub.add_parser("test", help="smoke-test provider end-to-end (foreground)")
    sp.add_argument("provider", choices=sorted(PROVIDERS))

    args = p.parse_args(argv)
    _setup_log(args.verbose)

    cmd = args.cmd

    # 'setup' is the bootstrap command — it also verifies camoufox.
    # For everything else, we still need the package installed.
    if cmd != "setup":
        need_camoufox_installed()

    # Daemon-mode commands run hidden on macOS (no dock icon).
    # 'login' stays visible so the user can interact with the browser.
    if cmd in ("serve", "start", "test"):
        hide_dock_icon()

    if cmd == "setup":
        from . import bootstrap
        return bootstrap.run()

    if cmd == "login":
        return asyncio.run(cmd_login(args.provider))

    if cmd == "serve":
        return asyncio.run(cmd_serve_fg(args.provider, args.port, args.headed))

    if cmd == "start":
        from .daemon import start_daemon
        return start_daemon(args.provider, port=args.port, headed=args.headed)

    if cmd == "stop":
        from .daemon import stop_daemon
        return stop_daemon(args.provider)

    if cmd == "restart":
        from .daemon import stop_daemon, start_daemon
        stop_daemon(args.provider)
        return start_daemon(args.provider, port=args.port, headed=args.headed)

    if cmd == "status":
        from .daemon import status_daemon
        return status_daemon(args.provider)

    if cmd == "logs":
        from .daemon import tail_logs
        return tail_logs(args.provider, follow=args.follow, n=args.n)

    if cmd == "install-service":
        from .daemon import install_service
        return install_service(args.provider, port=args.port)

    if cmd == "uninstall-service":
        from .daemon import uninstall_service
        return uninstall_service(args.provider)

    if cmd == "register-9router":
        from . import router9
        return router9.register(args.provider, port=args.port, prefix=args.prefix)

    if cmd == "unregister-9router":
        from . import router9
        return router9.unregister(args.provider, prefix=args.prefix)

    if cmd == "list-9router":
        from . import router9
        return router9.list_nodes()

    if cmd == "set-9router-key":
        from . import router9
        return router9.set_key(args.key)

    if cmd == "set-9router-url":
        from . import router9
        return router9.set_url(args.url)

    if cmd == "list":
        return cmd_list()

    if cmd == "logout":
        return cmd_logout(args.provider)

    if cmd == "test":
        return asyncio.run(cmd_test(args.provider))

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
