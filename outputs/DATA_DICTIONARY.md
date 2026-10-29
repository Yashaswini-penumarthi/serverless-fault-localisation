# Data and result files

## Live run folder (`data/runs/live/`, gitignored until the campaign is done)

| file | written by | content |
|---|---|---|
| `windows.json` | tools/campaign_driver.py | time range of every phase (calibration, steady / peak control + campaign, overhead-*) |
| `schedule_<load>.jsonl` | tools/campaign_driver.py | the injections, written before the first one: id, load, fault, target, start, end, parameters |
| `applied_<load>.jsonl` | injector/injector.py | the same with the real `applied_at` / `cleared_at` |
| `requests_<phase>.csv` | traffic/open_loop_client.py | t_sent, kind, status, latency_ms, trace_id, error |
| `series.parquet` | tools/pull_telemetry.py | per-minute `<service>/ErrorRate`, `ThrottleRate`, `Duration` |
| `spans.jsonl.gz` | tools/pull_telemetry.py | flat spans: trace_id, span_id, parent_id, service, start, end, fault, throttle |
| `overhead_<condition>.json` | tools/pull_overhead.py | requests, log bytes, traces recorded, bytes per 1000 requests, latency, policy |

## Results

| folder | from | content |
|---|---|---|
| `outputs/rcaeval/raw/<method>/<case>.json` | baseline_runner/ | ranking (services), time, error per case and method |
| `outputs/rcaeval/` | eval/benchmark_eval.py | ranks.csv, localisation*.csv, comparisons.csv, detection.json, top3_expectation.json, summary.md |
| `outputs/live/` | eval/live_campaign_eval.py, tools/pull_overhead.py | per_injection.csv, false_positives.csv, rig_summary.csv, control_false_positives.csv, thresholds.json, trace_calibration.json, overhead.json, summary.md |
| `outputs/sim/` | eval/live_campaign_eval.py --source sim | the same files from simulated telemetry - a functional check, NOT AWS data |

Figures go to `figures/<source>/`.

Yashaswini Penumarthi (24262404)
