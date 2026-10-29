#!/usr/bin/env bash
# Rebuilds every result table and figure of the report from the raw run data (no AWS access).
# Usage: bash tools/rebuild_tables.sh [output root, default outputs]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"; OUT="${1:-outputs}"
export PYTHONPATH="$PWD:$PWD/lambdas/layer" FAULTLAB_CONFIG=protocols/experiment_live.yaml
$PY -m eval.live_campaign_eval --source live --run data/runs/yash_live_steady --out "$OUT/yash_live_steady" --figures "$OUT/yash_live_steady/figures"
$PY -m eval.trace_ablation --run data/runs/yash_live_steady --out "$OUT/yash_live_steady/extras"
$PY -m eval.overhead_study --runs data/runs/yash_overhead_r1 data/runs/yash_overhead_r2 data/runs/yash_overhead_r3 \
    --out "$OUT/yash_overhead"
$PY -m eval.overhead_study --runs data/runs/yash_overhead3_r1 data/runs/yash_overhead3_r2 --conditions full,policy \
    --out "$OUT/yash_overhead_3rps"
$PY -m eval.benchmark_eval --raw outputs/rcaeval/raw --out "$OUT/rcaeval_rebuilt" --figures "$OUT/rcaeval_rebuilt/figures"
echo "analysis written under $OUT/"
