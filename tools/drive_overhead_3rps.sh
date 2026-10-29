#!/usr/bin/env bash
# Extra Leg 3 runs at 3 rps on the overhead stack: full vs policy, 2 repetitions, order rotated.
# Added during the run to test the policy above the 1 trace/s reservoir (protocols/experiment_live_oh3rps.yaml). Resumable.
# Usage: TFDIR=<terraform dir with its own state> bash tools/drive_overhead_3rps.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="${PY:-python}"; TFDIR="${TFDIR:?set TFDIR}"; STACK="${STACK:-faultlab-oh}"
export PYTHONPATH="$ROOT" FAULTLAB_CONFIG=protocols/experiment_live_oh3rps.yaml
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
declare -A P=( [full]="Active INFO 1 1000" [policy]="Active ERROR 0.05 1" )
ORDERS=("full policy" "policy full" "full policy")
REPS="${REPS:-1 2}"   # rep 3 was added after rep 2 full was hit by account-limit throttling
mkdir -p data/runs/yash_overhead3_r1
step data/runs/yash_overhead3_r1 warmup $PY -m workloads.loadgen --url "$(api)" --rps 3 --minutes 2 --out data/runs/yash_overhead3_r1/warmup.csv
for r in $REPS; do
  OR="data/runs/yash_overhead3_r$r"; mkdir -p "$OR"
  for c in ${ORDERS[$((r-1))]}; do
    step "$OR" "${c}_apply" apply ${P[$c]}
    # a config change starts new instances; warm them outside the measured window
    step "$OR" "${c}_warm" $PY -m workloads.loadgen --url "$(api)" --rps 3 --minutes 1 --out "$OR/warm_$c.csv"
    sleep 30
    step "$OR" "${c}_load" $PY tools/campaign_driver.py --phase "overhead-$c" --url "$(api)" --run "$OR" --config "$CFG"
    sleep 150
    step "$OR" "${c}_collect" $PY tools/pull_overhead.py --run "$OR" --condition "$c" --stack "$STACK" --config "$CFG"
  done
done
step data/runs/yash_overhead3_r1 final_policy apply Active ERROR 0.05 1
echo "OVERHEAD3_ALL_DONE $(stamp)"
