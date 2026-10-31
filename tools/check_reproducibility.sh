#!/usr/bin/env bash
# Offline reproducibility and artefact verification suite (no AWS credentials required).
# Usage: bash tools/check_reproducibility.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || command -v python)"
fi
export PYTHONPATH="$PWD:$PWD/lambdas/layer"
export AWS_EC2_METADATA_DISABLED=true
export AWS_ACCESS_KEY_ID=testing
export AWS_SECRET_ACCESS_KEY=testing
export AWS_DEFAULT_REGION=eu-west-1

echo "[1/4] Running unit test suite..."
"$PY" -m pytest -q

echo "[2/4] Re-executing baseline_runner.rule_arm on all 90 RE2-OB cases and checking 90/90 exact match..."
TMP_OUT="$(mktemp -d)"
trap 'rm -rf "$TMP_OUT"' EXIT
"$PY" -m baseline_runner.rule_arm --data data/rcaeval --dataset RE2-OB --out "$TMP_OUT/rules" >/dev/null
"$PY" - "$TMP_OUT/rules" << 'PYEOF'
import json, sys
from pathlib import Path

fresh_dir = Path(sys.argv[1])
arch_dir = Path("outputs/rcaeval/raw/rules")
fresh_files = sorted(fresh_dir.glob("*.json"))
assert len(fresh_files) == 90, f"Expected 90 rule_arm outputs, got {len(fresh_files)}"
for f in fresh_files:
    a = json.loads((arch_dir / f.name).read_text())
    b = json.loads(f.read_text())
    for k in ("case", "root_cause", "fault", "detected", "delay_s", "control_fp_episodes", "ranking", "ranking_source", "threshold_sha256"):
        assert a[k] == b[k], f"Mismatch on {f.name} key {k}: {a[k]} vs {b[k]}"
print("  90/90 RE2-OB cases matched archived rankings, detection fields, and threshold_sha256.")
PYEOF

echo "[3/4] Running tools/rebuild_tables.sh into temporary directory and verifying all summary metrics..."
PY="$PY" bash tools/rebuild_tables.sh "$TMP_OUT/outputs" >/dev/null
"$PY" - "$TMP_OUT/outputs" << 'PYEOF'
import json, sys
from pathlib import Path
import pandas as pd

res = Path(sys.argv[1])
rig = pd.read_csv(res / "yash_live_steady/rig_summary.csv").iloc[0]
assert abs(rig["precision"] - 20/22) < 1e-6
assert abs(rig["recall"] - 20/24) < 1e-6
assert abs(rig["f1"] - 40/46) < 1e-6
assert abs(rig["top1"] - 0.60) < 1e-6
assert abs(rig["top3"] - 0.90) < 1e-6
assert abs(rig["top3_end_to_end"] - 0.75) < 1e-6
assert abs(rig["mean_rank"] - 1.70) < 1e-6

loc = pd.read_csv(res / "rcaeval_rebuilt/localisation.csv").set_index("method")
assert abs(loc.loc["rules", "ac@1"] - 26/90) < 1e-6
assert abs(loc.loc["rules", "ac@3"] - 55/90) < 1e-6
assert abs(loc.loc["rules", "mean_rank"] - 2.977777777777778) < 1e-6
assert abs(loc.loc["circa", "ac@1"] - 53/90) < 1e-6
assert abs(loc.loc["circa", "ac@3"] - 79/90) < 1e-6
assert abs(loc.loc["baro", "ac@3"] - 79/90) < 1e-6
print("  Live AWS and RE2-OB summary tables verified.")
PYEOF

echo "[4/4] Checking for secrets, state files, or unpinned dependencies..."
if find . \( -name "*.tfstate*" -o -name ".env" -o -name ".terraform" \) | grep -q .; then
    echo "ERROR: Found state files, secrets, or .terraform cache" >&2
    exit 1
fi
test -f pinned-packages.txt
test -f data/rcaeval/sha256_manifest.json

echo "VERIFY OK"
