"""Extra analyses on the live run (added for the final report, run after the campaign).

    FAULTLAB_CONFIG=protocols/experiment_live.yaml python -m eval.trace_ablation --run data/runs/yash_live \
        --out results/yash_live/extras

Everything here reuses the frozen calibration of eval/live_campaign_eval.py; nothing is tuned on
the evaluation data. Labelled in the output:

  E1  localisation baselines on the same detected injections: X-Ray ranker,
      metric-only ranker (largest |z| per service in the 120 s before detection),
      uniform random ranker (analytic top-1 = 1/4, top-3 = 3/4 for 4 candidates);
      exact McNemar for paired rankers, paired Wilcoxon signed-rank on ranks,
      exact binomial against random; Holm. Also reports unconditional end-to-end
      localisation across all 24 injections (treating undetected faults as rank 5).
  E2  depth_weight sensitivity (POST HOC, reported for robustness only).
  E3  trace thinning: whole traces kept with probability r (20 seeds), ranker
      re-run on the thinned traces; links trace volume to localisation accuracy.
  E4  steady vs peak: Fisher exact tests on recall and end-to-end top-3.
  E5  bootstrap CI for detection F1 (resampling injections and false-positive
      episodes together), Wilson CIs for precision and recall.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats as st

from detector import ranker, rules
from eval import detection_scoring as metrics, live_campaign_eval as rig, nonparametric_tests as stats

ROOT = Path(__file__).resolve().parents[1]


def _metric_ranks(series, windows, per, exp, cal):
    out = {}
    for load in per["load"].unique():
        w = windows[load]
        minutes = rules.detect(rig._cut(series, w["control"][0], w["campaign"][1]), cal)
        for r in per[(per["load"] == load) & per["detected"]].itertuples():
            t = r.detected_at
            order = rules.metric_ranking(minutes, t - pd.Timedelta(seconds=rig.RANK_WINDOW_S), t)
            order += [s for s in sorted(exp["services"]) if s not in order]
            out[r.injection_id] = metrics.rank_of(order, r.target)
    return out


def _ranks(spans, per, exp, tcal, dw):
    out = {}
    for r in per[per["detected"]].itertuples():
        t = r.detected_at.timestamp()
        order = ranker.ranking(rig._spans_between(spans, t - rig.RANK_WINDOW_S, t, causal_cutoff=True),
                               exp["services"], tcal, dw)
        out[r.injection_id] = metrics.rank_of(order, r.target)
    return out


def thin(spans, rate, seed):
    ids = sorted({s["trace_id"] for s in spans})
    rng = np.random.default_rng(seed)
    keep = {i for i, u in zip(ids, rng.random(len(ids))) if u < rate}
    return [s for s in spans if s["trace_id"] in keep]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / os.environ.get("FAULTLAB_CONFIG", "protocols/experiment.yaml")))
    series, windows, spans, campaigns = rig.load_live(Path(a.run))
    res = rig.evaluate(series, spans, campaigns, windows, exp)
    per, fp, cal, tcal = res["per_injection"], res["false_positives"], res["thresholds"], res["trace_calibration"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    det = per[per["detected"]].copy()
    rep: dict = {"n_injections": int(len(per)), "n_detected": int(len(det)), "n_fp_episodes": int(len(fp))}

    # E1 localisation baselines (conditional on n_detected=20 and unconditional end-to-end on n_injections=24)
    det["rank_xray"] = det["rank"].astype(int)
    mr = _metric_ranks(series, windows, per, exp, cal)
    det["rank_metric"] = det["injection_id"].map(mr).astype(int)
    k_cand = len(exp["services"])
    n_all = len(per)
    e1, fam = {}, []
    for name in ("xray", "metric"):
        r = det[f"rank_{name}"]
        hits1, hits3 = int((r <= 1).sum()), int((r <= 3).sum())
        e1[name] = {"top1": float(hits1 / len(r)), "top3": float(hits3 / len(r)), "mean_rank": float(r.mean()),
                    "top1_ci": stats.wilson(hits1, len(r))[1:], "top3_ci": stats.wilson(hits3, len(r))[1:],
                    "end_to_end_top1": float(hits1 / n_all), "end_to_end_top3": float(hits3 / n_all),
                    "end_to_end_top1_ci": stats.wilson(hits1, n_all)[1:],
                    "end_to_end_top3_ci": stats.wilson(hits3, n_all)[1:]}
    e1["random"] = {"top1": 1 / k_cand, "top3": min(1.0, 3 / k_cand), "mean_rank": (k_cand + 1) / 2,
                    "end_to_end_top1": (len(det) / n_all) * (1 / k_cand),
                    "end_to_end_top3": (len(det) / n_all) * min(1.0, 3 / k_cand)}
    for k in (1, 3):
        m = stats.mcnemar(det["rank_xray"] <= k, det["rank_metric"] <= k)
        fam.append({"test": f"xray vs metric top-{k}", **m})
        for name in ("xray", "metric"):
            hits = int((det[f"rank_{name}"] <= k).sum())
            p = float(st.binomtest(hits, len(det), min(1.0, k / k_cand)).pvalue)
            fam.append({"test": f"{name} vs random top-{k}", "rate": hits / len(det), "p": p, "test_kind": "exact binomial"})
    paired_rank = stats.compare_paired(det["rank_xray"], det["rank_metric"], alpha=0.05, B=2000, seed=0)
    fam.append({"test": "xray vs metric rank (paired Wilcoxon)", "mean_xray": paired_rank["mean_x"],
                "mean_metric": paired_rank["mean_y"], "mean_diff": paired_rank["mean_diff"],
                "p": paired_rank["p"], "p_perm": paired_rank["p_perm"], "effect": paired_rank["effect"],
                "test_kind": paired_rank["test"]})
    adj = stats.holm([f["p"] for f in fam])
    for f, q in zip(fam, adj):
        f["p_holm"] = float(q)
    e1["tests"] = fam
    by_fault = det.groupby("fault").agg(n=("rank_xray", "size"), xray_top1=("rank_xray", lambda r: (r <= 1).mean()),
                                        xray_top3=("rank_xray", lambda r: (r <= 3).mean()),
                                        metric_top1=("rank_metric", lambda r: (r <= 1).mean()),
                                        metric_top3=("rank_metric", lambda r: (r <= 3).mean())).reset_index()
    by_target = det.groupby("target").agg(n=("rank_xray", "size"), xray_top1=("rank_xray", lambda r: (r <= 1).mean()),
                                          metric_top1=("rank_metric", lambda r: (r <= 1).mean())).reset_index()
    by_fault.to_csv(out / "e1_by_fault.csv", index=False)
    by_target.to_csv(out / "e1_by_target.csv", index=False)
    rep["E1_localisation_baselines"] = e1
    det.drop(columns=["detected_at"]).to_csv(out / "e1_ranks.csv", index=False)

    # E2 depth-weight sensitivity (post hoc)
    e2 = []
    for dw in (0.0, 0.25, 0.5, 1.0, 2.0):
        r = pd.Series(_ranks(spans, per, exp, tcal, dw))
        e2.append({"depth_weight": dw, "top1": float((r <= 1).mean()), "top3": float((r <= 3).mean()),
                   "mean_rank": float(r.mean())})
    pd.DataFrame(e2).to_csv(out / "e2_depth_weight.csv", index=False)
    rep["E2_depth_weight_post_hoc"] = e2

    # E3 trace thinning
    e3 = []
    dw = exp["localisation"]["depth_weight"]
    for rate in (1.0, 0.5, 0.2, 0.1, 0.05):
        seeds = [0] if rate == 1.0 else range(a.seeds)
        for s in seeds:
            sp = spans if rate == 1.0 else thin(spans, rate, s)
            r = pd.Series(_ranks(sp, per, exp, tcal, dw))
            e3.append({"rate": rate, "seed": s, "top1": float((r <= 1).mean()), "top3": float((r <= 3).mean()),
                       "mean_rank": float(r.mean())})
    e3 = pd.DataFrame(e3)
    e3.to_csv(out / "e3_thinning_raw.csv", index=False)
    g = e3.groupby("rate").agg(seeds=("seed", "size"), top1_mean=("top1", "mean"), top1_sd=("top1", "std"),
                               top1_q05=("top1", lambda x: x.quantile(0.05)), top3_mean=("top3", "mean"),
                               top3_sd=("top3", "std"), mean_rank=("mean_rank", "mean")).reset_index()
    g.to_csv(out / "e3_thinning.csv", index=False)
    rep["E3_trace_thinning"] = g.to_dict(orient="records")

    # E4 steady vs peak
    e4 = {}
    for what, col in (("recall", per["detected"]), ("top3_end_to_end", per["rank"].fillna(99) <= 3)):
        tab = [[int(col[per["load"] == ld].sum()), int((~col[per["load"] == ld]).sum())] for ld in ("steady", "peak")]
        e4[what] = {"table_steady_peak_hit_miss": tab, "p_fisher": float(st.fisher_exact(tab)[1])}
    rep["E4_steady_vs_peak"] = e4

    # E5 F1 bootstrap
    units = np.array([(1, 0, 0) if d else (0, 0, 1) for d in per["detected"]] + [(0, 1, 0)] * len(fp), float)
    rng = np.random.default_rng(0)
    boots = [stats.f1_from(*units[rng.integers(0, len(units), len(units))].T) for _ in range(2000)]
    tp, fn, nfp = int(per["detected"].sum()), int((~per["detected"]).sum()), int(len(fp))
    rep["E5_detection"] = {"tp": tp, "fp": nfp, "fn": fn, "f1": stats.f1_from(tp, nfp, fn),
                           "f1_ci": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
                           "precision_ci": stats.wilson(tp, tp + nfp), "recall_ci": stats.wilson(tp, tp + fn)}
    (out / "extras.json").write_text(json.dumps(rep, indent=2, default=float) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "E3_trace_thinning"}, indent=1, default=float)[:4000])
    print(g.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
