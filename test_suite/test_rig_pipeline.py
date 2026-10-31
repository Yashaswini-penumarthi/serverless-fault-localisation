"""The rig pipeline end to end on simulated telemetry (plumbing only, not AWS behaviour)."""
import pandas as pd
import pytest
import yaml

from eval import live_campaign_eval as rig
from sim import telemetry

EXP = yaml.safe_load(open("protocols/experiment.yaml"))


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    series, windows, spans, campaigns = rig.run_sim(EXP, per_type=3, calibration_hours=4)
    res = rig.evaluate(series, spans, campaigns, windows, EXP)
    out = tmp_path_factory.mktemp("rig")
    written = rig.write(res, windows, out / "res", out / "fig", "sim")
    return res, written, out


def test_every_injection_is_scored(result):
    res, written, _ = result
    per = res["per_injection"]
    assert len(per) == 2 * 4 * 3 and set(per["load"]) == {"steady", "peak"}
    assert per["detected"].mean() > 0.9 and (per.loc[per["detected"], "delay_s"] >= 0).all()


def test_localisation_finds_the_target_including_the_async_one(result):
    res, _, _ = result
    per = res["per_injection"].dropna(subset=["rank"])
    assert (per["rank"] <= 3).mean() == 1.0
    notif = per[per["target"] == "notifications"]
    assert len(notif) and (notif["rank"] == 1).mean() >= 0.75  # needs the span-level latency rule


def test_thresholds_are_frozen_and_outputs_labelled(result):
    res, written, out = result
    assert len(res["thresholds"]["sha256"]) == 64
    text = (out / "res" / "summary.md").read_text()
    assert "NOT AWS data" in text and "not same-rig" in text
    assert "No positioning is computed here" in text and "pp gap" not in text  # sim numbers never sit next to Xing
    assert text.rstrip().splitlines()[-1] == rig.SIGN
    assert (out / "fig" / "rig_detection_localisation.png").stat().st_size > 5000
    assert set(written["control"]["load"]) == {"steady", "peak"}


def test_simulated_faults_move_the_right_series():
    inj = [{"id": "x", "fault": "throttling", "target": "payments", "start": 600.0, "end": 660.0}]
    s = telemetry.series(inj, 0.0, 1200.0, seed=3)
    thr = s["payments/ThrottleRate"]
    assert thr[pd.Timestamp(600, unit="s", tz="UTC")] == 1.0 and thr.drop(pd.Timestamp(600, unit="s", tz="UTC")).max() == 0
