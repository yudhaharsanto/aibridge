"""Daemon management: start/stop/status/logs + install-service/uninstall-service.

Cross-platform strategy:
- runtime control (start/stop/status/logs) → unified via PID file + subprocess
- OS-level autostart → launchd (macOS) / systemd --user (Linux) / NSSM/schtasks
  (Windows, stub for now)

PID file layout: ~/.aibridge/run/<provider>.pid
Log tailing:    ~/.aibridge/logs/<provider>.log
"""
from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

from .config import home, log_path


def _run_dir() -> Path:
    d = home() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_file(provider: str) -> Path:
    return _run_dir() / f"{provider}.pid"


def _read_pid(provider: str) -> int | None:
    p = _pid_file(provider)
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
    except ValueError:
        return None
    if not _alive(pid):
        return None
    return pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _aibridge_exe() -> str:
    """
    Return a command suitable for spawning a child aibridge process.

    Preference order:
    1. The `aibridge` console script alongside the active Python interpreter
       (works for venv, pipx, or any system install).
    2. A bare "aibridge" fallback (relies on PATH).
    """
    # Most Python installs put console scripts next to the interpreter.
    for candidate in (
        Path(sys.executable).parent / "aibridge",
        Path(sys.executable).parent / "aibridge.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return "aibridge"


# -------------------- start / stop / status --------------------

def start_daemon(provider: str, *, port: int | None, headed: bool) -> int:
    from .config import PROVIDERS

    check_port = port or PROVIDERS[provider].default_port

    # Clean out any stale/conflicting processes first so we start from a known state.
    existing_pid = _read_pid(provider)
    existing_alive = existing_pid and _alive(existing_pid)
    port_holders = _pids_on_port(check_port)

    if existing_alive and existing_pid in port_holders and len(port_holders) == 1:
        print(f"==> already running (pid {existing_pid})")
        _print_endpoint(provider, port)
        return 0

    # Stale pid file, or stray process holding the port without matching our pid file.
    # Clean up everything on that port so we can start fresh.
    if port_holders:
        for pp in port_holders:
            try:
                os.kill(pp, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(1.5)
        for pp in _pids_on_port(check_port):
            try:
                os.kill(pp, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.5)
    _pid_file(provider).unlink(missing_ok=True)

    # If launchd plist exists, let launchd own the process (it'll KeepAlive-respawn
    # on exit). Otherwise spawn our own managed subprocess.
    plist = LAUNCHD_PLIST_DIR / f"{LAUNCHD_LABEL.format(provider=provider)}.plist"
    if _platform() == "macos" and plist.exists():
        uid = os.getuid()
        # Ensure not already bootstrapped (bootout is idempotent / safe when absent)
        subprocess.run(
            ["/bin/launchctl", "bootout", f"gui/{uid}", str(plist)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        rc = subprocess.run(
            ["/bin/launchctl", "bootstrap", f"gui/{uid}", str(plist)],
            check=False, capture_output=True, text=True,
        )
        if rc.returncode != 0:
            print(f"==> launchctl bootstrap failed: {rc.stderr.strip()}", file=sys.stderr)
            return 1
        # Poll for the port to come up
        for _ in range(20):  # up to 10s
            time.sleep(0.5)
            pids = _pids_on_port(check_port)
            if pids:
                _pid_file(provider).write_text(str(pids[0]))
                print(f"==> started {provider} (pid {pids[0]}, launchd)")
                _print_endpoint(provider, port)
                print(f"    log: {log_path(provider)}")
                return 0
        print(f"==> launchd bootstrapped but no listener on :{check_port} after 10s",
              file=sys.stderr)
        _tail_to_stderr(log_path(provider), n=30)
        return 1

    # No launchd: spawn our own subprocess
    log = log_path(provider)
    log.parent.mkdir(parents=True, exist_ok=True)

    args = [_aibridge_exe(), "serve", provider, "--foreground"]
    if port:
        args += ["--port", str(port)]
    if headed:
        args.append("--headed")

    with open(log, "ab") as fp:
        fp.write(f"\n==== {time.strftime('%F %T')} starting {provider} ====\n".encode())
        fp.flush()
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=fp,
            stderr=fp,
            start_new_session=True,
            close_fds=True,
        )

    _pid_file(provider).write_text(str(proc.pid))
    time.sleep(6)  # Camoufox cold start
    if not _alive(proc.pid):
        print(f"==> failed to start. last log ({log}):", file=sys.stderr)
        _tail_to_stderr(log, n=30)
        _pid_file(provider).unlink(missing_ok=True)
        return 1
    print(f"==> started {provider} (pid {proc.pid})")
    _print_endpoint(provider, port)
    print(f"    log: {log}")
    return 0


def _print_endpoint(provider: str, port: int | None) -> None:
    """Print the HTTP endpoint + current API token after a successful start."""
    from .config import PROVIDERS
    from . import tokens

    p = port or PROVIDERS[provider].default_port
    url = f"http://127.0.0.1:{p}/v1"
    key = tokens.get(provider)
    print(f"    url:  {url}")
    print(f"    key:  {key}")


def stop_daemon(provider: str) -> int:
    """
    Stop the daemon. Three-pronged:
    1. If launchd-managed service is installed, bootout it (so it doesn't respawn).
    2. Kill the pid from our pid file.
    3. Fallback: kill anything currently listening on the provider's default port.
    """
    from .config import PROVIDERS
    port = PROVIDERS[provider].default_port

    killed = False

    # 1. launchd bootout (macOS) — suspend so it doesn't respawn during our stop.
    if _platform() == "macos":
        plist = LAUNCHD_PLIST_DIR / f"{LAUNCHD_LABEL.format(provider=provider)}.plist"
        if plist.exists():
            uid = os.getuid()
            subprocess.run(
                ["/bin/launchctl", "bootout", f"gui/{uid}", str(plist)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # 2. Kill the pid from pid file.
    pid = _read_pid(provider)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.2)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # 3. Fallback: anything still holding the port? Kill it.
    for port_pid in _pids_on_port(port):
        try:
            os.kill(port_pid, signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
    # Second pass after grace period
    time.sleep(1.5)
    for port_pid in _pids_on_port(port):
        try:
            os.kill(port_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _pid_file(provider).unlink(missing_ok=True)
    if killed:
        print(f"==> stopped {provider}")
    else:
        print(f"==> {provider} not running")
    return 0


def _pids_on_port(port: int) -> list[int]:
    """Return PIDs listening on the given TCP port (127.0.0.1)."""
    try:
        out = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            pass
    return pids


def status_daemon(provider: str) -> int:
    from .config import PROVIDERS
    pid = _read_pid(provider)
    # Cross-check with launchd-managed state
    port_pids = _pids_on_port(PROVIDERS[provider].default_port)
    if pid and _alive(pid):
        print(f"{provider}: running (pid {pid})")
        return 0
    if port_pids:
        # Launchd or another instance is running; sync our pid file.
        _pid_file(provider).write_text(str(port_pids[0]))
        print(f"{provider}: running (pid {port_pids[0]}, launchd-managed)")
        return 0
    print(f"{provider}: stopped")
    return 1


def _tail_to_stderr(path: Path, *, n: int) -> None:
    if not path.exists():
        return
    lines = path.read_text(errors="replace").splitlines()[-n:]
    for ln in lines:
        print(ln, file=sys.stderr)


def tail_logs(provider: str, *, follow: bool = False, n: int = 80) -> int:
    path = log_path(provider)
    if not path.exists():
        print(f"==> no log yet: {path}")
        return 1
    lines = path.read_text(errors="replace").splitlines()[-n:]
    for ln in lines:
        print(ln)
    if not follow:
        return 0
    # simple follow (tail -f)
    with open(path, "r") as fp:
        fp.seek(0, 2)
        try:
            while True:
                chunk = fp.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            return 0


# -------------------- install / uninstall autostart --------------------

LAUNCHD_LABEL = "com.yudha.aibridge.{provider}"
LAUNCHD_PLIST_DIR = Path.home() / "Library/LaunchAgents"

SYSTEMD_UNIT_DIR = Path.home() / ".config/systemd/user"


def _platform() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "linux":
        return "linux"
    if s == "windows":
        return "windows"
    return s


def install_service(provider: str, *, port: int | None) -> int:
    p = _platform()
    if p == "macos":
        return _install_launchd(provider, port=port)
    if p == "linux":
        return _install_systemd(provider, port=port)
    if p == "windows":
        print("error: windows autostart not yet implemented. "
              "as workaround, create a Task Scheduler entry manually.",
              file=sys.stderr)
        return 2
    print(f"error: unsupported platform: {p}", file=sys.stderr)
    return 2


def uninstall_service(provider: str) -> int:
    p = _platform()
    if p == "macos":
        return _uninstall_launchd(provider)
    if p == "linux":
        return _uninstall_systemd(provider)
    print(f"error: unsupported platform: {p}", file=sys.stderr)
    return 2


# ----- macOS launchd -----

def _plist_path(provider: str) -> Path:
    LAUNCHD_PLIST_DIR.mkdir(parents=True, exist_ok=True)
    return LAUNCHD_PLIST_DIR / f"{LAUNCHD_LABEL.format(provider=provider)}.plist"


def _plist_contents(provider: str, *, port: int | None) -> str:
    exe = _aibridge_exe()
    label = LAUNCHD_LABEL.format(provider=provider)
    log = log_path(provider)
    program_args = [exe, "serve", provider, "--foreground"]
    if port:
        program_args += ["--port", str(port)]
    args_xml = "\n        ".join(f"<string>{a}</string>" for a in program_args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        {args_xml}
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""


def _install_launchd(provider: str, *, port: int | None) -> int:
    plist = _plist_path(provider)
    plist.write_text(_plist_contents(provider, port=port))
    label = LAUNCHD_LABEL.format(provider=provider)
    uid = os.getuid()
    # bootout dulu kalau ada
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
        check=True,
    )
    subprocess.run(
        ["launchctl", "enable", f"gui/{uid}/{label}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"==> autostart installed: {plist}")
    print(f"==> '{label}' will auto-run at login")
    return 0


def _uninstall_launchd(provider: str) -> int:
    label = LAUNCHD_LABEL.format(provider=provider)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    plist = _plist_path(provider)
    if plist.exists():
        plist.unlink()
        print(f"==> removed {plist}")
    else:
        print(f"==> nothing to remove for {provider}")
    return 0


# ----- Linux systemd --user -----

def _systemd_unit_path(provider: str) -> Path:
    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    return SYSTEMD_UNIT_DIR / f"aibridge-{provider}.service"


def _systemd_unit(provider: str, *, port: int | None) -> str:
    exe = _aibridge_exe()
    args = f"serve {provider} --foreground"
    if port:
        args += f" --port {port}"
    log = log_path(provider)
    return f"""[Unit]
Description=aibridge {provider} proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exe} {args}
Restart=on-failure
RestartSec=3
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def _install_systemd(provider: str, *, port: int | None) -> int:
    unit = _systemd_unit_path(provider)
    unit.write_text(_systemd_unit(provider, port=port))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", unit.name], check=False)
    print(f"==> autostart installed: {unit}")
    print(f"==> enabled via systemctl --user")
    return 0


def _uninstall_systemd(provider: str) -> int:
    unit = _systemd_unit_path(provider)
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", unit.name],
        check=False,
    )
    if unit.exists():
        unit.unlink()
        print(f"==> removed {unit}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return 0
