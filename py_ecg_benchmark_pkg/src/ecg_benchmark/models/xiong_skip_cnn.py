from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._torch_waveform import TorchWaveformShowcaseModel
from ecg_benchmark.models.raw_cnn import RAW_WAVEFORM_CONFIGURABLE_PARAMETERS, _import_torch
from ecg_benchmark.models.base import ModelInfo


class XiongSkipCnn1DModel(TorchWaveformShowcaseModel):
    """Small skip-connected 1D CNN inspired by Xiong et al.'s CinC 2017 model."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="xiong_skip_cnn_1d",
        description=(
            "Paper-inspired skip-connected 1D CNN over raw single-lead waveform windows, "
            "based on the robust ECG neural-network family from PhysioNet/CinC 2017."
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
                    nn.Conv1d(channels, channels, kernel_size=9, padding=4),
                    nn.BatchNorm1d(channels),
                    nn.ReLU(),
                    nn.Conv1d(channels, channels, kernel_size=9, padding=4),
                    nn.BatchNorm1d(channels),
                )
                self.activation = nn.ReLU()

            def forward(self, x):
                return self.activation(x + self.block(x))

        return nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=15, padding=7),
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.MaxPool1d(2),
            ResidualBlock(16),
            nn.Conv1d(16, 32, kernel_size=9, padding=4),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(2),
            ResidualBlock(32),
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(32, 1),
        )
