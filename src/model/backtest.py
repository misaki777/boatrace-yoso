"""回収率シミュレーション(バックテスト)。spec/03 6章。

賭け金は1点100円で一定。払戻は interim の payouts を使う。
- 的中: 同じ組合せの払戻をすべて足す(同着で払戻が複数あるとき)
- 欠場艇を含む組合せ: 返還(100円)
- 的中が無く、その券種に combination が null の払戻(特払・不成立)があれば、その額

CLI:
    python -m src.model.backtest --model-id <id> --period test --strategy top_k --bet-type trifecta --param k=5
    python -m src.model.backtest --model-id <id> --period test --strategy all
入力の予想は evaluate が書き出す reports/backtest/{model_id}/predictions_{period}.parquet。
出力は reports/backtest/{model_id}/{strategy}_{period}/summary.json と bets.parquet。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model import combinations as cb
from src.model.config import BOOTSTRAP_N, INTERIM_DIR, REPORTS_DIR, SEED
from src.model.data import race_matrix

STAKE = 100  # 1点あたりの賭け金(円)
MVP_BET_TYPES = ("trifecta", "exacta", "win")

# 戦略ごとの「リークが無いか」(確定オッズを見て買う戦略は後知恵)
LEAK_FREE = {
    "top_k": True,
    "prob_threshold": True,
    "confidence_skip": True,
    "ev_final": False,
    "ev_final_haircut": False,
}

# --strategy all のときに試すパラメータ
DEFAULT_GRID: dict[str, list[dict]] = {
    "top_k": [{"k": k} for k in (1, 3, 5, 10)],
    "prob_threshold": [{"theta": t} for t in (0.02, 0.05, 0.1, 0.2, 0.3)],
    "ev_final": [{"tau": t} for t in (1.0, 1.2, 1.5)],
    "ev_final_haircut": [{"tau": t, "haircut": 0.85} for t in (1.0, 1.2, 1.5)],
    "confidence_skip": [{"alpha": a, "k": 5} for a in (0.4, 0.5, 0.6)],
}


# ---------------------------------------------------------------------------
# 確率・オッズの行列
# ---------------------------------------------------------------------------


def p_win_matrix(preds: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """予想(1行=1艇)を (レース数, 6) の p_win 行列にする。足りない艇は0、行を合計1に正規化。"""
    race_ids, mat = race_matrix(preds, "p_win", fill=0.0)
    mat = np.nan_to_num(mat, nan=0.0)
    s = mat.sum(axis=1, keepdims=True)
    if np.any(s <= 0):
        raise ValueError("p_win の合計が0のレースがあります")
    return race_ids, mat / s


def odds_matrix(odds: pd.DataFrame, race_ids: np.ndarray, bet_type: str) -> np.ndarray:
    """確定オッズを (レース数, 組合せ数) に並べる。無いところは NaN。"""
    labels = cb.combination_labels(bet_type)
    sub = odds[odds["bet_type"] == bet_type]
    wide = sub.pivot_table(index="race_id", columns="combination", values="odds", aggfunc="first")
    wide = wide.reindex(index=race_ids, columns=list(labels))
    return wide.to_numpy(dtype=np.float64)


# ---------------------------------------------------------------------------
# 買い目の選択
# ---------------------------------------------------------------------------


def _top_k_mask(probs: np.ndarray, k: int) -> np.ndarray:
    k = min(int(k), probs.shape[1])
    order = np.argsort(-probs, axis=1, kind="stable")[:, :k]
    mask = np.zeros_like(probs, dtype=bool)
    np.put_along_axis(mask, order, True, axis=1)
    return mask


def select_bets(
    strategy: str,
    probs: np.ndarray,
    p_win: np.ndarray,
    odds: np.ndarray | None = None,
    **params,
) -> np.ndarray:
    """戦略に従って買う組合せを選ぶ。戻り値は probs と同じ形の bool 行列。"""
    if strategy == "top_k":
        return _top_k_mask(probs, params["k"])
    if strategy == "prob_threshold":
        return probs >= float(params["theta"])
    if strategy in ("ev_final", "ev_final_haircut"):
        if odds is None:
            raise ValueError(f"{strategy} には確定オッズが必要です")
        haircut = float(params.get("haircut", 0.85 if strategy == "ev_final_haircut" else 1.0))
        ev = probs * odds * haircut
        return np.nan_to_num(ev, nan=-1.0) >= float(params["tau"])
    if strategy == "confidence_skip":
        race_ok = p_win.max(axis=1) >= float(params["alpha"])
        return _top_k_mask(probs, params.get("k", 5)) & race_ok[:, None]
    raise ValueError(f"未対応の戦略です: {strategy}")


# ---------------------------------------------------------------------------
# 精算
# ---------------------------------------------------------------------------


def _entries_of(combination: pd.Series) -> pd.Series:
    return combination.str.split(r"[-=]")


def settle_bets(bets: pd.DataFrame, payouts: pd.DataFrame, absent: pd.DataFrame | None = None) -> pd.DataFrame:
    """買い目(race_id, bet_type, combination)に払戻を付ける。

    Args:
        bets: 1行=1点。
        payouts: interim の payouts(race_id, bet_type, combination, amount, label)。
        absent: 欠場艇(race_id, entry_number)。None なら返還なし。

    Returns:
        bets に stake, return, hit, refund の列を足したもの。
    """
    out = bets.copy()
    pay = payouts[payouts["race_id"].isin(out["race_id"].unique())]
    hit_pay = (
        pay[pay["combination"].notna()]
        .groupby(["race_id", "bet_type", "combination"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "_hit_amount"})
    )
    special = (
        pay[pay["combination"].isna()]
        .groupby(["race_id", "bet_type"], as_index=False)["amount"]
        .max()
        .rename(columns={"amount": "_special_amount"})
    )
    out = out.merge(hit_pay, on=["race_id", "bet_type", "combination"], how="left")
    out = out.merge(special, on=["race_id", "bet_type"], how="left")

    refund = np.zeros(len(out), dtype=bool)
    if absent is not None and len(absent):
        absent_sets = absent.groupby("race_id")["entry_number"].agg(lambda s: {str(int(x)) for x in s})
        entries = _entries_of(out["combination"])
        sets = out["race_id"].map(absent_sets)
        refund = np.array(
            [isinstance(a, set) and bool(a.intersection(e)) for a, e in zip(sets, entries)], dtype=bool
        )

    hit_amount = out["_hit_amount"].fillna(0).to_numpy(float)
    special_amount = out["_special_amount"].fillna(0).to_numpy(float)
    ret = np.where(refund, STAKE, np.where(hit_amount > 0, hit_amount, special_amount))
    out["stake"] = STAKE
    out["return"] = ret.astype(np.int64)
    out["hit"] = (~refund) & (hit_amount > 0)
    out["refund"] = refund
    return out.drop(columns=["_hit_amount", "_special_amount"])


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def bootstrap_roi_ci(
    stake_by_race: np.ndarray,
    return_by_race: np.ndarray,
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
    level: float = 0.95,
) -> list[float] | None:
    """レースごとのブートストラップで回収率の区間を出す(spec/03 6章)。

    買ったレースだけを渡すこと。レースが無ければ None。
    """
    stake = np.asarray(stake_by_race, dtype=float)
    ret = np.asarray(return_by_race, dtype=float)
    n = len(stake)
    if n == 0 or stake.sum() <= 0:
        return None
    rng = np.random.default_rng(seed)
    rois = np.empty(n_boot)
    chunk = max(1, min(n_boot, 2_000_000 // max(n, 1)))
    i = 0
    while i < n_boot:
        m = min(chunk, n_boot - i)
        idx = rng.integers(0, n, size=(m, n))
        s = stake[idx].sum(axis=1)
        rois[i : i + m] = np.divide(ret[idx].sum(axis=1), s, out=np.zeros(m), where=s > 0)
        i += m
    alpha = (1 - level) / 2
    return [float(np.quantile(rois, alpha)), float(np.quantile(rois, 1 - alpha))]


def max_drawdown(profit: np.ndarray) -> float:
    """累積損益の最大ドローダウン(円。正の値)。"""
    if len(profit) == 0:
        return 0.0
    cum = np.cumsum(np.asarray(profit, dtype=float))
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float(np.max(peak - cum))


def summarize(settled: pd.DataFrame, n_races: int, n_boot: int = BOOTSTRAP_N, seed: int = SEED) -> dict:
    """精算済みの買い目を集計する。"""
    by_race = (
        settled.groupby("race_id", sort=True)
        .agg(stake=("stake", "sum"), ret=("return", "sum"), hits=("hit", "sum"), bets=("stake", "size"))
        .reset_index()
    )
    stake = int(by_race["stake"].sum())
    ret = int(by_race["ret"].sum())
    hits = int(by_race["hits"].sum())
    bets = int(by_race["bets"].sum())
    races_hit = int((by_race["hits"] > 0).sum())
    by_race["month"] = by_race["race_id"].str.slice(0, 4) + "-" + by_race["race_id"].str.slice(4, 6)
    monthly = (
        by_race.groupby("month")
        .agg(races=("race_id", "size"), bets=("bets", "sum"), stake=("stake", "sum"), ret=("ret", "sum"))
        .reset_index()
    )
    return {
        "races": int(n_races),
        "bet_races": int(len(by_race)),
        "bets": bets,
        "hits": hits,
        "stake": stake,
        "return": ret,
        "roi": (ret / stake) if stake else None,
        "hit_rate": (races_hit / len(by_race)) if len(by_race) else None,  # 的中したレース / 買ったレース
        "bet_hit_rate": (hits / bets) if bets else None,  # 的中した点 / 買った点
        "refunds": int(settled["refund"].sum()) if len(settled) else 0,
        "roi_ci95": bootstrap_roi_ci(by_race["stake"].to_numpy(), by_race["ret"].to_numpy(), n_boot, seed),
        "max_drawdown": max_drawdown((by_race["ret"] - by_race["stake"]).to_numpy()),
        "by_month": [
            {
                "month": r.month,
                "races": int(r.races),
                "bets": int(r.bets),
                "stake": int(r.stake),
                "return": int(r.ret),
                "roi": (r.ret / r.stake) if r.stake else None,
            }
            for r in monthly.itertuples()
        ],
    }


def run_backtest(
    preds: pd.DataFrame,
    payouts: pd.DataFrame,
    strategy: str,
    bet_type: str,
    params: dict,
    odds: pd.DataFrame | None = None,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
) -> tuple[dict, pd.DataFrame]:
    """1つの戦略・券種・パラメータでバックテストする。

    Args:
        preds: 1行=1艇の予想(race_id, entry_number, p_win。is_absent・is_valid_result があれば使う)。
            予想は6艇で出したものをそのまま使う(欠場は返還として精算する。spec/04 4章)。

    Returns:
        (summary, bets)
    """
    if "is_valid_result" in preds.columns:
        preds = preds[preds.groupby("race_id")["is_valid_result"].transform("all")]
    race_ids, pw = p_win_matrix(preds)
    probs = cb.combination_prob_matrix(pw, bet_type, gamma2, gamma3)
    om = odds_matrix(odds, race_ids, bet_type) if odds is not None else None
    mask = select_bets(strategy, probs, pw, om, **params)

    r_idx, c_idx = np.nonzero(mask)
    labels = np.array(cb.combination_labels(bet_type))
    bets = pd.DataFrame(
        {
            "race_id": race_ids[r_idx],
            "bet_type": bet_type,
            "combination": labels[c_idx],
            "prob": probs[r_idx, c_idx],
        }
    )
    if om is not None:
        bets["odds_final"] = om[r_idx, c_idx]
        bets["ev_final"] = bets["prob"] * bets["odds_final"]
    absent = None
    if "is_absent" in preds.columns:
        absent = preds.loc[preds["is_absent"].astype(bool), ["race_id", "entry_number"]]
    settled = settle_bets(bets, payouts, absent)

    summary = {
        "strategy": strategy,
        "bet_type": bet_type,
        "params": dict(params),
        "leak_free": LEAK_FREE[strategy],
        "gamma2": gamma2,
        "gamma3": gamma3,
    }
    summary.update(summarize(settled, len(race_ids), n_boot, seed))
    return summary, settled


# ---------------------------------------------------------------------------
# interim の読み込みと CLI
# ---------------------------------------------------------------------------


def load_interim_table(name: str, race_ids: pd.Series | None = None) -> pd.DataFrame:
    """interim の payouts / odds を読む。特徴量担当の read_interim があればそれを使う。"""
    try:
        from src.features.build_interim import read_interim  # type: ignore[import-not-found]
    except ImportError:
        read_interim = None
    if read_interim is not None and (INTERIM_DIR / name).is_dir():
        if race_ids is not None and len(race_ids):
            ymd = pd.Series(race_ids).astype(str).str.slice(0, 8)
            d_from = pd.to_datetime(ymd.min()).date()
            d_to = pd.to_datetime(ymd.max()).date()
            df = read_interim(name, d_from, d_to)
            return df[df["race_id"].isin(set(race_ids))].reset_index(drop=True)
        return read_interim(name)
    d = INTERIM_DIR / name
    files = sorted(d.glob("*.parquet")) if d.is_dir() else []
    single = INTERIM_DIR / f"{name}.parquet"
    if not files and single.exists():
        files = [single]
    if not files:
        raise FileNotFoundError(f"interim の {name} が見つかりません: {d}")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if race_ids is not None:
        df = df[df["race_id"].isin(set(race_ids))]
    return df


def _parse_params(items: list[str]) -> dict:
    params = {}
    for item in items:
        key, _, value = item.partition("=")
        params[key] = int(value) if value.isdigit() else float(value)
    return params


def main() -> None:
    parser = argparse.ArgumentParser(description="回収率シミュレーション")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--period", default="test", choices=["valid", "test"])
    parser.add_argument("--strategy", default="top_k", choices=[*LEAK_FREE, "all"])
    parser.add_argument("--bet-type", default=None, choices=list(cb.BET_TYPES), help="省略時は MVP の3券種")
    parser.add_argument("--param", action="append", default=[], help="例: k=5(複数可)")
    parser.add_argument("--predictions", default=None, help="予想の parquet(省略時は evaluate の出力)")
    parser.add_argument("--payouts", default=None, help="payouts の parquet(省略時は data/interim/payouts)")
    parser.add_argument("--odds", default=None, help="odds の parquet(省略時は data/interim/odds)")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR))
    parser.add_argument("--n-boot", type=int, default=BOOTSTRAP_N)
    args = parser.parse_args()

    out_root = Path(args.reports_dir) / "backtest" / args.model_id
    pred_path = Path(args.predictions) if args.predictions else out_root / f"predictions_{args.period}.parquet"
    preds = pd.read_parquet(pred_path)
    payouts = pd.read_parquet(args.payouts) if args.payouts else load_interim_table("payouts", preds["race_id"])
    strategies = list(LEAK_FREE) if args.strategy == "all" else [args.strategy]
    needs_odds = any(not LEAK_FREE[s] for s in strategies)
    odds = None
    if needs_odds:
        odds = pd.read_parquet(args.odds) if args.odds else load_interim_table("odds", preds["race_id"])
    bet_types = [args.bet_type] if args.bet_type else list(MVP_BET_TYPES)

    for strategy in strategies:
        grid = [_parse_params(args.param)] if args.param and args.strategy != "all" else DEFAULT_GRID[strategy]
        runs, bets_all = [], []
        for bet_type in bet_types:
            for params in grid:
                summary, bets = run_backtest(
                    preds, payouts, strategy, bet_type, params,
                    odds=odds if not LEAK_FREE[strategy] else None, n_boot=args.n_boot,
                )
                runs.append(summary)
                bets_all.append(bets.assign(params=json.dumps(params, sort_keys=True)))
        out_dir = out_root / f"{strategy}_{args.period}"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"model_id": args.model_id, "period": args.period, "strategy": strategy, "runs": runs}
        (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        pd.concat(bets_all, ignore_index=True).to_parquet(out_dir / "bets.parquet", index=False)
        for r in runs:
            ci = r["roi_ci95"]
            ci_s = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "-"
            roi_s = f"{r['roi']:.3f}" if r["roi"] is not None else "-"
            print(f"{strategy:17s} {r['bet_type']:9s} {json.dumps(r['params'])}: 点数={r['bets']} 回収率={roi_s} 95%区間={ci_s}")
        print(f"→ {out_dir}")


if __name__ == "__main__":
    main()
