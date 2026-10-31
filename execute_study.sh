#!/usr/bin/env bash
# Full experiment on AWS from scratch: package, deploy both stacks, run the live campaign and the
# overhead runs, then the analysis. Takes about 5 hours of wall time (see the configuration manual).
# Needs AWS credentials in the environment. Leaves both stacks deployed in policy mode.
# Usage: bash execute_study.sh            (after bash bootstrap_env.sh)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH" PY="$PWD/.venv/bin/python" PYTHONPATH="$PWD"
export AWS_REGION="${AWS_REGION:-eu-west-1}" AWS_DEFAULT_REGION="${AWS_REGION:-eu-west-1}"
aws sts get-caller-identity --query Account --output text >/dev/null   # fails early without credentials
$PY tools/bundle_lambdas.py
(cd infra && terraform init -input=false && terraform apply -auto-approve -input=false)
$PY tools/seed_inventory.py --table faultlab-orders
OHDIR="${OHDIR:-$PWD/tf_overhead}"; mkdir -p "$OHDIR"; cp infra/*.tf "$OHDIR"/
# the campaign and the overhead runs use separate stacks and can run side by side
TFDIR="$OHDIR" bash tools/drive_overhead_1rps.sh > overhead_run.log 2>&1 &
OH=$!
bash tools/drive_live_campaign.sh > live_run.log 2>&1
wait $OH
TFDIR="$OHDIR" bash tools/drive_overhead_3rps.sh > overhead3_run.log 2>&1
bash tools/rebuild_tables.sh
