from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._torch_waveform import TorchWaveformShowcaseModel
from ecg_benchmark.models.raw_cnn import RAW_WAVEFORM_CONFIGURABLE_PARAMETERS, _import_torch
from ecg_benchmark.models.base import ModelInfo


class ZihlmannCrnnSpectrogramModel(TorchWaveformShowcaseModel):
    """Compact CRNN inspired by Zihlmann, Tschannen, and Perekrestenko."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="zihlmann_crnn_spectrogram",
        description=(
            "Paper-inspired convolutional recurrent network for short ECG windows. "
            "The showcase implementation uses raw waveform convolutions plus GRU temporal pooling."
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

        class Crnn(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.conv = nn.Sequential(
                    nn.Conv1d(1, 16, kernel_size=15, padding=7),
                    nn.BatchNorm1d(16),
                    nn.ReLU(),
                    nn.MaxPool1d(4),
                    nn.Conv1d(16, 32, kernel_size=9, padding=4),
                    nn.BatchNorm1d(32),
                    nn.ReLU(),
                    nn.MaxPool1d(4),
                )
                self.gru = nn.GRU(input_size=32, hidden_size=32, batch_first=True, bidirectional=True)
                self.head = nn.Linear(64, 1)

            def forward(self, x):
                x = self.conv(x)
                x = x.transpose(1, 2)
                _sequence, hidden = self.gru(x)
                hidden = hidden.transpose(0, 1).reshape(x.shape[0], -1)
                return self.head(hidden)

        return Crnn()
