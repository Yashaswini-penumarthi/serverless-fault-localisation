"""Campaign schedule - the ground truth by construction.

One campaign per load level: a fault-free control period, then 60 injections of
each of the four fault types, spread evenly over the three downstream services
(20 each), in a seeded random order. Every injection is on for 60 s and followed
by 300 s without a fault, so a detection cannot be credited to the previous one.
The schedule is written to disk before the first injection.
"""
from __future__ import annotations

import json
import random
from pathlib import Path


def build(exp: dict, load: str, start: float, seed: int | None = None) -> list[dict]:
    inj = exp["injection"]
    rng = random.Random(f"{seed if seed is not None else inj['seed']}-{load}")
    targets = exp["fault_targets"]
    items = [(ft, targets[i % len(targets)]) for ft in exp["fault_types"] for i in range(inj["per_type_per_load"])]
    rng.shuffle(items)
    t = start + exp["control"]["minutes"] * 60
    out = []
    for k, (ft, target) in enumerate(items, 1):
        row = {"id": f"{load}-{k:03d}", "load": load, "fault": ft, "target": target, "start": t,
               "end": t + inj["duration_s"]}
        if ft == "elevated_latency":
            row["latency_ms"] = rng.randint(*inj["latency_ms"])
        elif ft == "timeout":
            row["hold_ms"] = inj["timeout_hold_ms"]
        elif ft == "throttling":
            row["reserved_concurrency"] = inj["throttle_reserved_concurrency"]
        out.append(row)
        t += inj["duration_s"] + inj["recovery_s"]
    return out


def control_window(exp: dict, start: float) -> tuple[float, float]:
    return start, start + exp["control"]["minutes"] * 60


def hours(schedule: list[dict], exp: dict) -> float:
    if not schedule:
        return 0.0
    return (schedule[-1]["end"] + exp["injection"]["recovery_s"] - schedule[0]["start"]) / 3600 + \
        exp["control"]["minutes"] / 60


def write(schedule: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in schedule))
    return path


def read(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
