#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ecg_pipeline.manifest import SUPPORTED_PROVIDERS, load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate dataset manifest YAML")
    parser.add_argument("manifest", help="Path to a manifest YAML file")
    args = parser.parse_args()

    path = Path(args.manifest)
    try:
        manifest = load_manifest(path)
        provider = manifest.provider
    except Exception as exc:
        print(f"[invalid] {path}: {exc}", file=sys.stderr)
        return 2

    print("[ok]")
    print(f"  dataset.name: {manifest.dataset.name}")
    print(f"  force_redownload: {manifest.force_redownload}")
    print(f"  source.provider: {provider}")
    print(f"  source.url: {manifest.source.url}")
    print(f"  supported_providers: {', '.join(SUPPORTED_PROVIDERS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
