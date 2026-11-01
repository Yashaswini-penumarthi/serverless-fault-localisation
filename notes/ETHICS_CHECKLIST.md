# Ethics and acceptable-use checklist

The NCI ethics declaration is the student's own form; this checklist lists the
facts it should rest on.

## Scope

- [ ] No human participants and no personal data: every order, customer and SKU is made up by seeded generators
- [ ] Public benchmark: RCAEval (Pham et al. 2025), used under its MIT licence with attribution
- [ ] Faults only in the researcher's own AWS account, on resources created for this study (tag `student = 24262404`)
- [ ] No third-party systems are tested or loaded

## Acceptable use and budget

- [ ] Load stays at 2 rps (steady) and 10 rps (peak) - far below anything that strains shared capacity
- [ ] Faults are injected inside the service (sleep, raise, reserved concurrency 0 on own functions)
- [ ] `tools/cost_estimator.py` run before the campaign; optional monthly budget e-mail (`AlertEmail`)
- [ ] The number of runs is fixed by the power calculation, not by the budget

## Reporting integrity

- [ ] Thresholds calibrated once on fault-free data and frozen (SHA-256 checked); never retuned on evaluation data
- [ ] Xing et al. (2025) reported as a ceiling under their own data conditions, not as a defeated competitor
- [ ] False positives from the control periods reported next to the detections
- [ ] Single provider, single topology and synthetic traffic stated with every summary
- [ ] Negative results and refuted expectations reported as they are
- [ ] Any undocumented platform behaviour found is reported to AWS before it appears in the write-up

## Before and after

- [ ] No credentials in the repository (`.env.example` only)
- [ ] `sam delete` after the last run; console checked for the log groups, sampling rule and budget

Yashaswini Penumarthi (24262404)
