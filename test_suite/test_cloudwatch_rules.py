import numpy as np
import pandas as pd
import pytest
import yaml

from detector import rules

RULES = yaml.safe_load(open("protocols/experiment.yaml"))["detector"]["rules"]
T0 = pd.Timestamp("2026-01-01T00:00:00Z")


def minutes(values, start=T0):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="1min"), dtype=float)


def quiet(n=24 * 60, mean=100.0, sd=4.0, seed=1):
    return minutes(np.random.default_rng(seed).normal(mean, sd, n))


def cfg_for(series):
    return rules.calibrate(series, RULES, meta={"calibration": "test"})


def test_calibration_records_p99_and_is_frozen():
    cfg = cfg_for({"inventory/Duration": quiet()})
    st = cfg["series"]["inventory/Duration"]
    assert st["n"] == 1440 and 105 < st["threshold"] < 115
    cfg["series"]["inventory/Duration"]["threshold"] = 1e9  # "retuning" after the fact
    with pytest.raises(ValueError, match="frozen"):
        rules.detect({"inventory/Duration": quiet(60)}, cfg)


def test_quiet_series_rarely_fires():
    cfg = cfg_for({"inventory/Duration": quiet()})
    out = rules.detect({"inventory/Duration": quiet(600, seed=2)}, cfg)
    assert out["fired"].mean() < 0.03  # ~1 % above p99, plus a few 3-sigma minutes


def test_step_change_fires_in_its_first_minute():
    cfg = cfg_for({"payments/Duration": quiet()})
    s = quiet(60, seed=3)
    s.iloc[40:] += 200  # a 200 ms delay starts at minute 40
    out = rules.detect({"payments/Duration": s}, cfg).set_index("time")
    assert not out["fired"].iloc[:40].any() or out["fired"].iloc[:40].sum() <= 1
    assert out["fired"].iloc[40] and "sigma" in out["rule"].iloc[40]


def test_fired_minutes_do_not_raise_the_baseline():
    cfg = cfg_for({"payments/Duration": quiet()})
    s = quiet(90, seed=4)
    s.iloc[40:80] += 200  # a long fault keeps firing instead of becoming "normal"
    out = rules.detect({"payments/Duration": s}, cfg)
    assert out["fired"].iloc[40:80].all()


def test_zero_error_series_fires_on_errors_via_the_floor():
    cfg = cfg_for({"inventory/ErrorRate": minutes(np.zeros(1440))})
    s = minutes(np.zeros(40))
    s.iloc[30] = 0.2
    out = rules.detect({"inventory/ErrorRate": s}, cfg)
    assert out["fired"].sum() == 1 and out["fired"].iloc[30]


def test_threshold_rule_catches_a_slow_drift_the_baseline_absorbs():
    cfg = cfg_for({"orders_api/Duration": quiet(sd=1.0)})
    s = minutes(np.linspace(100, 130, 120))  # 0.25 ms per minute: never 3 sd above the recent baseline
    out = rules.detect({"orders_api/Duration": s}, cfg)
    assert out["fired"].any() and set(out.loc[out["fired"], "rule"]) <= {"threshold", "sigma+threshold"}


def test_episodes_merge_consecutive_minutes_and_use_the_minute_end():
    cfg = cfg_for({"a/Duration": quiet(), "b/Duration": quiet(seed=9)})
    sa, sb = quiet(60, seed=5), quiet(60, seed=6)
    sa.iloc[20:23] += 300
    sb.iloc[21] += 300
    sa.iloc[50] += 300
    eps = rules.episodes(rules.detect({"a/Duration": sa, "b/Duration": sb}, cfg))
    big = [e for e in eps if e["start"] == T0 + pd.Timedelta(minutes=20)][0]
    assert big["detected_at"] == T0 + pd.Timedelta(minutes=21) and big["services"] == ["a", "b"]
    assert any(e["start"] == T0 + pd.Timedelta(minutes=50) for e in eps)


def test_metric_ranking_orders_services_by_largest_z():
    cfg = cfg_for({"a/Duration": quiet(), "b/Duration": quiet(seed=9)})
    sa, sb = quiet(60, seed=5), quiet(60, seed=6)
    sa.iloc[30:32] += 60
    sb.iloc[30:32] += 300
    m = rules.detect({"a/Duration": sa, "b/Duration": sb}, cfg)
    assert rules.metric_ranking(m, T0 + pd.Timedelta(minutes=30), T0 + pd.Timedelta(minutes=32)) == ["b", "a"]


def test_uncalibrated_series_is_refused():
    cfg = cfg_for({"a/Duration": quiet()})
    with pytest.raises(KeyError):
        rules.detect({"zzz/Duration": quiet(30)}, cfg)
