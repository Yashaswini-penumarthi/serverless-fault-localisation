#!/usr/bin/env bash
# One-off setup: Python environment with the pinned versions and a check of the external tools.
# Usage: bash bootstrap_env.sh
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer needed"'
"$PYTHON" -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r pinned-packages.txt
if command -v terraform >/dev/null; then
    terraform version | head -1
else
    echo "note: terraform not found (needed only for live AWS deployment; offline check_reproducibility.sh and rebuild_tables.sh work without it)"
fi
.venv/bin/python -c "import boto3, numpy, pandas, scipy, matplotlib, yaml, pyarrow; print('python packages ok')"
echo "setup done. Run 'bash check_reproducibility.sh' for offline verification, or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY and AWS_REGION=eu-west-1 for live AWS runs."
