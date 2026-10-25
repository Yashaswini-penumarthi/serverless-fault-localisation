"""Turn trace data into one flat span format the ranker understands.

span = {trace_id, span_id, parent_id, service, start, end, fault, throttle}
(times in seconds; fault = error or fault flag; parent_id None for a root)

Two sources:

* X-Ray (BatchGetTraces). With active tracing every Lambda invocation shows up as
  an "AWS::Lambda" segment (the platform's view) and an "AWS::Lambda::Function"
  segment (the function's own work) whose parent is the first. The caller's boto3
  Invoke call is a subsegment, and the callee's AWS::Lambda segment points to it.
  Both callee segments are merged into one span; its parent is the span of the
  function that made the call. An Invoke subsegment with no callee segment (the
  call was throttled or never arrived) becomes a span of the callee built from the
  caller's view, so the fault is still charged to the right service.
* RCAEval traces.parquet (Jaeger-style spans: traceID, spanID, parentSpanID,
  serviceName, startTime and duration in microseconds, statusCode).
"""
from __future__ import annotations

import json

import pandas as pd

LAMBDA, FUNCTION = "AWS::Lambda", "AWS::Lambda::Function"


def service_names(stack: str, services: list[str]) -> dict[str, str]:
    """Function name -> service ("faultlab-orders-api" -> "orders_api")."""
    return {f"{stack}-{s.replace('_', '-')}": s for s in services}


def _flags(node: dict) -> tuple[bool, bool]:
    return bool(node.get("fault") or node.get("error")), bool(node.get("throttle"))


def _walk(node: dict, owner: str, out: dict) -> None:
    out[node["id"]] = (node, owner)
    for sub in node.get("subsegments", []) or []:
        _walk(sub, owner, out)


def xray_spans(trace: dict, names: dict[str, str]) -> list[dict]:
    docs = [json.loads(s["Document"]) if isinstance(s.get("Document"), str) else s["Document"]
            for s in trace.get("Segments", [])]
    nodes: dict[str, tuple[dict, str]] = {}  # node id -> (node, id of the top-level segment it lives in)
    for d in docs:
        _walk(d, d["id"], nodes)
    by_parent: dict[str, list[dict]] = {}
    for d in docs:
        if d.get("parent_id"):
            by_parent.setdefault(d["parent_id"], []).append(d)

    def fn_name(d: dict) -> str | None:
        return names.get(d.get("name")) or names.get((d.get("aws") or {}).get("function_name"))

    spans: dict[str, dict] = {}
    platform_of: dict[str, str] = {}  # AWS::Lambda segment id -> span id
    for d in docs:
        if d.get("origin") != LAMBDA or not fn_name(d):
            continue
        fault, throttle = _flags(d)
        for child in by_parent.get(d["id"], []):  # the function segment of the same invocation
            if child.get("origin") == FUNCTION:
                f2, t2 = _flags(child)
                fault, throttle = fault or f2, throttle or t2
                platform_of[child["id"]] = d["id"]
        spans[d["id"]] = {"trace_id": d["trace_id"], "span_id": d["id"], "parent_id": None, "service": fn_name(d),
                          "start": float(d["start_time"]), "end": float(d.get("end_time", d["start_time"])),
                          "fault": fault, "throttle": throttle, "_via": d.get("parent_id")}

    def span_of_owner(node_id: str) -> str | None:
        """The span (AWS::Lambda segment id) of the function whose document holds node_id."""
        owner = nodes[node_id][1] if node_id in nodes else None
        if owner in spans:
            return owner
        return platform_of.get(owner)

    for sp in spans.values():  # link a callee to the caller's span through the Invoke subsegment
        via = sp.pop("_via")
        if via and via in nodes:
            sp["parent_id"] = span_of_owner(via)

    for node_id, (node, _owner) in nodes.items():  # Invoke calls whose callee never started
        target = names.get((node.get("aws") or {}).get("function_name", ""))
        if (node.get("aws") or {}).get("operation") != "Invoke" or not target or node_id in by_parent:
            continue
        fault, throttle = _flags(node)
        spans[node_id] = {"trace_id": trace.get("Id") or node.get("trace_id"), "span_id": node_id,
                          "parent_id": span_of_owner(node_id), "service": target, "start": float(node["start_time"]),
                          "end": float(node.get("end_time", node["start_time"])), "fault": fault, "throttle": throttle}
    for sp in spans.values():
        sp["trace_id"] = sp["trace_id"] or trace.get("Id")
    return sorted(spans.values(), key=lambda s: (s["start"], s["span_id"]))


def rcaeval_spans(traces: pd.DataFrame, rename: dict[str, str] | None = None) -> list[dict]:
    """RCAEval / Jaeger spans. gRPC codes (< 100): non-zero is an error; HTTP codes: 5xx is an error."""
    rename = rename or {"frontendservice": "frontend"}
    t = traces
    code = pd.to_numeric(t["statusCode"], errors="coerce").fillna(0).astype(int)
    fault = ((code < 100) & (code != 0)) | (code >= 500)
    start = pd.to_numeric(t["startTime"], errors="coerce") / 1e6
    end = start + pd.to_numeric(t["duration"], errors="coerce") / 1e6
    parent = [None if pd.isna(p) or str(p) == "" else str(p) for p in t["parentSpanID"]]
    return [{"trace_id": str(tid), "span_id": str(sid), "parent_id": pid, "service": rename.get(str(svc), str(svc)),
             "start": float(s), "end": float(e), "fault": bool(f), "throttle": False}
            for tid, sid, pid, svc, s, e, f in zip(t["traceID"], t["spanID"], parent, t["serviceName"], start, end,
                                                   fault, strict=True)]
