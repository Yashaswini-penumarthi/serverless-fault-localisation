import datetime as dt

import boto3
import numpy as np
import pandas as pd
from botocore.stub import Stubber

from detector import cloudwatch, xray

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def client(name):
    return boto3.client(name, region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")


def test_rates_and_missing_minutes():
    idx = pd.date_range(T0, periods=3, freq="60s")
    raw = {"invocations_inventory": {idx[0]: 100.0, idx[1]: 50.0}, "errors_inventory": {idx[0]: 5.0},
           "throttles_inventory": {idx[1]: 50.0}, "duration_inventory": {idx[0]: 40.0, idx[1]: 42.0}}
    s = cloudwatch.to_series(raw, ["inventory"], idx)
    assert s["inventory/ErrorRate"].tolist() == [0.05, 0.0, 0.0]
    assert s["inventory/ThrottleRate"].tolist() == [0.0, 0.5, 0.0]  # 50 throttled out of 100 attempts
    assert np.isnan(s["inventory/Duration"].iloc[2])  # no invocations -> no duration, skipped by the rules


def test_get_metric_data_pagination_and_queries():
    cw = client("cloudwatch")
    with Stubber(cw) as st:
        st.add_response("get_metric_data", {"MetricDataResults": [
            {"Id": "invocations_payments", "Timestamps": [T0], "Values": [10.0]}], "NextToken": "n"})
        st.add_response("get_metric_data", {"MetricDataResults": [
            {"Id": "errors_payments", "Timestamps": [T0], "Values": [1.0]}]})
        s = cloudwatch.fetch(cw, {"payments": "faultlab-payments"}, T0, T0 + dt.timedelta(minutes=2))
    assert s["payments/ErrorRate"].iloc[0] == 0.1 and len(s["payments/ErrorRate"]) == 2
    q = cloudwatch.queries({"a": "fa", "b": "fb"})
    assert len(q) == 8 and {x["MetricStat"]["Stat"] for x in q} == {"Sum", "Average"}


def test_xray_ids_are_deduplicated_and_fetched_five_at_a_time():
    xr = client("xray")
    with Stubber(xr) as st:
        st.add_response("get_trace_summaries", {"TraceSummaries": [{"Id": f"1-{i}"} for i in range(6)] + [{"Id": "1-0"}]})
        st.add_response("batch_get_traces", {"Traces": [{"Id": "1-0", "Segments": []}]})
        st.add_response("batch_get_traces", {"Traces": [{"Id": "1-5", "Segments": []}]})
        ids = xray.trace_ids(xr, T0, T0 + dt.timedelta(hours=1))
        traces = xray.fetch_traces(xr, ids)
    assert ids == [f"1-{i}" for i in range(6)] and [t["Id"] for t in traces] == ["1-0", "1-5"]
