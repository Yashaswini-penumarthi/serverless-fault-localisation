"""payments: authorisation stub (approves up to 5000.00 EUR, deterministic, no real payment)."""
from __future__ import annotations

import hashlib
import os
import time

import boto3

from faultlab import fault, obs

SERVICE = "payments"
LIMIT_CENTS = 500_000
obs.patch_xray()
_clients: dict = {}


def clients():
    if not _clients:
        _clients["ssm"] = boto3.client("ssm")
    return _clients["ssm"]


def handler(event, context):
    t0 = time.perf_counter()
    applied = fault.apply(SERVICE, fault.current(clients(), os.environ["FAULT_PARAM"]))
    amount = int(event["amount_cents"])
    approved = 0 < amount <= LIMIT_CENTS
    auth = hashlib.sha256(f"{event['customer_id']}:{amount}:{time.time_ns()}".encode()).hexdigest()[:16]
    obs.log(SERVICE, "INFO", "authorise", amount_cents=amount, approved=approved, fault=applied,
            ms=round((time.perf_counter() - t0) * 1000, 1))
    return {"approved": approved, "auth_id": auth if approved else ""}
