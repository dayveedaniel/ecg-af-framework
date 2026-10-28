from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable


@dataclass(frozen=True)
class LeadSelection:
    """Selected single-lead column for feature generation."""

    column_name: str
    lead_name: str
    reason: str


def select_single_lead(columns: Iterable[str], preferred_leads: list[str]) -> LeadSelection:
    """Select one ECG lead by preference with deterministic fallback."""

    column_list = list(columns)
    lead_columns = [column for column in column_list if column.startswith("channel_")]
    if not lead_columns:
        raise ValueError("No ECG channel columns found")

    by_lead: dict[str, str] = {}
    for column in lead_columns:
        lead_name = _lead_name_from_column(column)
        by_lead.setdefault(lead_name.upper(), column)

    for preferred in preferred_leads:
        match = by_lead.get(preferred.upper())
        if match:
            return LeadSelection(column_name=match, lead_name=_lead_name_from_column(match), reason="preferred")

    fallback = sorted(lead_columns)[0]
    return LeadSelection(column_name=fallback, lead_name=_lead_name_from_column(fallback), reason="fallback_first_channel")


def numeric_window_features(samples: list[float], *, rr_intervals_seconds: list[float] | None = None) -> dict[str, float]:
    """Compute small baseline features for one ECG window."""

    clean = [float(value) for value in samples if value is not None and math.isfinite(float(value))]
    if not clean:
        return {
            "signal_mean": 0.0,
            "signal_std": 0.0,
            "signal_min": 0.0,
            "signal_max": 0.0,
            "signal_range": 0.0,
            "rr_mean": 0.0,
            "rr_std": 0.0,
            "rr_cv": 0.0,
            "rr_count": 0.0,
        }
    signal_mean = mean(clean)
    signal_std = pstdev(clean) if len(clean) > 1 else 0.0
    rr_features = rr_interval_features(rr_intervals_seconds or [])
    return {
        "signal_mean": float(signal_mean),
        "signal_std": float(signal_std),
        "signal_min": float(min(clean)),
        "signal_max": float(max(clean)),
        "signal_range": float(max(clean) - min(clean)),
        **rr_features,
    }


def rr_interval_features(rr_intervals_seconds: list[float]) -> dict[str, float]:
    """Compute RR summary features used by the rule and tabular baselines."""

    values = [float(value) for value in rr_intervals_seconds if value is not None and value > 0]
    if not values:
        return {"rr_mean": 0.0, "rr_std": 0.0, "rr_cv": 0.0, "rr_count": 0.0}
    rr_mean = mean(values)
    rr_std = pstdev(values) if len(values) > 1 else 0.0
    rr_cv = rr_std / rr_mean if rr_mean else 0.0
    return {
        "rr_mean": float(rr_mean),
        "rr_std": float(rr_std),
        "rr_cv": float(rr_cv),
        "rr_count": float(len(values)),
    }


def _lead_name_from_column(column_name: str) -> str:
    parts = column_name.split("_", 2)
    if len(parts) == 3:
        return parts[2]
    return column_name

