# Assumptions and implementation decisions

| # | decision | consequence |
|---|---|---|
| A1 | Faults are switched by one SSM parameter that every function reads with a 2 s cache (`FAULT_CACHE_S`). | A fault starts up to 2 s after its scheduled start and stops exactly at `until`. Delays are measured from the scheduled start, so they are slightly pessimistic. |
| A2 | Targets raise injected failures unhandled. | Lambda's Errors metric and X-Ray see them; orders-api catches the downstream error and answers 502, so its own Errors metric does not move. |
| A3 | orders-api calls inventory and payments synchronously with a 1 s client deadline and no SDK retries; notifications is invoked asynchronously. | A slow dependency surfaces once at the caller; an asynchronous fault never shows at the entry, which the ranker's span-level latency rule covers. |
| A4 | CloudWatch series are rates (Errors / Invocations, Throttles / (Invocations + Throttles)) plus average Duration, per minute. | The rules compare steady and peak load on the same scale. |
| A5 | Thresholds come from 24 h fault-free at steady load and are frozen. | Peak load is judged against steady-load thresholds - its false-positive floor is measured in its own control period. |
| A6 | Detection is known at the end of the fired minute. | Delays have a 60 s resolution on the rig (10 s on RCAEval). |
| A7 | The ranker uses only traces from the 120 s up to the detection time. | With the default sampling (1/s + 5 %) that is roughly 120-170 traces at 2 rps. |
| A8 | X-Ray sampling is decided by API Gateway using the stack's sampling rule and passed to the functions. | The rule matches every service in the account ("*"): use an account kept for the study. |
| A9 | RCAEval baselines run in their own venv with RCAEval 1.7.0 on the Hugging Face Parquet copy. | Their pinned old dependencies (numpy < 2, torch, ...) stay out of the project; trace methods get microsecond inject times, as their code expects. |
| A10 | RCAEval service names: "frontendservice" in traces is mapped to "frontend"; ranks of metrics or operations are reduced to their service. | Same service-level evaluation as RCAEval's own. |
| A11 | Learned-arm overhead is a lower bound: compressed benchmark telemetry per request. | Parquet is smaller than shipped telemetry and the benchmark's system is not this one - an order of magnitude, not a measurement. |
| A12 | The simulator (`sim/telemetry.py`) invents all its numbers. | `outputs/sim` only shows the pipeline works; nothing from it is a result. |
| A13 | Synthetic data everywhere: made-up customers, SKUs and orders from seeded generators. | No personal data; the benchmark data is public under the MIT licence. |

Yashaswini Penumarthi (24262404)
