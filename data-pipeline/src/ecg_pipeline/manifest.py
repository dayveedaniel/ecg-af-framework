from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from ecg_pipeline.utils.helpers import infer_provider_from_url

Provider = Literal["physionet_s3", "zenodo", "figshare", "direct_url"]
SUPPORTED_PROVIDERS: tuple[Provider, ...] = ("physionet_s3", "zenodo", "figshare", "direct_url")


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    display_name: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class SourceInfo:
    url: str
    provider: Provider | None = None


@dataclass(frozen=True)
class MetadataInfo:
    description: str | None = None
    citation: str | None = None
    license: str | None = None
    geographic_origin: str | None = None
    num_subjects: int | None = None
    num_recordings: int | None = None
    leads: list[str] | None = None


@dataclass(frozen=True)
class AnnotationsInfo:
    adapter: str
    options: dict[str, Any] = field(default_factory=dict)
    available: bool = False
    types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DatasetManifest:
    dataset: DatasetInfo
    source: SourceInfo
    force_redownload: bool = False
    metadata: MetadataInfo = field(default_factory=MetadataInfo)
    annotations: AnnotationsInfo = field(default_factory=lambda: AnnotationsInfo(adapter=""))
    quirks: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dataset_name(self) -> str:
        return self.dataset.name

    @property
    def provider(self) -> Provider:
        if self.source.provider:
            return self.source.provider
        inferred = infer_provider_from_url(self.source.url)
        return inferred

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DatasetManifest":
        dataset_dict = data.get("dataset") or {}
        source_dict = data.get("source") or {}

        if not isinstance(dataset_dict, dict):
            raise TypeError("manifest.dataset must be a mapping")
        if not isinstance(source_dict, dict):
            raise TypeError("manifest.source must be a mapping")
        if "archive_format" in source_dict:
            raise ValueError(
                "manifest.source.archive_format is no longer supported. "
                "Archive extraction is auto-detected from file extensions."
            )

        dataset = DatasetInfo(
            name=str(_req(dataset_dict, "name", "dataset")),
            display_name=str(_req(dataset_dict, "display_name", "dataset")),
            version=str(dataset_dict.get("version") or "1.0.0"),
        )
        provider_raw = source_dict.get("provider")
        provider: Provider | None = None
        if provider_raw is not None:
            candidate = str(provider_raw)
            if candidate not in SUPPORTED_PROVIDERS:
                allowed = ", ".join(SUPPORTED_PROVIDERS)
                raise ValueError(f"Unsupported source.provider '{candidate}'. Supported values: {allowed}")
            provider = cast(Provider, candidate)

        source = SourceInfo(
            url=str(_req(source_dict, "url", "source")),
            provider=provider,
        )
        force_redownload = _opt_bool(data.get("force_redownload"), key="force_redownload")

        metadata_dict = data.get("metadata") or {}
        if "annotations" not in data or data.get("annotations") is None:
            raise KeyError("Missing required key 'annotations'")
        annotations_dict = data.get("annotations")

        if metadata_dict and not isinstance(metadata_dict, dict):
            raise TypeError("manifest.metadata must be a mapping")
        if not isinstance(annotations_dict, dict):
            raise TypeError("manifest.annotations must be a mapping")

        metadata = MetadataInfo(
            description=metadata_dict.get("description"),
            citation=metadata_dict.get("citation"),
            license=metadata_dict.get("license"),
            geographic_origin=metadata_dict.get("geographic_origin"),
            num_subjects=_opt_int(metadata_dict.get("num_subjects")),
            num_recordings=_opt_int(metadata_dict.get("num_recordings")),
            leads=list(metadata_dict.get("leads") or []) or None,
        )

        options = annotations_dict.get("options") or {}
        if not isinstance(options, dict):
            raise TypeError("manifest.annotations.options must be a mapping")

        annotations = AnnotationsInfo(
            adapter=str(_req(annotations_dict, "adapter", "annotations")),
            options={str(k): v for k, v in options.items()},
            available=_opt_bool(annotations_dict.get("available"), key="annotations.available"),
            types=list(annotations_dict.get("types") or []),
        )

        # v4: `splits` is deprecated and intentionally ignored if present.
        known_keys = {"dataset", "source", "force_redownload", "metadata", "annotations", "splits", "quirks"}
        extra = {k: v for k, v in data.items() if k not in known_keys}

        quirks = data.get("quirks") or []
        if quirks and not isinstance(quirks, list):
            raise TypeError("manifest.quirks must be a list")

        return DatasetManifest(
            dataset=dataset,
            source=source,
            force_redownload=force_redownload,
            metadata=metadata,
            annotations=annotations,
            quirks=[str(x) for x in quirks],
            extra=extra,
        )


def load_manifest(path: str | Path) -> DatasetManifest:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Manifest root must be a mapping: {path}")
    return DatasetManifest.from_dict(data)


def _req(mapping: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping or mapping[key] is None or mapping[key] == "":
        raise KeyError(f"Missing required key '{ctx}.{key}'")
    return mapping[key]


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _opt_bool(value: Any, *, key: str) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise TypeError(f"manifest.{key} must be a boolean")
