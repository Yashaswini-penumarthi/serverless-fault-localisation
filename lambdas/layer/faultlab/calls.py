"""Calling another function of the order service.

Synchronous calls use a client deadline (CLIENT_TIMEOUT_MS) and no SDK retries, so
a slow or failing dependency surfaces at the caller exactly once. Asynchronous
calls (notifications) are fire-and-forget.

LOCAL mode (FAULTLAB_LOCAL=1) dispatches to handlers registered in LOCAL_HANDLERS
inside the same process - used by the tests and the local smoke run. A call that
takes longer than the deadline raises DownstreamTimeout after the callee has
finished, which is what happens on Lambda too (the callee keeps running).
"""
from __future__ import annotations

import json
import os
import time

LOCAL_HANDLERS: dict = {}


class DownstreamError(Exception):
    """The callee returned an error."""


class DownstreamTimeout(Exception):
    """The callee did not answer before the client deadline."""


def client(timeout_ms: int):
    import boto3
    from botocore.config import Config

    return boto3.client("lambda", config=Config(read_timeout=timeout_ms / 1000, connect_timeout=1,
                                                retries={"total_max_attempts": 1}))


def invoke(function_name: str, payload: dict, asynchronous: bool = False, lam=None, timeout_ms: int | None = None):
    timeout_ms = timeout_ms or int(os.environ.get("CLIENT_TIMEOUT_MS", "1000"))
    if os.environ.get("FAULTLAB_LOCAL") == "1":
        return _local(function_name, payload, asynchronous, timeout_ms)
    from botocore.exceptions import ClientError, ReadTimeoutError

    lam = lam or client(timeout_ms)
    try:
        resp = lam.invoke(FunctionName=function_name, InvocationType="Event" if asynchronous else "RequestResponse",
                          Payload=json.dumps(payload).encode())
    except ReadTimeoutError as exc:
        raise DownstreamTimeout(function_name) from exc
    except ClientError as exc:  # e.g. TooManyRequestsException while the callee is throttled
        raise DownstreamError(f"{function_name}: {exc.response.get('Error', {}).get('Code', 'ClientError')}") from exc
    if asynchronous:
        return {"accepted": resp.get("StatusCode") == 202}
    body = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise DownstreamError(f"{function_name}: {body.get('errorMessage', body)}")
    return body


def _local(function_name: str, payload: dict, asynchronous: bool, timeout_ms: int):
    handler = LOCAL_HANDLERS[function_name]
    t0 = time.perf_counter()
    try:
        body = handler(payload, None)
    except Exception as exc:  # noqa: BLE001 - on Lambda this is a FunctionError
        if asynchronous:
            return {"accepted": True}
        raise DownstreamError(f"{function_name}: {exc}") from exc
    if asynchronous:
        return {"accepted": True}
    if (time.perf_counter() - t0) * 1000 > timeout_ms:
        raise DownstreamTimeout(function_name)
    return body
