"""The rule arm on a synthetic case in RCAEval's layout (the real cases are ~10 MB each and not in git)."""
import numpy as np
import pandas as pd
import yaml

from baseline_runner import rcaeval_data as rd
from baseline_runner import rule_arm
from baseline_runner.run_baselines import to_services

EXP = yaml.safe_load(open("protocols/experiment.yaml"))
INJ = 1_705_666_511
SERVICES = ["frontend", "checkoutservice", "paymentservice", "emailservice"]


def fake_case(tmp_path, root="paymentservice", delay_s=0.4, traces=True, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(INJ - 720, INJ + 721)
    m = {"time": t}
    for s in SERVICES:
        lat = rng.normal(0.05, 0.004, len(t))
        if s in (root, "checkoutservice", "frontend"):  # the delay shows up along the call chain
            lat = lat + np.where(t >= INJ, delay_s, 0.0)
        m[f"{s}_latency-90"] = lat
        m[f"{s}_latency-50"] = lat / 2
        m[f"{s}_cpu"] = rng.normal(0.3, 0.01, len(t))
    folder = tmp_path / f"re2ob_{root}_delay_1"
    folder.mkdir()
    pd.DataFrame(m).to_parquet(folder / "metrics.parquet")
    (folder / "inject_time.txt").write_text(f"{INJ}\n")
    if traces:
        rows = []
        for k, ts in enumerate(range(INJ - 600, INJ + 300, 2)):
            slow = delay_s if ts >= INJ else 0.0
            tid, base = f"t{k}", ts * 1_000_000
            rows += [
                (tid, "a", None, "frontendservice", base, int((0.08 + slow) * 1e6), 0),
                (tid, "b", "a", "checkoutservice", base + 1000, int((0.06 + slow) * 1e6), 0),
                (tid, "c", "b", "paymentservice", base + 2000, int((0.02 + slow) * 1e6), 0),
                (tid, "d", "b", "emailservice", base + 3000, int(0.01 * 1e6), 0),
            ]
        tr = pd.DataFrame(rows, columns=["traceID", "spanID", "parentSpanID", "serviceName", "startTime", "duration",
                                         "statusCode"])
        tr.to_parquet(folder / "traces.parquet")
    return folder


def test_prepare_and_window_follow_rcaeval_main(tmp_path):
    c = rd.load(fake_case(tmp_path))
    m = rd.window(rd.prepare_metrics(c["metrics"]), c["inject_time"], 600)
    assert len(m) == 1200 and not any(col.endswith("-50") for col in m.columns)
    assert "paymentservice_latency" in m and rd.services_of(m.columns) == sorted(SERVICES)
    assert rd.root_cause("re2ob_checkoutservice_delay_1") == "checkoutservice" and rd.fault_of("re2ob_x_loss_3") == "loss"


def test_rule_arm_detects_and_localises_from_traces(tmp_path):
    res = rule_arm.run_case(fake_case(tmp_path), EXP)
    assert res["detected"] and 0 <= res["delay_s"] <= EXP["rcaeval"]["bucket_s"] * 2
    # p99 of 30 calibration buckets is close to their maximum, so a few of the 30 control buckets
    # cross it by chance - that is the false-positive floor the analysis reports, not a bug
    assert res["control_fp_episodes"] <= 6
    assert res["ranking_source"] == "traces" and res["ranking"][0] == "paymentservice"


def test_rule_arm_falls_back_to_metrics_without_traces(tmp_path):
    res = rule_arm.run_case(fake_case(tmp_path, traces=False), EXP)
    assert res["ranking_source"] == "metrics" and set(res["ranking"]) == set(SERVICES)


def test_baseline_ranks_become_services():
    assert to_services(["checkoutservice_latency", "checkoutservice_cpu", "frontendservice_GetAds", "redis-db_mem"]) == \
        ["checkoutservice", "frontend", "redis"]
