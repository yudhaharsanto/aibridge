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
    """Return the aibridge entry point (sys.argv[0] fallback to 'aibridge')."""
    # If installed via ./install.sh, there's a shim in ~/.local/bin/aibridge
    # that execs .venv/bin/aibridge. We prefer the venv entrypoint since it
    # has the correct Python baked in.
    venv_bin = Path(sys.executable).parent / "aibridge"
    if venv_bin.exists():
        return str(venv_bin)
    return "aibridge"


# -------------------- start / stop / status --------------------

def start_daemon(provider: str, *, port: int | None, headed: bool) -> int:
    existing = _read_pid(provider)
    if existing:
        print(f"==> already running (pid {existing})")
        return 0

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
    # Kasih waktu Camoufox boot + HTTP bind (berat ~3-8s).
    time.sleep(6)
    if not _alive(proc.pid):
        print(f"==> failed to start. last log ({log}):", file=sys.stderr)
        _tail_to_stderr(log, n=30)
        _pid_file(provider).unlink(missing_ok=True)
        return 1
    print(f"==> started {provider} (pid {proc.pid}), log: {log}")
    return 0


def stop_daemon(provider: str) -> int:
    pid = _read_pid(provider)
    if not pid:
        print(f"==> {provider} not running")
        _pid_file(provider).unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    # Wait up to 10s for clean exit
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _pid_file(provider).unlink(missing_ok=True)
    print(f"==> stopped {provider}")
    return 0


def status_daemon(provider: str) -> int:
    pid = _read_pid(provider)
    if pid:
        print(f"{provider}: running (pid {pid})")
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
