import json

import boto3
import pytest
from moto import mock_aws

from faultlab import calls, fault, obs

PARAM = "/faultlab/fault"


@pytest.fixture
def ssm(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        c = boto3.client("ssm", region_name="eu-west-1")
        c.put_parameter(Name=PARAM, Value="{}", Type="String")
        fault.reset_cache()
        yield c


def set_fault(ssm, **f):
    ssm.put_parameter(Name=PARAM, Value=json.dumps(f), Type="String", Overwrite=True)


def test_switch_is_read_with_a_cache_and_expires(ssm):
    set_fault(ssm, id=1, type="elevated_latency", target="payments", until=1000.0, latency_ms=10)
    assert fault.current(ssm, PARAM, now=990.0)["target"] == "payments"
    set_fault(ssm, id=2, type="timeout", target="inventory", until=2000.0)
    assert fault.current(ssm, PARAM, now=992.0)["id"] == 1  # still cached (5 s)
    assert fault.current(ssm, PARAM, now=996.0)["id"] == 2
    assert fault.current(ssm, PARAM, now=2001.0) == {}  # past `until`


def test_a_broken_switch_means_no_fault(ssm):
    ssm.delete_parameter(Name=PARAM)
    assert fault.current(ssm, PARAM, now=1.0) == {}


def test_apply_only_hits_the_target():
    slept = []
    f = {"type": "elevated_latency", "target": "payments", "latency_ms": 540}
    assert fault.apply("inventory", f, sleep=slept.append) is None and slept == []
    assert fault.apply("payments", f, sleep=slept.append) == "elevated_latency" and slept == [0.54]
    assert fault.apply("payments", {"type": "timeout", "target": "payments", "hold_ms": 1500},
                       sleep=slept.append) == "timeout" and slept[-1] == 1.5
    with pytest.raises(fault.InjectedFailure):
        fault.apply("payments", {"type": "dependency_failure", "target": "payments", "id": 3})
    assert fault.apply("payments", {"type": "throttling", "target": "payments"}) is None


def test_log_level_controls_volume(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert not obs.log("orders_api", "INFO", "order created")
    assert obs.log("orders_api", "ERROR", "payments failed", order="o1")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["service"] == "orders_api" and line["order"] == "o1"


def test_xray_is_not_patched_outside_lambda(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert not obs.patch_xray()


def test_a_throttled_invoke_becomes_a_downstream_error(monkeypatch):
    from botocore.exceptions import ClientError

    monkeypatch.delenv("FAULTLAB_LOCAL", raising=False)

    class Throttled:
        def invoke(self, **kw):
            raise ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate Exceeded."}}, "Invoke")

    with pytest.raises(calls.DownstreamError, match="TooManyRequestsException"):
        calls.invoke("faultlab-payments", {}, lam=Throttled())


def test_local_calls_report_errors_and_deadlines(monkeypatch):
    monkeypatch.setenv("FAULTLAB_LOCAL", "1")
    import time as _t

    calls.LOCAL_HANDLERS.update({"ok": lambda e, c: {"echo": e["x"]},
                                 "bad": lambda e, c: (_ for _ in ()).throw(RuntimeError("boom")),
                                 "slow": lambda e, c: _t.sleep(0.05) or {"late": True}})
    assert calls.invoke("ok", {"x": 1}) == {"echo": 1}
    with pytest.raises(calls.DownstreamError):
        calls.invoke("bad", {})
    with pytest.raises(calls.DownstreamTimeout):
        calls.invoke("slow", {}, timeout_ms=10)
    assert calls.invoke("bad", {}, asynchronous=True) == {"accepted": True}
