"""Per-minute CloudWatch series for the rule detector (GetMetricData, 60 s period).

Per function: Errors, Throttles, Invocations (Sum) and Duration (Average), turned into

    <service>/ErrorRate     Errors / Invocations
    <service>/ThrottleRate  Throttles / (Invocations + Throttles)
    <service>/Duration      average ms

Rates keep the rules comparable between steady and peak load. A minute without
invocations gives rate 0 and no Duration value (the rules skip missing minutes).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

STATS = {"Errors": "Sum", "Throttles": "Sum", "Invocations": "Sum", "Duration": "Average"}


def queries(functions: dict[str, str], period_s: int = 60) -> list[dict]:
    """functions: service -> function name."""
    out = []
    for svc, fn in sorted(functions.items()):
        for metric, stat in STATS.items():
            out.append({"Id": f"{metric.lower()}_{svc}", "ReturnData": True, "MetricStat": {
                "Metric": {"Namespace": "AWS/Lambda", "MetricName": metric,
                           "Dimensions": [{"Name": "FunctionName", "Value": fn}]},
                "Period": period_s, "Stat": stat}})
    return out


def fetch_raw(cw, functions: dict[str, str], start: dt.datetime, end: dt.datetime, period_s: int = 60) -> dict:
    raw: dict[str, dict] = {}
    q = queries(functions, period_s)
    for i in range(0, len(q), 500):
        kw = {"MetricDataQueries": q[i:i + 500], "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
        while True:
            resp = cw.get_metric_data(**kw)
            for r in resp["MetricDataResults"]:
                raw.setdefault(r["Id"], {}).update(zip(r["Timestamps"], r["Values"], strict=True))
            if not resp.get("NextToken"):
                break
            kw["NextToken"] = resp["NextToken"]
    return raw


def to_series(raw: dict, services: list[str], index: pd.DatetimeIndex) -> dict[str, pd.Series]:
    def s(metric, svc):
        d = raw.get(f"{metric.lower()}_{svc}", {})
        return pd.Series({pd.Timestamp(k).tz_convert("UTC") if pd.Timestamp(k).tzinfo else pd.Timestamp(k, tz="UTC"): v
                          for k, v in d.items()}, dtype=float).reindex(index)

    out = {}
    for svc in services:
        inv, err, thr, dur = (s(m, svc) for m in ("Invocations", "Errors", "Throttles", "Duration"))
        inv0, err0, thr0 = inv.fillna(0), err.fillna(0), thr.fillna(0)
        out[f"{svc}/ErrorRate"] = (err0 / inv0.where(inv0 > 0)).fillna(0.0)
        out[f"{svc}/ThrottleRate"] = (thr0 / (inv0 + thr0).where((inv0 + thr0) > 0)).fillna(0.0)
        out[f"{svc}/Duration"] = dur
    return out


def fetch(cw, functions: dict[str, str], start: dt.datetime, end: dt.datetime, period_s: int = 60) -> dict[str, pd.Series]:
    index = pd.date_range(pd.Timestamp(start).floor("min"), pd.Timestamp(end).floor("min"), freq=f"{period_s}s",
                          inclusive="left", tz="UTC")
    return to_series(fetch_raw(cw, functions, start, end, period_s), sorted(functions), index)
