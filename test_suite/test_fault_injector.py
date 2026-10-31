import json
from collections import Counter

import boto3
import pytest
import yaml
from moto import mock_aws

from injector import schedule
from injector.injector import Injector, can_throttle

EXP = yaml.safe_load(open("protocols/experiment.yaml"))


class Clock:
    """Fake time: sleeping moves the clock."""

    def __init__(self, t=0.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeLambda:
    def __init__(self, unreserved=900):
        self.calls, self.unreserved = [], unreserved

    def get_account_settings(self):
        return {"AccountLimit": {"UnreservedConcurrentExecutions": self.unreserved}}

    def put_function_concurrency(self, **kw):
        self.calls.append(("put", kw))

    def delete_function_concurrency(self, **kw):
        self.calls.append(("delete", kw))


@pytest.fixture
def ssm(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "eu-west-1", "AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t"}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        c = boto3.client("ssm")
        c.put_parameter(Name="/faultlab/fault", Value="{}", Type="String")
        yield c


def param(ssm):
    return ssm.get_parameter(Name="/faultlab/fault")["Parameter"]["Value"]


def test_schedule_is_balanced_spaced_and_seeded():
    s = schedule.build(EXP, "steady", start=0.0)
    assert len(s) == 240
    cells = Counter((r["fault"], r["target"]) for r in s)
    assert len(cells) == 12 and set(cells.values()) == {20}  # 4 types x 3 targets x 20
    assert {b["start"] - a["end"] for a, b in zip(s, s[1:], strict=False)} == {300}
    assert s[0]["start"] == 3600 and all(r["end"] - r["start"] == 60 for r in s)  # after the 60-min control
    assert s == schedule.build(EXP, "steady", start=0.0) and s != schedule.build(EXP, "peak", start=0.0)
    lat = [r["latency_ms"] for r in s if r["fault"] == "elevated_latency"]
    assert 300 <= min(lat) and max(lat) <= 800
    assert 24.9 < schedule.hours(s, EXP) < 25.2


def test_schedule_round_trip(tmp_path):
    s = schedule.build(EXP, "peak", start=100.0)
    assert schedule.read(schedule.write(s, tmp_path / "s.jsonl")) == s


def test_injector_switches_faults_on_and_off_on_time(ssm, tmp_path):
    clock, lam, written = Clock(), FakeLambda(), []
    put = ssm.put_parameter
    ssm.put_parameter = lambda **kw: (written.append(kw["Value"]), put(**kw))[1]
    inj = Injector(ssm, lam, "faultlab", EXP["services"], clock=clock.time, sleep=clock.sleep)
    sched = [{"id": "a", "fault": "elevated_latency", "target": "payments", "start": 10.0, "end": 70.0, "latency_ms": 400},
             {"id": "b", "fault": "throttling", "target": "inventory", "start": 370.0, "end": 430.0,
              "reserved_concurrency": 1}]
    assert inj.run(sched, tmp_path / "applied.jsonl") == 2
    rows = [json.loads(line) for line in (tmp_path / "applied.jsonl").read_text().splitlines()]
    assert [(r["applied_at"], r["cleared_at"]) for r in rows] == [(10.0, 70.0), (370.0, 430.0)]
    first = json.loads(written[0])
    assert first == {"id": "a", "type": "elevated_latency", "target": "payments", "until": 70.0, "latency_ms": 400}
    assert lam.calls == [("put", {"FunctionName": "faultlab-inventory", "ReservedConcurrentExecutions": 1}),
                         ("delete", {"FunctionName": "faultlab-inventory"})]
    assert param(ssm) == "{}"


def test_a_fault_is_cleared_even_when_the_run_is_interrupted(ssm, tmp_path):
    clock, lam = Clock(), FakeLambda()

    def sleep(s):
        clock.t += s
        if clock.t >= 30:
            raise KeyboardInterrupt

    inj = Injector(ssm, lam, "faultlab", EXP["services"], clock=clock.time, sleep=sleep)
    with pytest.raises(KeyboardInterrupt):
        inj.run([{"id": "a", "fault": "throttling", "target": "payments", "start": 10.0, "end": 70.0}],
                tmp_path / "a.jsonl")
    assert param(ssm) == "{}" and lam.calls[-1] == ("delete", {"FunctionName": "faultlab-payments"})


def test_reserving_more_than_zero_needs_enough_account_concurrency(ssm, tmp_path):
    assert can_throttle(FakeLambda(10), 0)  # the configured fault (0) works on a brand-new account
    assert can_throttle(FakeLambda(900), 1) and not can_throttle(FakeLambda(10), 1)
    inj = Injector(ssm, FakeLambda(10), "faultlab", EXP["services"], clock=Clock().time, sleep=Clock().sleep)
    with pytest.raises(RuntimeError, match="concurrency"):
        inj.run([{"id": "a", "fault": "throttling", "target": "payments", "start": 1.0, "end": 2.0,
                  "reserved_concurrency": 1}], tmp_path / "x")
    assert EXP["injection"]["throttle_reserved_concurrency"] == 0
