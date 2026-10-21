#!/usr/bin/env python
"""Seed deterministic, synthetic SKUs into the orders table (no real products or people).

    python tools/seed_inventory.py --table <TableName output> [--skus 200]
"""
from __future__ import annotations

import argparse
import random


def items(n: int, seed: int = 24262404) -> list[dict]:
    rng = random.Random(seed)
    return [{"pk": {"S": f"SKU#{i:04d}"}, "stock": {"N": str(rng.randint(50, 5000))},
             "price_cents": {"N": str(rng.randint(199, 19999))}} for i in range(1, n + 1)]


def seed(ddb, table: str, n: int) -> int:
    rows = items(n)
    for i in range(0, len(rows), 25):
        ddb.batch_write_item(RequestItems={table: [{"PutRequest": {"Item": it}} for it in rows[i:i + 25]]})
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", required=True)
    ap.add_argument("--skus", type=int, default=200)
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args(argv)
    import boto3

    print(seed(boto3.client("dynamodb", region_name=args.region), args.table, args.skus), "SKUs written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
