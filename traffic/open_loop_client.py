"""Open-loop load generator for the order API.

    python -m traffic.open_loop_client --url https://abc.execute-api.eu-west-1.amazonaws.com/prod --rps 2 \
        --minutes 60 --out data/runs/live/steady/requests.csv

Requests leave on a fixed timetable whatever the latency, so a slow service does
not slow the load down (no coordinated omission). Mix: 80 % create, 15 % get of
an order created earlier, 5 % cancel. Bodies come from a seeded RNG: made-up
customers CUST-0001..0500 and SKUs 0001..0200 - no personal data.
Every request is written with its send time, status, latency and X-Ray trace id.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FIELDS = ["t_sent", "kind", "status", "latency_ms", "trace_id", "error"]


def plan(rps: float, seconds: float, seed: int = 24262404) -> list[tuple[float, str]]:
    rng = random.Random(seed)
    n = int(rps * seconds)
    kinds = rng.choices(["create", "get", "cancel"], weights=[80, 15, 5], k=n)
    return [(i / rps, k) for i, k in enumerate(kinds)]


def order_body(rng: random.Random) -> dict:
    return {"customer_id": f"CUST-{rng.randint(1, 500):04d}", "sku": f"{rng.randint(1, 200):04d}",
            "qty": rng.randint(1, 3)}


def http_send(url: str, method: str, path: str, body: dict | None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, payload = r.status, r.headers, r.read()
    except urllib.error.HTTPError as exc:
        status, headers, payload = exc.code, exc.headers, exc.read()
    except Exception as exc:  # noqa: BLE001 - a network error is a result too
        return {"status": 0, "latency_ms": (time.perf_counter() - t0) * 1000, "trace_id": "", "error": str(exc)[:120]}
    out = {"status": status, "latency_ms": (time.perf_counter() - t0) * 1000,
           "trace_id": headers.get("x-amzn-trace-id", "") if headers else "", "error": ""}
    try:
        out["body"] = json.loads(payload or b"{}")
    except json.JSONDecodeError:
        out["body"] = {}
    return out


def run(url: str, rps: float, seconds: float, out: Path, seed: int = 24262404, workers: int = 64,
        sender=http_send, clock=time.time, sleep=time.sleep) -> int:
    rng = random.Random(seed + 1)
    created: list[str] = []
    lock = threading.Lock()
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = open(out, "w", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader()

    def one(t_sent: float, kind: str) -> None:
        with lock:
            oid = rng.choice(created) if created and kind != "create" else None
            body = order_body(rng) if kind == "create" or oid is None else None
        if body is not None:
            kind, res = "create", sender(url, "POST", "/orders", body)
            if res["status"] == 201:
                with lock:
                    created.append(res.get("body", {}).get("order_id", ""))
        elif kind == "get":
            res = sender(url, "GET", f"/orders/{oid}", None)
        else:
            res = sender(url, "POST", f"/orders/{oid}/cancel", None)
        with lock:
            w.writerow({"t_sent": round(t_sent, 3), "kind": kind, "status": res["status"],
                        "latency_ms": round(res["latency_ms"], 2), "trace_id": res.get("trace_id", ""),
                        "error": res.get("error", "")})

    t0 = clock()
    timetable = plan(rps, seconds, seed)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for offset, kind in timetable:
            while (left := t0 + offset - clock()) > 0:
                sleep(min(left, 0.05))
            pool.submit(one, t0 + offset, kind)
    fh.close()
    return len(timetable)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    n = run(args.url, args.rps, args.minutes * 60, Path(args.out))
    print(n, "requests ->", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
