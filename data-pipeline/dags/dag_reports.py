from __future__ import annotations

import json

from dag_shared import build_s3, parse_s3_uri
from ecg_pipeline.reader.models import ProcessingResult


def load_ok_results_from_processing_report(*, report_uri: str) -> list[ProcessingResult]:
    bucket, key = parse_s3_uri(report_uri)
    s3_client = build_s3()
    payload = json.loads(s3_client.get_bytes(bucket=bucket, key=key).decode("utf-8"))
    rows = payload.get("results") or []
    if not isinstance(rows, list):
        raise TypeError("Processing report 'results' must be a list")
    return [ProcessingResult(**item) for item in rows]
