# Fault taxonomy - the rig and RCAEval side by side

Frozen before calibration. The rig can only inject what a Lambda service can suffer and
CloudWatch / X-Ray can see; RCAEval's container faults partly have no serverless
counterpart, and that gap is reported, not hidden.

| rig fault | how it is injected (injector/, lambdas/layer/faultlab/fault.py) | signal it leaves | closest RCAEval fault |
|---|---|---|---|
| `elevated_latency` | the target sleeps 300-800 ms (drawn per injection) before working normally | Duration of the target and of its synchronous caller; span self-time | `delay` |
| `timeout` | the target holds the call 1.5 s, past orders-api's 1 s client deadline | target Duration, caller gives up (504); long span self-time | `delay` (large), `loss` |
| `dependency_failure` | the target raises an unhandled error | Lambda Errors of the target; caller answers 502; faulted span | `loss` (requests fail) |
| `throttling` | reserved concurrency of the target set to 0 for 60 s | Lambda Throttles; throttled Invoke subsegment (429) | `socket` (connection exhaustion) |
| - | not possible on Lambda: no per-function CPU, memory or disk metric | - | `cpu`, `mem`, `disk` |

In RE2-OB (90 cases) the rig-comparable faults are `delay`, `loss` and `socket`
(45 cases). eval/benchmark_eval.py reports every fault type separately, so results can be
read for the comparable half and for the whole benchmark.

Targets on the rig: inventory, payments (called synchronously) and
notifications (called asynchronously). orders-api is the entry service and is
not a target.

Yashaswini Penumarthi (24262404)
