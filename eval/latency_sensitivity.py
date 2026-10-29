"""Latency sensitivity for the 1 rps overhead runs: drop the first 30 s after each configuration change
(cold starts) and the rep-2 'off' run, which overlapped account-limit throttling (17:46-17:51 UTC).
    python -m eval.latency_sensitivity --out outputs/yash_overhead/latency_sensitivity.json"""
import argparse, json
import numpy as np, pandas as pd, scipy.stats as st
from eval import nonparametric_tests as stats

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args(argv)
    L = {}
    for c in ("full", "policy", "off"):
        v = []
        for r in (1, 2, 3):
            if c == "off" and r == 2:
                continue
            q = pd.read_csv(f"data/runs/yash_overhead_r{r}/requests_overhead-{c}.csv")
            q = q[(q.t_sent >= q.t_sent.min() + 30) & (q.status > 0)]
            v.append(q.latency_ms.to_numpy())
        L[c] = np.concatenate(v)
    rep = {"conditions": {c: {"n": int(len(v)), "median_ms": float(np.median(v)), "p95_ms": float(np.quantile(v, .95))}
                          for c, v in L.items()}, "tests": []}
    for x, y in (("full", "off"), ("policy", "off"), ("full", "policy")):
        u = st.mannwhitneyu(L[x], L[y], alternative="two-sided")
        rep["tests"].append({"pair": f"{x} vs {y}", "p": float(u.pvalue),
                             "rank_biserial": float(1 - 2 * u.statistic / (len(L[x]) * len(L[y])))})
    for t, q in zip(rep["tests"], stats.holm([t["p"] for t in rep["tests"]])):
        t["p_holm"] = float(q)
    open(a.out, "w").write(json.dumps(rep, indent=1) + "\n"); print(json.dumps(rep))

if __name__ == "__main__":
    main()
