#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import psycopg2


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize ECG pipeline metadata schema in Postgres")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).resolve().parents[1] / "src/ecg_pipeline/registrar/schema.sql"),
        help="Path to schema.sql",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    sql = schema_path.read_text(encoding="utf-8")

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.db,
        user=args.user,
        password=args.password,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()
    print(f"Applied schema: {schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

