"""較正・戦略比較の集計関数(spec/08 5.3、spec/04 8.2)。エクスポート担当(E)が import して使う。

E との契約(シグネチャを変えるときは PM を通す):

    wilson_ci(k, n, z=1.96) -> [lo, hi] | None
    calibration_bins(p, y, n_bins=10) -> list[{"bin","n","pred_mean","actual","ci95"}]
    calibration_from_predictions(preds) -> 同上(1行=1艇の表から。きれいなレースだけ)
    max_losing_streak(hit_by_race) -> int
    max_drawdown_yen(profit_by_race) -> int
    bootstrap_roi_ci(stake_by_race, return_by_race, n_boot=1000, seed=42) -> [lo, hi] | None
    sample_note(races) -> "few" | None
    strategy_row(settled, n_races, name, short_label, is_baseline=False, leak_free=True, params=None) -> dict
    compare_strategies(preds, payouts, odds=None, strategies=None, gamma2=1.0, gamma3=1.0, n_boot=1000) -> list[dict]
    by_month(settled) -> list[{"month","races","bets","stake","return","roi","roi_ci95"}]

入力の形:
- preds: 1行=1艇。race_id, entry_number, p_win(確定版の予想)。あれば is_absent(返還の判定)、
  is_valid_result、finish_position(較正用)。
- payouts: interim の payouts と同じ列(race_id, bet_type, combination, amount, label)。
- settled: backtest.settle_bets / run_backtest が返す1行=1点の表(race_id, stake, return, hit, refund)。
レースの順番(最大連敗・最大ドローダウン)は race_id の昇順(日付→場→R)とする。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.model import combinations as cb
from src.model.backtest import (
    LEAK_FREE,
    bootstrap_roi_ci,
    max_drawdown,
    p_win_matrix,
    select_bets,
    settle_bets,
)
from src.model.config import BOOTSTRAP_N, SEED, VARIANTS
from src.model.data import clean_race_mask, race_matrix

FEW_RACES = 200  # これ未満なら sample_note="few"(spec/04 8.2)

__all__ = [
    "wilson_ci", "calibration_bins", "calibration_from_predictions", "max_losing_streak", "max_drawdown_yen",
    "bootstrap_roi_ci", "sample_note", "strategy_row", "compare_strategies", "by_month", "DEFAULT_STRATEGIES",
]

# 戦略比較に載せる既定の戦略。(name, short_label, 券種, 選び方, パラメータ, is_baseline)
# 選び方: backtest.select_bets の戦略名、または "baseline_win1" / "baseline_tri1_top5"
DEFAULT_STRATEGIES: list[dict] = [
    {"name": "top_k", "short_label": "3連単 上位5点", "bet_type": "trifecta", "rule": "top_k", "params": {"k": 5}},
    {"name": "top_k_exacta", "short_label": "2連単 上位3点", "bet_type": "exacta", "rule": "top_k", "params": {"k": 3}},
    {"name": "top_k_win", "short_label": "単勝 本命1点", "bet_type": "win", "rule": "top_k", "params": {"k": 1}},
    {"name": "confidence_skip", "short_label": "3連単 上位5点(本命40%以上のみ)", "bet_type": "trifecta",
     "rule": "confidence_skip", "params": {"alpha": 0.4, "k": 5}},
    {"name": "baseline_win1", "short_label": "1号艇 単勝", "bet_type": "win", "rule": "baseline_win1",
     "params": {}, "is_baseline": True},
    {"name": "baseline_tri1_top5", "short_label": "1号艇から3連単 上位5点", "bet_type": "trifecta",
     "rule": "baseline_tri1_top5", "params": {"k": 5}, "is_baseline": True},
]


# ---------------------------------------------------------------------------
# 区間・基本の集計
# ---------------------------------------------------------------------------


def wilson_ci(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """二項割合 k/n の Wilson 区間(既定は95%)。n=0 なら None。"""
    if n <= 0:
        return None
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - half), min(1.0, center + half)]


def calibration_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    """1着確率を等幅の帯(0〜10%、…、90〜100%)に分け、帯ごとの件数・予測の平均・実際の1着率・Wilson区間。

    件数0の帯は出さない。bin は "0-10" のような文字列(%)。
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    idx = np.minimum((p * n_bins).astype(int), n_bins - 1)
    width = 100 // n_bins
    out = []
    for b in range(n_bins):
        m = idx == b
        n = int(m.sum())
        if n == 0:
            continue
        k = int(y[m].sum())
        out.append({"bin": f"{b * width}-{(b + 1) * width}", "n": n, "pred_mean": float(p[m].mean()),
                    "actual": k / n, "ci95": wilson_ci(k, n)})
    return out


def calibration_from_predictions(preds: pd.DataFrame, p_col: str = "p_win") -> list[dict]:
    """1行=1艇の予想と結果(finish_position 等)から較正の表を作る。

    使うのはきれいなレース(中止・欠場・同着1着を除く。evaluate と同じ基準)だけ。
    """
    d = preds.copy()
    for col, default in (("is_valid_result", True), ("is_absent", False)):
        if col not in d.columns:
            d[col] = default
    d = d[clean_race_mask(d)]
    if d.empty:
        return []
    _, P = race_matrix(d, p_col)
    _, F = race_matrix(d, "finish_position")
    P = P / P.sum(axis=1, keepdims=True)
    return calibration_bins(P.reshape(-1), (F == 1).reshape(-1).astype(float))


def max_losing_streak(hit_by_race) -> int:
    """的中の無いレースが続いた最大の回数(買ったレースだけを順に並べて渡す)。"""
    best = cur = 0
    for h in hit_by_race:
        cur = 0 if h else cur + 1
        best = max(best, cur)
    return best


def max_drawdown_yen(profit_by_race) -> int:
    """累積収支の最大ドローダウン(円、正の値)。"""
    return int(round(max_drawdown(np.asarray(profit_by_race, dtype=float))))


def sample_note(races: int) -> str | None:
    """レース数が200未満なら "few"(PWA は数字を薄く表示する)。"""
    return "few" if races < FEW_RACES else None


# ---------------------------------------------------------------------------
# 戦略の行
# ---------------------------------------------------------------------------


def _by_race(settled: pd.DataFrame) -> pd.DataFrame:
    return (
        settled.groupby("race_id", sort=True)
        .agg(stake=("stake", "sum"), ret=("return", "sum"), hits=("hit", "sum"), bets=("stake", "size"))
        .reset_index()
    )


def strategy_row(
    settled: pd.DataFrame,
    n_races: int,
    name: str,
    short_label: str,
    is_baseline: bool = False,
    leak_free: bool = True,
    params: dict | None = None,
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
) -> dict:
    """精算済みの買い目から、spec/04 8.2 の strategies の1行を作る。

    races は「対象にしたレース数」(見送りも含む)、bet_races は実際に買ったレース数。
    hit_rate は「的中したレース / 買ったレース」。
    """
    br = _by_race(settled) if len(settled) else pd.DataFrame(columns=["race_id", "stake", "ret", "hits", "bets"])
    stake, ret = int(br["stake"].sum()), int(br["ret"].sum())
    return {
        "name": name,
        "short_label": short_label,
        "params": params or {},
        "races": int(n_races),
        "bet_races": int(len(br)),
        "bets": int(br["bets"].sum()),
        "stake": stake,
        "return": ret,
        "roi": ret / stake if stake else None,
        "roi_ci95": bootstrap_roi_ci(br["stake"].to_numpy(), br["ret"].to_numpy(), n_boot, seed),
        "hit_rate": float((br["hits"] > 0).mean()) if len(br) else None,
        "max_losing_streak": max_losing_streak((br["hits"] > 0).to_numpy()),
        "max_drawdown_yen": max_drawdown_yen((br["ret"] - br["stake"]).to_numpy()),
        "is_baseline": bool(is_baseline),
        "leak_free": bool(leak_free),
        "sample_note": sample_note(int(n_races)),
    }


def by_month(settled: pd.DataFrame, n_boot: int = BOOTSTRAP_N, seed: int = SEED) -> list[dict]:
    """月別の回収率と95%区間(race_id の先頭 YYYYMM で分ける)。"""
    if not len(settled):
        return []
    br = _by_race(settled)
    br["month"] = br["race_id"].str.slice(0, 4) + "-" + br["race_id"].str.slice(4, 6)
    out = []
    for month, g in br.groupby("month"):
        stake, ret = int(g["stake"].sum()), int(g["ret"].sum())
        out.append({"month": month, "races": int(len(g)), "bets": int(g["bets"].sum()), "stake": stake,
                    "return": ret, "roi": ret / stake if stake else None,
                    "roi_ci95": bootstrap_roi_ci(g["stake"].to_numpy(), g["ret"].to_numpy(), n_boot, seed)})
    return out


# ---------------------------------------------------------------------------
# 戦略比較
# ---------------------------------------------------------------------------


def _select(rule: str, bet_type: str, probs: np.ndarray, pw: np.ndarray, odds, params: dict) -> np.ndarray:
    labels = cb.combination_labels(bet_type)
    if rule == "baseline_win1":
        if bet_type != "win":
            raise ValueError("baseline_win1 は単勝だけ")
        mask = np.zeros_like(probs, dtype=bool)
        mask[:, 0] = True
        return mask
    if rule in ("baseline_tri1_top5", "baseline_first1_topk"):
        # 1号艇1着の組合せの中で、モデルの確率の上位 k 点(2連単・3連単)
        first1 = np.array([lab.startswith("1-") for lab in labels])
        masked = np.where(first1[None, :], probs, -1.0)
        k = int(params.get("k", 5))
        order = np.argsort(-masked, axis=1, kind="stable")[:, :k]
        mask = np.zeros_like(probs, dtype=bool)
        np.put_along_axis(mask, order, True, axis=1)
        return mask & first1[None, :]
    return select_bets(rule, probs, pw, odds, **params)


def settle_strategy(
    preds: pd.DataFrame,
    payouts: pd.DataFrame,
    spec: dict,
    odds: pd.DataFrame | None = None,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> tuple[pd.DataFrame, int]:
    """1つの戦略の買い目を作って精算する。戻り値は (settled, 対象レース数)。"""
    from src.model.backtest import odds_matrix as _odds_matrix

    if "is_valid_result" in preds.columns:
        preds = preds[preds.groupby("race_id")["is_valid_result"].transform("all").astype(bool)]
    race_ids, pw = p_win_matrix(preds)
    bt = spec["bet_type"]
    odds_matrix = _odds_matrix(odds, race_ids, bt) if odds is not None else None
    probs = cb.combination_prob_matrix(pw, bt, gamma2, gamma3)
    mask = _select(spec["rule"], bt, probs, pw, odds_matrix, spec.get("params", {}))
    r, c = np.nonzero(mask)
    labels = np.array(cb.combination_labels(bt))
    bets = pd.DataFrame({"race_id": race_ids[r], "bet_type": bt, "combination": labels[c], "prob": probs[r, c]})
    absent = None
    if "is_absent" in preds.columns:
        absent = preds.loc[preds["is_absent"].fillna(False).astype(bool), ["race_id", "entry_number"]]
    return settle_bets(bets, payouts, absent), len(race_ids)


def compare_strategies(
    preds: pd.DataFrame,
    payouts: pd.DataFrame,
    odds: pd.DataFrame | None = None,
    strategies: list[dict] | None = None,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
) -> list[dict]:
    """戦略比較の表(spec/04 8.2 の strategies)。基準の2戦略は必ず含める。

    Args:
        preds: 確定版の予想(1行=1艇)。結果のあるレースだけを渡すこと(結果が無いレースは外れ扱いになる)。
        odds: 確定オッズを使う戦略(ev_final 等)を入れるときだけ必要。
        strategies: DEFAULT_STRATEGIES と同じ形のリスト。省略時は DEFAULT_STRATEGIES。
    """
    strategies = list(strategies or DEFAULT_STRATEGIES)
    names = {s["name"] for s in strategies}
    for base in DEFAULT_STRATEGIES:
        if base.get("is_baseline") and base["name"] not in names:
            strategies.append(base)
    rows = []
    for spec in strategies:
        leak_free = LEAK_FREE.get(spec["rule"], True)
        if not leak_free and odds is None:
            continue  # 確定オッズが無ければ後知恵の戦略は出さない
        settled, n_races = settle_strategy(preds, payouts, spec, odds if not leak_free else None, gamma2, gamma3)
        rows.append(strategy_row(settled, n_races, spec["name"], spec["short_label"], spec.get("is_baseline", False),
                                 leak_free, spec.get("params"), n_boot, seed))
    return rows


# ---------------------------------------------------------------------------
# CLI: バックテスト期間の集計を reports/ に出す(performance.json の backtest の元)
# ---------------------------------------------------------------------------


def backtest_performance(model_id: str = "latest", period: str = "test", variant: str = "pre",
                         n_boot: int = BOOTSTRAP_N) -> dict:
    """バックテスト期間の calibration / strategies / by_month(spec/04 8.2 の backtest の形)。"""
    from src.model.backtest import load_interim_table
    from src.model.config import Periods
    from src.model.data import features_path, filter_period, load_features
    from src.model.lgbm import gammas_from_meta, load_model
    from src.model.report import predict_p

    booster, meta = load_model(model_id, variant=variant)
    periods = Periods(**meta["periods"])
    part = filter_period(load_features(features_path(meta["variant"])), *periods.range_of(period))
    cols = ["race_id", "entry_number", "finish_position", "is_valid_result", "is_absent"]
    preds = part[cols].assign(p_win=predict_p(part, booster, meta))
    payouts = load_interim_table("payouts", preds["race_id"])
    odds = load_interim_table("odds", preds["race_id"])
    odds = odds[odds["bet_type"] == "trifecta"]
    g2, g3 = gammas_from_meta(meta)
    strategies = DEFAULT_STRATEGIES + [
        {"name": "ev_final", "short_label": "3連単 期待値1.2以上(確定オッズ・後知恵)", "bet_type": "trifecta",
         "rule": "ev_final", "params": {"tau": 1.2}},
    ]
    rows = compare_strategies(preds, payouts, odds, strategies, g2, g3, n_boot)
    s1, _ = settle_strategy(preds, payouts, DEFAULT_STRATEGIES[0], None, g2, g3)
    races = int(preds.loc[preds["is_valid_result"].astype(bool), "race_id"].nunique())
    return {
        "model_id": meta["model_id"],
        "period": {"from": periods.range_of(period)[0], "to": periods.range_of(period)[1]},
        "calibration": calibration_from_predictions(preds),
        "strategies": rows,
        "by_month": by_month(s1, n_boot),
        "by_month_strategy": DEFAULT_STRATEGIES[0]["short_label"],
        "races": races,
        "sample_note": sample_note(races),
    }


def main() -> None:
    import argparse
    import json

    from src.model.config import REPORTS_DIR

    parser = argparse.ArgumentParser(description="バックテスト期間の較正・戦略比較を reports/ に出す")
    parser.add_argument("--model-id", default="latest")
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--period", default="test", choices=["valid", "test"])
    parser.add_argument("--n-boot", type=int, default=BOOTSTRAP_N)
    args = parser.parse_args()
    res = backtest_performance(args.model_id, args.period, args.variant, args.n_boot)
    out = REPORTS_DIR / "backtest" / res["model_id"] / f"performance_{args.period}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in res["strategies"]:
        print(f"{r['short_label']:28s} races={r['races']} bets={r['bets']} roi={r['roi']:.3f} {r['roi_ci95']} "
              f"hit={r['hit_rate']:.3f} 最大連敗={r['max_losing_streak']} 最大DD={r['max_drawdown_yen']}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
