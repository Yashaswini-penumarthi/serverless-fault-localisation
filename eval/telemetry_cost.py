"""Leg 3: monitoring overhead on the live rig.

Each telemetry condition (protocols/experiment.yaml, tracing: full / policy / off)
runs the same steady load for the same time. Per condition:

  volume   CloudWatch Logs IncomingBytes of the four log groups plus X-Ray trace
           bytes (traces recorded x mean segment bytes), per 1000 requests
  latency  end-to-end latency seen by the load generator (median, p95)
  cost     the volume priced with protocols/prices.yaml, per million requests

Every figure is reported with its sampling policy (Mertz and Nunes 2023; Huang
et al. 2024). The learned baselines run offline and cannot instrument the live
service, so their telemetry is only bounded from below by what their inputs
need: the benchmark's own metrics, logs and traces per request
(learned_lower_bound, from the RCAEval case files). That asymmetry goes with
every overhead comparison.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from eval import nonparametric_tests as stats


def condition(requests_csv: str | Path, log_bytes: float, traces_recorded: int, trace_bytes_mean: float,
              policy: dict) -> dict:
    req = pd.read_csv(requests_csv)
    ok = req[req["status"] > 0]
    n = len(req)
    total = log_bytes + traces_recorded * trace_bytes_mean
    return {"requests": n, "log_bytes": log_bytes, "traces_recorded": traces_recorded,
            "trace_bytes": traces_recorded * trace_bytes_mean, "bytes_per_1000_requests": 1000 * total / n if n else None,
            "latency_median_ms": float(ok["latency_ms"].median()), "latency_p95_ms": float(ok["latency_ms"].quantile(0.95)),
            "error_share": float((req["status"] >= 500).mean()), "policy": policy}


def reduction(policy: dict, full: dict) -> float:
    """Share of telemetry volume the rule arm's policy saves against full tracing / logging."""
    return 1 - policy["bytes_per_1000_requests"] / full["bytes_per_1000_requests"]


def latency_effect(on_csv: str | Path, off_csv: str | Path, alpha: float = 0.05) -> dict:
    on = pd.read_csv(on_csv).query("status > 0")["latency_ms"]
    off = pd.read_csv(off_csv).query("status > 0")["latency_ms"]
    return {"hypothesis": "H0: tracing does not change end-to-end latency", **stats.compare(on, off, alpha=alpha)}


def cost_per_million(cond: dict, prices: dict) -> dict:
    n = cond["requests"]
    gb = cond["log_bytes"] / 1e9 / n * 1e6
    traces = cond["traces_recorded"] / n * 1e6
    return {"logs_ingest_usd": gb * prices["logs_ingest_per_gb"],
            "xray_recorded_usd": traces * prices["xray_traces_recorded"],
            "total_usd": gb * prices["logs_ingest_per_gb"] + traces * prices["xray_traces_recorded"]}


def learned_lower_bound(data_dir: str | Path, pattern: str = "re2ob_*") -> pd.DataFrame:
    """Bytes of benchmark telemetry per request (one request = one trace), per case.

    Parquet is compressed, so this is a lower bound on what the learned methods'
    inputs weigh when they are collected and shipped.
    """
    rows = []
    for case in sorted(Path(data_dir).glob(pattern)):
        files = {k: case / f"{k}.parquet" for k in ("metrics", "logs", "traces")}
        if not files["traces"].exists():
            continue
        n_req = pd.read_parquet(files["traces"], columns=["traceID"])["traceID"].nunique()
        size = {k: f.stat().st_size if f.exists() else 0 for k, f in files.items()}
        rows.append({"case": case.name, "requests": n_req, **{f"{k}_bytes": v for k, v in size.items()},
                     "bytes_per_1000_requests": 1000 * sum(size.values()) / n_req if n_req else None})
    return pd.DataFrame(rows)


def write(out: Path, report: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / "overhead.json"
    p.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return p
