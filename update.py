"""Self-update from GitHub Releases (only for the frozen .exe).

The release workflow publishes Mail-Assistant.exe plus Mail-Assistant.exe.sha256.
The running exe cannot overwrite itself, so a small batch file waits for the
process to exit, swaps the file and restarts the program.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from version import VERSION

GITHUB_REPO = "ewerkstatt/mail-assistant"
ASSET_NAME = "Mail-Assistant.exe"
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse(v: str) -> tuple:
    nums = re.findall(r"\d+", v.split("-")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mail-Assistant", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def check_for_update():
    """Return (new_version, exe_url, sha_url) or None."""
    if not is_frozen() or "OWNER/" in GITHUB_REPO or VERSION.endswith("-dev"):
        return None
    release = json.loads(_get(API))
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or _parse(tag) <= _parse(VERSION):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    exe_url, sha_url = assets.get(ASSET_NAME), assets.get(ASSET_NAME + ".sha256")
    if not exe_url or not sha_url:
        return None
    return tag.lstrip("v"), exe_url, sha_url


def install_update(exe_url: str, sha_url: str) -> None:
    """Download, verify checksum, schedule replacement. Caller must exit the app afterwards."""
    data = _get(exe_url, timeout=300)
    expected = _get(sha_url).decode("ascii", "ignore").split()[0].lower()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Prüfsumme des Updates stimmt nicht")

    target = Path(sys.executable)
    tmpdir = Path(tempfile.gettempdir())
    new_exe = tmpdir / "Mail-Assistant-update.exe"
    new_exe.write_bytes(data)
    log = Path(os.environ.get("APPDATA") or Path.home()) / "MailAssistant" / "update.log"

    # Waits (max ~2 min) until the old process has released the exe, swaps it, restarts.
    # ping instead of timeout: works without an interactive console.
    script = tmpdir / "mail-assistant-update.cmd"
    script.write_text(
        "@echo off\r\n"
        f'set "TARGET={target}"\r\n'
        f'set "NEW={new_exe}"\r\n'
        f'set "LOG={log}"\r\n'
        "echo %date% %time% start update>>\"%LOG%\"\r\n"
        "for /l %%i in (1,1,120) do (\r\n"
        '  move /y "%NEW%" "%TARGET%" >nul 2>nul && goto ok\r\n'
        "  ping -n 2 127.0.0.1 >nul\r\n"
        ")\r\n"
        "echo %date% %time% FAILED: exe still locked>>\"%LOG%\"\r\n"
        'start "" "%TARGET%"\r\n'
        "exit /b 1\r\n"
        ":ok\r\n"
        "echo %date% %time% replaced, restarting>>\"%LOG%\"\r\n"
        'start "" "%TARGET%"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n',
        encoding="mbcs" if os.name == "nt" else "utf-8",
    )
    # CREATE_NO_WINDOW alone: cmd gets a hidden console that child processes inherit.
    # (DETACHED_PROCESS would make every child console program open its own window.)
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=CREATE_NO_WINDOW, close_fds=True)
