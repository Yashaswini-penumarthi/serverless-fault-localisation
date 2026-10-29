"""Detection and localisation on the AWS rig (or on the simulator) - legs 1 and 3 of the protocol.

    python -m eval.live_campaign_eval --source sim --out outputs/sim --figures figures/sim         # functional check, not AWS data
    python -m eval.live_campaign_eval --source live --run data/runs/live --out outputs/live --figures figures/live

1. Calibrate the rules on the fault-free calibration series and the ranker on
   the calibration traces; both are frozen (the thresholds carry a SHA-256).
2. Run the rules over the control period and each campaign. Fired minutes are
   matched to the schedule: TP / FN / delay per injection, alarm episodes that
   match nothing are false positives (eval/detection_scoring.py).
3. For every detected injection, rank the services from the traces of the
   `rank_window_s` seconds up to the detection time (enforcing causal availability:
   only traces whose spans have all finished by the detection timestamp are eligible)
   and score the target's rank.
4. Precision / recall / F1, delay (median, p95), conditional top-1 / top-3 / mean rank
   and unconditional end-to-end top-1 / top-3, per fault type and load level, with
   Wilson CIs; control false positives per hour.

The live source reads what tools/pull_telemetry.py saved in the run folder:
series.parquet, spans.jsonl.gz, schedule_<load>.jsonl and windows.json.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from detector import ranker, rules  # noqa: E402
from eval import detection_scoring as metrics, nonparametric_tests as stats  # noqa: E402
from injector import schedule as sched  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SIGN = "Yashaswini Penumarthi (24262404)"
LABELS = {"sim": "simulated telemetry - functional check, NOT AWS data", "live": "live AWS measurement"}
RANK_WINDOW_S = 120


def _cut(ser: dict[str, pd.Series], t0: float, t1: float) -> dict[str, pd.Series]:
    lo, hi = pd.Timestamp(t0, unit="s", tz="UTC"), pd.Timestamp(t1, unit="s", tz="UTC")
    return {k: s[(s.index >= lo) & (s.index < hi)] for k, s in ser.items()}


def _spans_between(spans: list[dict], t0: float, t1: float, causal_cutoff: bool = True) -> list[dict]:
    """Select whole traces whose root started in [t0, t1) and (when causal_cutoff=True) completed by t1."""
    roots = {s["trace_id"] for s in spans if s["parent_id"] is None and t0 <= s["start"] < t1}
    if not roots:
        return []
    if causal_cutoff:
        late = {s["trace_id"] for s in spans if s["trace_id"] in roots and s["end"] > t1}
        roots = roots - late
    return [s for s in spans if s["trace_id"] in roots]


def evaluate(series: dict[str, pd.Series], spans: list[dict], campaigns: dict[str, list[dict]], windows: dict,
             exp: dict) -> dict:
    """windows: {"calibration": [t0, t1], "<load>": {"control": [t0, t1], "campaign": [t0, t1]}}"""
    det = exp["detector"]
    c0, c1 = windows["calibration"]
    cal = rules.calibrate(_cut(series, c0, c1), det["rules"], meta={"calibration_utc": [c0, c1]})
    tcal = ranker.calibrate(_spans_between(spans, c0, c1, causal_cutoff=True))
    per_inj, fps_rows = [], []
    for load, schedule in campaigns.items():
        w = windows[load]
        minutes = rules.detect(_cut(series, w["control"][0], w["campaign"][1]), cal)
        eps = rules.episodes(minutes, period_s=det["period_s"])
        inj = pd.DataFrame(schedule)
        inj["start"] = pd.to_datetime(inj["start"], unit="s", utc=True)
        inj["end"] = pd.to_datetime(inj["end"], unit="s", utc=True)
        fired = minutes.loc[minutes["fired"], "time"]
        res, fps = metrics.match(fired, eps, inj, det["match_tolerance_s"], det["period_s"])
        res["load"] = load
        ranks = []
        for r in res.itertuples():
            if not r.detected:
                ranks.append(np.nan)
                continue
            t_det = r.detected_at.timestamp()
            order = ranker.ranking(_spans_between(spans, t_det - RANK_WINDOW_S, t_det, causal_cutoff=True),
                                   exp["services"], tcal, exp["localisation"]["depth_weight"])
            ranks.append(metrics.rank_of(order, r.target))
        res["rank"] = ranks
        per_inj.append(res)
        control_end = pd.Timestamp(w["control"][1], unit="s", tz="UTC")
        for ep in fps:
            fps_rows.append({"load": load, "start": ep["start"], "in_control": ep["start"] < control_end,
                             "services": ",".join(ep["services"])})
    per = pd.concat(per_inj, ignore_index=True)
    fp = pd.DataFrame(fps_rows, columns=["load", "start", "in_control", "services"])
    return {"per_injection": per, "false_positives": fp, "thresholds": cal, "trace_calibration": tcal}


def summarise(per: pd.DataFrame, fp: pd.DataFrame, windows: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for (load, fault), g in [(("all", "all"), per), *[((ld, "all"), g) for ld, g in per.groupby("load")],
                             *[(("all", f), g) for f, g in per.groupby("fault")]]:
        tp, fn = int(g["detected"].sum()), int((~g["detected"]).sum())
        n_fp = int(len(fp)) if (load, fault) == ("all", "all") else int((fp["load"] == load).sum()) \
            if fault == "all" else 0
        r = metrics.prf(tp, n_fp if fault == "all" else 0, fn)
        _, lo, hi = stats.wilson(tp, tp + fn)
        loc = metrics.localisation(g["rank"].dropna().astype(int).tolist())
        rows.append({"load": load, "fault": fault, "injections": len(g), **r, "recall_lo": lo, "recall_hi": hi,
                     **{f"delay_{k}": v for k, v in metrics.delays(g["delay_s"]).items() if k != "n"},
                     "top1": loc["top1"], "top3": loc["top3"], "mean_rank": loc["mean_rank"],
                     "top1_end_to_end": float((g["rank"].fillna(99) <= 1).mean()),
                     "top3_end_to_end": float((g["rank"].fillna(99) <= 3).mean())})
    control = []
    for load, w in windows.items():
        if load == "calibration":
            continue
        hours = (w["control"][1] - w["control"][0]) / 3600
        n = int(((fp["load"] == load) & fp["in_control"]).sum())
        control.append({"load": load, "control_hours": hours, "false_positive_episodes": n, "per_hour": n / hours})
    return pd.DataFrame(rows), pd.DataFrame(control)


def md(df: pd.DataFrame) -> str:
    f = lambda v: ("" if np.isnan(v) else f"{v:.3g}") if isinstance(v, float) else str(v)  # noqa: E731
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(f(v) for v in r) + " |" for r in df.itertuples(index=False)])


def figures(per: pd.DataFrame, out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    faults = sorted(per["fault"].unique())
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    data = [per.loc[(per["fault"] == f) & per["detected"], "delay_s"].to_numpy() for f in faults]
    axes[0].boxplot([d if len(d) else [np.nan] for d in data], showfliers=False)
    axes[0].set_xticks(range(1, len(faults) + 1), [f.replace("_", "\n") for f in faults], fontsize=8)
    axes[0].set_ylabel("detection delay (s)")
    rates = [(per.loc[per["fault"] == f, "detected"].mean(), (per.loc[per["fault"] == f, "rank"] <= 3).mean())
             for f in faults]
    x = np.arange(len(faults))
    axes[1].bar(x - 0.2, [r[0] for r in rates], 0.4, label="recall")
    axes[1].bar(x + 0.2, [r[1] for r in rates], 0.4, label="top-3 (end to end)")
    axes[1].set_xticks(x, [f.replace("_", "\n") for f in faults], fontsize=8)
    axes[1].set_ylim(0, 1.05)
    axes[1].legend(frameon=False, fontsize=8)
    fig.suptitle(f"Rule arm on the rig  [{label}]", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "rig_detection_localisation.png", dpi=150)
    plt.close(fig)


def write(res: dict, windows: dict, out: Path, figs: Path, source: str, ceiling_f1: float = 0.933) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    table, control = summarise(res["per_injection"], res["false_positives"], windows)
    res["per_injection"].to_csv(out / "per_injection.csv", index=False)
    res["false_positives"].to_csv(out / "false_positives.csv", index=False)
    table.to_csv(out / "rig_summary.csv", index=False)
    control.to_csv(out / "control_false_positives.csv", index=False)
    (out / "thresholds.json").write_text(json.dumps(res["thresholds"], indent=2) + "\n")
    (out / "trace_calibration.json").write_text(json.dumps(res["trace_calibration"], indent=2) + "\n")
    figures(res["per_injection"], figs, LABELS[source])
    f1 = float(table.iloc[0]["f1"])
    ceiling = ("Xing et al. (2025) report detection F1 0.933 in their table (0.938 in their abstract; 0.938 precision) "
               "with labelled training data on their own benchmark. ")
    if source == "live":
        ceiling += (f"The rule arm's F1 here is {f1:.3f}; the {100 * (ceiling_f1 - f1):.1f} pp gap (vs table F1 0.933; "
                    f"{100 * (0.938 - f1):.1f} pp vs abstract 0.938) is a descriptive positioning against that "
                    "published reference, not a same-rig comparison.")
    else:
        # made-up telemetry says nothing about where the rule arm sits against a published result
        ceiling += "No positioning is computed here: this source is not AWS data."
    text = ["# Rig results - rule arm (detection + localisation)", "", "Generated by `eval/live_campaign_eval.py`.", "",
            f"- Source: **{LABELS[source]}**",
            f"- Thresholds frozen after calibration: sha256 `{res['thresholds']['sha256'][:16]}...`", "",
            "## Detection and localisation", "", md(table), "",
            "## False positives in the fault-free control periods", "", md(control), "",
            "## Leg 1 - published ceiling (citation only, not same-rig)", "", ceiling, "", SIGN, ""]
    (out / "summary.md").write_text("\n".join(text))
    return {"table": table, "control": control}


def run_sim(exp: dict, per_type: int, calibration_hours: float, seed: int = 0) -> tuple[dict, dict, list, dict]:
    from sim import telemetry

    e = json.loads(json.dumps(exp))
    e["injection"]["per_type_per_load"] = per_type
    t0 = 1_767_225_600.0  # 2026-01-01T00:00:00Z, arbitrary
    windows = {"calibration": [t0, t0 + calibration_hours * 3600]}
    campaigns = {}
    start = windows["calibration"][1]
    for load in ("steady", "peak"):
        s = sched.build(e, load, start)
        campaigns[load] = s
        windows[load] = {"control": list(sched.control_window(e, start)), "campaign": [s[0]["start"], s[-1]["end"] + 300]}
        start = s[-1]["end"] + e["injection"]["recovery_s"]
    all_inj = [i for s in campaigns.values() for i in s]
    series = telemetry.series(all_inj, t0, start, seed)
    spans = telemetry.traces(all_inj, t0, start, seed=seed + 1)
    return series, windows, spans, campaigns


def load_live(run: Path) -> tuple[dict, dict, list, dict]:
    df = pd.read_parquet(run / "series.parquet")
    series = {c: df[c] for c in df.columns}
    with gzip.open(run / "spans.jsonl.gz", "rt") as fh:
        spans = [json.loads(line) for line in fh]
    windows = json.loads((run / "windows.json").read_text())
    campaigns = {load: sched.read(run / f"schedule_{load}.jsonl") for load in windows if load != "calibration"}
    return series, windows, spans, campaigns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["sim", "live"], default="sim")
    ap.add_argument("--run", help="live run folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures", required=True)
    ap.add_argument("--per-type", type=int, default=6, help="sim only: injections per fault type per load")
    ap.add_argument("--calibration-hours", type=float, default=6, help="sim only")
    args = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / __import__("os").environ.get("FAULTLAB_CONFIG", "protocols/experiment.yaml")))
    if args.source == "sim":
        series, windows, spans, campaigns = run_sim(exp, args.per_type, args.calibration_hours)
    else:
        series, windows, spans, campaigns = load_live(Path(args.run))
    res = evaluate(series, spans, campaigns, windows, exp)
    out = write(res, windows, Path(args.out), Path(args.figures), args.source)
    print(out["table"][["load", "fault", "injections", "precision", "recall", "f1", "top1", "top3", "top3_end_to_end"]].to_string(index=False))
    print("->", Path(args.out) / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
