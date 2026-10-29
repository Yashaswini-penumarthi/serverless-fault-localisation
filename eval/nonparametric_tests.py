"""Statistics for the comparison (plan: notes/PROTOCOL_SPEC.md).

Continuous outcomes (detection delay, rank, end-to-end latency): Shapiro-Wilk
decides between Welch's t-test with Cohen's d and Mann-Whitney U with the
rank-biserial correlation. When two methods score the SAME
cases (e.g., per-case ranks on RCAEval or detected live injections), compare_paired()
preserves case pairing using the Wilcoxon signed-rank test (or paired t-test when
differences are normal), matched-pairs rank-biserial correlation, and a paired
sign-flip permutation p-value on the mean difference. F1 and top-k come from per-case
binary outcomes on the SAME cases for both arms, so they get paired tests: a
permutation test and a paired bootstrap interval for the F1 difference, and
McNemar's exact test for top-k. Holm-Bonferroni over the family.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as st

NAN = float("nan")


def wilson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float, float]:
    if n == 0:
        return NAN, NAN, NAN
    z, p = float(st.norm.ppf(1 - alpha / 2)), k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, min(p, max(0.0, c - h)), max(p, min(1.0, c + h))


def f1_from(tp, fp, fn) -> float:
    tp, fp, fn = float(np.sum(tp)), float(np.sum(fp)), float(np.sum(fn))
    return 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else NAN


def paired_f1(a: np.ndarray, b: np.ndarray, B: int = 2000, perms: int = 10000, seed: int = 0) -> dict:
    """a, b: per-case (tp, fp, fn) rows for the two arms, same cases in the same order."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    diff = f1_from(*a.T) - f1_from(*b.T)
    idx = rng.integers(0, len(a), (B, len(a)))
    boots = np.array([f1_from(*a[i].T) - f1_from(*b[i].T) for i in idx])
    swap = rng.random((perms, len(a))) < 0.5
    null = []
    for m in swap:
        pa, pb = np.where(m[:, None], b, a), np.where(m[:, None], a, b)
        null.append(f1_from(*pa.T) - f1_from(*pb.T))
    p = (np.sum(np.abs(null) >= abs(diff) - 1e-12) + 1) / (perms + 1)
    return {"f1_a": f1_from(*a.T), "f1_b": f1_from(*b.T), "diff": diff, "ci_lo": float(np.nanquantile(boots, 0.025)),
            "ci_hi": float(np.nanquantile(boots, 0.975)), "p": float(p), "test": "paired permutation"}


def mcnemar(hit_a, hit_b) -> dict:
    """Exact McNemar on paired hits (e.g. top-3) - only discordant cases carry information."""
    a, b = np.asarray(hit_a, bool), np.asarray(hit_b, bool)
    n01, n10 = int((~a & b).sum()), int((a & ~b).sum())
    p = float(st.binomtest(n10, n01 + n10, 0.5).pvalue) if n01 + n10 else 1.0
    return {"rate_a": float(a.mean()), "rate_b": float(b.mean()), "diff": float(a.mean() - b.mean()),
            "a_only": n10, "b_only": n01, "p": p, "test": "McNemar exact"}


def is_normal(x, alpha: float = 0.05, seed: int = 0) -> tuple[bool, float]:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 3 or np.ptp(x) == 0:
        return False, NAN
    if len(x) > 5000:
        x = np.random.default_rng(seed).choice(x, 5000, replace=False)
    p = float(st.shapiro(x).pvalue)
    return p >= alpha, p


def compare(x, y, alpha: float = 0.05, B: int = 2000, seed: int = 0) -> dict:
    x, y = np.asarray(x, float), np.asarray(y, float)
    x, y = x[~np.isnan(x)], y[~np.isnan(y)]
    nx, px = is_normal(x, alpha, seed)
    ny, py = is_normal(y, alpha, seed)
    rng = np.random.default_rng(seed)
    bx = x[rng.integers(0, len(x), (B, len(x)))]
    by = y[rng.integers(0, len(y), (B, len(y)))]
    dmed = np.median(bx, axis=1) - np.median(by, axis=1)
    out = {"median_x": float(np.median(x)), "median_y": float(np.median(y)), "p95_x": float(np.percentile(x, 95)),
           "p95_y": float(np.percentile(y, 95)), "median_diff": float(np.median(x) - np.median(y)),
           "ci_lo": float(np.quantile(dmed, 0.025)), "ci_hi": float(np.quantile(dmed, 0.975)),
           "shapiro_p_x": px, "shapiro_p_y": py}
    if np.ptp(np.concatenate([x, y])) == 0:
        return out | {"test": "none (all equal)", "p": 1.0, "effect": 0.0, "effect_name": "rank_biserial"}
    if nx and ny:
        t = st.ttest_ind(x, y, equal_var=False)
        sd = math.sqrt((x.var(ddof=1) + y.var(ddof=1)) / 2)
        return out | {"test": "welch_t", "p": float(t.pvalue), "effect": float((x.mean() - y.mean()) / sd) if sd else NAN,
                      "effect_name": "cohen_d"}
    u = st.mannwhitneyu(x, y, alternative="two-sided")
    return out | {"test": "mann_whitney_u", "p": float(u.pvalue),
                  "effect": float(2 * u.statistic / (len(x) * len(y)) - 1), "effect_name": "rank_biserial"}


def compare_paired(x, y, alpha: float = 0.05, B: int = 2000, perms: int = 10000, seed: int = 0) -> dict:
    """Paired comparison on the same cases (Wilcoxon signed-rank + paired bootstrap & permutation)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    d = x - y
    nd, pd_ = is_normal(d, alpha, seed)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), (B, len(d)))
    d_boot = d[idx]
    dmean_boot = d_boot.mean(axis=1)
    dmed_boot = np.median(d_boot, axis=1)
    signs = rng.choice([-1.0, 1.0], size=(perms, len(d)))
    null = (signs * d).mean(axis=1)
    p_perm = float((np.sum(np.abs(null) >= abs(d.mean()) - 1e-12) + 1) / (perms + 1)) if len(d) else 1.0
    nz = d[d != 0]
    out = {"mean_x": float(x.mean()), "mean_y": float(y.mean()), "mean_diff": float(d.mean()),
           "mean_ci_lo": float(np.quantile(dmean_boot, 0.025)), "mean_ci_hi": float(np.quantile(dmean_boot, 0.975)),
           "median_x": float(np.median(x)), "median_y": float(np.median(y)),
           "median_diff": float(np.median(x) - np.median(y)), "paired_median_diff": float(np.median(d)),
           "ci_lo": float(np.quantile(dmed_boot, 0.025)), "ci_hi": float(np.quantile(dmed_boot, 0.975)),
           "shapiro_p_diff": pd_, "p_perm": p_perm, "n_nonzero": int(len(nz))}
    if len(nz) == 0:
        return out | {"test": "none (all equal)", "p": 1.0, "effect": 0.0, "effect_name": "matched_rank_biserial"}
    r_ranks = st.rankdata(np.abs(nz))
    rb_paired = float((np.sum(r_ranks[nz > 0]) - np.sum(r_ranks[nz < 0])) / np.sum(r_ranks))
    w = st.wilcoxon(x, y, zero_method="wilcox", alternative="two-sided")
    return out | {"test": "wilcoxon_signed_rank", "p": float(w.pvalue), "effect": rb_paired,
                  "effect_name": "matched_rank_biserial"}


def holm(pvals) -> np.ndarray:
    p = np.asarray(pvals, float)
    adj = np.full(len(p), NAN)
    ok = np.where(~np.isnan(p))[0]
    running = 0.0
    for rank, i in enumerate(ok[np.argsort(p[ok])]):
        running = max(running, min(1.0, (len(ok) - rank) * p[i]))
        adj[i] = running
    return adj


def n_two_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Per-group n to tell p1 from p2 (two-sided) - checks the proposal's 60-per-type replication."""
    za, zb = st.norm.ppf(1 - alpha / 2), st.norm.ppf(power)
    pbar = (p1 + p2) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p1 - p2) ** 2)
