"""Applies a schedule to the deployed stack and records what actually happened.

    python -m injector.injector --schedule data/runs/live/steady/schedule.jsonl --stack faultlab

For each injection: wait for its start, write the fault to /<stack>/fault (for
throttling also set the target's reserved concurrency to 1), wait for its end,
clear the switch (and the reserved concurrency) and append the real on / off
times to applied.jsonl. The switch is always cleared on the way out, also on
Ctrl+C. The functions read the switch with a short cache (FAULT_CACHE_S), so a
fault can start up to that many seconds late; scoring uses the scheduled start,
which makes measured delays slightly pessimistic, never optimistic.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from detector.spans import service_names
from injector.schedule import read

AWS_MIN_UNRESERVED = 100  # Lambda keeps at least this much concurrency unreserved per account


def can_throttle(lam, reserve: int = 0) -> bool:
    """Reserving 0 (the default throttling fault) always works; reserving more needs an account
    limit that still leaves 100 unreserved afterwards."""
    if reserve == 0:
        return True
    limit = lam.get_account_settings()["AccountLimit"]["UnreservedConcurrentExecutions"]
    return limit - reserve >= AWS_MIN_UNRESERVED


class Injector:
    def __init__(self, ssm, lam, stack: str, services: list[str], clock=time.time, sleep=time.sleep):
        self.ssm, self.lam, self.param = ssm, lam, f"/{stack}/fault"
        self.fn = {v: k for k, v in service_names(stack, services).items()}  # service -> function name
        self.clock, self.sleep = clock, sleep

    def _wait_until(self, t: float) -> None:
        while (left := t - self.clock()) > 0:
            self.sleep(min(left, 1.0))

    def on(self, inj: dict) -> float:
        body = {"id": inj["id"], "type": inj["fault"], "target": inj["target"], "until": inj["end"],
                **{k: inj[k] for k in ("latency_ms", "hold_ms") if k in inj}}
        if inj["fault"] == "throttling":
            self.lam.put_function_concurrency(FunctionName=self.fn[inj["target"]],
                                              ReservedConcurrentExecutions=inj.get("reserved_concurrency", 0))
        self.ssm.put_parameter(Name=self.param, Value=json.dumps(body), Type="String", Overwrite=True)
        return self.clock()

    def off(self, inj: dict | None = None) -> float:
        self.ssm.put_parameter(Name=self.param, Value="{}", Type="String", Overwrite=True)
        if inj and inj["fault"] == "throttling":
            self.lam.delete_function_concurrency(FunctionName=self.fn[inj["target"]])
        return self.clock()

    def run(self, schedule: list[dict], log_path: Path) -> int:
        reserve = max((i.get("reserved_concurrency", 0) for i in schedule if i["fault"] == "throttling"), default=None)
        if reserve is not None and not can_throttle(self.lam, reserve):
            raise RuntimeError(f"account concurrency limit too low to reserve {reserve} for the throttling fault "
                               "(use 0, or ask AWS for a higher limit - see docs/CONFIGURATION_MANUAL.md)")
        done = 0
        current = None
        try:
            with open(log_path, "a", encoding="utf-8") as log:
                for inj in schedule:
                    self._wait_until(inj["start"])
                    current = inj
                    t_on = self.on(inj)
                    self._wait_until(inj["end"])
                    t_off = self.off(inj)
                    current = None
                    log.write(json.dumps({**inj, "applied_at": t_on, "cleared_at": t_off}) + "\n")
                    log.flush()
                    done += 1
        finally:
            self.off(current)  # never leave a fault behind
        return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--stack", default="faultlab")
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args(argv)
    import boto3
    import yaml

    exp = yaml.safe_load(open(Path(__file__).resolve().parents[1] / __import__("os").environ.get("FAULTLAB_CONFIG", "protocols/experiment.yaml")))
    inj = Injector(boto3.client("ssm", region_name=args.region), boto3.client("lambda", region_name=args.region),
                   args.stack, exp["services"])
    n = inj.run(read(args.schedule), Path(args.schedule).with_name("applied.jsonl"))
    print(n, "injections applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
