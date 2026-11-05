"""買い方のシミュレーター(spec/11 2.6、M-16)。simulator.json の中身を計算する。

グリッド(81通り) = (券種, 点数) 9通り × 見送り 3通り × 配分 3通り
- 券種・点数: 単勝 1・2、2連単 1・3・5、3連単 1・3・5・10(モデルの確率の上位 k 点)
- 見送り: none(なし)/ skip_rough(「荒」= 本命の確率 35% 未満なら見送り)/ skip_mid_or_below(50% 未満なら見送り)
- 配分: equal(均等)/ top(上位厚め)/ prob(確率に比例)。オッズの要らないものだけ
- 1レースの予算は SIM_BUDGET_PER_RACE_YEN(1,000円。Q16)。金額はフロントの allocateBets と同じ規則
  (docs/app/betsum.js。テストケースは docs/app/tests/ledger-test.js と同じものを tests/model で通す)。

守ること(spec/08 3章・11 2.6):
- **配分の入力はそのレースの予算・買い目・確率だけ**。過去の収支・連敗・前のレースの結果は使わない。
  負けたあとの増額は扱わない。
- 期間は「検証」(モデルが学習に使っていない期間のバックテスト)と「実運用」(確定版の予想だけ)を分けて出す。
  検証には「後知恵を含む」の注意を付ける(条件を見てから選ぶと、たまたま良かったものを選びやすい)。
- 期待値で絞る条件は、締切前オッズが貯まるまで入れない(確定オッズでの計算は出さない)。
- 並び順は「券種 → 点数 → 見送り → 配分」の固定順。回収率で並べ替えない。「最適」「おすすめ」は使わない。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.analytics_rules import (
    SAMPLE_INSUFFICIENT,
    SIM_BUDGET_PER_RACE_YEN,
    bootstrap_roi_interval,
    ci_crosses,
    sample_level,
)
from src.model import combinations as cb
from src.model.backtest import p_win_matrix, settle_bets
from src.model.config import MODELS_DIR
from src.model.summary import max_drawdown_yen, max_losing_streak

BET_TYPES_K = (("win", (1, 2)), ("exacta", (1, 3, 5)), ("trifecta", (1, 3, 5, 10)))
SKIPS = ("none", "skip_rough", "skip_mid_or_below")
SKIP_THRESHOLD = {"none": 0.0, "skip_rough": 0.35, "skip_mid_or_below": 0.50}  # 本命の確率がこれ未満なら見送り
ALLOCATIONS = ("equal", "top", "prob")
TOP_WEIGHTS = (3, 2)  # フロントの TOP_WEIGHTS と同じ(4位以下は1)
BET_LABELS = {"win": "単勝", "exacta": "2連単", "trifecta": "3連単"}
SKIP_LABELS = {"none": "見送りなし", "skip_rough": "荒は見送り", "skip_mid_or_below": "中以下は見送り"}
ALLOC_LABELS = {"equal": "均等", "top": "上位厚め", "prob": "確率比例"}
HINDSIGHT_NOTE = "後知恵を含む(81通りの中から結果を見て選ぶと、たまたま良かった条件を選びやすい)"


# ---------------------------------------------------------------------------
# 配分(docs/app/betsum.js の allocateBets の equal / top / prob と同じ結果になること)
# ---------------------------------------------------------------------------


def _proportional(weights: list[float], units: int, prob_order: list[int]) -> list[int]:
    s = sum(weights)
    if not s > 0:
        return [0 for _ in weights]
    out = [int(np.floor((units * w) / s)) for w in weights]
    left = units - sum(out)
    pos = {i: r for r, i in enumerate(prob_order)}
    order = sorted((i for i in range(len(weights)) if weights[i] > 0), key=lambda i: (-weights[i], pos[i]))
    j = 0
    while left > 0 and order:
        out[order[j % len(order)]] += 1
        j += 1
        left -= 1
    return out


def allocate_bets(method: str, probs: list[float], budget: int) -> list[int]:
    """買い目ごとの金額(円)。入力はこのレースの配分のしかた・確率・予算だけ。

    フロントの allocateBets(method, bets, budget) の equal / top / prob と同じ規則:
    100円単位、切り捨てて余りを重みの大きい順(同じ重みなら確率の高い順)に1単位ずつ。合計は予算以内。
    """
    n = len(probs)
    units = int(max(0, budget) // 100)
    if n == 0 or units == 0:
        return [0] * n
    prob_order = sorted(range(n), key=lambda i: (-probs[i], i))
    if method == "equal":
        w = [1.0] * n
    elif method == "top":
        w = [1.0] * n
        for r, wt in enumerate(TOP_WEIGHTS):
            if r < n:
                w[prob_order[r]] = float(wt)
    elif method == "prob":
        w = [max(0.0, float(p)) for p in probs]
    else:
        raise ValueError(f"シミュレーターで使わない配分です: {method}(オッズが要る配分は入れない)")
    return [u * 100 for u in _proportional(w, units, prob_order)]


# ---------------------------------------------------------------------------
# グリッドの計算
# ---------------------------------------------------------------------------


def _gammas_by_model(model_ids, models_dir: Path) -> dict:
    from src.model.lgbm import gammas_from_meta, model_dir

    out = {}
    for mid in set(map(str, model_ids)):
        f = model_dir(mid, models_dir) / "meta.json"
        out[mid] = gammas_from_meta(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else (1.0, 1.0)
    return out


def _top_k(preds: pd.DataFrame, payouts: pd.DataFrame, bet_type: str, kmax: int, models_dir: Path):
    """上位 kmax 点の確率と、100円あたりの払戻(的中・返還・特払)を (レース数, kmax) で返す。"""
    race_ids, pw = p_win_matrix(preds)
    if "model_id" in preds.columns:
        mid = preds.groupby("race_id", sort=False)["model_id"].first().reindex(race_ids).astype(str).to_numpy()
    else:
        mid = np.array(["?"] * len(race_ids))
    gam = _gammas_by_model(np.unique(mid), models_dir)
    labels = np.array(cb.combination_labels(bet_type))
    probs = np.zeros((len(race_ids), len(labels)))
    for m in np.unique(mid):
        sel = mid == m
        probs[sel] = cb.combination_prob_matrix(pw[sel], bet_type, *gam.get(m, (1.0, 1.0)))
    k = min(kmax, len(labels))
    order = np.argsort(-probs, axis=1, kind="stable")[:, :k]
    P = np.take_along_axis(probs, order, axis=1)
    bets = pd.DataFrame({"race_id": np.repeat(race_ids, k), "bet_type": bet_type,
                         "combination": labels[order].reshape(-1), "prob": P.reshape(-1)})
    absent = None
    if "is_absent" in preds.columns:
        absent = preds.loc[preds["is_absent"].astype("boolean").fillna(False).astype(bool), ["race_id", "entry_number"]]
    s = settle_bets(bets, payouts, absent)
    R = s["return"].to_numpy(float).reshape(-1, k) / 100.0  # 1円あたりの払戻
    H = s["hit"].to_numpy(bool).reshape(-1, k)
    return race_ids, pw.max(axis=1), P, R, H


def _row(race_ids, stake, ret, hit, n_considered) -> dict:
    """1戦略の集計。stake・ret・hit は対象レースごと(race_id の昇順)。"""
    bought = stake > 0
    n_bet = int(bought.sum())
    lvl = sample_level(int(n_considered))
    total_s, total_r = int(stake.sum()), int(round(ret.sum()))
    row = {
        "races": int(n_considered), "bet_races": n_bet, "stake": total_s, "return": total_r,
        "level": lvl, "roi": None, "roi_ci95": None, "ci_crosses_100": None, "hit_rate": None,
        "max_losing_streak": max_losing_streak(hit[bought]),
        "max_drawdown_yen": max_drawdown_yen((ret - stake)[bought]),
        "leak_free": True,
    }
    if lvl != SAMPLE_INSUFFICIENT and total_s > 0:
        ci = bootstrap_roi_interval(stake[bought].tolist(), ret[bought].tolist())
        row.update({"roi": round(total_r / total_s, 4), "roi_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
                    "ci_crosses_100": ci_crosses(ci), "hit_rate": round(float(hit[bought].mean()), 4) if n_bet else None})
    return row


def simulate_grid(preds: pd.DataFrame, payouts: pd.DataFrame, budget: int = SIM_BUDGET_PER_RACE_YEN,
                  models_dir: str | Path = MODELS_DIR) -> list[dict]:
    """81通りの成績。preds は1行=1艇(race_id, entry_number, p_win, 任意で model_id・is_absent・is_valid_result)。

    結果が出たレースだけを渡すこと。並びは「券種 → 点数 → 見送り → 配分」の固定順。
    """
    if "is_valid_result" in preds.columns:
        ok = preds["is_valid_result"].astype("boolean").fillna(False).astype(bool)
        preds = preds[ok.groupby(preds["race_id"]).transform("all")]
    preds = preds.sort_values(["race_id", "entry_number"])
    rows = []
    for bt, ks in BET_TYPES_K:
        race_ids, pfav, P, R, H = _top_k(preds, payouts, bt, max(ks), Path(models_dir))
        for k in ks:
            amounts = {a: np.array([allocate_bets(a, P[i, :k].tolist(), budget) for i in range(len(P))], dtype=float)
                       for a in ALLOCATIONS}
            for skip in SKIPS:
                keep = pfav >= SKIP_THRESHOLD[skip]
                for a in ALLOCATIONS:
                    A = amounts[a] * keep[:, None]
                    stake = A.sum(axis=1)
                    ret = (A * R[:, :k]).sum(axis=1)
                    hit = ((A > 0) & H[:, :k]).any(axis=1)
                    row = {"id": f"{bt}_k{k}_{skip}_{a}", "bet_type": bt, "k": k, "skip": skip, "allocation": a,
                           "label": f"{BET_LABELS[bt]} 上位{k}点・{SKIP_LABELS[skip]}・{ALLOC_LABELS[a]}"}
                    row.update(_row(race_ids, stake, ret, hit, len(race_ids)))
                    rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 検証(バックテスト)と実運用
# ---------------------------------------------------------------------------


def validation_predictions(model_id: str = "latest", variant: str = "pre", models_dir=MODELS_DIR) -> tuple[pd.DataFrame, dict]:
    """検証用の予想: モデルが学習にも選択にも使っていない期間(meta の test 期間)。

    毎月の作り直し(10 2.5)の記録が貯まったら、各月の valid を月ごとにつなぐ形に置き換える。
    """
    from src.model.data import features_path, filter_period, load_features
    from src.model.lgbm import load_model
    from src.model.report import predict_p

    booster, meta = load_model(model_id, models_dir, variant)
    periods = meta["periods"]
    part = filter_period(load_features(features_path(meta["variant"])), periods["test_from"], periods["test_to"])
    preds = part[["race_id", "entry_number", "is_valid_result", "is_absent"]].assign(
        p_win=predict_p(part, booster, meta), model_id=meta["model_id"])
    return preds, {"from": periods["test_from"], "to": periods["test_to"], "model_id": meta["model_id"]}


def build_simulator(live_from, live_to, budget: int = SIM_BUDGET_PER_RACE_YEN, models_dir=MODELS_DIR,
                    pred_dir=None) -> dict:
    """simulator.json の中身: 検証と実運用(前日版・直前版)を分けた81通りの表。"""
    from src.features.build_interim import read_interim
    from src.model.analytics_eval import load_live
    from src.model.backtest import load_interim_table
    from src.model.config import PREDICTIONS_DIR

    vpreds, vinfo = validation_predictions(models_dir=models_dir)
    vpay = load_interim_table("payouts", vpreds["race_id"])
    out = {
        "budget_per_race_yen": budget,
        "order": "券種 → 点数 → 見送り → 配分(回収率で並べ替えない)",
        "validation": {"period": vinfo, "note": HINDSIGHT_NOTE,
                       "rows": simulate_grid(vpreds, vpay, budget, models_dir)},
        "live": {},
    }
    live = load_live(live_from, live_to, pred_dir or PREDICTIONS_DIR)
    lpay = read_interim("payouts", pd.Timestamp(live_from).date(), pd.Timestamp(live_to).date())
    for v in ("pre", "preview"):
        d = live[live["version"] == v]
        d = d[d["finish_position"].notna().groupby(d["race_id"]).transform("any")] if len(d) else d
        out["live"][v] = {"period": {"from": str(pd.Timestamp(live_from).date()), "to": str(pd.Timestamp(live_to).date())},
                          "rows": simulate_grid(d, lpay, budget, models_dir) if len(d) else []}
    return out
