"""Leg 3 over the overhead repetitions (Latin-square order).

    python -m eval.overhead_study --runs data/runs/yash_overhead_r1 data/runs/yash_overhead_r2 \
        data/runs/yash_overhead_r3 --out outputs/yash_overhead --figures figures/yash

Per repetition and condition: requests, log bytes, traces recorded, bytes per
1000 requests, latency. Across repetitions: mean and t-based 95% CI (n = 3, so
the intervals are wide). Latency is analysed both at the run level (paired t-test
and Wilcoxon signed-rank across independent repetitions, preserving run-level
clustering) and at the pooled request level (secondary descriptive check).
Cost per million requests from protocols/prices.yaml.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats as st

from eval import telemetry_cost as overhead, nonparametric_tests as stats

ROOT = Path(__file__).resolve().parents[1]
CONDS = ("full", "policy", "off")


def tci(x):
    x = np.asarray(x, float)
    m = float(x.mean())
    if len(x) < 2:
        return m, float("nan"), float("nan")
    h = float(st.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))
    return m, m - h, m + h


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conditions", default="full,policy,off")
    a = ap.parse_args(argv)
    conds = tuple(a.conditions.split(","))
    prices = yaml.safe_load(open(ROOT / "protocols/prices.yaml"))
    rows, lat, lat_trim = [], {c: [] for c in ("full", "policy", "off")}, {}
    for i, run in enumerate(a.runs, 1):
        run = Path(run)
        for c in conds:
            d = json.loads((run / f"overhead_{c}.json").read_text())
            req = pd.read_csv(run / f"requests_overhead-{c}.csv")
            ok = req[req["status"] > 0]
            lat[c].append(ok["latency_ms"].to_numpy())
            t0 = req["t_sent"].min()
            trim = req[req["t_sent"] >= t0 + 30]
            lat_trim.setdefault(c, []).append(trim[trim["status"] > 0]["latency_ms"].to_numpy())
            err_first30 = int(((req["t_sent"] < t0 + 30) & (req["status"] >= 500)).sum())
            cost = overhead.cost_per_million(d, prices)
            rows.append({"rep": i, "condition": c, "requests": d["requests"], "log_bytes": d["log_bytes"],
                         "traces_recorded": d["traces_recorded"], "trace_bytes": d["trace_bytes"],
                         "bytes_per_1000_requests": d["bytes_per_1000_requests"],
                         "log_bytes_per_1000": 1000 * d["log_bytes"] / d["requests"],
                         "traces_per_1000": 1000 * d["traces_recorded"] / d["requests"],
                         "latency_median_ms": d["latency_median_ms"], "latency_p95_ms": d["latency_p95_ms"],
                         "error_share": d["error_share"], "errors_5xx": int((req["status"] >= 500).sum()),
                         "errors_first30s": err_first30,
                         "latency_median_trim_ms": float(np.median(lat_trim[c][-1])),
                         "latency_p95_trim_ms": float(np.quantile(lat_trim[c][-1], 0.95)), "cost_usd_per_million": cost["total_usd"],
                         "cost_logs": cost["logs_ingest_usd"], "cost_xray": cost["xray_recorded_usd"]})
    df = pd.DataFrame(rows)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "overhead_per_rep.csv", index=False)
    summ = []
    for c in conds:
        g = df[df["condition"] == c]
        r = {"condition": c}
        for col in ("bytes_per_1000_requests", "log_bytes_per_1000", "traces_per_1000", "latency_median_ms",
                    "latency_p95_ms", "cost_usd_per_million", "error_share"):
            m, lo, hi = tci(g[col])
            r[col] = m
            r[col + "_lo"], r[col + "_hi"] = lo, hi
        summ.append(r)
    summ = pd.DataFrame(summ)
    summ.to_csv(out / "overhead_summary.csv", index=False)
    piv = df.pivot(index="rep", columns="condition", values="bytes_per_1000_requests")
    red = 1 - piv["policy"] / piv["full"]
    red_off = 1 - piv["off"] / piv["full"] if "off" in piv else pd.Series([np.nan])
    rep = {"per_rep_reduction_policy_vs_full": red.tolist(), "reduction_policy_vs_full": tci(red),
           "per_rep_reduction_off_vs_full": red_off.tolist(), "reduction_off_vs_full": tci(red_off),
           "expected_reduction": 0.5}
    pairs = [(x, y) for x, y in (("full", "off"), ("policy", "off"), ("full", "policy")) if x in conds and y in conds]
    # Primary cluster-robust inference: paired run-level comparisons across repetitions
    piv_med = df.pivot(index="rep", columns="condition", values="latency_median_ms")
    run_tests = []
    for x, y in pairs:
        rx, ry = piv_med[x].to_numpy(float), piv_med[y].to_numpy(float)
        diff = rx - ry
        p_t = float(st.ttest_rel(rx, ry).pvalue) if len(diff) >= 2 and np.ptp(diff) > 0 else 1.0
        nz = diff[diff != 0]
        p_w = float(st.wilcoxon(rx, ry).pvalue) if len(nz) >= 1 else 1.0
        run_tests.append({"pair": f"{x} vs {y}", "n_reps": int(len(diff)), "mean_median_a_ms": float(rx.mean()),
                          "mean_median_b_ms": float(ry.mean()), "mean_diff_ms": float(diff.mean()),
                          "p_paired_t": p_t, "p_wilcoxon": p_w})
    for t, q in zip(run_tests, stats.holm([t["p_paired_t"] for t in run_tests])):
        t["p_holm"] = float(q)
    rep["run_level_latency_tests"] = run_tests
    tests = []
    for x, y in pairs:
        A, B = np.concatenate(lat[x]), np.concatenate(lat[y])
        u = st.mannwhitneyu(A, B, alternative="two-sided")
        tests.append({"pair": f"{x} vs {y}", "n_a": len(A), "n_b": len(B), "median_a": float(np.median(A)),
                      "median_b": float(np.median(B)), "U": float(u.statistic), "p": float(u.pvalue),
                      "rank_biserial": float(1 - 2 * u.statistic / (len(A) * len(B)))})
    for t, q in zip(tests, stats.holm([t["p"] for t in tests])):
        t["p_holm"] = float(q)
    rep["latency_tests"] = tests
    tt = []
    for x, y in pairs:
        A, B = np.concatenate(lat_trim[x]), np.concatenate(lat_trim[y])
        u = st.mannwhitneyu(A, B, alternative="two-sided")
        tt.append({"pair": f"{x} vs {y}", "median_a": float(np.median(A)), "median_b": float(np.median(B)),
                   "p": float(u.pvalue), "rank_biserial": float(1 - 2 * u.statistic / (len(A) * len(B)))})
    for t, q in zip(tt, stats.holm([t["p"] for t in tt])):
        t["p_holm"] = float(q)
    rep["latency_tests_without_first_30s"] = tt
    (out / "overhead_reps.json").write_text(json.dumps(rep, indent=2, default=float) + "\n")
    print(df.to_string(index=False))
    print(summ.T.to_string())
    print(json.dumps(rep, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
