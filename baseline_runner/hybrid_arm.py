"""Hybrid Rule + Lightweight ML (Isolation Forest) arm on RCAEval cases.

    python -m baseline_runner.hybrid_arm --data data/rcaeval --dataset RE2-OB --out outputs/rcaeval/raw/hybrid

This is the novel contribution: we use the fast rule-based method for detection,
but for localisation we use an Isolation Forest (Lightweight ML) fitted on the
control period. Then during the injection period, we score each service's 
metrics and traces.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import IsolationForest

from baseline_runner import rcaeval_data as rd
from detector import ranker, rules, spans

ROOT = Path(__file__).resolve().parents[1]


def series_from(m: pd.DataFrame, rc: dict) -> dict[str, pd.Series]:
    t = pd.to_datetime(m["time"], unit="s", utc=True).dt.floor(f"{rc['bucket_s']}s")
    out = {}
    for col in m.columns:
        svc, _, kind = col.partition("_")
        if kind in rc["series"]:
            out[f"{svc}/{rc['series'][kind]}"] = m[col].groupby(t).mean()
    return out


def rules_for(exp: dict) -> dict:
    r, rc = dict(exp["detector"]["rules"]), exp["rcaeval"]
    r["window_min"] = rc["rolling_s"] / 60
    r["min_std"] = rc["min_std"]
    return r


def extract_features(ser: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Extract features for the ML model over a specific time window."""
    data = []
    # group by service
    svcs = set([k.split('/')[0] for k in ser.keys()])
    for svc in svcs:
        row = {"service": svc}
        for k, s in ser.items():
            if k.startswith(svc + "/"):
                metric_name = k.split('/')[1]
                w = s[(s.index >= start) & (s.index < end)]
                row[f"{metric_name}_mean"] = w.mean() if not w.empty else 0
                row[f"{metric_name}_std"] = w.std() if not w.empty else 0
                row[f"{metric_name}_max"] = w.max() if not w.empty else 0
        data.append(row)
    df = pd.DataFrame(data).fillna(0).set_index("service")
    return df


def ml_localisation(ser: dict, t_start: pd.Timestamp, t_cal: pd.Timestamp, t_inj: pd.Timestamp, t_end: pd.Timestamp, candidates: list[str]) -> list[str]:
    """Train unsupervised lightweight ML (IsolationForest) on normal data and score anomaly data."""
    # We fit IF on instances (time-steps x services) or just (services) aggregated.
    # A robust way is to fit on the control period for each service.
    
    # We will score each candidate.
    scores = {}
    for svc in candidates:
        # Collect metric series for this service
        svc_keys = [k for k in ser.keys() if k.startswith(svc + "/")]
        if not svc_keys:
            scores[svc] = 0
            continue
            
        # Build dataframe for this service over time
        df = pd.DataFrame({k.split('/')[1]: ser[k] for k in svc_keys}).fillna(0)
        
        df_normal = df[(df.index >= t_start) & (df.index < t_cal)]
        df_anomaly = df[(df.index >= t_inj) & (df.index < t_end)]
        
        if df_normal.empty or df_anomaly.empty or len(df_normal) < 2:
            scores[svc] = 0
            continue
            
        clf = IsolationForest(contamination=0.01, random_state=42)
        try:
            clf.fit(df_normal.values)
            # anomaly score: negative is more anomalous. 
            # we want to rank highest the ones that are MOST anomalous.
            # score_samples returns negative scores for anomalies.
            s = clf.score_samples(df_anomaly.values)
            # The more negative, the more anomalous. Take the min score (most anomalous point)
            scores[svc] = -np.min(s)
        except:
            scores[svc] = 0
            
    # Rank by max anomaly score
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [s for s, _ in ranked]
    
    
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
    
    # ML Localisation
    order = ml_localisation(ser, t_start, t_cal, t_inj, t_inj + pd.Timedelta(seconds=rc["side_s"]), candidates)
    
    # If traces available, we can hybridize the score
    if c["traces"] is not None:
        tr = c["traces"]
        start_s = pd.to_numeric(tr["startTime"]) / 1e6
        normal = tr[(start_s >= it - rc["side_s"]) & (start_s < it)]
        anomal = tr[(start_s >= it) & (start_s < it + rc["rank_window_s"])]
        tcal = ranker.calibrate(spans.rcaeval_spans(normal))
        trace_order = ranker.ranking(spans.rcaeval_spans(anomal), candidates, tcal, exp["localisation"]["depth_weight"])
        
        # Hybrid combine:
        # Give a boost to the top choices from both methods
        combined_scores = {s: 0 for s in candidates}
        for i, s in enumerate(order):
            combined_scores[s] += (len(candidates) - i) * 0.5
        for i, s in enumerate(trace_order):
            combined_scores[s] += (len(candidates) - i) * 1.5 # weigh traces slightly higher
            
        order = sorted(combined_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        order = [s for s, _ in order]
        source = "hybrid_ml_traces"
    else:
        # Fill missing candidates
        order += [s for s in candidates if s not in order]
        source = "hybrid_ml_metrics"
        
    return {"case": c["case"], "root_cause": rd.root_cause(c["case"]), "fault": rd.fault_of(c["case"]),
            "detected": detected_at is not None,
            "delay_s": (detected_at - t_inj).total_seconds() if detected_at is not None else None,
            "control_fp_episodes": len(control_eps), "ranking": order, "ranking_source": source,
            "rank_seconds": round(time.perf_counter() - t0, 3), "threshold_sha256": cal["sha256"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/rcaeval")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--out", default="outputs/rcaeval/raw/hybrid")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / "protocols/experiment.yaml"))
    ds = args.dataset or exp["rcaeval"]["dataset"]
    prefix = ds.lower().replace("-", "")
    folders = sorted(p for p in Path(args.data).glob(f"{prefix}_*") if (p / "metrics.parquet").exists())[: args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(folders, 1):
        try:
            res = run_case(f, exp)
            (out / f"{f.name}.json").write_text(json.dumps(res, default=str) + "\n")
            print(f"{i}/{len(folders)} {f.name} detected={res['detected']} rank={res['ranking'][:3]}", flush=True)
        except Exception as e:
            print(f"Error on {f.name}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
