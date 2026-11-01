# Demo script (5-10 minutes)

Steps for the viva / demo. Have the stack deployed and the
calibration done beforehand; the numbers on screen come from your own runs.

1. **Architecture (1 min).** Show the diagram in README.md: API Gateway -> orders-api ->
   inventory, payments (sync) and notifications (async), DynamoDB, CloudWatch, X-Ray.
2. **Normal traffic (1 min).** Start a short load:
   `python -m traffic.open_loop_client --url $API_URL --rps 2 --minutes 8 --out /tmp/demo.csv`
   and show the X-Ray service map with all nodes green.
3. **Inject one fault (1 min).** In a second terminal:
   `python -m injector.injector --schedule demo/schedule.jsonl --stack faultlab`
   with a one-line schedule, e.g. elevated latency on payments for 60 s
   (make it with `injector.schedule.write`).
4. **Detection (2 min).** After the minute ends, run the detector on the last 40 minutes
   (`tools/pull_telemetry.py` + `python -m eval.live_campaign_eval --source live`) or show the
   fired series in CloudWatch: payments Duration crossing its frozen threshold.
5. **Localisation (1 min).** Show the ranking for the detection window - payments first -
   and the X-Ray trace with the long payments segment.
6. **Results (2 min).** `outputs/rcaeval/summary.md` (rule arm vs baselines on RCAEval) and,
   after the campaign, `outputs/live/summary.md` and `outputs/live/overhead.json`.
7. **Limits (1 min).** One account, one region, one topology, synthetic traffic; Xing et al.
   is a ceiling, not a same-rig result; learned-arm overhead is only a lower bound.

Yashaswini Penumarthi (24262404)
