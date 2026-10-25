#!/usr/bin/env python
"""Pull the rig's telemetry for `eval/live_campaign_eval.py --source live` (within 15 days: 1-minute metrics expire).

    python tools/pull_telemetry.py --run data/runs/live --stack faultlab

CloudWatch: the per-minute series of all four functions from the start of
calibration to the end of the last campaign. X-Ray: the last two hours of the
calibration period (to calibrate the ranker) and, per injection, the traces from
three minutes before it starts to three minutes after it ends - every window
the ranker can be asked about lies inside that, and retrieving only these keeps
the trace-access cost small. Writes series.parquet and spans.jsonl.gz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]


def trace_windows(windows: dict, schedules: dict[str, list[dict]], pad_s: int = 180,
                  calibration_tail_s: int = 7200) -> list[tuple[float, float]]:
    c0, c1 = windows["calibration"]
    out = [(max(c0, c1 - calibration_tail_s), c1)]
    for s in schedules.values():
        out += [(i["start"] - pad_s, i["end"] + pad_s) for i in s]
    out.sort()
    merged: list[list[float]] = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="data/runs/live")
    ap.add_argument("--stack", default="faultlab")
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args(argv)
    import boto3

    from detector import cloudwatch, spans, xray
    from injector import schedule as sched

    exp = yaml.safe_load(open(ROOT / __import__("os").environ.get("FAULTLAB_CONFIG", "protocols/experiment.yaml")))
    run = Path(args.run)
    windows = json.loads((run / "windows.json").read_text())
    schedules = {k: sched.read(run / f"schedule_{k}.jsonl") for k in windows if k in ("steady", "peak")}
    end = max(w["campaign"][1] for k, w in windows.items() if k in schedules)
    names = spans.service_names(args.stack, exp["services"])
    fns = {v: k for k, v in names.items()}
    utc = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc)  # noqa: E731
    series = cloudwatch.fetch(boto3.client("cloudwatch", region_name=args.region), fns,
                              utc(windows["calibration"][0]), utc(end))
    pd.DataFrame(series).to_parquet(run / "series.parquet")
    xr = boto3.client("xray", region_name=args.region)
    n = 0
    with gzip.open(run / "spans.jsonl.gz", "wt") as fh:
        for a, b in trace_windows(windows, schedules):
            for s in xray.window_spans(xr, utc(a), utc(b), names, f'service("{args.stack}-orders-api")'):
                fh.write(json.dumps(s) + "\n")
                n += 1
    print(f"{len(series)} series, {n} spans -> {run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
