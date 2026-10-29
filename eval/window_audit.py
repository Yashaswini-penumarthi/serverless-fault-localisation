"""Unplanned account-limit throttling ("incident minutes") and a sensitivity re-run without them.

The front-end function orders_api is never a fault target, so any Throttles on it mean the account
concurrency limit (10) was hit. In this study that happened when the driver host stalled and the
open-loop generator then sent its backlog in a burst.

    fetch:  python -m eval.window_audit fetch --run data/runs/yash_live       (needs AWS read access)
    rerun:  python -m eval.window_audit rerun --run data/runs/yash_live --out results/yash_live/sensitivity

The rerun is post hoc: it masks incident minutes in the calibration data (series and traces), re-derives
the thresholds, rescores, and labels every false-positive episode by whether it overlaps an incident.
"""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from eval import live_campaign_eval as rig
from detector import rules


def fetch(run: Path, stacks=("faultlab", "faultlab-oh")) -> None:
    import boto3, datetime as dt
    w = json.loads((run / "windows.json").read_text())
    t0 = w["calibration"][0]
    t1 = max(v["campaign"][1] for k, v in w.items() if k != "calibration")
    cw = boto3.client("cloudwatch")
    out = {}
    for st in stacks:
        r = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName="Throttles", Period=60, Statistics=["Sum"],
                                     Dimensions=[{"Name": "FunctionName", "Value": f"{st}-orders-api"}],
                                     StartTime=dt.datetime.fromtimestamp(t0, dt.timezone.utc),
                                     EndTime=dt.datetime.fromtimestamp(t1 + 60, dt.timezone.utc))
        out[st] = sorted([[p["Timestamp"].timestamp(), p["Sum"]] for p in r["Datapoints"] if p["Sum"] > 0])
    (run / "incidents.json").write_text(json.dumps(out, indent=1) + "\n")
    print({k: len(v) for k, v in out.items()}, "minutes with front-end throttles")


def rerun(run: Path, out: Path, exp: dict) -> dict:
    inc = json.loads((run / "incidents.json").read_text())["faultlab"]
    bad = [t for t, _ in inc]
    series, windows, spans, campaigns = rig.load_live(run)
    c0, c1 = windows["calibration"]
    mask_idx = pd.DatetimeIndex([pd.Timestamp(t, unit="s", tz="UTC") for t in bad if c0 <= t < c1])
    s2 = {}
    for k, s in series.items():
        s = s.copy(); s.loc[s.index.isin(mask_idx)] = np.nan; s2[k] = s
    def in_bad(t):
        return any(b <= t < b + 60 for b in bad if c0 <= b < c1)
    sp2 = [s for s in spans if not (c0 <= s["start"] < c1 and in_bad(s["start"]))]
    res = rig.evaluate(s2, sp2, campaigns, windows, exp)
    table, control = rig.summarise(res["per_injection"], res["false_positives"], windows)
    fp = res["false_positives"].copy()
    if len(fp):
        st = pd.to_datetime(fp["start"], utc=True).map(lambda x: x.timestamp())
        fp["overlaps_incident"] = [any(b - 60 <= x <= b + 120 for b, _ in inc) for x in st]
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "rig_summary.csv", index=False)
    res["per_injection"].to_csv(out / "per_injection.csv", index=False)
    fp.to_csv(out / "false_positives.csv", index=False)
    rep = {"masked_calibration_minutes": len(mask_idx), "incident_minutes_all": len(inc),
           "fp_total": int(len(fp)), "fp_overlapping_incident": int(fp["overlaps_incident"].sum()) if len(fp) else 0}
    (out / "sensitivity.json").write_text(json.dumps(rep, indent=1) + "\n")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "rerun"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    run = Path(a.run)
    if a.cmd == "fetch":
        fetch(run)
    else:
        import os, yaml
        exp = yaml.safe_load(open(rig.ROOT / os.environ.get("FAULTLAB_CONFIG", "protocols/experiment.yaml")))
        print(rerun(run, Path(a.out), exp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
