from __future__ import annotations

import math
import re
from collections.abc import Iterable

import numpy as np

DEFAULT_SAMPLE_RATE_HZ = 250

_SAMPLE_RATE_PATTERNS = [
    re.compile(
        r"(?:sampling[_\s-]*rate|sample[_\s-]*rate|sample[_\s-]*frequency|fs|freq(?:uency)?)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)",
        flags=re.IGNORECASE,
    ),
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*hz", flags=re.IGNORECASE),
]


def decode_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def normalize_sex(value: object) -> str | None:
    text = decode_text(value).strip().lower()
    if not text:
        return None
    if text in {"m", "male"}:
        return "M"
    if text in {"f", "female"}:
        return "F"
    return text.upper()


def parse_sample_rate_from_text(text: str) -> int | None:
    for pattern in _SAMPLE_RATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return normalize_sample_rate(float(match.group(1)))
        except (TypeError, ValueError):
            continue
    return None


def normalize_sample_rate(value: float | int | None) -> int | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rate):
        return None
    rounded = int(round(rate))
    if rounded < 1 or rounded > 10000:
        return None
    return rounded


def infer_sample_rate_from_time_values(values: np.ndarray) -> int | None:
    if values.size < 3:
        return None
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 3:
        return None
    diffs = np.diff(arr)
    diffs = diffs[(diffs > 0) & np.isfinite(diffs)]
    if diffs.size == 0:
        return None
    median_dt = float(np.median(diffs))
    if median_dt <= 0:
        return None

    hz_candidates = [1.0 / median_dt, 1000.0 / median_dt]
    for candidate in hz_candidates:
        normalized = normalize_sample_rate(candidate)
        if normalized is not None:
            return normalized
    return None


def normalize_signal_matrix(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    arr = np.squeeze(arr)

    if arr.ndim == 0:
        raise ValueError("Signal array is scalar")
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)

    if arr.ndim != 2:
        raise ValueError(f"Expected 2D signal matrix, got shape {arr.shape}")

    rows, cols = arr.shape
    if rows <= 32 and cols > rows * 2:
        arr = arr.T
        rows, cols = arr.shape

    if rows == 1 and cols > 1:
        arr = arr.reshape(-1, 1)

    if arr.shape[0] < 2:
        raise ValueError(f"Signal has too few samples: shape {arr.shape}")
    return arr


def default_channel_names(count: int, provided: list[str] | None = None) -> list[str]:
    if provided:
        out = [item for item in [decode_text(x).strip() for x in provided] if item]
        if len(out) == count:
            return out
    return [f"channel_{idx}" for idx in range(count)]


def flatten_strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            out: list[str] = []
            for item in value.flat:
                out.extend(flatten_strings(item))
            return out
        if value.dtype.kind in {"U", "S"}:
            return [decode_text(x).strip() for x in value.flatten().tolist() if decode_text(x).strip()]
        return []
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(flatten_strings(item))
        return out
    text = decode_text(value).strip()
    return [text] if text else []


def first_non_empty(values: Iterable[object]) -> str | None:
    for value in values:
        text = decode_text(value).strip()
        if text:
            return text
    return None
