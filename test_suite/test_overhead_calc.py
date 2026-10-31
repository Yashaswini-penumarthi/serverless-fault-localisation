import numpy as np
import pandas as pd

from eval import telemetry_cost as overhead

PRICES = {"logs_ingest_per_gb": 0.57, "xray_traces_recorded": 0.000005}


def requests(tmp_path, name, mean_ms, n=400, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"t_sent": np.arange(n) / 2, "kind": "create", "status": 201,
                       "latency_ms": rng.lognormal(np.log(mean_ms), 0.2, n), "trace_id": "", "error": ""})
    p = tmp_path / f"{name}.csv"
    df.to_csv(p, index=False)
    return p


def test_volume_reduction_and_cost(tmp_path):
    full = overhead.condition(requests(tmp_path, "full", 80), log_bytes=400 * 900, traces_recorded=400,
                              trace_bytes_mean=2500, policy={"sampling": "100 %", "logs": "INFO"})
    pol = overhead.condition(requests(tmp_path, "pol", 80), log_bytes=400 * 20, traces_recorded=40,
                             trace_bytes_mean=2500, policy={"sampling": "1/s + 5 %", "logs": "ERROR"})
    assert full["bytes_per_1000_requests"] == 1000 * (900 + 2500)
    assert 0.9 < overhead.reduction(pol, full) < 1.0
    c = overhead.cost_per_million(full, PRICES)
    assert abs(c["xray_recorded_usd"] - 5.0) < 1e-9 and c["total_usd"] > c["logs_ingest_usd"]


def test_latency_effect_detects_a_tracing_cost(tmp_path):
    r = overhead.latency_effect(requests(tmp_path, "on", 90, seed=1), requests(tmp_path, "off", 80, seed=2))
    assert r["p"] < 0.05 and r["median_diff"] > 0 and r["hypothesis"].startswith("H0")


def test_learned_lower_bound_from_case_files(tmp_path):
    case = tmp_path / "re2ob_cartservice_delay_1"
    case.mkdir()
    pd.DataFrame({"traceID": ["a", "a", "b"], "x": [1, 2, 3]}).to_parquet(case / "traces.parquet")
    pd.DataFrame({"time": [1, 2]}).to_parquet(case / "metrics.parquet")
    lb = overhead.learned_lower_bound(tmp_path)
    assert lb["requests"].iloc[0] == 2 and lb["bytes_per_1000_requests"].iloc[0] > 0
