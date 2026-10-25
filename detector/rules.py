"""Rule-based detection over per-minute CloudWatch series.

A series is one metric of one service ("inventory/ErrorRate"), one value per
60-second period. A minute fires when

  * its value departs from the rolling baseline of the preceding 30 minutes by
    more than 3 standard deviations (either direction), or
  * its value is above the fixed threshold, the 99th percentile of that series
    over the fault-free calibration period,

whichever comes first - both are checked every minute. Minutes that fired do not
enter later baselines, so a fault does not raise its own bar.

Thresholds come from calibrate() once and are then frozen: the config carries a
SHA-256 of its content and detect() refuses a config that was edited afterwards
(no retuning on evaluation data).
"""
from __future__ import annotations

import hashlib
import json
from collections import deque

import numpy as np
import pandas as pd

RULE_KEYS = ("window_min", "sigma", "percentile", "min_baseline_points", "min_std")


def _canon(cfg: dict) -> str:
    return json.dumps({k: v for k, v in cfg.items() if k != "sha256"}, sort_keys=True)


def calibrate(series: dict[str, pd.Series], rules: dict, meta: dict | None = None) -> dict:
    """series maps "service/metric" to a fault-free Series indexed by minute timestamps."""
    stats = {}
    for key, s in sorted(series.items()):
        v = np.round(s.dropna().to_numpy(float), 12)
        if len(v) < 2:
            raise ValueError(f"{key}: not enough calibration data")
        mean = round(float(v.mean()), 12)
        std = round(float(v.std(ddof=1)), 12)
        p = round(float(np.percentile(v, rules["percentile"])), 12)
        # the same floor as for the sd: on a (nearly) flat series the p99 is the flat value itself,
        # and one quantisation step above it would count as a fault
        thr = round(max(p, mean + _floor(key, rules)), 12)
        stats[key] = {"n": int(len(v)), "mean": mean, "std": std, "p": p, "threshold": thr}
    cfg = {"rules": {k: rules[k] for k in RULE_KEYS}, "series": stats, **(meta or {})}
    cfg["sha256"] = hashlib.sha256(_canon(cfg).encode()).hexdigest()
    return cfg


def check_frozen(cfg: dict) -> None:
    if hashlib.sha256(_canon(cfg).encode()).hexdigest() != cfg.get("sha256"):
        raise ValueError("threshold config changed after calibration - thresholds are frozen")


def _floor(key: str, rules: dict) -> float:
    return float(rules["min_std"].get(key.rsplit("/", 1)[-1], 1e-9))


def detect_series(key: str, s: pd.Series, cfg: dict) -> pd.DataFrame:
    rules, cal = cfg["rules"], cfg["series"][key]
    window = pd.Timedelta(minutes=rules["window_min"])
    floor = _floor(key, rules)
    hist: deque[tuple[pd.Timestamp, float]] = deque()  # non-firing minutes, newest last
    rows = []
    for t, x in s.items():
        if pd.isna(x):
            continue
        while hist and hist[0][0] < t - window:
            hist.popleft()
        if len(hist) >= rules["min_baseline_points"]:
            vals = np.array([v for _, v in hist])
            mu, sd = float(vals.mean()), float(vals.std(ddof=1))
        else:
            mu, sd = cal["mean"], cal["std"]
        sd = max(sd, floor)
        z = (x - mu) / sd
        why = [r for r, hit in (("sigma", abs(z) > rules["sigma"]), ("threshold", x > cal["threshold"])) if hit]
        if not why:
            hist.append((t, float(x)))
        rows.append({"time": t, "series": key, "value": float(x), "baseline": mu, "z": z,
                     "fired": bool(why), "rule": "+".join(why)})
    return pd.DataFrame(rows, columns=["time", "series", "value", "baseline", "z", "fired", "rule"])


def detect(series: dict[str, pd.Series], cfg: dict) -> pd.DataFrame:
    """Every minute of every series with its z-score and whether it fired."""
    check_frozen(cfg)
    missing = sorted(set(series) - set(cfg["series"]))
    if missing:
        raise KeyError(f"no calibration for {missing}")
    parts = [detect_series(k, s, cfg) for k, s in sorted(series.items())]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def episodes(minutes: pd.DataFrame, period_s: int = 60, gap_min: int = 1) -> list[dict]:
    """Group fired minutes (any series) into alarm episodes.

    A detection is only known once its minute is over, so the detection time of
    an episode is the end of its first fired minute.
    """
    fired = minutes[minutes["fired"]].sort_values("time")
    out: list[dict] = []
    for t, g in fired.groupby("time", sort=True):
        t_end = t + pd.Timedelta(seconds=period_s)
        if out and t - out[-1]["last"] <= pd.Timedelta(minutes=gap_min):
            ep = out[-1]
        else:
            ep = {"start": t, "detected_at": t_end, "last": t, "series": set(), "first_series": sorted(g["series"])}
            out.append(ep)
        ep["last"] = t
        ep["end"] = t_end
        ep["series"].update(g["series"])
    for ep in out:
        ep["series"] = sorted(ep["series"])
        ep["services"] = sorted({s.split("/")[0] for s in ep["series"]})
    return out


def metric_ranking(minutes: pd.DataFrame, start, end) -> list[str]:
    """Metric-only localisation (used where no traces exist): services by their largest |z| in [start, end)."""
    w = minutes[(minutes["time"] >= start) & (minutes["time"] < end)]
    if w.empty:
        return []
    score = w.assign(service=w["series"].str.split("/").str[0], az=w["z"].abs()).groupby("service")["az"].max()
    return [s for s, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))]
