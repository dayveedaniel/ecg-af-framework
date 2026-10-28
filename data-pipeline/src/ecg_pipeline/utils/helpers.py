from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Literal

Provider = Literal["physionet_s3", "zenodo", "figshare", "direct_url"]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "dataset"


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{num_bytes}B"


def infer_provider_from_url(url: str) -> Provider:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()

    query = urllib.parse.parse_qs(parsed.query)
    if host.endswith("harvard.edu") and parsed.path.startswith("/dataset.xhtml") and "persistentId" in query:
        raise ValueError("Harvard dataset portal URLs are no longer supported by this pipeline")

    if "zenodo.org" in url:
        return "zenodo"
    if "figshare.com" in url:
        return "figshare"
    if "physionet.org/content/" in url or "physionet.org/files/" in url:
        return "physionet_s3"
    return "direct_url"


def zenodo_record_id(url: str) -> str | None:
    match = re.search(r"zenodo\.org/records/(\d+)", url)
    return match.group(1) if match else None


def figshare_article_id(url: str) -> str | None:
    # Supports both the older `articles/<slug>/<id>` form and the modern
    # `articles/<type>/<slug>/<id>` form (e.g. articles/dataset/Name/23807286).
    match = re.search(r"figshare\.com/articles/(?:[^/]+/)+(\d+)", url)
    return match.group(1) if match else None


def figshare_collection_id(url: str) -> str | None:
    match = re.search(r"figshare\.com/collections/[^/]+/(\d+)", url)
    return match.group(1) if match else None


def parse_physionet_dataset_base(url: str) -> tuple[str, str] | None:
    """
    Parse:
      https://physionet.org/content/afdb/1.0.0/
      https://physionet.org/files/afdb/1.0.0/
    -> ("afdb", "1.0.0")
    """
    match = re.search(r"physionet\.org/(?:content|files)/([^/]+)/([^/]+)/?", url)
    if not match:
        return None
    return match.group(1), match.group(2)


def filename_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name
    if not name:
        return None
    return name
