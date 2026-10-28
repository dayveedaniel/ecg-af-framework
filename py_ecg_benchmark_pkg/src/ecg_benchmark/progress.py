from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any


class ProgressReporter:
    """Write structured workflow progress events as JSON lines."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._counter = 0

    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        row = {
            "event": event,
            "timestamp": _utc_now(),
            **payload,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str) + "\n")
        return row

    def plan(self, *, total: int, label: str | None = None, **metadata: Any) -> None:
        self.emit("plan", total=max(0, int(total)), label=label, **metadata)

    def start(self, stage: str, label: str, **metadata: Any) -> str:
        with self._lock:
            self._counter += 1
            stage_id = f"{self._counter:04d}-{stage}"
        self.emit(
            "stage_started",
            stage_id=stage_id,
            stage=stage,
            label=label,
            **metadata,
        )
        return stage_id

    def finish(self, stage_id: str, *, status: str = "succeeded", **metadata: Any) -> None:
        self.emit("stage_finished", stage_id=stage_id, status=status, **metadata)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
