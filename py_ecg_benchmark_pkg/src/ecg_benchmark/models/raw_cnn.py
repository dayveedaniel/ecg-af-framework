from __future__ import annotations

import io
import math
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ecg_benchmark.artifacts import parse_s3_uri
from ecg_benchmark.models.base import FeatureRows, ModelInfo, ModelNotFittedError
from ecg_benchmark.storage import S3ArtifactStore


RAW_WAVEFORM_CONFIGURABLE_PARAMETERS = {
    "target_length": "Number of waveform samples per window after pad/truncate/resample. Default: inferred from rows.",
    "epochs": "Training epochs. Default: 3.",
    "batch_size": "Training batch size. Default: 32.",
    "learning_rate": "Adam learning rate. Default: 0.001.",
    "max_train_windows": "Optional cap on training windows for smoke-test runs. Default: 2048.",
    "max_eval_windows": "Optional deterministic cap on evaluation windows. Default: no cap.",
    "device": "Torch device: auto, cpu, cuda, etc. Default: auto.",
    "random_state": "Integer seed. Default: benchmark random_seed when called through CLI.",
}


@dataclass
class RawCnn1DModel:
    """Small 1D CNN baseline over fixed-length single-lead ECG windows."""

    target_length: int | None = None
    training_config: dict[str, Any] = field(default_factory=dict)
    network: Any | None = None

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="raw_cnn_1d",
        description="Small PyTorch 1D CNN over raw single-lead ECG waveform windows.",
        required_columns=[
            "label",
            "parquet_path",
            "lead_column",
            "start_sample",
            "end_sample",
        ],
        output_score="PyTorch sigmoid probability for AF from a fixed-length waveform window.",
        reproducibility="Deterministic when random_state is set, subject to PyTorch backend determinism.",
        configurable_parameters=dict(RAW_WAVEFORM_CONFIGURABLE_PARAMETERS),
        input_kind="waveform",
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "RawCnn1DModel":
        torch = _import_torch()
        nn = torch.nn
        optim = torch.optim

        if not train_features:
            raise RuntimeError("raw_cnn_1d requires at least one training waveform window")

        params = _RawCnnParams.from_config(config)
        _set_random_seed(params.random_state, torch)
        target_length = params.target_length or _infer_target_length(train_features)
        rows = _limit_rows(train_features, params.max_train_windows, params.random_state)
        x_train, y_train = _load_waveform_tensors(rows, target_length)
        if x_train.shape[0] == 0:
            raise RuntimeError("raw_cnn_1d could not load any training waveform windows")

        device = _resolve_device(params.device, torch)
        network = _TinyRawCnn1D()
        network.to(device)
        network.train()

        labels = torch.tensor(y_train, dtype=torch.float32)
        loss_fn = nn.BCEWithLogitsLoss(pos_weight=_pos_weight_for_binary_labels(labels, torch=torch, device=device))
        optimizer = optim.Adam(network.parameters(), lr=params.learning_rate)
        dataset = torch.utils.data.TensorDataset(
            torch.tensor(x_train, dtype=torch.float32),
            labels,
        )
        generator = torch.Generator()
        generator.manual_seed(params.random_state)
        loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=params.batch_size,
            shuffle=True,
            generator=generator,
        )

        for _epoch in range(params.epochs):
            for x_batch, y_batch in loader:
                x_batch = x_batch.to(device)
                y_batch = y_batch.to(device)
                optimizer.zero_grad(set_to_none=True)
                logits = network(x_batch).squeeze(1)
                loss = loss_fn(logits, y_batch)
                loss.backward()
                optimizer.step()

        network.eval()
        self.target_length = int(target_length)
        self.training_config = params.to_dict()
        self.network = network.to("cpu")
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        if self.network is None or self.target_length is None:
            raise ModelNotFittedError("raw_cnn_1d has not been fitted")
        if not features:
            return []

        torch = _import_torch()
        rows = list(features)
        x_eval, _labels = _load_waveform_tensors(rows, self.target_length, require_labels=False)
        if x_eval.shape[0] == 0:
            return []

        device = _resolve_device(str(self.training_config.get("device", "auto")), torch)
        network = self.network.to(device)
        network.eval()
        batch_size = int(self.training_config.get("batch_size", 32))
        scores: list[float] = []
        with torch.no_grad():
            for start in range(0, len(x_eval), batch_size):
                batch = torch.tensor(x_eval[start : start + batch_size], dtype=torch.float32, device=device)
                logits = network(batch).squeeze(1)
                scores.extend(torch.sigmoid(logits).detach().cpu().tolist())
        self.network = network.to("cpu")
        return [float(score) for score in scores]

    def save(self, s3_uri: str) -> None:
        if self.network is None or self.target_length is None:
            raise ModelNotFittedError("raw_cnn_1d has not been fitted")
        torch = _import_torch()
        payload = {
            "model_id": self.info.model_id,
            "target_length": self.target_length,
            "training_config": self.training_config,
            "state_dict": self.network.state_dict(),
        }
        store = S3ArtifactStore.from_env()
        parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pt"))
        with tempfile.TemporaryDirectory(prefix="ecg-bench-raw-cnn-save-") as tmpdir:
            path = Path(tmpdir) / "model.pt"
            torch.save(payload, path)
            store.upload_file(parsed.bucket, parsed.key, path)

    @classmethod
    def load(cls, s3_uri: str) -> "RawCnn1DModel":
        torch = _import_torch()
        store = S3ArtifactStore.from_env()
        parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pt"))
        payload = torch.load(io.BytesIO(store.get_bytes(parsed.bucket, parsed.key)), map_location="cpu")
        network = _TinyRawCnn1D()
        network.load_state_dict(payload["state_dict"])
        network.eval()
        return cls(
            target_length=int(payload["target_length"]),
            training_config=dict(payload.get("training_config") or {}),
            network=network,
        )


@dataclass(frozen=True)
class _RawCnnParams:
    target_length: int | None = None
    epochs: int = 3
    batch_size: int = 32
    learning_rate: float = 1e-3
    max_train_windows: int | None = 2048
    device: str = "auto"
    random_state: int = 0

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "_RawCnnParams":
        target_length = _optional_positive_int(raw.get("target_length"), "target_length")
        max_train = _optional_positive_int(raw.get("max_train_windows", 2048), "max_train_windows")
        return cls(
            target_length=target_length,
            epochs=_positive_int(raw.get("epochs", 3), "epochs"),
            batch_size=_positive_int(raw.get("batch_size", 32), "batch_size"),
            learning_rate=_positive_float(raw.get("learning_rate", 1e-3), "learning_rate"),
            max_train_windows=max_train,
            device=str(raw.get("device", "auto")),
            random_state=int(raw.get("random_state", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_length": self.target_length,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "learning_rate": self.learning_rate,
            "max_train_windows": self.max_train_windows,
            "device": self.device,
            "random_state": self.random_state,
        }


def _import_torch():
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("raw_cnn_1d requires PyTorch. Install the package with the 'raw' extra.") from exc
    return torch


def _set_random_seed(seed: int, torch) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _resolve_device(device: str, torch):
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device)


def _pos_weight_for_binary_labels(labels: Any, *, torch: Any, device: Any):
    positives = float(labels.sum().item())
    negatives = float(len(labels) - positives)
    if positives > 0 and negatives > 0:
        return torch.tensor([negatives / positives], dtype=torch.float32, device=device)
    return None


def _infer_target_length(rows: FeatureRows) -> int:
    for row in rows:
        for key in ["target_length", "window_samples", "expected_samples"]:
            value = row.get(key)
            if value:
                return max(1, int(value))
        if row.get("end_sample") is not None and row.get("start_sample") is not None:
            return max(1, int(row["end_sample"]) - int(row["start_sample"]))
        samples = row.get("samples")
        if samples:
            return max(1, len(samples))
    return 2500


def _limit_rows(rows: FeatureRows, max_rows: int | None, seed: int) -> FeatureRows:
    rows = list(rows)
    if not max_rows or len(rows) <= max_rows:
        return rows
    by_label: dict[int, list[int]] = {0: [], 1: []}
    other: list[int] = []
    for index, row in enumerate(rows):
        label = int(row.get("label", -1))
        if label in by_label:
            by_label[label].append(index)
        else:
            other.append(index)
    rng = random.Random(seed)
    for group in [*by_label.values(), other]:
        rng.shuffle(group)
    selected_indices = by_label[0][: max_rows // 2] + by_label[1][: max_rows // 2]
    selected_index_set = set(selected_indices)
    remaining = [index for index in range(len(rows)) if index not in selected_index_set]
    rng.shuffle(remaining)
    selected_indices.extend(remaining[: max_rows - len(selected_indices)])
    rng.shuffle(selected_indices)
    return [rows[index] for index in selected_indices]


def _load_waveform_tensors(
    rows: FeatureRows,
    target_length: int,
    *,
    require_labels: bool = True,
) -> tuple[Any, list[int]]:
    import numpy as np

    labels: list[int] = []
    tensors: list[Any] = []
    with tempfile.TemporaryDirectory(prefix="ecg-bench-waveforms-") as tmpdir:
        cache = _ParquetSignalCache(Path(tmpdir))
        for row in rows:
            signal = _signal_for_row(row, cache)
            tensors.append(_prepare_signal(signal, target_length))
            if require_labels:
                labels.append(_label(row))
            elif "label" in row:
                labels.append(int(row["label"]))
    if not tensors:
        return np.zeros((0, 1, target_length), dtype="float32"), labels
    return np.stack(tensors, axis=0).astype("float32"), labels


def _signal_for_row(row: dict[str, Any], cache: "_ParquetSignalCache"):
    import numpy as np

    samples = row.get("samples")
    if samples is not None:
        return np.asarray(samples, dtype="float32")

    parquet_path = row.get("parquet_path")
    lead_column = row.get("lead_column")
    if not parquet_path or not lead_column:
        raise KeyError("Waveform rows must contain 'parquet_path' and 'lead_column'")
    frame = cache.frame(str(parquet_path))
    if "sample_index" not in frame.columns:
        raise KeyError(f"Parquet ECG data is missing required column 'sample_index': {parquet_path}")
    if str(lead_column) not in frame.columns:
        raise KeyError(f"Parquet ECG data is missing lead column '{lead_column}': {parquet_path}")
    start_sample = int(row["start_sample"])
    end_sample = int(row["end_sample"])
    mask = (frame["sample_index"] >= start_sample) & (frame["sample_index"] < end_sample)
    return frame.loc[mask, str(lead_column)].to_numpy(dtype="float32")


def _prepare_signal(signal, target_length: int):
    import numpy as np

    values = np.asarray(signal, dtype="float32")
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    if len(values) == 0:
        values = np.zeros(target_length, dtype="float32")
    elif len(values) != target_length:
        if len(values) == 1:
            values = np.full(target_length, float(values[0]), dtype="float32")
        else:
            source_x = np.linspace(0.0, 1.0, num=len(values), dtype="float32")
            target_x = np.linspace(0.0, 1.0, num=target_length, dtype="float32")
            values = np.interp(target_x, source_x, values).astype("float32")
    mean = float(values.mean())
    std = float(values.std())
    if math.isfinite(std) and std > 1e-6:
        values = (values - mean) / std
    else:
        values = values - mean
    return values.reshape(1, target_length).astype("float32")


class _ParquetSignalCache:
    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir
        self.store = S3ArtifactStore.from_env()
        self._frames: dict[str, Any] = {}

    def frame(self, uri: str):
        if uri not in self._frames:
            self._frames[uri] = self._read_frame(uri)
        return self._frames[uri]

    def _read_frame(self, uri: str):
        import pandas as pd

        parsed = parse_s3_uri(uri)
        prefix = parsed.key.rstrip("/")
        keys = [key for key in self.store.iter_keys(parsed.bucket, prefix) if key.endswith(".parquet")]
        if not keys and parsed.key.endswith(".parquet"):
            keys = [parsed.key]
        if not keys:
            raise RuntimeError(f"No parquet files found for waveform source: {uri}")

        root = self.tmpdir / f"source-{len(self._frames)}"
        for key in keys:
            try:
                relative = Path(key).relative_to(prefix)
            except ValueError:
                relative = Path(Path(key).name)
            if str(relative) == ".":
                relative = Path(Path(key).name)
            target = root / relative
            self.store.download_file(parsed.bucket, key, target)
        return pd.read_parquet(root)


def _label(row: dict[str, Any]) -> int:
    if "label" not in row:
        raise KeyError("Waveform rows must contain a 'label' column")
    label = int(row["label"])
    if label not in {0, 1}:
        raise ValueError(f"Labels must be 0 or 1, got: {label}")
    return label


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _optional_positive_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, name)


def _positive_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _artifact_key_uri(s3_uri: str, filename: str) -> str:
    return f"{s3_uri.rstrip('/')}/{filename}"


def _TinyRawCnn1D():
    torch = _import_torch()
    nn = torch.nn
    return nn.Sequential(
        nn.Conv1d(1, 8, kernel_size=15, padding=7),
        nn.ReLU(),
        nn.MaxPool1d(kernel_size=4),
        nn.Conv1d(8, 16, kernel_size=9, padding=4),
        nn.ReLU(),
        nn.MaxPool1d(kernel_size=4),
        nn.Conv1d(16, 32, kernel_size=5, padding=2),
        nn.ReLU(),
        nn.AdaptiveAvgPool1d(1),
        nn.Flatten(),
        nn.Linear(32, 1),
    )
