#!/usr/bin/env python
"""Whole-study budget estimate from list prices (protocols/prices.yaml), before any run.

    python tools/cost_estimator.py

Phases from protocols/experiment.yaml: 24 h calibration and the steady campaign at
2 rps, the peak campaign at 5x, and 30 minutes per telemetry condition. The
per-request constants below are assumptions (the pilot shows the real values).
The free tier is ignored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MIX = {"create": 0.80, "get": 0.15, "cancel": 0.05}          # traffic/open_loop_client.py
INVOCATIONS = {"create": 4, "get": 1, "cancel": 2}          # orders_api + inventory + payments + notifications
BILLED_MS = {"create": 210, "get": 40, "cancel": 90}        # assumed billed Lambda ms per request, all functions
WRU = {"create": 1.0, "get": 0.0, "cancel": 1.0}            # order put / status update
RRU = {"create": 0.5, "get": 0.5, "cancel": 0.0}            # SKU read / order read (eventually consistent)
REPORT_BYTES = 350                                          # START / END / REPORT lines Lambda always writes
INFO_BYTES = 250                                            # one INFO line per function call (full condition)


def per_request(table: dict) -> float:
    return sum(MIX[k] * v for k, v in table.items())


def traces_per_s(rps: float, reservoir: float, rate: float) -> float:
    return min(rps, reservoir) + max(0.0, rps - reservoir) * rate


def phases(exp: dict) -> list[dict]:
    steady = exp["load"]["steady_rps"]
    peak = steady * exp["load"]["peak_multiplier"]
    inj = exp["injection"]
    campaign_h = (4 * inj["per_type_per_load"] * (inj["duration_s"] + inj["recovery_s"])) / 3600 + \
        exp["control"]["minutes"] / 60
    pol, full = exp["tracing"]["policy"], exp["tracing"]["full"]
    over_h = exp["overhead"]["minutes_per_condition"] / 60
    return [
        {"phase": "calibration", "hours": exp["calibration"]["hours"], "rps": steady, "tracing": pol},
        {"phase": "campaign steady", "hours": campaign_h, "rps": steady, "tracing": pol},
        {"phase": "campaign peak", "hours": campaign_h, "rps": peak, "tracing": pol},
        {"phase": "overhead full", "hours": over_h, "rps": steady, "tracing": full},
        {"phase": "overhead policy", "hours": over_h, "rps": steady, "tracing": pol},
        {"phase": "overhead off", "hours": over_h, "rps": steady, "tracing": exp["tracing"]["off"]},
    ]


def estimate(exp: dict, prices: dict, memory_mb: int = 256) -> tuple[list[dict], float]:
    rows, total = [], 0.0
    for p in phases(exp):
        req = p["rps"] * p["hours"] * 3600
        inv = req * per_request(INVOCATIONS)
        gb_s = req * per_request(BILLED_MS) / 1000 * memory_mb / 1024
        t = p["tracing"]
        traces = 0.0 if t["mode"] == "PassThrough" else traces_per_s(p["rps"], t["reservoir_per_s"], t["fixed_rate"]) * p["hours"] * 3600
        log_gb = inv * (REPORT_BYTES + (INFO_BYTES if t["log_level"] == "INFO" else 0)) / 1e9
        usd = {"api": req * prices["api_rest_request"],
               "lambda": inv * prices["lambda_request"] + gb_s * prices["lambda_arm_gb_second"],
               "dynamodb": req * (per_request(WRU) * prices["dynamodb_write_request_unit"]
                                  + per_request(RRU) * prices["dynamodb_read_request_unit"]),
               "xray": traces * (prices["xray_traces_recorded"] + prices["xray_traces_accessed"]),
               "logs": log_gb * prices["logs_ingest_per_gb"]}
        rows.append({"phase": p["phase"], "hours": round(p["hours"], 2), "requests": int(req), "traces": int(traces),
                     "log_gb": round(log_gb, 3), **{k: round(v, 3) for k, v in usd.items()},
                     "usd": round(sum(usd.values()), 2)})
        total += sum(usd.values())
    return rows, total


def main() -> int:
    exp = yaml.safe_load(open(ROOT / "protocols/experiment.yaml"))
    prices = yaml.safe_load(open(ROOT / "protocols/prices.yaml"))
    rows, total = estimate(exp, prices)
    cols = list(rows[0])
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))
    hours = sum(r["hours"] for r in rows)
    print(f"total USD {total:.2f} over {hours:.0f} hours of load (free tier ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
