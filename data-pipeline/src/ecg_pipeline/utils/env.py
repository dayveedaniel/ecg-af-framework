from __future__ import annotations

from pathlib import Path


def load_dotenv_file(path: str | Path) -> dict[str, str]:
    """
    Minimal .env parser (no dependency on python-dotenv).
    - Supports KEY=VALUE
    - Strips surrounding single/double quotes
    - Ignores blank lines and comments starting with '#'
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f".env file not found: {path}")

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        env[key] = value
    return env

