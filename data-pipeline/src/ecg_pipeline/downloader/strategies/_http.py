from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import urllib.request

from ecg_pipeline.downloader.strategies.base import USER_AGENT


def http_get_json(url: str, *, timeout_seconds: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_to_file(
    url: str,
    target: Path,
    *,
    timeout_seconds: int,
    chunk_size_bytes: int,
) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp, target.open("wb") as out:
        total = 0
        while True:
            chunk = resp.read(chunk_size_bytes)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    return total


def filename_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name
    if not name:
        raise ValueError(f"Unable to infer filename from URL: {url}")
    return name

