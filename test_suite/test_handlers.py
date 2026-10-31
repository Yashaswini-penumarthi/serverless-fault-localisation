"""The four functions together, in-process (FAULTLAB_LOCAL=1), on moto DynamoDB + SSM."""
import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from faultlab import calls, fault
from tools import seed_inventory

NAMES = {"orders_api": "faultlab-orders-api", "inventory": "faultlab-inventory", "payments": "faultlab-payments",
         "notifications": "faultlab-notifications"}


def load(service):
    spec = importlib.util.spec_from_file_location(f"{service}_app", Path("lambdas") / service / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def app(monkeypatch):
    env = {"AWS_DEFAULT_REGION": "eu-west-1", "AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t",
           "FAULTLAB_LOCAL": "1", "TABLE_NAME": "orders", "FAULT_PARAM": "/faultlab/fault", "CLIENT_TIMEOUT_MS": "150",
           "INVENTORY_FN": NAMES["inventory"], "PAYMENTS_FN": NAMES["payments"],
           "NOTIFICATIONS_FN": NAMES["notifications"], "LOG_LEVEL": "ERROR", "AWS_XRAY_SDK_ENABLED": "false"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        ddb, ssm = boto3.client("dynamodb"), boto3.client("ssm")
        ddb.create_table(TableName="orders", BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])
        seed_inventory.seed(ddb, "orders", 20)
        ssm.put_parameter(Name="/faultlab/fault", Value="{}", Type="String")
        mods = {s: load(s) for s in NAMES}
        seen = []

        def recording(service, handler):
            return lambda e, c: seen.append(service) or handler(e, c)

        calls.LOCAL_HANDLERS.clear()
        for s, m in mods.items():
            calls.LOCAL_HANDLERS[NAMES[s]] = recording(s, m.handler)
        fault.reset_cache()

        def inject(**f):
            ssm.put_parameter(Name="/faultlab/fault", Value=json.dumps({"until": 4e9, **f}), Type="String",
                              Overwrite=True)
            fault.reset_cache()

        yield {"orders": mods["orders_api"], "mods": mods, "inject": inject, "seen": seen, "ddb": ddb}


def post(app, body):
    return app["orders"].handler({"httpMethod": "POST", "resource": "/orders", "body": json.dumps(body)}, None)


ORDER = {"customer_id": "CUST-0001", "sku": "0003", "qty": 2}


def test_order_round_trip(app):
    r = post(app, ORDER)
    assert r["statusCode"] == 201 and app["seen"] == ["inventory", "payments", "notifications"]
    oid = json.loads(r["body"])["order_id"]
    get = {"httpMethod": "GET", "resource": "/orders/{id}", "pathParameters": {"id": oid}}
    assert json.loads(app["orders"].handler(get, None)["body"])["status"] == "CONFIRMED"
    cancel = {**get, "httpMethod": "POST", "resource": "/orders/{id}/cancel"}
    assert app["orders"].handler(cancel, None)["statusCode"] == 200
    assert json.loads(app["orders"].handler(get, None)["body"])["status"] == "CANCELLED"


def test_bad_requests(app):
    assert post(app, {"sku": "1"})["statusCode"] == 400
    assert post(app, {**ORDER, "qty": 50})["statusCode"] == 400
    miss = {"httpMethod": "GET", "resource": "/orders/{id}", "pathParameters": {"id": "nope"}}
    assert app["orders"].handler(miss, None)["statusCode"] == 404


def test_dependency_failure_is_raised_by_the_target_and_502_at_the_entry(app):
    app["inject"](id=1, type="dependency_failure", target="payments")
    with pytest.raises(fault.InjectedFailure):  # unhandled in the target -> Lambda Errors metric
        app["mods"]["payments"].handler({"customer_id": "c", "amount_cents": 100}, None)
    assert post(app, ORDER)["statusCode"] == 502


def test_timeout_and_elevated_latency(app):
    app["inject"](id=2, type="timeout", target="inventory", hold_ms=250)  # > 150 ms client deadline
    assert post(app, ORDER)["statusCode"] == 504
    app["inject"](id=3, type="elevated_latency", target="inventory", latency_ms=60)  # slow but in time
    assert post(app, ORDER)["statusCode"] == 201


def test_async_notification_failure_does_not_fail_the_order(app):
    app["inject"](id=4, type="dependency_failure", target="notifications")
    assert post(app, ORDER)["statusCode"] == 201
