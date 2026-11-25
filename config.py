"""Configuration: INI file in %APPDATA%\\MailAssistant, secrets in Windows Credential Manager."""
import configparser
import os
from pathlib import Path

try:
    import keyring  # Windows Credential Manager backend
except ImportError:  # optional; falls back to INI storage
    keyring = None

APP_NAME = "MailAssistant"
KEYRING_SERVICE = "Mail-Assistant"

DEFAULTS = {
    "imap_server": "securemail.a1.net",
    "imap_port": "993",
    "imap_user": "",
    "folder": "INBOX",
    "days": "7",
    "max_mails": "80",
    "model": "claude-haiku-5-5",
    "font_size": "22",
    "speech_rate": "-1",
    "auto_speak": "1",
    "auto_refresh_minutes": "10",
    "trusted_senders": "",
    "auto_update": "1",
}


def app_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


class Config:
    def __init__(self):
        self.path = app_dir() / "config.ini"
        # interpolation=None: passwords may contain '%'
        self.cp = configparser.ConfigParser(interpolation=None)
        self.cp["main"] = DEFAULTS.copy()
        if self.path.exists():
            self.cp.read(self.path, encoding="utf-8")
        self.s = self.cp["main"]

    def get(self, key: str) -> str:
        return self.s.get(key, DEFAULTS.get(key, ""))

    def getint(self, key: str) -> int:
        try:
            return int(self.get(key))
        except ValueError:
            return int(DEFAULTS[key])

    def set(self, key: str, value) -> None:
        self.s[key] = str(value)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            self.cp.write(f)

    def trusted(self) -> list:
        """Trusted senders: full addresses or '@domain.at' entries, comma separated."""
        return [t.strip().lower() for t in self.get("trusted_senders").split(",") if t.strip()]

    # --- secrets (IMAP password, API key) ---
    def get_secret(self, name: str) -> str:
        if keyring:
            try:
                value = keyring.get_password(KEYRING_SERVICE, name)
                if value:
                    return value
            except Exception:
                pass
        return self.s.get(f"secret_{name}", "")

    def set_secret(self, name: str, value: str) -> None:
        if keyring:
            try:
                keyring.set_password(KEYRING_SERVICE, name, value)
                if f"secret_{name}" in self.s:
                    del self.s[f"secret_{name}"]
                return
            except Exception:
                pass
        self.s[f"secret_{name}"] = value  # fallback: plain INI (only if keyring unavailable)
