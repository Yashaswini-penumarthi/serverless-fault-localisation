# data

- `rcaeval/` - RCAEval cases downloaded by `make rcaeval-data` (Hugging Face Parquet copy, MIT licence). Not in git (~0.9 GB for RE2-OB).
- `runs/live/` - the live runs (see `results/SCHEMA.md`). Commit them after the campaign; they hold only synthetic orders.
- `runs/sim/` - not used by default (the simulator runs in memory).

No personal data anywhere: customers, SKUs and orders are made up by seeded generators.

Yashaswini Penumarthi (24262404)
