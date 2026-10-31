import csv
import random
from collections import Counter

import pytest

from traffic import open_loop_client as loadgen


class Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_plan_rate_and_mix():
    p = loadgen.plan(2, 3600)
    assert len(p) == 7200 and p[1][0] - p[0][0] == 0.5
    share = Counter(k for _, k in p)
    assert 0.77 < share["create"] / 7200 < 0.83 and 0.03 < share["cancel"] / 7200 < 0.07
    assert p == loadgen.plan(2, 3600)


def test_bodies_are_synthetic():
    b = loadgen.order_body(random.Random(1))
    assert b["customer_id"].startswith("CUST-") and len(b["sku"]) == 4 and 1 <= b["qty"] <= 3


def test_open_loop_keeps_the_timetable_when_the_service_is_slow(tmp_path):
    clock, sent = Clock(), []

    def sender(url, method, path, body, timeout=10):
        sent.append((method, path))
        created = method == "POST" and path == "/orders"
        return {"status": 201 if created else 200, "latency_ms": 5000.0, "trace_id": "Root=1-abc",
                "body": {"order_id": f"o{len(sent)}"}}

    n = loadgen.run("https://x", rps=4, seconds=5, out=tmp_path / "r.csv", sender=sender, clock=clock.time,
                    sleep=clock.sleep, workers=4)
    rows = list(csv.DictReader(open(tmp_path / "r.csv")))
    assert n == 20 and len(rows) == 20
    t = sorted(float(r["t_sent"]) - 1000.0 for r in rows)
    assert t[0] == 0 and t[-1] == pytest.approx(4.75)  # 5000 ms responses did not delay the next sends
    assert all(r["trace_id"] == "Root=1-abc" for r in rows)
    assert sent[0] == ("POST", "/orders")  # nothing to get or cancel before the first order exists
