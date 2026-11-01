# serverless-fault-localisation

**Author:** Yashaswini Penumarthi (Student ID: 24262404, `x24262404@student.ncirl.ie`)  
**Programme:** MSc in Cloud Computing, National College of Ireland  
**Supervisor:** Dr Aqeel Kazmi  
**Repository:** https://github.com/Yashaswini-penumarthi/serverless-fault-localisation

Training-free fault detection (CloudWatch per-minute error, throttle, and duration series) and causal-cutoff dependency localisation (AWS X-Ray traces) for an AWS serverless order-processing microservice topology, evaluated across a three-stage protocol (`notes/PROTOCOL_SPEC.md`):

1. **Stage 1 (Literature Ceiling)** - Published supervised upper bound from Xing et al. (2025) (`0.933` table F1 / `0.938` abstract F1), cited strictly under its own labelled training-data assumptions.
2. **Stage 2 (Common-Data Benchmark on RCAEval RE2-OB)** - Deterministic evaluation of the frozen rule detector and X-Ray ranker (`baseline_runner/rule_arm.py`) against reproducible RCAEval 1.7.0 baselines (`CIRCA`, `BARO`, `TraceRCA`, `Hybrid` Isolation Forest, plus a 4-case `CausalRCA` feasibility check) across all 90 public RE2-OB benchmark cases bundled in `data/rcaeval/` (`outputs/rcaeval/`).
3. **Stage 3a & 3b (Live AWS Fault Campaign and Telemetry Overhead in `eu-west-1`)** - 60-minute fault-free calibration, 20-minute control period, and 24 steady-load injected faults (`data/runs/yash_live_steady/`, `outputs/yash_live_steady/`), paired with Latin-square multi-repetition telemetry volume, latency, and cloud cost measurements across `full`, `policy`, and `off` tracing modes at 1 rps and 3 rps (`outputs/yash_overhead/`, `outputs/yash_overhead_3rps/`).

---

## Offline Verification (No AWS Credentials Required)

```bash
bash bootstrap_env.sh           # creates .venv and installs pinned-packages.txt (Python 3.12 or 3.13)
bash check_reproducibility.sh   # runs 79 unit/integration tests, re-executes baseline_runner.rule_arm
                                # on all 90 RE2-OB cases (90/90 exact match), and rebuilds all tables
```

---

## Repository Directory Map

```text
├── bootstrap_env.sh                 # one-command virtualenv setup from pinned-packages.txt
├── check_reproducibility.sh         # offline test + 90-case RE2-OB + table verification suite
├── execute_study.sh                 # full live AWS deployment, fault campaign, overhead & analysis driver
├── pinned-packages.txt              # exact pinned dependencies (Python 3.12 and 3.13)
├── lambdas/                         # 4 ARM64 Python 3.12 Lambda handlers + shared fault/tracing layer
│   ├── orders_api/handler.py        # synchronous entry point (1000 ms deadline, no SDK retries)
│   ├── inventory/handler.py         # synchronous stock reservation service (DynamoDB)
│   ├── payments/handler.py          # synchronous payment authorisation service
│   ├── notifications/handler.py     # asynchronous Event-invoked notification service
│   └── layer/faultlab/              # SSM fault hook (2 s cache), call wrapper, structured logging
├── infra/                           # Terraform 1.9.8 stack (stack_resources.tf, stack_inputs.tf,
│                                    # stack_outputs.tf, provider_versions.tf)
├── protocols/                       # frozen YAML experiment protocols and eu-west-1 list prices
├── detector/                        # SHA-256 frozen CloudWatch rules, X-Ray span merger & ranker
├── traffic/                         # open-loop Poisson/timetable HTTP traffic client
├── injector/                        # seeded fault schedule generator and SSM switch writer
├── tools/                           # bundle_lambdas.py, seed_inventory.py, campaign_driver.py,
│                                    # pull_telemetry.py, pull_overhead.py, rebuild_tables.sh
├── eval/                            # live_campaign_eval.py, benchmark_eval.py, trace_ablation.py,
│                                    # overhead_study.py, detection_scoring.py, nonparametric_tests.py
├── baseline_runner/                 # RE2-OB dataset loader, deterministic rule_arm & hybrid_arm
├── data/rcaeval/                    # compact 90-case RE2-OB bundle + sha256_manifest.json
├── data/runs/                       # archived raw AWS telemetry (requests, series, spans, schedules)
├── outputs/                         # regenerated CSV and JSON result tables cited in the report
├── notes/                           # protocol specification, fault mapping, design decisions, ethics
└── test_suite/                      # 79 unit and moto integration tests
```
