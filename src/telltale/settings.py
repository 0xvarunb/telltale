"""Settings for Telltale, read from a .env file and the environment."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_MODEL = "claude-haiku-5-5"
# The model that judges each cited sentence in an eval run.
DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"
KEY_NAME = "ANTHROPIC_API_KEY"


class SettingsError(Exception):
    """Raised when a required setting is missing."""


@dataclass(frozen=True)
class Settings:
    # None only when loaded with require_key=False (ingest needs no key).
    anthropic_api_key: str | None
    data_dir: Path = DEFAULT_DATA_DIR
    model: str = DEFAULT_MODEL

    def __repr__(self) -> str:
        # Keep the key out of logs and tracebacks.
        key = "'***'" if self.anthropic_api_key else "None"
        return (
            f"Settings(anthropic_api_key={key}, data_dir={self.data_dir!r}, model={self.model!r})"
        )


def _read_env_file(env_file: Path) -> dict[str, str]:
    """Parse KEY=value lines. Blank lines and # comments are skipped."""
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(env_file: Path | None = None, *, require_key: bool = True) -> Settings:
    """Load settings. An environment variable wins over the same key in the file.

    Without a key this raises `SettingsError`, unless `require_key` is False: then the key is None.
    """
    file_values = _read_env_file(env_file if env_file is not None else DEFAULT_ENV_FILE)
    key = os.environ.get(KEY_NAME) or file_values.get(KEY_NAME)
    if not key and require_key:
        raise SettingsError(f"{KEY_NAME} is not set. Add it to .env")
    return Settings(anthropic_api_key=key or None)
