from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactObject:
    key: str
    last_modified: dt.datetime | None = None
    size: int = 0
