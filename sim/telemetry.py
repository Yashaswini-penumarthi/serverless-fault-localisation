"""Synthetic telemetry for the order service - a functional check of the rig pipeline, NOT AWS data.

It produces what the collectors would return from AWS - per-minute CloudWatch
series per function (ErrorRate, ThrottleRate, Duration) and flat trace spans
(orders_api calls inventory and payments synchronously, notifications
asynchronously) - for a calibration period and a campaign schedule. The faults
act the way the injector's do:

  elevated_latency    the target takes latency_ms longer; a synchronous caller waits for it
  timeout             the target holds the call for 1.5 s, the caller gives up at its 1 s deadline
  dependency_failure  every call to the target fails (Lambda Errors); the caller answers 502
  throttling          reserved concurrency 0: every call to the target is throttled

Noise, a slow daily drift and rare random blips come from a seeded RNG, so the
detector also meets false-positive opportunities. All numbers are invented;
only the plumbing is being checked.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BASE_MS = {"orders_api": 60.0, "inventory": 25.0, "payments": 20.0, "notifications": 10.0}
SYNC = ("inventory", "payments")
CLIENT_DEADLINE_MS = 1000.0
HOLD_MS = 1500.0


def _overlap(t0: float, t1: float, schedule: list[dict]) -> list[tuple[dict, float]]:
    """Injections overlapping [t0, t1) with the overlapping fraction."""
    out = []
    for inj in schedule:
        o = min(t1, inj["end"]) - max(t0, inj["start"])
        if o > 0:
            out.append((inj, o / (t1 - t0)))
    return out


def minute_values(t0: float, schedule: list[dict], rng: np.random.Generator, drift: float) -> dict[str, dict]:
    v = {s: {"dur": b * drift * rng.normal(1, 0.06), "err": max(0.0, rng.normal(0.001, 0.0008)), "thr": 0.0}
         for s, b in BASE_MS.items()}
    for s in v:
        if rng.random() < 0.002:  # a random blip - not a fault
            v[s]["dur"] *= 1.6
    for inj, f in _overlap(t0, t0 + 60, schedule):
        tgt, kind = inj["target"], inj["fault"]
        if kind == "elevated_latency":
            v[tgt]["dur"] += f * inj.get("latency_ms", 500)
            if tgt in SYNC:
                v["orders_api"]["dur"] += f * inj.get("latency_ms", 500)
        elif kind == "timeout":
            v[tgt]["dur"] += f * (HOLD_MS - BASE_MS[tgt])
            if tgt in SYNC:
                v["orders_api"]["dur"] += f * (CLIENT_DEADLINE_MS - BASE_MS["orders_api"])
        elif kind == "dependency_failure":
            v[tgt]["err"] += f
        elif kind == "throttling":
            v[tgt]["thr"] += f
    return v


def series(schedule: list[dict], start: float, end: float, seed: int = 0) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(pd.Timestamp(start, unit="s", tz="UTC").floor("min"),
                        pd.Timestamp(end, unit="s", tz="UTC").floor("min"), freq="60s", inclusive="left")
    cols: dict[str, list[float]] = {f"{s}/{m}": [] for s in BASE_MS for m in ("Duration", "ErrorRate", "ThrottleRate")}
    for t in idx:
        t0 = t.timestamp()
        drift = 1 + 0.04 * np.sin(2 * np.pi * t0 / 86400)
        v = minute_values(t0, schedule, rng, drift)
        for s, x in v.items():
            cols[f"{s}/Duration"].append(round(x["dur"], 3))
            cols[f"{s}/ErrorRate"].append(min(1.0, x["err"]))
            cols[f"{s}/ThrottleRate"].append(min(1.0, x["thr"]))
    return {k: pd.Series(vals, index=idx) for k, vals in cols.items()}


def traces(schedule: list[dict], start: float, end: float, per_minute: int = 12, seed: int = 1) -> list[dict]:
    """Sampled traces, per_minute of them spread over every minute."""
    rng = np.random.default_rng(seed)
    spans: list[dict] = []
    n = int((end - start) // 60 * per_minute)
    for k in range(n):
        t = start + (k + rng.random()) * 60 / per_minute
        active = {inj["target"]: inj for inj, _ in _overlap(t, t + 1e-3, schedule)}
        tid = f"s-{k}"
        cursor = t + 0.002
        root = {"trace_id": tid, "span_id": f"{tid}-o", "parent_id": None, "service": "orders_api", "start": t,
                "fault": False, "throttle": False}
        children = []
        failed_early = False
        for svc in SYNC:
            if failed_early:
                break
            d = BASE_MS[svc] * rng.normal(1, 0.06) / 1000
            sp = {"trace_id": tid, "span_id": f"{tid}-{svc}", "parent_id": root["span_id"], "service": svc,
                  "start": cursor, "fault": False, "throttle": False}
            inj = active.get(svc)
            if inj and inj["fault"] == "elevated_latency":
                d += inj.get("latency_ms", 500) / 1000
            elif inj and inj["fault"] == "timeout":
                d = HOLD_MS / 1000
            elif inj and inj["fault"] == "dependency_failure":
                sp["fault"], failed_early = True, True
            elif inj and inj["fault"] == "throttling":
                sp["throttle"], sp["fault"], failed_early, d = True, True, True, 0.003
            sp["end"] = cursor + d
            children.append(sp)
            waited = min(d, CLIENT_DEADLINE_MS / 1000)
            cursor += waited + 0.001
            if inj and inj["fault"] == "timeout":
                failed_early = True
        root["end"] = cursor + BASE_MS["orders_api"] * rng.normal(1, 0.06) / 1000
        if not failed_early:  # asynchronous notification after the order is stored
            inj = active.get("notifications")
            nd = BASE_MS["notifications"] / 1000 + (inj.get("latency_ms", 0) / 1000 if inj and
                                                   inj["fault"] == "elevated_latency" else 0.0)
            children.append({"trace_id": tid, "span_id": f"{tid}-n", "parent_id": root["span_id"],
                             "service": "notifications", "start": root["end"], "end": root["end"] + nd,
                             "fault": bool(inj and inj["fault"] == "dependency_failure"),
                             "throttle": bool(inj and inj["fault"] == "throttling")})
        spans.append(root)
        spans.extend(children)
    return spans
