#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec bash tools/check_reproducibility.sh "$@"
