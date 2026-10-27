#!/usr/bin/env python
"""Fill the learned-arm overhead lower bound from RCAEval parquet (non-AWS).

Downloads a cheap RE2-OB subset (default 12 cases with traces), writes
outputs/live/learned_lower_bound.csv, and patches outputs/live/overhead.json.
Does not invent a number when parquet cannot be fetched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baseline_runner import rcaeval_data  # noqa: E402
from eval import telemetry_cost as overhead  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-data", default=str(ROOT / "data/rcaeval"))
    ap.add_argument("--limit", type=int, default=12, help="RE2-OB cases with traces (cheap subset)")
    ap.add_argument("--overhead", default=str(ROOT / "outputs/live/overhead.json"))
    args = ap.parse_args(argv)
    data = Path(args.out_data)
    print("loading RCAEval case index…", flush=True)
    idx = rcaeval_data.index(data)
    cases = idx[(idx["dataset"] == "RE2-OB") & idx["has_traces"]].sort_values("case")["case"].tolist()
    cases = cases[: args.limit]
    if not cases:
        print("no RE2-OB cases with traces in index", file=sys.stderr)
        return 2
    for i, c in enumerate(cases, 1):
        rcaeval_data.download(c, data, traces=True)
        print(f"{i}/{len(cases)} {c}", flush=True)
    lb = overhead.learned_lower_bound(data)
    out_csv = ROOT / "outputs/live/learned_lower_bound.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    lb.to_csv(out_csv, index=False)
    report_path = Path(args.overhead)
    report = json.loads(report_path.read_text())
    if len(lb) and lb["bytes_per_1000_requests"].notna().any():
        med = float(lb["bytes_per_1000_requests"].median())
        report["learned_lower_bound_bytes_per_1000_requests"] = med
        report["learned_lower_bound_n_cases"] = int(len(lb))
        report["learned_lower_bound_note"] = (
            f"median compressed RCAEval telemetry bytes/1000 req over {len(lb)} RE2-OB cases "
            f"with traces.parquet (subset; parquet is a lower bound, not live-service volume)"
        )
        report.pop("learned_lower_bound_omitted", None)
    else:
        report["learned_lower_bound_bytes_per_1000_requests"] = None
        report["learned_lower_bound_note"] = "parquet downloaded but no usable traces; omitted"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"wrote {out_csv} n={len(lb)} median={report.get('learned_lower_bound_bytes_per_1000_requests')}")
    print(f"patched {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
