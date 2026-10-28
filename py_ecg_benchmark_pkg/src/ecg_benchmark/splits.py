from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class RecordingRecord:
    """Catalog row used for split generation."""

    dataset_name: str
    full_recording_id: str
    subject_id: str | None = None
    af_present: bool | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def split_group(self) -> str:
        if self.subject_id:
            return f"subject:{self.dataset_name}:{self.subject_id}"
        return f"recording:{self.dataset_name}:{self.full_recording_id}"


@dataclass(frozen=True)
class SplitAssignment:
    dataset_name: str
    full_recording_id: str
    split: str
    group_id: str
    af_present: bool | None


def make_deterministic_splits(
    records: Iterable[RecordingRecord],
    *,
    seed: int,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    stratify_by: str = "af_present",
) -> list[SplitAssignment]:
    """Assign train/val/test deterministically with subject-safe grouping."""

    records = sorted(list(records), key=lambda item: (item.dataset_name, item.full_recording_id))
    if not records:
        return []
    _validate_ratios(train_ratio, val_ratio, test_ratio)
    rng = random.Random(seed)

    groups: dict[str, list[RecordingRecord]] = defaultdict(list)
    for record in records:
        groups[record.split_group].append(record)

    strata: dict[str, list[str]] = defaultdict(list)
    for group_id, group_records in groups.items():
        strata[_group_stratum(group_records, stratify_by)].append(group_id)

    group_to_split: dict[str, str] = {}
    for stratum, group_ids in sorted(strata.items()):
        ordered = sorted(group_ids)
        rng.shuffle(ordered)
        counts = _split_counts(len(ordered), train_ratio, val_ratio, test_ratio)
        cursor = 0
        for split_name in ["train", "val", "test"]:
            count = counts[split_name]
            for group_id in ordered[cursor : cursor + count]:
                group_to_split[group_id] = split_name
            cursor += count

    assignments = [
        SplitAssignment(
            dataset_name=record.dataset_name,
            full_recording_id=record.full_recording_id,
            split=group_to_split[record.split_group],
            group_id=record.split_group,
            af_present=record.af_present,
        )
        for record in records
    ]
    return assignments


def split_manifest(
    assignments: Iterable[SplitAssignment],
    *,
    seed: int,
    config_hash: str,
    ratios: dict[str, float],
) -> dict[str, Any]:
    """Build the JSON-serializable split artifact."""

    assignment_list = sorted(
        list(assignments),
        key=lambda item: (item.dataset_name, item.full_recording_id),
    )
    stats_counter = Counter(item.split for item in assignment_list)
    dataset_counter: dict[str, Counter[str]] = defaultdict(Counter)
    for item in assignment_list:
        dataset_counter[item.dataset_name][item.split] += 1
    payload = {
        "schema_version": 1,
        "random_seed": seed,
        "config_hash": config_hash,
        "ratios": ratios,
        "counts": dict(sorted(stats_counter.items())),
        "dataset_counts": {
            dataset: dict(sorted(counter.items()))
            for dataset, counter in sorted(dataset_counter.items())
        },
        "assignments": [asdict(item) for item in assignment_list],
    }
    payload["split_hash"] = split_hash(payload)
    return payload


def split_hash(payload: dict[str, Any]) -> str:
    payload_without_hash = {key: value for key, value in payload.items() if key != "split_hash"}
    canonical = json.dumps(payload_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_ratios(train: float, val: float, test: float) -> None:
    if any(value < 0 or value > 1 for value in [train, val, test]):
        raise ValueError("Split ratios must be between 0 and 1")
    if abs((train + val + test) - 1.0) > 1e-6:
        raise ValueError("Split ratios must sum to 1.0")


def _split_counts(n_items: int, train_ratio: float, val_ratio: float, test_ratio: float) -> dict[str, int]:
    train = int(n_items * train_ratio)
    val = int(n_items * val_ratio)
    test = n_items - train - val
    fractional = [
        ("train", (n_items * train_ratio) - train),
        ("val", (n_items * val_ratio) - val),
        ("test", (n_items * test_ratio) - test),
    ]
    counts = {"train": train, "val": val, "test": test}
    while sum(counts.values()) < n_items:
        name = max(fractional, key=lambda item: item[1])[0]
        counts[name] += 1
        fractional = [(key, -1.0 if key == name else value) for key, value in fractional]
    return counts


def _group_stratum(records: list[RecordingRecord], stratify_by: str) -> str:
    if stratify_by == "none":
        return "all"
    if stratify_by == "dataset":
        return f"dataset:{records[0].dataset_name}"
    if stratify_by == "dataset_af_present":
        return f"dataset:{records[0].dataset_name}:{_af_stratum(records)}"
    if stratify_by != "af_present":
        raise ValueError(f"Unsupported stratification key: {stratify_by}")
    return _af_stratum(records)


def _af_stratum(records: list[RecordingRecord]) -> str:
    values = {record.af_present for record in records}
    if values == {True}:
        return "af_present:true"
    if values == {False}:
        return "af_present:false"
    if values == {None}:
        return "af_present:null"
    return "af_present:mixed"
