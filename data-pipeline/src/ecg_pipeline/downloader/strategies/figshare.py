from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.downloader.strategies._extract import extract_archive, resolve_archive_format
from ecg_pipeline.downloader.strategies._http import download_to_file, http_get_json
from ecg_pipeline.downloader.strategies.base import BaseDownloadStrategy
from ecg_pipeline.utils.helpers import figshare_article_id, figshare_collection_id, slugify


class FigshareStrategy(BaseDownloadStrategy):
    def run(self) -> Iterable[DownloadFileResult]:
        url = self.ctx.manifest.source.url
        article_id = figshare_article_id(url)
        if article_id:
            yield from self._download_article(article_id, url=url)
            return

        collection_id = figshare_collection_id(url)
        if collection_id:
            yield from self._download_collection(collection_id, url=url)
            return

        yield DownloadFileResult(status="error", message="unable to parse Figshare article/collection ID", url=url)

    def _download_collection(self, collection_id: str, *, url: str) -> Iterable[DownloadFileResult]:
        articles_api = f"https://api.figshare.com/v2/collections/{collection_id}/articles?page_size=1000"
        try:
            articles = http_get_json(articles_api, timeout_seconds=self.ctx.timeout_seconds)
        except Exception as exc:
            yield DownloadFileResult(status="error", message=f"Figshare collection API failed: {exc}", url=articles_api)
            return

        if not articles:
            yield DownloadFileResult(status="error", message="no articles in Figshare collection", url=url)
            return

        for article in articles:
            aid = str(article.get("id") or "")
            if not aid:
                continue
            title = article.get("title") or f"article-{aid}"
            article_slug = slugify(title)
            yield from self._download_article(aid, url=url, subdir=article_slug)

    def _download_article(self, article_id: str, *, url: str, subdir: str | None = None) -> Iterable[DownloadFileResult]:
        api_url = f"https://api.figshare.com/v2/articles/{article_id}"
        try:
            data = http_get_json(api_url, timeout_seconds=self.ctx.timeout_seconds)
        except Exception as exc:
            yield DownloadFileResult(status="error", message=f"Figshare article API failed: {exc}", url=api_url)
            return

        files = data.get("files", []) or []
        if not files:
            yield DownloadFileResult(status="error", message="no files in Figshare article", url=url)
            return

        with tempfile.TemporaryDirectory(prefix="ecg-pipeline-figshare-") as tmp:
            tmp_dir = Path(tmp)
            for item in files:
                name = item.get("name") or f"{item.get('id', 'file')}.bin"
                link = item.get("download_url")
                if not link:
                    yield DownloadFileResult(status="error", file=name, url=url, message="missing file download_url")
                    continue

                local_path = tmp_dir / name
                try:
                    size = download_to_file(
                        link,
                        local_path,
                        timeout_seconds=self.ctx.timeout_seconds,
                        chunk_size_bytes=self.ctx.chunk_size_bytes,
                    )
                except Exception as exc:
                    yield DownloadFileResult(status="error", file=name, url=link, message=str(exc))
                    continue

                base_key = f"{self.ctx.raw_prefix}{subdir}/" if subdir else self.ctx.raw_prefix

                archive_format = resolve_archive_format(local_path.name)
                if archive_format:
                    extracted_dir = tmp_dir / "extracted" / (subdir or "root") / name
                    try:
                        extracted = extract_archive(local_path, archive_format=archive_format, output_dir=extracted_dir)
                    except Exception as exc:
                        s3_key = f"{base_key}{name}"
                        self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=local_path)
                        yield DownloadFileResult(
                            status="error",
                            file=name,
                            url=link,
                            message=f"archive extraction failed ({archive_format}): {exc} (uploaded original)",
                            s3_key=s3_key,
                            size_bytes=size,
                        )
                        continue

                    for rel, path in extracted:
                        s3_key = f"{base_key}{rel}"
                        uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=path)
                        yield DownloadFileResult(status="ok", file=rel, url=link, message="extracted", s3_key=uploaded.key)
                    continue

                s3_key = f"{base_key}{name}"
                uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=local_path)
                yield DownloadFileResult(
                    status="ok",
                    file=name,
                    url=link,
                    message="downloaded",
                    s3_key=uploaded.key,
                    size_bytes=size,
                )
