"""inventory: stock check for one SKU (reads the SKU#... items seeded by tools/seed_inventory.py).

An injected dependency_failure is raised, not caught, so Lambda counts it in
the Errors metric and X-Ray marks the segment - that is what the detector and
the ranker look for.
"""
from __future__ import annotations

import os
import time

import boto3

from faultlab import fault, obs

SERVICE = "inventory"
obs.patch_xray()
_clients: dict = {}


def clients() -> tuple:
    if not _clients:
        _clients["ddb"], _clients["ssm"] = boto3.client("dynamodb"), boto3.client("ssm")
    return _clients["ddb"], _clients["ssm"]


def handler(event, context):
    ddb, ssm = clients()
    t0 = time.perf_counter()
    applied = fault.apply(SERVICE, fault.current(ssm, os.environ["FAULT_PARAM"]))
    sku, qty = event["sku"], int(event.get("qty", 1))
    item = ddb.get_item(TableName=os.environ["TABLE_NAME"], Key={"pk": {"S": f"SKU#{sku}"}}).get("Item")
    stock = int(item["stock"]["N"]) if item else 1000
    price = int(item["price_cents"]["N"]) if item else 1999
    obs.log(SERVICE, "INFO", "reserve", sku=sku, qty=qty, stock=stock, fault=applied,
            ms=round((time.perf_counter() - t0) * 1000, 1))
    return {"reserved": stock >= qty, "price_cents": price}
