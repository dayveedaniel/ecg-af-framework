from __future__ import annotations

import gzip
import tarfile
import zipfile
from pathlib import Path

from ecg_pipeline.downloader.strategies.base import safe_join


def detect_archive_format(path_or_name: str | Path) -> str | None:
    name = str(path_or_name).lower()
    if name.endswith(".tar.gz"):
        return "tar.gz"
    if name.endswith(".tgz"):
        return "tgz"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".gz"):
        return "gz"
    return None


def resolve_archive_format(path_or_name: str | Path) -> str | None:
    return detect_archive_format(path_or_name)


def extract_archive(
    archive_path: Path,
    *,
    archive_format: str,
    output_dir: Path,
) -> list[tuple[str, Path]]:
    """
    Extract an archive into output_dir.
    Returns list of (relative_path, extracted_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_format = archive_format.lower()

    if archive_format == "zip":
        return _extract_zip(archive_path, output_dir=output_dir)
    if archive_format in {"tar.gz", "tgz"}:
        return _extract_tar_gz(archive_path, output_dir=output_dir)
    if archive_format == "gz":
        return _extract_gz(archive_path, output_dir=output_dir)

    raise ValueError(f"Unsupported archive_format: {archive_format}")


def _extract_zip(archive_path: Path, *, output_dir: Path) -> list[tuple[str, Path]]:
    extracted: list[tuple[str, Path]] = []
    with zipfile.ZipFile(archive_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            rel = member.filename
            target = safe_join(output_dir, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append((rel, target))
    return extracted


def _extract_tar_gz(archive_path: Path, *, output_dir: Path) -> list[tuple[str, Path]]:
    extracted: list[tuple[str, Path]] = []
    with tarfile.open(archive_path, mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            rel = member.name
            target = safe_join(output_dir, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append((rel, target))
    return extracted


def _extract_gz(archive_path: Path, *, output_dir: Path) -> list[tuple[str, Path]]:
    name = archive_path.name
    if name.endswith(".gz"):
        name = name[: -len(".gz")]
    rel = name
    target = safe_join(output_dir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "rb") as src, target.open("wb") as dst:
        dst.write(src.read())
    return [(rel, target)]
