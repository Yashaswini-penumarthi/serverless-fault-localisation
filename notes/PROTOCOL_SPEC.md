# Analysis plan

Defined prior to calibration. Changes
made after that go in the **Amendments** table with a date and a reason.

## Question and protocol

What accuracy-overhead position does rule-based detection (CloudWatch) and
localisation (X-Ray) take relative to learned baselines in AWS serverless
microservices? Three legs, because the two kinds of method cannot run on the
same inputs:

1. **Ceiling (citation):** Xing et al. (2025) - detection F1 93.8 %, accuracy
   95.4 %, localisation precision 87.6 % with labelled training data. Quoted as
   the ideal-data ceiling, never as a same-rig competitor.
2. **Like-for-like (RCAEval):** the rule arm and reproducible RCAEval baselines
   on identical cases (`eval/benchmark_eval.py`).
3. **Overhead (live AWS rig):** telemetry volume, latency with tracing on / off
   and cost for the rule arm; the learned arm only bounded from below
   (`eval/telemetry_cost.py`). The asymmetry is stated with every overhead figure.

## Units, variables, replication

- Rig: the unit is one injection (60 s on, 300 s off). IVs: fault type (4),
  load level (steady 2 rps, peak 10 rps); 60 injections per type per load,
  evenly over the three targets (480 in total), seeded random order. A 60-min
  fault-free control before each campaign gives the false-positive floor.
- RCAEval: the unit is one failure case (RE2-OB, 90 cases: 6 faults x 5
  services x 3 repetitions). Every method sees the same 600 s before / after
  the injection (RCAEval main.py).
- Power: `eval/stats.n_two_proportions(0.9, 0.8)` = 199 per group, so the
  240 injections per load level detect a 10 pp F1 difference at about 80 %
  power when pooled over fault types; per fault type (60) they do not, and
  per-type results are read as descriptive.

## Measures

| measure | definition |
|---|---|
| precision / recall / F1 | TP = injection with a fired minute known inside [start, end + 120 s]; FP = alarm episode matching no injection; FN = missed injection (`eval/detection_scoring.py`) |
| detection delay | first detection time minus scheduled start (median, p95) - includes up to 2 s of fault-switch cache |
| top-1 / top-3 / mean rank | rank of the injected target in the ranking at detection time; also "end to end" with misses counted as rank > 3 |
| AC@k, Avg@k | RCAEval's definitions on the benchmark (service level) |
| telemetry volume | log bytes + trace bytes per 1000 requests, per condition |
| added latency | end-to-end latency, tracing on minus off (median, p95) |
| cost | volume x dated list prices (`protocols/prices.yaml`), per million requests |

## Hypotheses and tests (alpha 0.05, Holm-Bonferroni per family)

- H0 (detection): F1 does not differ between the rule arm and the baselines on
  common data. Paired permutation test + paired bootstrap CI on per-case
  (TP, FP, FN) - **see the open decision below**.
- H0 (localisation): top-k does not differ. McNemar exact per baseline (top-1,
  top-3), rank comparison by Shapiro-Wilk then Welch t (Cohen's d) or
  Mann-Whitney U (rank-biserial); family = 3 tests x 4 baselines.
- H0 (overhead): tracing does not change end-to-end latency. Shapiro-Wilk, then
  Welch t or Mann-Whitney U; medians and p95 reported.

A paired or two-sample test is used for continuous outcomes
(delay, rank, latency). F1 and top-k are built from per-case binary outcomes on
the same cases, so they get the paired tests above.

## Pre-committed expectations and decision rules

| expectation | rule |
|---|---|
| rule arm concedes at most 10 pp of F1 vs the strongest reproducible baseline | 95 % CI of the F1 gap entirely below 10 pp = supported, entirely above = refuted, else inconclusive |
| rule arm concedes more than 10 pp in top-3 | paired bootstrap CI of AC@3(best baseline) - AC@3(rule arm): lower bound > 10 pp = supported, upper < 10 pp = refuted, else inconclusive |
| rule policy cuts telemetry volume by at least half vs full tracing / logging | 1 - volume(policy) / volume(full) >= 0.5 |
| competitive | F1 within 10 pp of the strongest baseline **and** volume at least halved |

## Open decision for the student and supervisor

RCAEval's baselines are localisers: they are handed the injection time and do
not detect. So "F1 vs the strongest reproducible baseline on common data" has no
detector to compare against in the harness as published. The artefact reports
the rule arm's detection F1 on RCAEval and on the rig, and its localisation
against the baselines. Before the write-up, decide one of: (a) restate the
detection expectation against the Xing ceiling (citation only), or (b) add a
detector baseline to the harness and document it. Neither is done silently here.

## Frozen before evaluation

- Rules: 30-min rolling baseline, 3 sd, p99 threshold, sd floors
  (`protocols/experiment.yaml`), calibrated once over 24 h fault-free and hashed
  (SHA-256); `rules.detect` refuses an edited config.
- Ranker: self-time and root p99 from the calibration traces; depth weight 0.5.
- RCAEval adaptation: 10 s buckets, 300 s calibration / 300 s control, windows
  scaled to the 600 s RCAEval provides; floors in seconds.

## Amendments

| date | change | reason |
|---|---|---|
| 2026-09-11 | REST API instead of HTTP API | HTTP APIs cannot be traced by X-Ray; end-to-end X-Ray tracing requires API Gateway REST API |
| 2026-09-11 | throttling = reserved concurrency 0 on the target | reserved concurrency 1 hardly throttles a 25 ms function at 2 rps, and reserving 1 is impossible on new accounts; 0 throttles every call and needs no spare account concurrency |
| 2026-09-11 | a trace also counts as failed when a span's self-time is above its p99 | found on simulated telemetry: faults in the asynchronous notifications service never reach the root span otherwise |
| 2026-09-11 | sd floor also applied to the p99 threshold; RCAEval floors in seconds | found while running one RCAEval case during development (re2ob_checkoutservice_delay_1), before the 90-case run: latencies there are in seconds, and nearly flat series fired on a single quantisation step |
| 2026-09-11 | baselines: BARO, CIRCA, TraceRCA on all 90 cases; CausalRCA on repetition 1 (30 cases) | narrowed early; CausalRCA took ~290 s per case on the development machine |
| 2026-09-11 | CIRCA and the other methods get the entry service's latency as SLI | RCAEval main.py passes the root cause's own latency, which leaks the answer |
| 2026-09-11 | sanity check: a method whose ranking is the same list on more than half of its cases is flagged and cannot be the "strongest baseline" | CausalRCA's first cases came back in input column order (alphabetical), which puts checkoutservice third by luck; the check is generic and does not change any method's numbers |

Yashaswini Penumarthi (24262404)
