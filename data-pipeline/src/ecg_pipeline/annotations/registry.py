from __future__ import annotations

from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter

_ADAPTERS: dict[str, type[AnnotationAdapter]] = {}
_DEFAULTS_LOADED = False


def register_annotation_adapter(name: str):
    adapter_name = str(name).strip()
    if not adapter_name:
        raise ValueError("Annotation adapter name must be non-empty")

    def decorator(cls: type[AnnotationAdapter]) -> type[AnnotationAdapter]:
        _ADAPTERS[adapter_name] = cls
        cls.adapter_name = adapter_name
        return cls

    return decorator


def build_annotation_adapter(adapter_name: str, *, options: dict[str, Any] | None = None) -> AnnotationAdapter:
    _load_default_adapters()
    candidate = str(adapter_name).strip()
    cls = _ADAPTERS.get(candidate)
    if cls is None:
        known = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"Unknown annotation adapter '{candidate}'. Registered adapters: {known}")
    return cls(options=options)


def list_annotation_adapters() -> list[str]:
    _load_default_adapters()
    return sorted(_ADAPTERS)


def _load_default_adapters() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    import ecg_pipeline.annotations.adapters  # noqa: F401

    _DEFAULTS_LOADED = True
