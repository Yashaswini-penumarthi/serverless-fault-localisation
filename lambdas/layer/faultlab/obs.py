"""Logging and tracing set-up shared by the functions.

LOG_LEVEL decides how much goes to CloudWatch Logs: INFO writes one JSON line per
request (the "full" telemetry condition), ERROR only writes failures (the rule
arm's policy). X-Ray: with active tracing the Lambda service records the
invocation; patching boto3 adds a subsegment for every AWS call (the downstream
Invoke, DynamoDB, SSM), which is what the dependency ranking needs. Patching is
skipped outside Lambda and when AWS_XRAY_SDK_ENABLED=false (tests, tracing off).
"""
from __future__ import annotations

import json
import os
import sys
import time

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def xray_enabled() -> bool:
    return "AWS_LAMBDA_FUNCTION_NAME" in os.environ and os.environ.get("AWS_XRAY_SDK_ENABLED", "true").lower() != "false"


def patch_xray() -> bool:
    if not xray_enabled():
        return False
    from aws_xray_sdk.core import patch  # only inside Lambda, from the layer

    patch(("boto3", "botocore"))
    return True


def log(service: str, level: str, message: str, **fields) -> bool:
    """One JSON line if `level` is at or above LOG_LEVEL; returns whether it was written."""
    if LEVELS[level] < LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), 20):
        return False
    print(json.dumps({"ts": round(time.time(), 3), "level": level, "service": service, "msg": message, **fields},
                     default=str), file=sys.stdout, flush=True)
    return True
