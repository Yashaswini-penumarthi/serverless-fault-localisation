#!/usr/bin/env python
"""Run one phase of the live experiment: load (and injections) plus its time window.

    python tools/campaign_driver.py --phase calibration   --url $API     # 24 h fault-free, steady load
    python tools/campaign_driver.py --phase steady        --url $API     # 60 min control + 240 injections
    python tools/campaign_driver.py --phase peak          --url $API     # the same at 5x the load
    python tools/campaign_driver.py --phase overhead-full --url $API     # 30 min per telemetry condition
                                                                    # (redeploy with its parameters first)

The schedule is written before anything starts; data/runs/live/windows.json
gets the phase's time range, which tools/pull_telemetry.py and eval/live_campaign_eval.py
read afterwards. Thresholds are calibrated once from the calibration phase and
never touched again.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def plan_phase(exp: dict, phase: str, now: float) -> dict:
    from injector import schedule as sched

    start = now + 60  # a minute to get everything going
    steady = exp["load"]["steady_rps"]
    if phase == "calibration":
        return {"rps": steady, "seconds": exp["calibration"]["hours"] * 3600, "window": [start, start +
                exp["calibration"]["hours"] * 3600], "schedule": [], "start": start}
    if phase in ("steady", "peak"):
        s = sched.build(exp, phase, start)
        end = s[-1]["end"] + exp["injection"]["recovery_s"]
        return {"rps": steady * (exp["load"]["peak_multiplier"] if phase == "peak" else 1), "seconds": end - start,
                "window": {"control": list(sched.control_window(exp, start)), "campaign": [s[0]["start"], end]},
                "schedule": s, "start": start}
    if phase.startswith("overhead-"):
        secs = exp["overhead"]["minutes_per_condition"] * 60
        return {"rps": steady, "seconds": secs, "window": [start, start + secs], "schedule": [], "start": start}
    raise ValueError(f"unknown phase {phase}")


def record_window(run: Path, phase: str, window) -> dict:
    path = run / "windows.json"
    w = json.loads(path.read_text()) if path.exists() else {}
    if phase in w:
        raise FileExistsError(f"{phase} already has a window in {path} - a phase is never re-run into the same folder")
    w[phase] = window
    path.write_text(json.dumps(w, indent=2) + "\n")
    return w


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--run", default="data/runs/live")
    ap.add_argument("--stack", default="faultlab")
    ap.add_argument("--region", default="eu-west-1")
    ap.add_argument("--config", default="protocols/experiment.yaml",
                    help="experiment yaml (use protocols/experiment_lite_overhead.yaml for lite Leg 3)")
    args = ap.parse_args(argv)
    import boto3

    from injector import schedule as sched
    from injector.injector import Injector
    from traffic import open_loop_client as loadgen

    cfg = Path(args.config)
    if not cfg.is_absolute():
        cfg = ROOT / cfg
    exp = yaml.safe_load(open(cfg))
    run = Path(args.run)
    run.mkdir(parents=True, exist_ok=True)
    p = plan_phase(exp, args.phase, time.time())
    record_window(run, args.phase, p["window"])
    if p["schedule"]:
        sched.write(p["schedule"], run / f"schedule_{args.phase}.jsonl")
    while time.time() < p["start"]:
        time.sleep(0.5)
    load = threading.Thread(target=loadgen.run, args=(args.url, p["rps"], p["seconds"],
                                                      run / f"requests_{args.phase}.csv"), daemon=True)
    load.start()
    if p["schedule"]:
        inj = Injector(boto3.client("ssm", region_name=args.region), boto3.client("lambda", region_name=args.region),
                       args.stack, exp["services"])
        inj.run(p["schedule"], run / f"applied_{args.phase}.jsonl")
    load.join()
    print(f"{args.phase} finished; window recorded in {run / 'windows.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
