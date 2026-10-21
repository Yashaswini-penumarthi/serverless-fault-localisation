"""The fault switch every function reads.

The injector writes one SSM parameter, /<stack>/fault, as JSON, e.g.

    {"id": 17, "type": "elevated_latency", "target": "payments", "until": 1767225660.0, "latency_ms": 540}

or "{}" when nothing is injected. A warm container reads it at most every
FAULT_CACHE_S seconds (5 by default - one GetParameter per container per 5 s,
far below the SSM limit), and applies it only when it is the target and `until`
lies in the future. Reading the switch never breaks the service: any error
counts as "no fault".

  elevated_latency    sleep latency_ms, then work normally
  timeout             sleep hold_ms (longer than the caller's client deadline), then work normally -
                      the caller gives up, the callee finishes late
  dependency_failure  raise InjectedFailure - the function returns an error
  throttling          nothing here: the injector sets the target's reserved concurrency to 1
"""
from __future__ import annotations

import json
import os
import time

CACHE_S = float(os.environ.get("FAULT_CACHE_S", "5"))
_cache: dict = {"at": float("-inf"), "value": {}}


class InjectedFailure(Exception):
    """The dependency_failure fault."""


def reset_cache() -> None:
    _cache.update(at=float("-inf"), value={})


def current(ssm, name: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if now - _cache["at"] >= CACHE_S:
        try:
            _cache["value"] = json.loads(ssm.get_parameter(Name=name)["Parameter"]["Value"] or "{}")
        except Exception:  # noqa: BLE001 - the switch must never take the service down
            _cache["value"] = {}
        _cache["at"] = now
    f = _cache["value"]
    return f if f and float(f.get("until", 0)) > now else {}


def apply(service: str, fault: dict, sleep=time.sleep) -> str | None:
    """Apply the fault if this service is its target; returns the fault type applied (for the log)."""
    if not fault or fault.get("target") != service:
        return None
    kind = fault.get("type")
    if kind == "elevated_latency":
        sleep(float(fault.get("latency_ms", 500)) / 1000)
    elif kind == "timeout":
        sleep(float(fault.get("hold_ms", 1500)) / 1000)
    elif kind == "dependency_failure":
        raise InjectedFailure(f"injected dependency failure (injection {fault.get('id')})")
    else:
        return None
    return kind
