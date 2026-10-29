#!/usr/bin/env bash
# Full live protocol on AWS (protocols/experiment_live.yaml). Resumable: a finished step leaves a
# marker in $RUN/done_<step> and is skipped on a rerun. AWS credentials come from the environment.
# Usage: bash tools/drive_live_campaign.sh            (from the artefact root, stack already applied)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="${PY:-python}"
export PYTHONPATH="$ROOT"
export FAULTLAB_CONFIG=protocols/experiment_live.yaml
CFG=$FAULTLAB_CONFIG
RUN=data/runs/yash_live
mkdir -p "$RUN"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
apply() { (cd infra && terraform apply -auto-approve -input=false -no-color \
  -var="tracing_mode=$1" -var="log_level=$2" -var="sampling_fixed_rate=$3" -var="sampling_reservoir=$4" >/dev/null) ; }
api() { (cd infra && terraform output -raw api_url); }
step() { local name=$1; shift; if [ -f "$RUN/done_$name" ]; then echo "skip $name"; return 0; fi
  echo "== $name start $(stamp)"; "$@" && touch "$RUN/done_$name" && echo "== $name done $(stamp)" || { echo "!! $name failed $(stamp)"; exit 1; }; }

# legs 1+3a: detection and localisation (full tracing, INFO logs)
step apply_full apply Active INFO 1 1000
URL=$(api)
step calibration $PY tools/campaign_driver.py --phase calibration --url "$URL" --run "$RUN" --config "$CFG"
step steady      $PY tools/campaign_driver.py --phase steady      --url "$URL" --run "$RUN" --config "$CFG"
step peak        $PY tools/campaign_driver.py --phase peak        --url "$URL" --run "$RUN" --config "$CFG"
step collect     $PY tools/pull_telemetry.py --run "$RUN"

# leg 3b: overhead, 3 repetitions, order rotated
declare -A P=( [full]="Active INFO 1 1000" [policy]="Active ERROR 0.05 1" [off]="PassThrough ERROR 0.05 1" )
ORDERS=("full policy off" "policy off full" "off full policy")
for r in 1 2 3; do
  OR="data/runs/yash_overhead_r$r"; mkdir -p "$OR"
  for c in ${ORDERS[$((r-1))]}; do
    step "oh_r${r}_${c}_apply" apply ${P[$c]}
    URL=$(api)
    step "oh_r${r}_${c}_load" $PY tools/campaign_driver.py --phase "overhead-$c" --url "$URL" --run "$OR" --config "$CFG"
    sleep 150   # log delivery and trace indexing before measuring
    step "oh_r${r}_${c}_collect" $PY tools/pull_overhead.py --run "$OR" --condition "$c" --config "$CFG"
  done
done
# leave the stack in the rule-arm policy configuration (cheapest traced mode)
step final_policy apply Active ERROR 0.05 1
echo "ALL_DONE $(stamp)"
