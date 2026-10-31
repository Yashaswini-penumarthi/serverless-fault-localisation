import datetime as dt
import json

import boto3
import pytest
import yaml
from botocore.stub import Stubber

from tools import cost_estimator as budget_estimate, campaign_driver as campaign, pull_overhead as collect_overhead, pull_telemetry as collect_telemetry

EXP = yaml.safe_load(open("protocols/experiment.yaml"))
PRICES = yaml.safe_load(open("protocols/prices.yaml"))
T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def client(name):
    return boto3.client(name, region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")


def test_budget_phases_and_total():
    rows, total = budget_estimate.estimate(EXP, PRICES)
    assert [r["phase"] for r in rows][:3] == ["calibration", "campaign steady", "campaign peak"]
    assert abs(rows[1]["hours"] - 25.0) < 0.01 and rows[2]["requests"] == 5 * rows[1]["requests"]
    off = next(r for r in rows if r["phase"] == "overhead off")
    assert off["traces"] == 0 and off["xray"] == 0
    assert 1 < total < 30


def test_sampling_arithmetic():
    assert budget_estimate.traces_per_s(2, 1, 0.05) == pytest.approx(1.05)
    assert budget_estimate.traces_per_s(0.5, 1, 0.05) == 0.5
    assert budget_estimate.traces_per_s(2, 1000, 1.0) == 2


def test_phase_plans():
    p = campaign.plan_phase(EXP, "steady", now=1000.0)
    assert p["window"]["control"] == [1060.0, 1060.0 + 3600] and len(p["schedule"]) == 240 and p["rps"] == 2
    assert campaign.plan_phase(EXP, "peak", 1000.0)["rps"] == 10
    cal = campaign.plan_phase(EXP, "calibration", 0.0)
    assert cal["seconds"] == 24 * 3600 and cal["schedule"] == []
    assert campaign.plan_phase(EXP, "overhead-full", 0.0)["seconds"] == 1800
    with pytest.raises(ValueError):
        campaign.plan_phase(EXP, "bogus", 0.0)


def test_a_phase_window_is_recorded_once(tmp_path):
    campaign.record_window(tmp_path, "calibration", [0, 10])
    with pytest.raises(FileExistsError):
        campaign.record_window(tmp_path, "calibration", [0, 10])
    assert json.loads((tmp_path / "windows.json").read_text()) == {"calibration": [0, 10]}


def test_trace_windows_cover_every_injection_and_merge():
    windows = {"calibration": [0.0, 86400.0]}
    sched = {"steady": [{"start": 100000.0, "end": 100060.0}, {"start": 100200.0, "end": 100260.0}]}
    w = collect_telemetry.trace_windows(windows, sched)
    assert w == [(86400 - 7200, 86400.0), (100000 - 180, 100260 + 180)]  # the two injection windows overlap


def test_log_bytes_and_trace_volume():
    cw = client("cloudwatch")
    with Stubber(cw) as st:
        st.add_response("get_metric_statistics", {"Datapoints": [{"Sum": 1000.0}, {"Sum": 500.0}]})
        st.add_response("get_metric_statistics", {"Datapoints": [{"Sum": 250.0}]})
        assert collect_overhead.log_bytes(cw, ["a", "b"], T0, T0 + dt.timedelta(minutes=30)) == 1750.0
    xr = client("xray")
    with Stubber(xr) as st:
        st.add_response("get_trace_summaries", {"TraceSummaries": [{"Id": "1-a"}, {"Id": "1-b"}]})
        st.add_response("batch_get_traces", {"Traces": [
            {"Id": "1-a", "Segments": [{"Id": "s", "Document": "x" * 300}]},
            {"Id": "1-b", "Segments": [{"Id": "t", "Document": "y" * 100}]}]})
        n, mean = collect_overhead.trace_volume(xr, T0, T0 + dt.timedelta(minutes=30))
    assert n == 2 and mean == 200.0
