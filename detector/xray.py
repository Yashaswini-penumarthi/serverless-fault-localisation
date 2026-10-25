"""X-Ray traces overlapping a time window, as flat spans (detector/spans.py).

GetTraceSummaries finds the trace ids (in chunks of at most 6 hours, the API's
limit per call), BatchGetTraces fetches them 5 at a time. Retrieved and scanned
traces are billed separately from recorded ones - the overhead leg counts them.
"""
from __future__ import annotations

import datetime as dt

from detector import spans

CHUNK = dt.timedelta(hours=6)


def trace_ids(xr, start: dt.datetime, end: dt.datetime, filter_expression: str | None = None) -> list[str]:
    ids: list[str] = []
    t = start
    while t < end:
        stop = min(t + CHUNK, end)
        kw = {"StartTime": t, "EndTime": stop, "Sampling": False}
        if filter_expression:
            kw["FilterExpression"] = filter_expression
        while True:
            resp = xr.get_trace_summaries(**kw)
            ids += [s["Id"] for s in resp.get("TraceSummaries", [])]
            if not resp.get("NextToken"):
                break
            kw["NextToken"] = resp["NextToken"]
        t = stop
    return list(dict.fromkeys(ids))


def fetch_traces(xr, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 5):
        kw = {"TraceIds": ids[i:i + 5]}
        while True:
            resp = xr.batch_get_traces(**kw)
            out += resp.get("Traces", [])
            if not resp.get("NextToken"):
                break
            kw["NextToken"] = resp["NextToken"]
    return out


def window_spans(xr, start: dt.datetime, end: dt.datetime, names: dict[str, str],
                 filter_expression: str | None = None) -> list[dict]:
    ids = trace_ids(xr, start, end, filter_expression)
    return [s for tr in fetch_traces(xr, ids) for s in spans.xray_spans(tr, names)]
