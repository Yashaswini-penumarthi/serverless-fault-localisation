"""The rule-based arm on RCAEval cases (leg 2 of the comparison protocol).

    python -m baseline_runner.rule_arm --data data/rcaeval --dataset RE2-OB --out outputs/rcaeval/raw/rules

Same code as on AWS - detector/rules.py for detection, detector/ranker.py for
localisation - adapted to the benchmark's telemetry (protocols/experiment.yaml,
rcaeval section): per-service p90 latency stands in for Duration and the error
series for ErrorRate (RCAEval has no throttle metric), 1 s samples go into 10 s
buckets, and the 30-minute rolling baseline is scaled to the 300 s available.

Nothing is fitted on the faulty part. Each case calibrates on its own first
300 s, the next 300 s before the injection are the fault-free control (a firing
there is a false positive) and the 600 s after the injection are the fault.
Localisation ranks services from the traces of the first 300 s after the
injection (using async_leaf_slow=False because RE2-OB traces are synchronous
request trees rooted at frontend); cases without traces use the metric ranking
(largest |z|).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import yaml

from baseline_runner import rcaeval_data as rd
from detector import ranker, rules, spans

ROOT = Path(__file__).resolve().parents[1]


def series_from(m: pd.DataFrame, rc: dict) -> dict[str, pd.Series]:
    t = pd.to_datetime(m["time"], unit="s", utc=True).dt.floor(f"{rc['bucket_s']}s")
    out = {}
    for col in m.columns:
        svc, _, kind = col.partition("_")
        if kind in rc["series"]:
            out[f"{svc}/{rc['series'][kind]}"] = m[col].groupby(t).mean().round(12)
    return out


def rules_for(exp: dict) -> dict:
    """The AWS rules with the time windows scaled to RCAEval (sigma and percentile unchanged)."""
    r, rc = dict(exp["detector"]["rules"]), exp["rcaeval"]
    r["window_min"] = rc["rolling_s"] / 60
    r["min_std"] = rc["min_std"]  # the same floors, in the benchmark's units (seconds)
    return r


def run_case(folder: Path, exp: dict) -> dict:
    rc = exp["rcaeval"]
    c = rd.load(folder)
    it = c["inject_time"]
    m = rd.window(rd.prepare_metrics(c["metrics"]), it, rc["side_s"])
    ser = series_from(m, rc)
    t_inj = pd.Timestamp(it, unit="s", tz="UTC")
    t_cal = t_inj - pd.Timedelta(seconds=rc["control_s"])
    t_start = t_cal - pd.Timedelta(seconds=rc["calibration_s"])
    cal = rules.calibrate({k: s[(s.index >= t_start) & (s.index < t_cal)] for k, s in ser.items()}, rules_for(exp),
                          meta={"case": c["case"]})
    minutes = rules.detect({k: s[s.index >= t_cal] for k, s in ser.items()}, cal)
    step = pd.Timedelta(seconds=rc["bucket_s"])
    fired = minutes[minutes["fired"]]
    eps = rules.episodes(minutes, period_s=rc["bucket_s"], gap_min=rc["bucket_s"] / 60)
    control_eps = [e for e in eps if e["start"] < t_inj]
    after = fired[fired["time"] + step >= t_inj]
    detected_at = (after["time"].min() + step) if len(after) else None
    candidates = rd.services_of(c["metrics"].columns)
    t0 = time.perf_counter()
    if c["traces"] is not None:
        tr = c["traces"]
        start_s = pd.to_numeric(tr["startTime"]) / 1e6
        normal = tr[(start_s >= it - rc["side_s"]) & (start_s < it)]
        anomal = tr[(start_s >= it) & (start_s < it + rc["rank_window_s"])]
        tcal = ranker.calibrate(spans.rcaeval_spans(normal))
        order = ranker.ranking(spans.rcaeval_spans(anomal), candidates, tcal, exp["localisation"]["depth_weight"],
                               async_leaf_slow=False)
        source = "traces"
    elif c.get("anomal_spans") is not None and c.get("trace_calibration") is not None:
        order = ranker.ranking(c["anomal_spans"], candidates, c["trace_calibration"],
                               exp["localisation"]["depth_weight"], async_leaf_slow=False)
        source = "traces"
    else:
        order = rules.metric_ranking(minutes, t_inj, t_inj + pd.Timedelta(seconds=rc["side_s"]))
        order += [s for s in candidates if s not in order]
        source = "metrics"
    return {"case": c["case"], "root_cause": rd.root_cause(c["case"]), "fault": rd.fault_of(c["case"]),
            "detected": detected_at is not None,
            "delay_s": (detected_at - t_inj).total_seconds() if detected_at is not None else None,
            "control_fp_episodes": len(control_eps), "ranking": order, "ranking_source": source,
            "rank_seconds": round(time.perf_counter() - t0, 3), "threshold_sha256": cal["sha256"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/rcaeval")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--out", default="outputs/rcaeval/raw/rules")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / "protocols/experiment.yaml"))
    ds = args.dataset or exp["rcaeval"]["dataset"]
    prefix = ds.lower().replace("-", "")
    folders = sorted(p for p in Path(args.data).glob(f"{prefix}_*") if (p / "metrics.parquet").exists())[: args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(folders, 1):
        res = run_case(f, exp)
        (out / f"{f.name}.json").write_text(json.dumps(res, default=str) + "\n")
        print(f"{i}/{len(folders)} {f.name} detected={res['detected']} rank={res['ranking'][:3]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
