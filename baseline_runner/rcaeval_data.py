"""RCAEval cases - the Hugging Face Parquet copy of the 735 failure cases (MIT licence).

    python -m baseline_runner.rcaeval_data --dataset RE2-OB --out data/rcaeval    # about 1 GB

A case folder holds metrics.parquet and inject_time.txt, and for RE2 / RE3 also
logs.parquet and traces.parquet (or the compact pre-annotated anomal_spans.parquet
+ trace_calibration.json bundle shipped inside the artefact ZIP alongside
sha256_manifest.json for byte-level corpus provenance). prepare_metrics() and
window() repeat what RCAEval's main.py does before it calls a baseline
(latency-50 dropped, latency-90 renamed to latency, inf / NaN filled, 600 s on
each side of the injection), so the rule arm and the baselines see the same
numbers. Only standard-library HTTP is used, so this also works inside the
RCAEval venv.
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HF = "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/main"
FILES = ("inject_time.txt", "metrics.parquet", "traces.parquet")


def fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "rcaeval-fetch"}), timeout=120) as r, \
            open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh)
    tmp.rename(dest)
    return dest


def index(data: Path) -> pd.DataFrame:
    return pd.read_parquet(fetch(f"{HF}/cases.parquet", Path(data) / "cases.parquet"))


def download(case: str, data: Path, traces: bool = True) -> Path:
    for f in FILES:
        if f == "traces.parquet" and not traces:
            continue
        try:
            fetch(f"{HF}/{case}/{f}", Path(data) / case / f)
        except urllib.error.HTTPError as exc:  # RE1 has no traces
            if f != "traces.parquet" or exc.code != 404:
                raise
    return Path(data) / case


def root_cause(case: str) -> str:
    return case.split("_")[1]


def fault_of(case: str) -> str:
    return case.split("_")[2]


def load(folder: Path) -> dict:
    folder = Path(folder)
    tr = folder / "traces.parquet"
    anom = folder / "anomal_spans.parquet"
    tcal = folder / "trace_calibration.json"
    return {"case": folder.name, "inject_time": int((folder / "inject_time.txt").read_text().split()[0]),
            "metrics": pd.read_parquet(folder / "metrics.parquet"),
            "traces": pd.read_parquet(tr) if tr.exists() else None,
            "anomal_spans": (pd.read_parquet(anom).assign(parent_id=lambda d: [None if pd.isna(v) or v == "" else str(v) for v in d["parent_id"]]).to_dict("records") if "parent_id" in pd.read_parquet(anom).columns else pd.read_parquet(anom).to_dict("records")) if anom.exists() and not tr.exists() else None,
            "trace_calibration": json.loads(tcal.read_text()) if tcal.exists() and not tr.exists() else None}


def prepare_metrics(m: pd.DataFrame) -> pd.DataFrame:
    m = m.loc[:, ~m.columns.str.endswith("_latency-50")]
    m = m.rename(columns={c: c.replace("_latency-90", "_latency") for c in m.columns if c.endswith("_latency-90")})
    return m.replace([np.inf, -np.inf], np.nan).ffill().fillna(0)


def window(m: pd.DataFrame, inject_time: int, side_s: int = 600) -> pd.DataFrame:
    normal = m[m["time"] < inject_time].tail(side_s)
    anomal = m[m["time"] >= inject_time].head(side_s)
    return pd.concat([normal, anomal], ignore_index=True)


def services_of(columns) -> list[str]:
    return sorted({c.split("_")[0].replace("-db", "") for c in columns if c != "time"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="RE2-OB")
    ap.add_argument("--out", default="data/rcaeval")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    idx = index(Path(args.out))
    cases = idx[idx["dataset"] == args.dataset].sort_values("case")["case"].tolist()[: args.limit]
    for i, c in enumerate(cases, 1):
        download(c, Path(args.out), traces=bool(idx.set_index("case").loc[c, "has_traces"]))
        print(f"{i}/{len(cases)} {c}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
