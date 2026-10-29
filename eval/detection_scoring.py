"""Scoring detections and rankings against the injector's ground truth.

Detection. An injection counts as detected (TP) when any fired minute becomes
known - the end of that minute - inside [start, end + tolerance]. The delay is the
first such time minus the injection start. An alarm episode that holds no fired
minute inside any injection window is a false positive. Injections nobody caught
are false negatives.

Localisation. The rank of the true root-cause service in a ranking (1 = first);
a ranking that leaves the service out gets len(ranking) + 1. top-k = rank <= k.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * p * r / (p + r) if (tp + fp) and (tp + fn) and (p + r) else 0.0 if tp == 0 else float("nan")
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def match(fired_minutes, episodes: list[dict], injections: pd.DataFrame, tolerance_s: float,
          period_s: int = 60) -> tuple[pd.DataFrame, list[dict]]:
    """fired_minutes: start times of fired minutes; injections: id, fault, target, start, end."""
    step, tol = pd.Timedelta(seconds=period_s), pd.Timedelta(seconds=tolerance_s)
    known = sorted(utc(t) + step for t in fired_minutes)
    windows = [(utc(i.start), utc(i.end) + tol) for i in injections.itertuples()]
    rows = []
    for inj, (lo, hi) in zip(injections.itertuples(), windows, strict=True):
        first = next((t for t in known if lo <= t <= hi), None)
        rows.append({"injection_id": inj.id, "fault": inj.fault, "target": inj.target, "detected": first is not None,
                     "detected_at": first, "delay_s": (first - lo).total_seconds() if first is not None else np.nan})
    fps = []
    for ep in episodes:
        ends = pd.date_range(utc(ep["start"]) + step, utc(ep["end"]), freq=step)  # when each fired minute became known
        if not any(lo <= t <= hi for t in ends for lo, hi in windows):
            fps.append(ep)
    return pd.DataFrame(rows), fps


def rank_of(ranking: list[str], truth: str) -> int:
    return ranking.index(truth) + 1 if truth in ranking else len(ranking) + 1


def localisation(ranks: list[int], ks=(1, 3)) -> dict:
    r = np.asarray(ranks, float)
    out = {f"top{k}": float((r <= k).mean()) if len(r) else float("nan") for k in ks}
    out["mean_rank"] = float(r.mean()) if len(r) else float("nan")
    out["n"] = int(len(r))
    return out


def avg_at_k(ranks: list[int], k: int = 5) -> float:
    """RCAEval's Avg@k: mean of AC@1..AC@k."""
    r = np.asarray(ranks, float)
    return float(np.mean([(r <= j).mean() for j in range(1, k + 1)])) if len(r) else float("nan")


def delays(delay_s: pd.Series) -> dict:
    d = pd.Series(delay_s).dropna()
    return {"n": int(len(d)), "median_s": float(d.median()) if len(d) else float("nan"),
            "p95_s": float(d.quantile(0.95)) if len(d) else float("nan")}
