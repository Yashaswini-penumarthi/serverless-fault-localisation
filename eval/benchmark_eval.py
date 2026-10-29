"""Leg 2: the rule arm against RCAEval baselines on identical benchmark cases.

    python -m eval.benchmark_eval --raw outputs/rcaeval/raw --out outputs/rcaeval --figures figures/rcaeval

Reads raw/rules/*.json (baseline_runner/rule_arm.py) and raw/<method>/*.json
(baseline_runner/run_baselines.py). The rule arm's cases define the case set;
each baseline is compared with the rule arm on the cases both have (the slow deep
baseline may run on a documented subset), and a separate table puts every method
on the cases all of them share.

Localisation: AC@1, AC@3, Avg@5 (RCAEval's definitions) and mean rank per method,
overall and per fault type, with Wilson CIs. Rule arm vs each baseline on the
same cases: McNemar's exact test for top-1 and top-3, and the paired case-level
rank comparison (Wilcoxon signed-rank + paired permutation, preserving case pairing),
with Holm-Bonferroni over the whole family.
A method that failed on a case gets a miss (rank = candidates + 1).

Detection: rule arm only. RCAEval's baselines are handed the injection time and
do not detect, so detection F1 has no like-for-like baseline here; the rule
arm's precision / recall / F1 / delay come from each case's control window
(false positives) and fault window.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from eval import detection_scoring as metrics, nonparametric_tests as stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SIGN = "Yashaswini Penumarthi (24262404)"
LABEL = "RCAEval benchmark telemetry, offline run (public data - not AWS)"


def load(raw: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for d in sorted(p for p in Path(raw).iterdir() if p.is_dir()):
        rows = [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))]
        if rows:
            out[d.name] = pd.DataFrame(rows).set_index("case")
    return out


def ranks_table(runs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per rule-arm case, one rank column per method (NaN where a method has no result)."""
    rules = runs["rules"]
    n_cand = rules["ranking"].map(len)
    tab = rules[["root_cause", "fault"]].copy()
    for m, df in runs.items():
        col = []
        for c in tab.index:
            if c not in df.index:
                col.append(np.nan)
            elif "error" in df and isinstance(df.loc[c, "error"], str):
                col.append(n_cand[c] + 1)  # failed = miss
            else:
                col.append(metrics.rank_of(list(df.loc[c, "ranking"]), tab.loc[c, "root_cause"]))
        tab[m] = col
    return tab.sort_index()


def localisation_table(tab: pd.DataFrame, methods: list[str], by: str | None = None) -> pd.DataFrame:
    groups = [("all", tab)] if by is None else list(tab.groupby(by))
    rows = []
    for g, sub in groups:
        for m in methods:
            r = sub[m].dropna().to_numpy()
            n = len(r)
            if n == 0:
                continue
            row = {"group": g, "method": m, "cases": n, "avg@5": metrics.avg_at_k(r, 5), "mean_rank": float(r.mean())}
            for k in (1, 3):
                p, lo, hi = stats.wilson(int((r <= k).sum()), n)
                row |= {f"ac@{k}": p, f"ac@{k}_lo": lo, f"ac@{k}_hi": hi}
            rows.append(row)
    return pd.DataFrame(rows)


def comparisons(tab: pd.DataFrame, baselines: list[str], alpha: float, B: int, seed: int) -> pd.DataFrame:
    rows = []
    for b in baselines:
        sub = tab[tab[b].notna()]
        for k in (1, 3):
            r = stats.mcnemar(sub["rules"] <= k, sub[b] <= k)
            rows.append({"baseline": b, "cases": len(sub), "outcome": f"top-{k}",
                         **{x: r[x] for x in ("rate_a", "rate_b", "diff", "p", "test")},
                         "mean_diff": r["diff"], "effect": r["diff"], "p_perm": r["p"]})
        c = stats.compare_paired(sub["rules"], sub[b], alpha=alpha, B=B, seed=seed)
        rows.append({"baseline": b, "cases": len(sub), "outcome": "rank", "rate_a": c["mean_x"],
                     "rate_b": c["mean_y"], "diff": c["mean_diff"], "p": c["p"], "test": c["test"],
                     "mean_diff": c["mean_diff"], "effect": c["effect"], "p_perm": c["p_perm"]})
    df = pd.DataFrame(rows)
    df["p_holm"] = stats.holm(df["p"])
    df["reject_h0"] = df["p_holm"] < alpha
    return df


def fixed_order(runs: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Share of each method's (non-failed) cases that got that method's most common ranking.

    A method that hands back the same list on most cases is ranking by the order
    of its input columns, not by the data - RCAEval's CausalRCA does this when it
    keeps no edge above its 0.3 cut-off. Its AC@k then only says where each root
    cause happens to sit in that fixed list, so it is flagged in the summary and
    cannot be picked as the strongest baseline.
    """
    out = {}
    for m, df in runs.items():
        ok = df[~df["error"].map(lambda e: isinstance(e, str))] if "error" in df else df
        r = ok["ranking"].map(tuple)
        out[m] = float(r.value_counts().iloc[0] / len(r)) if len(r) > 1 else 0.0
    return out


def top3_expectation(tab: pd.DataFrame, baselines: list[str], concession: float, B: int, seed: int) -> dict:
    """Pre-committed: the rule arm concedes MORE than `concession` in top-3 vs the strongest baseline."""
    best = max(baselines, key=lambda b: ((tab[b].dropna() <= 3).mean(), b))
    sub = tab[tab[best].notna()]
    a, b = (sub[best] <= 3).to_numpy(float), (sub["rules"] <= 3).to_numpy(float)
    idx = np.random.default_rng(seed).integers(0, len(a), (B, len(a)))
    boot = a[idx].mean(axis=1) - b[idx].mean(axis=1)
    lo, hi = float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))
    gap = float(a.mean() - b.mean())
    decision = "supported" if lo > concession else "refuted" if hi < concession else "inconclusive"
    return {"expectation": f"rule arm concedes > {concession * 100:.0f} pp in top-3", "strongest_baseline": best,
            "cases": len(sub), "gap": gap, "ci_lo": lo, "ci_hi": hi, "decision": decision}


def detection(rules: pd.DataFrame) -> dict:
    tp = int(rules["detected"].sum())
    fn = int((~rules["detected"]).sum())
    fp = int(rules["control_fp_episodes"].sum())
    d = metrics.delays(pd.to_numeric(rules["delay_s"], errors="coerce"))
    return {**metrics.prf(tp, fp, fn), "cases_with_control_fp": int((rules["control_fp_episodes"] > 0).sum()),
            "cases": len(rules), **{f"delay_{k}": v for k, v in d.items()}}


def md(df: pd.DataFrame) -> str:
    def f(v):
        if isinstance(v, float):
            return "" if np.isnan(v) else f"{v:.3g}"
        return str(v)
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(f(v) for v in r) + " |" for r in df.itertuples(index=False)])


def figure(loc: pd.DataFrame, path: Path, dataset: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = np.arange(len(loc))
    for i, k in enumerate((1, 3)):
        v = loc[f"ac@{k}"].to_numpy()
        err = [np.clip(v - loc[f"ac@{k}_lo"], 0, None), np.clip(loc[f"ac@{k}_hi"] - v, 0, None)]
        ax.bar(x + (i - 0.5) * 0.38, v, 0.38, yerr=err, capsize=3, label=f"AC@{k}")
    ax.set_xticks(x, [f"{m}\n(n={n})" for m, n in zip(loc["method"], loc["cases"], strict=True)], fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("share of cases")
    ax.set_title(f"Root-cause localisation on {dataset}, 95 % Wilson CI\n[{LABEL}]", fontsize=9)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def analyse(raw, out, figures, dataset: str = "RE2-OB", alpha=0.05, B=2000, seed=0, concession=0.10) -> dict:
    out, figures = Path(out), Path(figures)
    out.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    runs = load(Path(raw))
    if "rules" not in runs:
        raise FileNotFoundError(f"{raw}/rules has no results")
    baselines = [m for m in runs if m != "rules"]
    methods = ["rules", *baselines]
    tab = ranks_table(runs)
    common = tab.dropna(subset=methods)
    loc = localisation_table(tab, methods)
    loc_common = localisation_table(common, methods)
    loc_fault = localisation_table(tab, methods, by="fault")
    cmp_ = comparisons(tab, baselines, alpha, B, seed) if baselines else pd.DataFrame()
    fixed = fixed_order(runs)
    flagged = [m for m in baselines if fixed[m] > 0.5]
    eligible = [b for b in baselines if b not in flagged] or baselines
    exp3 = top3_expectation(tab, eligible, concession, B, seed) if baselines else {}
    det = detection(runs["rules"].loc[tab.index])
    failures = {m: int(df["error"].map(lambda e: isinstance(e, str)).sum()) if "error" in df else 0
                for m, df in runs.items()}
    seconds = {m: float(pd.to_numeric(df.get("seconds", df.get("rank_seconds")), errors="coerce").mean())
               for m, df in runs.items()}
    tab.to_csv(out / "ranks.csv")
    loc.to_csv(out / "localisation.csv", index=False)
    loc_common.to_csv(out / "localisation_common_cases.csv", index=False)
    loc_fault.to_csv(out / "localisation_by_fault.csv", index=False)
    cmp_.to_csv(out / "comparisons.csv", index=False)
    (out / "detection.json").write_text(json.dumps(det, indent=2) + "\n")
    (out / "top3_expectation.json").write_text(json.dumps(exp3, indent=2) + "\n")
    (out / "fixed_order.json").write_text(json.dumps({"share_of_cases_with_modal_ranking": fixed,
                                                      "flagged": flagged}, indent=2) + "\n")
    figure(loc, figures / "localisation.png", dataset)
    cols = ["group", "method", "cases", "ac@1", "ac@3", "avg@5", "mean_rank"]
    sanity = pd.DataFrame([{"method": m, "share_of_cases_with_its_most_common_ranking": v, "flagged": m in flagged}
                           for m, v in fixed.items()])
    text = [f"# Leg 2 - rule arm vs RCAEval baselines ({dataset})", "", "Generated by `eval/benchmark_eval.py`.", "",
            f"- Source: **{LABEL}**",
            f"- Cases: {len(tab)} for the rule arm; " + ", ".join(f"{m} {int(tab[m].notna().sum())}" for m in baselines),
            f"- Cases every method has: {len(common)}",
            f"- Failed cases per method: {failures}",
            "- Mean seconds per case: " + ", ".join(f"{m} {v:.2f}" for m, v in seconds.items() if not np.isnan(v)), "",
            "## Sanity check - the same ranking on most cases", "", md(sanity), "",
            ("Flagged methods returned one fixed list (their input column order) on most cases, so their AC@k "
             "only shows where each root cause happens to sit in that list. They stay in the tables but are not "
             "used as the strongest baseline." if flagged else "No method returned one fixed list on most cases."), "",
            "## Localisation (service level, each method on its own cases)", "", md(loc), "",
            f"## Localisation on the {len(common)} cases every method has", "", md(loc_common[cols]) if len(loc_common) else "-", "",
            "## By fault type", "", md(loc_fault[cols]), "",
            f"## Rule arm vs each baseline on shared cases ({len(cmp_)} tests, Holm)", "", md(cmp_) if len(cmp_) else "-", "",
            "## Pre-committed expectation (top-3)", "", md(pd.DataFrame([exp3])) if exp3 else "-", "",
            "## Detection (rule arm; RCAEval baselines take the injection time as input and do not detect)", "",
            md(pd.DataFrame([det])), "", SIGN, ""]
    (out / "summary.md").write_text("\n".join(text))
    return {"ranks": tab, "localisation": loc, "common": loc_common, "comparisons": cmp_, "detection": det, "top3": exp3,
            "fixed": fixed, "flagged": flagged}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="outputs/rcaeval/raw")
    ap.add_argument("--out", default="outputs/rcaeval")
    ap.add_argument("--figures", default="figures/rcaeval")
    args = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / "protocols/experiment.yaml"))
    res = analyse(args.raw, args.out, args.figures, exp["rcaeval"]["dataset"], exp["stats"]["alpha"],
                  exp["stats"]["bootstrap"], 0, exp["expectations"]["top3_concession_pp"] / 100)
    print(res["localisation"][["method", "cases", "ac@1", "ac@3", "avg@5", "mean_rank"]].to_string(index=False))
    print("->", Path(args.out) / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
