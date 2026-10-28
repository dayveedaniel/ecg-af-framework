from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dashboard.server import make_dashboard_handler, serve_dashboard
from dashboard.services import DashboardDataService
from dashboard.runner import DockerComposeCommandFactory, ExperimentRunner
from dashboard.types import ArtifactObject
from dashboard.results import attach_timing_rows, parse_run_log, pivot_metric_rows

__all__ = [
    "ArtifactObject",
    "DashboardDataService",
    "DockerComposeCommandFactory",
    "ExperimentRunner",
    "attach_timing_rows",
    "make_dashboard_handler",
    "parse_run_log",
    "pivot_metric_rows",
    "serve_dashboard",
]
