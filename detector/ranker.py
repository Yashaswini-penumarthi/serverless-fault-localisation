"""X-Ray dependency ranking. Deterministic, no training.

For the traces that overlap a detection window:

failed trace  a span in it faulted, erred or was throttled, its root span took longer
              than the root p99 of the calibration period, or (when async_leaf_slow is
              enabled, as on AWS Lambda where notifications is invoked asynchronously
              with InvocationType='Event' and does not contribute to root duration) one
              of its spans spent longer on its own work than that service's p99. On
              synchronous request-tree benchmarks (RCAEval RE2-OB), async_leaf_slow=False
              avoids treating normal entry-SLI traces with 1-in-100 leaf jitter as failed.
implicated    in a failed trace, a service is implicated when one of its spans
                (a) carries a fault flag while none of its children does - the
                    fault starts there instead of passing through it - or
                (b) spends longer on its own work (self time: duration minus the
                    time covered by its children) than that service's p99
score(s)      share of failed traces that implicate s
              + depth_weight x (mean depth of the implicated spans of s / deepest span)
              A service that is never implicated scores 0.

The depth term is how "close to the symptom" is read here: a fault shows up at
the entry service but starts at the deepest implicated span, so between two
equally implicated services the deeper one is ranked first. Ties break on the
name. Every candidate gets a rank, so the mean rank is always defined.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np


def _index(spans: list[dict]) -> dict[str, dict]:
    """trace id -> {"spans": {span id: span}, "children": {span id: [child spans]}, "roots": [...]}"""
    out: dict[str, dict] = defaultdict(lambda: {"spans": {}, "children": defaultdict(list), "roots": []})
    for s in spans:
        out[s["trace_id"]]["spans"][s["span_id"]] = s
    for tr in out.values():
        for s in tr["spans"].values():
            if s["parent_id"] in tr["spans"]:
                tr["children"][s["parent_id"]].append(s)
            else:
                tr["roots"].append(s)
    return out


def _covered(span: dict, children: list[dict]) -> float:
    """Time of span covered by its children (union of their intervals, clipped to the span)."""
    iv = sorted((max(c["start"], span["start"]), min(c["end"], span["end"])) for c in children)
    total, cur_s, cur_e = 0.0, None, None
    for s, e in iv:
        if e <= s:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def annotate(spans: list[dict]) -> list[dict]:
    """Adds depth, self_time and fault_origin to every span (returns new dicts)."""
    if spans and "self_time" in spans[0] and "depth" in spans[0] and "fault_origin" in spans[0]:
        return spans
    out = []
    for tr in _index(spans).values():
        stack = [(r, 0) for r in tr["roots"]]
        while stack:
            s, depth = stack.pop()
            kids = tr["children"].get(s["span_id"], [])
            out.append({**s, "depth": depth, "is_root": depth == 0,
                        "self_time": max(0.0, (s["end"] - s["start"]) - _covered(s, kids)),
                        "fault_origin": bool(s["fault"] or s["throttle"])
                        and not any(k["fault"] or k["throttle"] for k in kids)})
            stack.extend((k, depth + 1) for k in kids)
    return out


def calibrate(spans: list[dict], percentile: float = 99) -> dict:
    """Frozen per-service self-time p99 and root-duration p99 from fault-free traces."""
    ann = annotate(spans)
    by_service: dict[str, list[float]] = defaultdict(list)
    roots = []
    for s in ann:
        by_service[s["service"]].append(s["self_time"])
        if s["is_root"]:
            roots.append(s["end"] - s["start"])
    return {"percentile": percentile,
            "self_time": {k: round(float(np.percentile(v, percentile)), 12) for k, v in sorted(by_service.items())},
            "root_duration": round(float(np.percentile(roots, percentile)), 12) if roots else float("inf")}


def rank(spans: list[dict], candidates: list[str], cal: dict, depth_weight: float = 0.5,
         async_leaf_slow: bool = True) -> list[dict]:
    ann = annotate(spans)
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for s in ann:
        by_trace[s["trace_id"]].append(s)
    max_depth = max((s["depth"] for s in ann), default=0) or 1
    hits: dict[str, int] = defaultdict(int)
    depths: dict[str, list[int]] = defaultdict(list)
    n_failed = 0
    for tr in by_trace.values():
        slow = {s["span_id"]: s["self_time"] > cal["self_time"].get(s["service"], float("inf")) + 1e-9 for s in tr}
        slow_root = any(s["is_root"] and (s["end"] - s["start"]) > cal["root_duration"] + 1e-9 for s in tr)
        leaf_trigger = any(slow.values()) if async_leaf_slow else False
        if not (slow_root or leaf_trigger or any(s["fault"] or s["throttle"] for s in tr)):
            continue
        n_failed += 1
        implicated = set()
        for s in tr:
            if s["fault_origin"] or slow[s["span_id"]]:
                implicated.add(s["service"])
                depths[s["service"]].append(s["depth"])
        for svc in implicated:
            hits[svc] += 1
    rows = []
    for svc in candidates:
        share = hits[svc] / n_failed if n_failed else 0.0
        depth = float(np.mean(depths[svc])) / max_depth if depths[svc] else 0.0
        rows.append({"service": svc, "score": share + depth_weight * depth if share else 0.0,
                     "implicated_share": share, "depth_norm": depth, "failed_traces": n_failed})
    return sorted(rows, key=lambda r: (-r["score"], r["service"]))


def ranking(spans: list[dict], candidates: list[str], cal: dict, depth_weight: float = 0.5,
            async_leaf_slow: bool = True) -> list[str]:
    return [r["service"] for r in rank(spans, candidates, cal, depth_weight, async_leaf_slow=async_leaf_slow)]
