"""orders-api: validate and orchestrate (the entry service).

REST API (proxy) routes
    POST /orders               {"customer_id", "sku", "qty"} -> inventory (sync) -> payments (sync)
                               -> store the order -> notifications (async)
    GET  /orders/{id}
    POST /orders/{id}/cancel

A dependency that does not answer in time gives 504, one that fails gives 502,
an injected failure in this function gives 500. Synthetic data only.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import boto3

from faultlab import calls, fault, obs

SERVICE = "orders_api"
obs.patch_xray()
_clients: dict = {}


def clients() -> tuple:
    if not _clients:
        _clients["ddb"], _clients["ssm"] = boto3.client("dynamodb"), boto3.client("ssm")
    return _clients["ddb"], _clients["ssm"]


def respond(code: int, body: dict) -> dict:
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def create(body: dict, ddb) -> dict:
    missing = [k for k in ("customer_id", "sku", "qty") if k not in body]
    if missing or not isinstance(body.get("qty"), int) or not 1 <= body["qty"] <= 10:
        return respond(400, {"error": f"bad order: {missing or 'qty must be 1..10'}"})
    stock = calls.invoke(os.environ["INVENTORY_FN"], {"action": "reserve", "sku": body["sku"], "qty": body["qty"]})
    if not stock.get("reserved"):
        return respond(409, {"error": "out of stock"})
    amount = int(stock["price_cents"]) * body["qty"]
    pay = calls.invoke(os.environ["PAYMENTS_FN"], {"action": "authorise", "customer_id": body["customer_id"],
                                                    "amount_cents": amount})
    if not pay.get("approved"):
        return respond(402, {"error": "payment declined"})
    order_id = str(uuid.uuid4())
    ddb.put_item(TableName=os.environ["TABLE_NAME"], Item={
        "pk": {"S": f"ORDER#{order_id}"}, "status": {"S": "CONFIRMED"}, "customer_id": {"S": body["customer_id"]},
        "sku": {"S": body["sku"]}, "qty": {"N": str(body["qty"])}, "amount_cents": {"N": str(amount)},
        "auth_id": {"S": pay["auth_id"]}, "created_at": {"N": str(int(time.time()))}})
    calls.invoke(os.environ["NOTIFICATIONS_FN"], {"order_id": order_id, "event": "created"}, asynchronous=True)
    return respond(201, {"order_id": order_id, "status": "CONFIRMED", "amount_cents": amount})


def get(order_id: str, ddb) -> dict:
    item = ddb.get_item(TableName=os.environ["TABLE_NAME"], Key={"pk": {"S": f"ORDER#{order_id}"}}).get("Item")
    if not item:
        return respond(404, {"error": "no such order"})
    return respond(200, {"order_id": order_id, "status": item["status"]["S"], "sku": item["sku"]["S"]})


def cancel(order_id: str, ddb) -> dict:
    try:
        ddb.update_item(TableName=os.environ["TABLE_NAME"], Key={"pk": {"S": f"ORDER#{order_id}"}},
                        UpdateExpression="SET #s = :c", ConditionExpression="attribute_exists(pk)",
                        ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":c": {"S": "CANCELLED"}})
    except ddb.exceptions.ConditionalCheckFailedException:
        return respond(404, {"error": "no such order"})
    calls.invoke(os.environ["NOTIFICATIONS_FN"], {"order_id": order_id, "event": "cancelled"}, asynchronous=True)
    return respond(200, {"order_id": order_id, "status": "CANCELLED"})


def handler(event, context):
    ddb, ssm = clients()
    t0 = time.perf_counter()
    route = f"{event.get('httpMethod')} {event.get('resource')}"
    try:
        fault.apply(SERVICE, fault.current(ssm, os.environ["FAULT_PARAM"]))
        oid = (event.get("pathParameters") or {}).get("id", "")
        if route == "POST /orders":
            res = create(json.loads(event.get("body") or "{}"), ddb)
        elif route == "GET /orders/{id}":
            res = get(oid, ddb)
        elif route == "POST /orders/{id}/cancel":
            res = cancel(oid, ddb)
        else:
            res = respond(404, {"error": f"no route {route}"})
    except calls.DownstreamTimeout as exc:
        res = respond(504, {"error": f"dependency timed out: {exc}"})
    except calls.DownstreamError as exc:
        res = respond(502, {"error": f"dependency failed: {exc}"})
    except fault.InjectedFailure as exc:
        res = respond(500, {"error": str(exc)})
    except json.JSONDecodeError:
        res = respond(400, {"error": "body is not JSON"})
    level = "ERROR" if res["statusCode"] >= 500 else "INFO"
    obs.log(SERVICE, level, "request", route=route, status=res["statusCode"],
            ms=round((time.perf_counter() - t0) * 1000, 1))
    return res
