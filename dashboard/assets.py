from __future__ import annotations

from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ASSET_ROOT / "templates"
STATIC_DIR = ASSET_ROOT / "static"

CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


def read_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def read_static_asset(name: str) -> tuple[bytes, str]:
    path = (STATIC_DIR / name).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file():
        raise FileNotFoundError(name)
    return path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream")
