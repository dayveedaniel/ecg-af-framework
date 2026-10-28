from __future__ import annotations

from dataclasses import replace
from math import gcd

import numpy as np
from scipy.signal import resample_poly

from ecg_pipeline.reader.models import ECGAnnotations, RhythmEpisode


def resample_signal(signal: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
    if original_rate <= 0:
        raise ValueError(f"original_rate must be positive, got {original_rate}")
    if target_rate <= 0:
        raise ValueError(f"target_rate must be positive, got {target_rate}")
    if original_rate == target_rate:
        return signal
    if signal.ndim != 2:
        raise ValueError(f"signal must be 2D [num_samples, num_channels], got shape={signal.shape}")

    factor = gcd(original_rate, target_rate)
    up = target_rate // factor
    down = original_rate // factor
    return resample_poly(signal, up=up, down=down, axis=0)


def adjust_annotations(annotations: ECGAnnotations, original_rate: int, target_rate: int) -> ECGAnnotations:
    if original_rate == target_rate:
        return annotations

    def _scale(value: int) -> int:
        return int(round(value * target_rate / original_rate))

    beat_positions = None
    if annotations.beat_positions is not None:
        beat_positions = [_scale(x) for x in annotations.beat_positions]

    rhythm_episodes = None
    if annotations.rhythm_episodes is not None:
        rhythm_episodes = [
            RhythmEpisode(
                start_sample=_scale(ep.start_sample),
                end_sample=_scale(ep.end_sample),
                rhythm_type=ep.rhythm_type,
                original_label=ep.original_label,
            )
            for ep in annotations.rhythm_episodes
        ]

    delineation = annotations.delineation
    if delineation is not None:
        delineation = replace(
            delineation,
            p_onsets=_scale_list(delineation.p_onsets, _scale),
            p_offsets=_scale_list(delineation.p_offsets, _scale),
            qrs_onsets=_scale_list(delineation.qrs_onsets, _scale),
            qrs_offsets=_scale_list(delineation.qrs_offsets, _scale),
            t_onsets=_scale_list(delineation.t_onsets, _scale),
            t_offsets=_scale_list(delineation.t_offsets, _scale),
        )

    return ECGAnnotations(
        beat_positions=beat_positions,
        beat_labels=annotations.beat_labels,
        rhythm_episodes=rhythm_episodes,
        delineation=delineation,
    )


def _scale_list(values: list[int] | None, scale_fn) -> list[int] | None:
    if values is None:
        return None
    return [scale_fn(v) for v in values]
