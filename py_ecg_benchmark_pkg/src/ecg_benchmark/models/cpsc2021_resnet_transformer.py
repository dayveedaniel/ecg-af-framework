from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._torch_waveform import TorchWaveformShowcaseModel
from ecg_benchmark.models.raw_cnn import RAW_WAVEFORM_CONFIGURABLE_PARAMETERS, _import_torch
from ecg_benchmark.models.base import ModelInfo


class Cpsc2021ResNetTransformerModel(TorchWaveformShowcaseModel):
    """Small ResNet+Transformer inspired by CPSC 2021 paroxysmal-AF work."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="cpsc2021_resnet_transformer",
        description=(
            "Paper-inspired residual CNN plus Transformer encoder for paroxysmal AF event "
            "screening on raw single-lead waveform windows."
        ),
        required_columns=["label", "parquet_path", "lead_column", "start_sample", "end_sample"],
        output_score="PyTorch sigmoid probability for AF from raw ECG.",
        reproducibility="Deterministic when random_state is set, subject to PyTorch backend determinism.",
        configurable_parameters=dict(RAW_WAVEFORM_CONFIGURABLE_PARAMETERS),
        input_kind="waveform",
    )

    def _build_network(self, target_length: int, config: dict[str, Any]):
        torch = _import_torch()
        nn = torch.nn

        class ResidualBlock(nn.Module):
            def __init__(self, channels: int) -> None:
                super().__init__()
                self.block = nn.Sequential(
                    nn.Conv1d(channels, channels, kernel_size=7, padding=3),
                    nn.BatchNorm1d(channels),
                    nn.ReLU(),
                    nn.Conv1d(channels, channels, kernel_size=7, padding=3),
                    nn.BatchNorm1d(channels),
                )
                self.activation = nn.ReLU()

            def forward(self, x):
                return self.activation(x + self.block(x))

        class ResNetTransformer(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.stem = nn.Sequential(
                    nn.Conv1d(1, 32, kernel_size=15, padding=7),
                    nn.BatchNorm1d(32),
                    nn.ReLU(),
                    nn.MaxPool1d(4),
                    ResidualBlock(32),
                    nn.Conv1d(32, 48, kernel_size=7, padding=3),
                    nn.BatchNorm1d(48),
                    nn.ReLU(),
                    nn.MaxPool1d(4),
                    ResidualBlock(48),
                )
                encoder_layer = nn.TransformerEncoderLayer(
                    d_model=48,
                    nhead=4,
                    dim_feedforward=96,
                    dropout=0.1,
                    batch_first=True,
                )
                self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=1)
                self.head = nn.Linear(48, 1)

            def forward(self, x):
                x = self.stem(x).transpose(1, 2)
                x = self.encoder(x)
                return self.head(x.mean(dim=1))

        return ResNetTransformer()
