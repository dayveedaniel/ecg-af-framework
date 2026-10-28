from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from ecg_benchmark.artifacts import parse_s3_uri
from ecg_benchmark.models.base import FeatureRows, ModelInfo, ModelNotFittedError
from ecg_benchmark.models.raw_cnn import (
    _RawCnnParams,
    _artifact_key_uri,
    _import_torch,
    _infer_target_length,
    _limit_rows,
    _load_waveform_tensors,
    _pos_weight_for_binary_labels,
    _resolve_device,
    _set_random_seed,
)
from ecg_benchmark.storage import S3ArtifactStore


class TorchWaveformShowcaseModel:
    """Shared trainer/persistence for raw-waveform PyTorch showcase models."""

    info: ClassVar[ModelInfo]

    def __init__(
        self,
        target_length: int | None = None,
        training_config: dict[str, Any] | None = None,
        network: Any | None = None,
    ) -> None:
        self.target_length = target_length
        self.training_config = training_config or {}
        self.network = network

    def fit(
        self,
        train_features: FeatureRows,
        val_features: FeatureRows,
        config: dict[str, Any],
    ) -> "TorchWaveformShowcaseModel":
        torch = _import_torch()
        nn = torch.nn
        optim = torch.optim
        if not train_features:
            raise RuntimeError(f"{self.info.model_id} requires at least one training waveform window")

        params = _RawCnnParams.from_config(config)
        _set_random_seed(params.random_state, torch)
        target_length = params.target_length or _infer_target_length(train_features)
        rows = _limit_rows(train_features, params.max_train_windows, params.random_state)
        x_train, y_train = _load_waveform_tensors(rows, target_length)
        if x_train.shape[0] == 0:
            raise RuntimeError(f"{self.info.model_id} could not load any training waveform windows")

        device = _resolve_device(params.device, torch)
        network = self._build_network(target_length, params.to_dict())
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
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        if not features:
            return []

        torch = _import_torch()
        x_eval, _labels = _load_waveform_tensors(features, self.target_length, require_labels=False)
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
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        torch = _import_torch()
        payload = {
            "model_id": self.info.model_id,
            "target_length": self.target_length,
            "training_config": self.training_config,
            "state_dict": self.network.state_dict(),
        }
        store = S3ArtifactStore.from_env()
        parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pt"))
        with tempfile.TemporaryDirectory(prefix=f"ecg-bench-{self.info.model_id}-save-") as tmpdir:
            path = Path(tmpdir) / "model.pt"
            torch.save(payload, path)
            store.upload_file(parsed.bucket, parsed.key, path)

    @classmethod
    def load(cls, s3_uri: str) -> "TorchWaveformShowcaseModel":
        torch = _import_torch()
        store = S3ArtifactStore.from_env()
        parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pt"))
        payload = torch.load(io.BytesIO(store.get_bytes(parsed.bucket, parsed.key)), map_location="cpu")
        target_length = int(payload["target_length"])
        training_config = dict(payload.get("training_config") or {})
        network = cls()._build_network(target_length, training_config)
        network.load_state_dict(payload["state_dict"])
        network.eval()
        return cls(target_length=target_length, training_config=training_config, network=network)

    def _build_network(self, target_length: int, config: dict[str, Any]):
        raise NotImplementedError
