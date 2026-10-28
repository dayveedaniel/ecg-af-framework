from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


AF_ALIASES = {"AF", "AFIB", "ATRIAL_FIBRILLATION", "ATRIAL FIBRILLATION", "(AFIB"}
AFL_ALIASES = {"AFL", "AFLUTTER", "ATRIAL_FLUTTER", "ATRIAL FLUTTER"}
NSR_ALIASES = {"NSR", "N", "NORMAL", "SINUS", "SINUS_RHYTHM", "SINUS RHYTHM"}
OTHER_KNOWN_NON_AF_ALIASES = {"OTHER"}
UNKNOWN_ALIASES = {"", "UNKNOWN", "UNLABELED", "NOISE"}
SUPPORTED_LABEL_POLICIES = {
    "af_vs_nsr",
    "af_vs_non_af",
    "af_or_afl_vs_nsr",
    "af_or_afl_vs_non_af",
}


@dataclass(frozen=True)
class RhythmEpisode:
    """One rhythm episode from an annotation sidecar, in seconds."""

    start_seconds: float
    end_seconds: float
    label: str

    def contains(self, window_start: float, window_end: float) -> bool:
        return self.start_seconds <= window_start and window_end <= self.end_seconds


@dataclass(frozen=True)
class WindowLabel:
    """Label assigned to one fixed-width ECG window."""

    start_seconds: float
    end_seconds: float
    label: int | None
    rhythm: str | None
    reason: str


def canonical_rhythm(label: str | None) -> str | None:
    """Normalize source rhythm strings into benchmark rhythm categories."""

    if label is None:
        return None
    normalized = str(label).strip().upper()
    if normalized in AF_ALIASES:
        return "AF"
    if normalized in AFL_ALIASES:
        return "AFL"
    if normalized in NSR_ALIASES:
        return "NSR"
    if normalized in OTHER_KNOWN_NON_AF_ALIASES:
        return "OTHER"
    if normalized in UNKNOWN_ALIASES:
        return None
    return None


def label_window(
    episodes: Iterable[RhythmEpisode],
    *,
    window_start: float,
    window_end: float,
    policy: str = "af_vs_nsr",
) -> WindowLabel:
    """Label a window according to a documented AF benchmark policy.

    A window is kept only when it is fully inside accepted positive or negative
    rhythm episodes for the selected policy. Boundary-spanning, unknown, and
    unmapped rhythm windows are intentionally dropped by returning ``label=None``.
    """

    positive, negative = _policy_rhythm_sets(policy)

    matching: list[tuple[str, str]] = []
    for episode in episodes:
        rhythm = canonical_rhythm(episode.label)
        if rhythm is not None and episode.contains(window_start, window_end):
            if rhythm in positive:
                matching.append(("positive", rhythm))
            elif rhythm in negative:
                matching.append(("negative", rhythm))

    categories = sorted({category for category, _ in matching})
    rhythms = sorted({rhythm for _, rhythm in matching})
    if categories == ["positive"]:
        rhythm = "+".join(rhythms)
        reason = "fully_inside_af" if rhythms == ["AF"] else "fully_inside_positive_rhythm"
        return WindowLabel(window_start, window_end, 1, rhythm, reason)
    if categories == ["negative"]:
        rhythm = "+".join(rhythms)
        reason = "fully_inside_nsr" if rhythms == ["NSR"] else "fully_inside_negative_rhythm"
        return WindowLabel(window_start, window_end, 0, rhythm, reason)
    if not categories:
        return WindowLabel(window_start, window_end, None, None, "not_fully_inside_accepted_rhythm")
    return WindowLabel(window_start, window_end, None, None, "ambiguous_overlapping_rhythm")


def iter_window_bounds(duration_seconds: float, window_seconds: int, stride_seconds: int) -> Iterable[tuple[float, float]]:
    """Yield deterministic fixed-width window bounds for one recording."""

    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if stride_seconds <= 0:
        raise ValueError("stride_seconds must be positive")
    if duration_seconds < window_seconds:
        return
    start = 0.0
    while start + window_seconds <= duration_seconds + 1e-9:
        end = start + window_seconds
        yield (round(start, 9), round(end, 9))
        start += stride_seconds


def labels_for_recording(
    *,
    duration_seconds: float,
    episodes: Iterable[RhythmEpisode],
    window_seconds: int,
    stride_seconds: int,
    policy: str = "af_vs_nsr",
) -> list[WindowLabel]:
    """Create labels for all candidate windows in one recording."""

    episode_list = list(episodes)
    return [
        label_window(episode_list, window_start=start, window_end=end, policy=policy)
        for start, end in iter_window_bounds(duration_seconds, window_seconds, stride_seconds)
    ]


def episodes_from_annotation_json(payload: dict[str, Any], *, sample_rate_hz: int | None = None) -> list[RhythmEpisode]:
    """Extract rhythm episodes from known annotation sidecar shapes.

    The current pipeline sidecars keep rhythm intervals as samples
    (``start_sample``/``end_sample``/``rhythm_type``). This parser also accepts
    common second-based aliases for future adapters and tests.
    """

    raw_episodes = (
        payload.get("rhythm_episodes")
        or payload.get("rhythms")
        or payload.get("episodes")
        or payload.get("annotations")
        or []
    )
    if not isinstance(raw_episodes, list):
        raise ValueError("Annotation JSON rhythm episodes must be a list")
    episodes: list[RhythmEpisode] = []
    for index, item in enumerate(raw_episodes):
        if not isinstance(item, dict):
            raise ValueError(f"Episode at index {index} must be a mapping")
        start = _first_present(item, ["start_seconds", "start_sec", "start", "onset_seconds"])
        end = _first_present(item, ["end_seconds", "end_sec", "end", "offset_seconds"])
        if start is None or end is None:
            start_sample = _first_present(item, ["start_sample", "onset_sample"])
            end_sample = _first_present(item, ["end_sample", "offset_sample"])
            if start_sample is not None and end_sample is not None:
                rate = sample_rate_hz or payload.get("target_sampling_rate") or payload.get("sampling_rate")
                if not rate:
                    raise ValueError("sample_rate_hz is required when rhythm episodes use sample indexes")
                start = float(start_sample) / float(rate)
                end = float(end_sample) / float(rate)
        label = _first_present(item, ["label", "rhythm", "rhythm_type", "symbol", "type"])
        if start is None or end is None:
            continue
        start_f = float(start)
        end_f = float(end)
        if end_f <= start_f:
            continue
        episodes.append(RhythmEpisode(start_seconds=start_f, end_seconds=end_f, label=str(label or "")))
    return episodes


def _first_present(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _policy_rhythm_sets(policy: str) -> tuple[set[str], set[str]]:
    if policy == "af_vs_nsr":
        return {"AF"}, {"NSR"}
    if policy == "af_vs_non_af":
        return {"AF"}, {"NSR", "AFL", "OTHER"}
    if policy == "af_or_afl_vs_nsr":
        return {"AF", "AFL"}, {"NSR"}
    if policy == "af_or_afl_vs_non_af":
        return {"AF", "AFL"}, {"NSR", "OTHER"}
    raise ValueError(f"Unsupported label policy: {policy}")
