"""The X-Ray ranker on hand-built traces for each fault type (orders_api -> inventory -> payments)."""
import json

import pandas as pd

from detector import ranker, spans

CANDIDATES = ["inventory", "notifications", "orders_api", "payments"]


def chain(tid, t0=0.0, inv=(0.05, False, False), pay=(0.05, False, False), pay_parent_ok=True):
    """orders_api calls inventory, inventory calls payments. (duration, fault, throttle) per callee."""
    pd_, pf, pt = pay
    idur, ifa, ith = inv
    p = {"trace_id": tid, "span_id": f"{tid}-p", "parent_id": f"{tid}-i", "service": "payments",
         "start": t0 + 0.02, "end": t0 + 0.02 + pd_, "fault": pf, "throttle": pt}
    i = {"trace_id": tid, "span_id": f"{tid}-i", "parent_id": f"{tid}-o", "service": "inventory",
         "start": t0 + 0.01, "end": t0 + 0.03 + max(idur, pd_), "fault": ifa or (pf and not pay_parent_ok),
         "throttle": ith}
    o = {"trace_id": tid, "span_id": f"{tid}-o", "parent_id": None, "service": "orders_api", "start": t0,
         "end": i["end"] + 0.01, "fault": i["fault"] or pf, "throttle": False}
    return [o, i, p]


def normal(n=200):
    return [s for k in range(n) for s in chain(f"n{k}", t0=k)]


CAL = ranker.calibrate(normal())


def test_self_time_excludes_children_and_depth_counts_from_the_root():
    ann = {s["service"]: s for s in ranker.annotate(chain("t"))}
    assert ann["orders_api"]["depth"] == 0 and ann["payments"]["depth"] == 2
    assert abs(ann["inventory"]["self_time"] - (ann["inventory"]["end"] - ann["inventory"]["start"] - 0.05)) < 1e-9


def test_dependency_failure_is_charged_to_where_it_starts():
    faulty = [s for k in range(20) for s in chain(f"f{k}", t0=k, pay=(0.05, True, False))]
    assert ranker.ranking(faulty, CANDIDATES, CAL)[0] == "payments"


def test_elevated_latency_is_found_through_self_time():
    slow = [s for k in range(20) for s in chain(f"s{k}", t0=k, inv=(0.6, False, False))]
    assert ranker.ranking(slow, CANDIDATES, CAL)[0] == "inventory"


def test_throttle_counts_like_a_fault():
    thr = [s for k in range(20) for s in chain(f"h{k}", t0=k, pay=(0.001, False, True))]
    assert ranker.ranking(thr, CANDIDATES, CAL)[0] == "payments"


def test_every_candidate_gets_a_rank_and_order_is_deterministic():
    faulty = [s for k in range(5) for s in chain(f"f{k}", t0=k, pay=(0.05, True, False))]
    r1 = ranker.ranking(faulty, CANDIDATES, CAL)
    assert sorted(r1) == sorted(CANDIDATES) and r1 == ranker.ranking(list(reversed(faulty)), CANDIDATES, CAL)


def test_no_failed_traces_means_all_zero_scores():
    rows = ranker.rank(normal(10), CANDIDATES, CAL)
    assert all(r["score"] == 0 for r in rows) and [r["service"] for r in rows] == sorted(CANDIDATES)


def seg(id_, name, origin, start, end, parent=None, subs=None, **flags):
    d = {"id": id_, "trace_id": "1-abc", "name": name, "origin": origin, "start_time": start, "end_time": end,
         "subsegments": subs or [], **flags}
    if parent:
        d["parent_id"] = parent
    return {"Id": id_, "Document": json.dumps(d)}


def test_xray_segments_become_one_span_per_invocation():
    names = spans.service_names("faultlab", ["orders_api", "inventory", "payments"])
    invoke = {"id": "sub-inv", "name": "Lambda", "namespace": "aws", "start_time": 1.01, "end_time": 1.5,
              "aws": {"operation": "Invoke", "function_name": "faultlab-inventory"}, "fault": True}
    throttled = {"id": "sub-pay", "name": "Lambda", "namespace": "aws", "start_time": 1.02, "end_time": 1.03,
                 "aws": {"operation": "Invoke", "function_name": "faultlab-payments"}, "throttle": True, "error": True}
    trace = {"Id": "1-abc", "Segments": [
        seg("L-o", "faultlab-orders-api", "AWS::Lambda", 1.0, 1.6),
        seg("F-o", "faultlab-orders-api", "AWS::Lambda::Function", 1.0, 1.6, parent="L-o", subs=[invoke, throttled],
            fault=True),
        seg("L-i", "faultlab-inventory", "AWS::Lambda", 1.02, 1.9, parent="sub-inv"),
        seg("F-i", "faultlab-inventory", "AWS::Lambda::Function", 1.02, 1.9, parent="L-i"),
    ]}
    got = {s["service"]: s for s in spans.xray_spans(trace, names)}
    assert set(got) == {"orders_api", "inventory", "payments"}
    assert got["inventory"]["parent_id"] == got["orders_api"]["span_id"]  # linked through the Invoke subsegment
    assert got["payments"]["throttle"] and got["payments"]["parent_id"] == got["orders_api"]["span_id"]


def test_rcaeval_spans_status_codes_and_units():
    df = pd.DataFrame({"traceID": ["t", "t", "t"], "spanID": ["a", "b", "c"], "parentSpanID": [None, "a", "a"],
                       "serviceName": ["frontendservice", "cartservice", "ts-order"],
                       "startTime": [1_000_000, 1_100_000, 1_200_000], "duration": [500_000, 100_000, 10],
                       "statusCode": [0, 14, 503]})
    got = {s["span_id"]: s for s in spans.rcaeval_spans(df)}
    assert got["a"]["service"] == "frontend" and not got["a"]["fault"] and got["a"]["end"] == 1.5
    assert got["b"]["fault"] and got["c"]["fault"] and got["b"]["parent_id"] == "a"


def test_causal_cutoff_excludes_spans_finishing_after_detection():
    from eval import live_campaign_eval as rig
    spans_list = [
        {"trace_id": "t_ok", "span_id": "s1", "parent_id": None, "service": "orders_api", "start": 10.0, "end": 19.8},
        {"trace_id": "t_ok", "span_id": "s2", "parent_id": "s1", "service": "inventory", "start": 10.1, "end": 19.7},
        {"trace_id": "t_late", "span_id": "s3", "parent_id": None, "service": "orders_api", "start": 19.5, "end": 20.4},
        {"trace_id": "t_late", "span_id": "s4", "parent_id": "s3", "service": "inventory", "start": 19.6, "end": 20.3},
    ]
    kept_causal = rig._spans_between(spans_list, 0.0, 20.0, causal_cutoff=True)
    kept_old = rig._spans_between(spans_list, 0.0, 20.0, causal_cutoff=False)
    assert {s["trace_id"] for s in kept_causal} == {"t_ok"}
    assert {s["trace_id"] for s in kept_old} == {"t_ok", "t_late"}


def test_synchronous_tree_mode_ignores_leaf_jitter_when_root_is_fast():
    # Build normal calibration traces where payments self_time is 0.05s and root is 0.12s
    cal = ranker.calibrate(normal(200))
    # One trace where root is fast (0.09s <= cal root_duration) and no fault, but leaf payments took 0.06s (> cal p99)
    tr = [
        {"trace_id": "j1", "span_id": "o", "parent_id": None, "service": "orders_api",
         "start": 0.0, "end": 0.09, "fault": False, "throttle": False},
        {"trace_id": "j1", "span_id": "p", "parent_id": "o", "service": "payments",
         "start": 0.01, "end": 0.07, "fault": False, "throttle": False},
    ]
    rows_async = ranker.rank(tr, CANDIDATES, cal, async_leaf_slow=True)
    rows_sync = ranker.rank(tr, CANDIDATES, cal, async_leaf_slow=False)
    assert rows_async[0]["failed_traces"] == 1 and rows_async[0]["service"] == "payments"
    assert rows_sync[0]["failed_traces"] == 0 and all(r["score"] == 0.0 for r in rows_sync)
