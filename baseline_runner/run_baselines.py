"""Run RCAEval baselines on the same cases - inside the RCAEval venv, not the project one.

    python3.12 -m venv .venv-rcaeval
    .venv-rcaeval/bin/pip install -r baseline_runner/requirements-rcaeval.txt
    .venv-rcaeval/bin/python -m baseline_runner.run_baselines --data data/rcaeval --dataset RE2-OB \
        --methods baro,circa,causalrca,tracerca --out outputs/rcaeval/raw

The input is prepared exactly like RCAEval's main.py (latency-50 dropped,
latency-90 renamed, inf / NaN filled, 600 s each side of the injection), with
one deliberate difference: main.py hands a method the root-cause service's own
latency as the SLI when that column exists, which tells a method that uses the
SLI where the answer is. Here every method gets the entry service's latency
(protocols/experiment.yaml, rcaeval.entry_sli).

Every case and method gives one JSON file (ranked services, wall time, error).
Existing files are skipped, so a stopped run can be resumed.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import yaml

from baseline_runner import rcaeval_data as rd

ROOT = Path(__file__).resolve().parents[1]
TRACE_METHODS = {"tracerca", "microrank"}


def to_services(ranks: list, rename: dict[str, str] | None = None) -> list[str]:
    """RCAEval ranks metrics or operations ("checkoutservice_latency"); keep the first hit per service."""
    rename = rename or {"frontendservice": "frontend"}
    out: list[str] = []
    for r in ranks:
        svc = str(getattr(r, "entity", r)).split("_")[0].replace("-db", "")
        svc = rename.get(svc, svc)
        if svc not in out:
            out.append(svc)
    return out


def run_one(method: str, folder: Path, rc: dict, dataset_key: str) -> list:
    from RCAEval import e2e  # only available in the RCAEval venv

    c = rd.load(folder)
    it = c["inject_time"]
    func = getattr(e2e, method)
    if method in TRACE_METHODS:
        tr = c["traces"]
        if tr is None:
            raise ValueError("no traces in this case")
        start = tr["startTime"] / 1e6
        tr = tr[(start >= it - rc["side_s"]) & (start < it + rc["side_s"])].copy()
        out = func(tr, inject_time=it * 1_000_000, dataset=dataset_key)  # spans are in microseconds
    else:
        m = rd.window(rd.prepare_metrics(c["metrics"]), it, rc["side_s"])
        out = func(m, it, dataset=dataset_key, anomalies=None, dk_select_useful=False,
                   sli=rc["entry_sli"] if rc["entry_sli"] in m else None, verbose=False, n_iter=m.shape[1] - 1)
    return list(out["ranks"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/rcaeval")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--methods", default=None)
    ap.add_argument("--out", default="outputs/rcaeval/raw")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pattern", help="glob for case folders (default <dataset>_*); e.g. 're2ob_*_1' = repetition 1 "
                                      "of every service x fault, the subset used for the slow deep baseline")
    args = ap.parse_args(argv)
    exp = yaml.safe_load(open(ROOT / "protocols/experiment.yaml"))
    rc = exp["rcaeval"]
    ds = args.dataset or rc["dataset"]
    methods = args.methods.split(",") if args.methods else rc["baselines"]
    prefix = ds.lower().replace("-", "")
    pattern = args.pattern or f"{prefix}_*"
    folders = sorted(p for p in Path(args.data).glob(pattern) if (p / "metrics.parquet").exists())[: args.limit]
    for method in methods:
        out = Path(args.out) / method
        out.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(folders, 1):
            dest = out / f"{f.name}.json"
            if dest.exists():
                continue
            t0 = time.perf_counter()
            try:
                ranks, err = run_one(method, f, rc, ds.lower()), None
            except Exception:  # noqa: BLE001 - a failed case is recorded, not hidden
                ranks, err = [], traceback.format_exc(limit=3)
            res = {"case": f.name, "method": method, "root_cause": rd.root_cause(f.name), "fault": rd.fault_of(f.name),
                   "ranking": to_services(ranks), "ranks_raw": [str(getattr(r, "entity", r)) for r in ranks[:20]],
                   "seconds": round(time.perf_counter() - t0, 3), "error": err}
            dest.write_text(json.dumps(res) + "\n")
            print(f"{method} {i}/{len(folders)} {f.name} {res['seconds']}s top3={res['ranking'][:3]}"
                  f"{' ERROR' if err else ''}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


