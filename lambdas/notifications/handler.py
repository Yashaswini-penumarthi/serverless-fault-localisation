"""notifications: event notification stub, invoked asynchronously (nothing is really sent)."""
from __future__ import annotations

import os
import time

import boto3

from faultlab import fault, obs

SERVICE = "notifications"
obs.patch_xray()
_clients: dict = {}


def clients():
    if not _clients:
        _clients["ssm"] = boto3.client("ssm")
    return _clients["ssm"]


def handler(event, context):
    t0 = time.perf_counter()
    applied = fault.apply(SERVICE, fault.current(clients(), os.environ["FAULT_PARAM"]))
    obs.log(SERVICE, "INFO", "notify", order_id=event.get("order_id"), event=event.get("event"), fault=applied,
            ms=round((time.perf_counter() - t0) * 1000, 1))
    return {"sent": True}
