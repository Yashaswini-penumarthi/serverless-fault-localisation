#!/usr/bin/env bash
# Leg 3 overhead on its own stack (name prefix faultlab-oh, own Terraform state), so it can run
# while the detection campaign uses the main stack. 3 repetitions x full / policy / off, 5 min
# each at 1 rps, condition order rotated (Latin square). Resumable through done_ markers.
# Usage: TFDIR=<terraform dir with its own state> bash tools/drive_overhead_1rps.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="${PY:-python}"; TFDIR="${TFDIR:?set TFDIR}"; STACK="${STACK:-faultlab-oh}"
export PYTHONPATH="$ROOT" FAULTLAB_CONFIG=protocols/experiment_live.yaml
CFG=$FAULTLAB_CONFIG
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
apply() { (cd "$TFDIR" && terraform apply -auto-approve -input=false -no-color -var="name_prefix=$STACK" \
  -var="layer_package=$ROOT/build/faultlab-layer.zip" -var="orders_api_package=$ROOT/build/orders-api.zip" \
  -var="inventory_package=$ROOT/build/inventory.zip" -var="payments_package=$ROOT/build/payments.zip" \
  -var="notifications_package=$ROOT/build/notifications.zip" \
  -var="tracing_mode=$1" -var="log_level=$2" -var="sampling_fixed_rate=$3" -var="sampling_reservoir=$4" >/dev/null); }
api() { (cd "$TFDIR" && terraform output -raw api_url); }
step() { local d=$1 name=$2; shift 2; if [ -f "$d/done_$name" ]; then echo "skip $name"; return 0; fi
  echo "== $name start $(stamp)"; "$@" && touch "$d/done_$name" && echo "== $name done $(stamp)" || { echo "!! $name failed $(stamp)"; exit 1; }; }
declare -A P=( [full]="Active INFO 1 1000" [policy]="Active ERROR 0.05 1" [off]="PassThrough ERROR 0.05 1" )
ORDERS=("full policy off" "policy off full" "off full policy")
mkdir -p data/runs/yash_overhead_r1
step data/runs/yash_overhead_r1 init bash -c "cd '$TFDIR' && terraform init -input=false -no-color >/dev/null"
step data/runs/yash_overhead_r1 first_apply apply Active INFO 1 1000
step data/runs/yash_overhead_r1 seed $PY tools/seed_inventory.py --table "$STACK-orders"
step data/runs/yash_overhead_r1 warmup $PY -m workloads.loadgen --url "$(api)" --rps 1 --minutes 3 --out data/runs/yash_overhead_r1/warmup.csv
for r in 1 2 3; do
  OR="data/runs/yash_overhead_r$r"; mkdir -p "$OR"
  for c in ${ORDERS[$((r-1))]}; do
    step "$OR" "${c}_apply" apply ${P[$c]}
    sleep 30
    step "$OR" "${c}_load" $PY tools/campaign_driver.py --phase "overhead-$c" --url "$(api)" --run "$OR" --config "$CFG"
    sleep 150   # log delivery and trace indexing before measuring
    step "$OR" "${c}_collect" $PY tools/pull_overhead.py --run "$OR" --condition "$c" --stack "$STACK" --config "$CFG"
  done
done
step data/runs/yash_overhead_r1 final_policy apply Active ERROR 0.05 1
echo "OVERHEAD_ALL_DONE $(stamp)"
