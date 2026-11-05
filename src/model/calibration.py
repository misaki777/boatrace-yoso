"""1着確率の較正(温度T以外の方法も含む)と Harville γ の探索。

すべての方法は「LightGBM の生スコア s(と枠番)→ レース内で合計1の p_win」の写像で、
**valid だけ**で当てはめ・選択する(test は選択に使わない)。

方法(meta.json の "calibration": {"method": ..., "params": ...} に保存する):
- temperature:   p ∝ exp(s / T)                         (従来どおり)
- course_bias:   p ∝ exp(a·s + b_枠番)                   (条件付きロジット。枠番ごとの偏りを直す)
- prior_shrink:  p = (1-w)·softmax(a·s) + w·コース別1着率 (本命を推しすぎる分を事前分布へ縮める)
- isotonic:      softmax(s/T) を艇単位で等温回帰 → レース内で正規化
選択は valid を日付で前半・後半に分けた2分割の交差検証(片方で当てはめ、もう片方で win_logloss)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression

from src.model import combinations as cb
from src.model.data import fit_temperature, race_matrix, race_softmax

METHODS = ("temperature", "course_bias", "prior_shrink", "isotonic")
_EPS = 1e-12


# ---------------------------------------------------------------------------
# 行列形式(レース数, 6)での計算。s は生スコア、欠けた艇は NaN
# ---------------------------------------------------------------------------


def _softmax_rows(z: np.ndarray) -> np.ndarray:
    z = np.where(np.isnan(z), -np.inf, z)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def apply_matrix(S: np.ndarray, calib: dict) -> np.ndarray:
    """生スコアの行列 S (n,6) に較正を適用して p_win (n,6) を返す。"""
    m, prm = calib["method"], calib["params"]
    if m == "temperature":
        return _softmax_rows(S / prm["T"])
    if m == "course_bias":
        return _softmax_rows(prm["a"] * S + np.asarray(prm["b"])[None, :])
    if m == "prior_shrink":
        p = _softmax_rows(prm["a"] * S)
        prior = np.where(np.isnan(S), 0.0, np.asarray(prm["prior"])[None, :])
        prior = prior / prior.sum(axis=1, keepdims=True)
        return (1 - prm["w"]) * p + prm["w"] * prior
    if m == "isotonic":
        p = _softmax_rows(S / prm["T"])
        q = np.interp(p, prm["x"], prm["y"])
        q = np.where(np.isnan(S), 0.0, np.maximum(q, 1e-6))
        return q / q.sum(axis=1, keepdims=True)
    raise ValueError(f"未対応の較正方法です: {m}")


def _nll(P: np.ndarray, winner: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(winner)), winner], _EPS, 1))))


def fit_matrix(method: str, S: np.ndarray, winner: np.ndarray, prior: np.ndarray) -> dict:
    """valid の (S, 1着艇の列番号) で較正のパラメータを当てはめる。"""
    rid = np.repeat(np.arange(len(S)), 6)
    is_win = np.zeros(S.size, dtype=int)
    is_win[np.arange(len(S)) * 6 + winner] = 1
    T, _ = fit_temperature(rid, S.reshape(-1), is_win)
    if method == "temperature":
        return {"method": method, "params": {"T": T}}
    if method == "course_bias":
        def f(x):
            return _nll(_softmax_rows(x[0] * S + np.r_[0.0, x[1:]][None, :]), winner)
        r = minimize(f, np.r_[1 / T, np.zeros(5)], method="L-BFGS-B")
        return {"method": method, "params": {"a": float(r.x[0]), "b": [0.0, *map(float, r.x[1:])]}}
    if method == "prior_shrink":
        def f(x):
            return _nll(apply_matrix(S, {"method": method, "params": {"a": x[0], "w": x[1], "prior": prior}}), winner)
        r = minimize(f, [1 / T, 0.05], method="L-BFGS-B", bounds=[(0.05, 5.0), (0.0, 0.9)])
        return {"method": method, "params": {"a": float(r.x[0]), "w": float(r.x[1]), "prior": [float(v) for v in prior]}}
    if method == "isotonic":
        p = _softmax_rows(S / T)
        y = np.zeros_like(p)
        y[np.arange(len(S)), winner] = 1
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p.reshape(-1), y.reshape(-1))
        return {"method": method, "params": {"T": T, "x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}}
    raise ValueError(method)


def select_calibration(
    S: np.ndarray, winner: np.ndarray, dates: np.ndarray, prior: np.ndarray, methods=METHODS
) -> tuple[dict, dict]:
    """valid 内の2分割交差検証で方法を選び、valid 全体で当て直した較正を返す。

    Returns:
        (選んだ較正, {方法: 交差検証の win_logloss})
    """
    cut = np.median(dates.astype("datetime64[D]").astype(np.int64))
    first = dates.astype("datetime64[D]").astype(np.int64) <= cut
    cv = {}
    for m in methods:
        losses = []
        for a, b in ((first, ~first), (~first, first)):
            c = fit_matrix(m, S[a], winner[a], prior)
            losses.append(_nll(apply_matrix(S[b], c), winner[b]) * b.sum())
        cv[m] = float(sum(losses) / len(S))
    best = min(cv, key=cv.get)
    return fit_matrix(best, S, winner, prior), cv


# ---------------------------------------------------------------------------
# 1行=1艇の表への適用(predict・evaluate 用)
# ---------------------------------------------------------------------------


def calibration_from_meta(meta: dict) -> dict:
    """meta に calibration が無ければ従来の温度Tとして扱う。"""
    return meta.get("calibration") or {"method": "temperature", "params": {"T": meta["temperature"]}}


def apply_rows(race_ids: np.ndarray, entry_numbers: np.ndarray, scores: np.ndarray, calib: dict) -> np.ndarray:
    """1行=1艇のスコアに較正を適用し、行の並びのまま p_win を返す。"""
    if calib["method"] == "temperature":
        return race_softmax(race_ids, scores, calib["params"]["T"])
    df = pd.DataFrame({"race_id": race_ids, "entry_number": entry_numbers, "s": scores})
    codes, S = race_matrix(df, "s")
    P = apply_matrix(S, calib)
    r = pd.Index(codes).get_indexer(df["race_id"])
    return P[r, df["entry_number"].to_numpy().astype(int) - 1]


# ---------------------------------------------------------------------------
# Harville γ の探索(valid の3連単 logloss)
# ---------------------------------------------------------------------------


def trifecta_index(F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """着順行列 F (n,6) から、1〜3着が決まるレースのマスクと3連単の列番号を返す。"""
    ok = np.all([(F == k).sum(axis=1) == 1 for k in (1, 2, 3)], axis=0)
    labels = {lab: i for i, lab in enumerate(cb.combination_labels("trifecta"))}
    idx = np.array(
        [labels["-".join(str(int(np.argmax(row == k)) + 1) for k in (1, 2, 3))] for row in F[ok]], dtype=int
    )
    return ok, idx


def trifecta_logloss(P: np.ndarray, idx: np.ndarray, gamma2: float, gamma3: float) -> float:
    T = cb.combination_prob_matrix(P, "trifecta", gamma2, gamma3)
    return float(-np.mean(np.log(np.clip(T[np.arange(len(idx)), idx], _EPS, 1))))


def search_gamma(P: np.ndarray, idx: np.ndarray, grid=np.round(np.arange(0.5, 1.2001, 0.05), 2)) -> tuple[float, float, float]:
    """γ2, γ3 ∈ [0.5, 1.2] をグリッド探索し、(γ2, γ3, logloss) を返す。"""
    best = (1.0, 1.0, trifecta_logloss(P, idx, 1.0, 1.0))
    for g2 in grid:
        for g3 in grid:
            ll = trifecta_logloss(P, idx, float(g2), float(g3))
            if ll < best[2] - 1e-12:
                best = (float(g2), float(g3), ll)
    return best
