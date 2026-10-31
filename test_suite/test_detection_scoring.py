import numpy as np
import pandas as pd

from eval import detection_scoring as metrics

T0 = pd.Timestamp("2026-01-01T00:00:00Z")


def at(minutes, seconds=0):
    return T0 + pd.Timedelta(minutes=minutes, seconds=seconds)


def injections():
    return pd.DataFrame([
        {"id": 1, "fault": "timeout", "target": "inventory", "start": at(10, 30), "end": at(11, 30)},
        {"id": 2, "fault": "elevated_latency", "target": "payments", "start": at(16, 30), "end": at(17, 30)},
        {"id": 3, "fault": "throttling", "target": "payments", "start": at(22, 30), "end": at(23, 30)},
    ])


def test_prf():
    r = metrics.prf(8, 2, 2)
    assert r["precision"] == 0.8 and r["recall"] == 0.8 and abs(r["f1"] - 0.8) < 1e-12
    assert metrics.prf(0, 0, 5)["f1"] == 0.0


def test_matching_uses_the_end_of_the_fired_minute():
    fired = pd.Series([at(10), at(17), at(30)])  # minute 10 holds the first 30 s of injection 1
    eps = [{"start": at(10), "end": at(11)}, {"start": at(17), "end": at(18)}, {"start": at(30), "end": at(31)}]
    per, fps = metrics.match(fired, eps, injections(), tolerance_s=120)
    per = per.set_index("injection_id")
    assert per.loc[1, "detected"] and per.loc[1, "delay_s"] == 30.0  # known at 11:00, fault began 10:30
    assert per.loc[2, "detected"] and per.loc[2, "delay_s"] == 90.0
    assert not per.loc[3, "detected"] and np.isnan(per.loc[3, "delay_s"])
    assert [e["start"] for e in fps] == [at(30)]


def test_detection_after_the_tolerance_is_a_miss_and_a_false_positive():
    fired = pd.Series([at(14)])  # known at 15:00, injection 1 window closed at 13:30
    per, fps = metrics.match(fired, [{"start": at(14), "end": at(15)}], injections().iloc[:1], tolerance_s=120)
    assert not per["detected"].iloc[0] and len(fps) == 1


def test_ranks_and_localisation_scores():
    assert metrics.rank_of(["payments", "inventory"], "inventory") == 2
    assert metrics.rank_of(["payments"], "notifications") == 2  # missing = after the last
    loc = metrics.localisation([1, 2, 4, 1])
    assert loc["top1"] == 0.5 and loc["top3"] == 0.75 and loc["mean_rank"] == 2.0
    assert abs(metrics.avg_at_k([1, 2, 6], 5) - np.mean([1 / 3, 2 / 3, 2 / 3, 2 / 3, 2 / 3])) < 1e-12


def test_delay_summary():
    d = metrics.delays(pd.Series([30, 60, 90, np.nan]))
    assert d["n"] == 3 and d["median_s"] == 60
