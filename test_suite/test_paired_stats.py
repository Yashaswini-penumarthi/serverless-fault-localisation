import numpy as np
import pytest
from statsmodels.stats.contingency_tables import mcnemar as sm_mcnemar
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

from eval import nonparametric_tests as stats


def test_wilson_matches_statsmodels_and_contains_p():
    for k, n in [(0, 60), (60, 60), (17, 40)]:
        p, lo, hi = stats.wilson(k, n)
        elo, ehi = proportion_confint(k, n, method="wilson")
        assert lo == pytest.approx(elo, abs=1e-9) and hi == pytest.approx(ehi, abs=1e-9) and lo <= p <= hi


def test_f1_from_counts():
    assert stats.f1_from([8], [2], [2]) == pytest.approx(0.8)


def test_paired_f1_finds_a_real_gap_and_not_a_fake_one():
    rng = np.random.default_rng(3)
    n = 240
    a = np.column_stack([rng.random(n) < 0.95, np.zeros(n), np.zeros(n)]).astype(float)
    a[:, 2] = 1 - a[:, 0]
    b = np.column_stack([rng.random(n) < 0.75, np.zeros(n), np.zeros(n)]).astype(float)
    b[:, 2] = 1 - b[:, 0]
    r = stats.paired_f1(a, b, B=500, perms=2000)
    assert r["diff"] > 0.05 and r["p"] < 0.01 and r["ci_lo"] > 0
    same = stats.paired_f1(a, a.copy(), B=200, perms=500)
    assert same["diff"] == 0 and same["p"] == 1.0


def test_mcnemar_matches_statsmodels_exact():
    a = np.array([1] * 30 + [0] * 10 + [1] * 5 + [0] * 15, bool)
    b = np.array([1] * 30 + [1] * 10 + [0] * 5 + [0] * 15, bool)
    r = stats.mcnemar(a, b)
    ref = sm_mcnemar([[30, 5], [10, 15]], exact=True).pvalue
    assert r["a_only"] == 5 and r["b_only"] == 10 and r["p"] == pytest.approx(ref)


def test_compare_picks_the_test_by_normality():
    rng = np.random.default_rng(1)
    assert stats.compare(rng.normal(10, 1, 200), rng.normal(10.6, 1, 200))["test"] == "welch_t"
    r = stats.compare(rng.lognormal(0, 1, 200), rng.lognormal(0.7, 1, 200))
    assert r["test"] == "mann_whitney_u" and r["effect"] < 0 and r["p"] < 0.05


def test_holm_matches_statsmodels():
    p = [0.01, 0.04, 0.03, 0.2]
    assert np.allclose(stats.holm(p), multipletests(p, method="holm")[1])


def test_replication_check_for_ten_points():
    # 240 injections per arm (60 x 4 types) is enough for a 10 pp gap around 85-95 %, 60 per type alone is not
    n = stats.n_two_proportions(0.90, 0.80)
    assert 150 < n < 240
    assert stats.n_two_proportions(0.90, 0.80) > 60


def test_compare_paired_preserves_case_pairing():
    x = np.array([3.0, 4.0, 2.0, 5.0, 3.0, 4.0, 3.0, 5.0, 4.0, 3.0])
    y = np.array([1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 2.0, 2.0, 1.0, 1.0])
    r = stats.compare_paired(x, y, B=400, perms=1000, seed=0)
    assert r["test"] == "wilcoxon_signed_rank" and r["mean_diff"] > 1.5 and r["p"] < 0.01 and r["p_perm"] < 0.01
    same = stats.compare_paired(x, x.copy(), B=200, perms=500, seed=0)
    assert same["test"] == "none (all equal)" and same["p"] == 1.0
