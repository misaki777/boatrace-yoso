"""回収率の地図(市場のゆがみ探し)。reports/market_map.md / .json と、公開用の集計表 market_map_public.json を出す。

データ: 2026年の確定オッズ(interim の odds、turnmark)と払戻。全組合せを100円ずつ買ったとした回収率を、
券種 × 人気の度合い(オッズ帯・人気順位)× 条件(場・時間帯・グレード・季節・艇番・直前情報の有無)で集計する。

注意(必ずレポートに書く):
- **確定オッズは締切後にしかわからない(後知恵)**。ここでの回収率は「その人気の組合せを全部買ったら」の参考値。
  実際に買う判断には、締切前オッズ(spec/13。2026-11-07 から記録中)で確かめ直す必要がある。
- 多重比較: 調べるマス目が多いほど、偶然高い回収率のマス目が出る。2026年を前半(1〜6月)・後半(7〜10月)に分け、
  前半で見つけたマス目が後半でも高いか(再現したか)を見る。候補の数で割り引いた区間(ボンフェローニ)も添える。
- 返還・中止のあるレースは外す。区間はレース単位のブートストラップ(numpy、200回、seed 固定)。
- 「最適」「必ず」は使わない。賭け金を増やす提案はしない。公開用の表にはオッズの数値を入れない(人気順位の帯と回収率・件数だけ)。
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd

from src.common.analytics_rules import grade_bucket, sample_level
from src.model.config import REPORTS_DIR

BET_TYPES = ("win", "place", "exacta", "quinella", "quinella_place", "trifecta", "trio")
BET_JA = {"win": "単勝", "place": "複勝", "exacta": "2連単", "quinella": "2連複", "quinella_place": "拡連複",
          "trifecta": "3連単", "trio": "3連複"}
ODDS_BANDS = [1, 2, 5, 10, 20, 50, 100, 300, 1000, np.inf]
POP_BANDS = [(1, 1), (2, 3), (4, 6), (7, 10), (11, 20), (21, 40), (41, 120)]  # 人気順位(1番人気〜)
SEASON = {1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋", 11: "秋", 12: "冬"}
N_BOOT = 200
SEED = 20261107
MIN_RACES = 200          # これ未満のマス目は候補にしない
FIRST_HALF = ("2026-01-01", "2026-06-30")
SECOND_HALF = ("2026-07-01", "2026-10-31")
FORBIDDEN_WORDS = ("最適", "必ず", "おすすめ", "増額", "増や")


def odds_band(o: np.ndarray) -> np.ndarray:
    idx = np.digitize(o, ODDS_BANDS[1:-1])
    labels = [f"{ODDS_BANDS[i]:g}〜{ODDS_BANDS[i + 1]:g}倍" if np.isfinite(ODDS_BANDS[i + 1]) else f"{ODDS_BANDS[i]:g}倍〜"
              for i in range(len(ODDS_BANDS) - 1)]
    return np.array(labels, dtype=object)[idx]


def pop_band(rank: np.ndarray) -> np.ndarray:
    out = np.empty(len(rank), dtype=object)
    for lo, hi in POP_BANDS:
        out[(rank >= lo) & (rank <= hi)] = f"{lo}番人気" if lo == hi else f"{lo}〜{hi}番人気"
    return out


def roi_ci(stake: np.ndarray, ret: np.ndarray, n_boot: int = N_BOOT, level: float = 0.95) -> list[float] | None:
    """レース単位のブートストラップ区間(numpy。seed 固定)。"""
    n = len(stake)
    if n == 0 or stake.sum() <= 0:
        return None
    rng = np.random.default_rng(SEED)
    rois = np.empty(n_boot)
    chunk = max(1, 4_000_000 // n)
    for i in range(0, n_boot, chunk):
        m = min(chunk, n_boot - i)
        idx = rng.integers(0, n, (m, n))
        rois[i:i + m] = ret[idx].sum(axis=1) / stake[idx].sum(axis=1)
    a = (1 - level) / 2
    return [float(np.quantile(rois, a)), float(np.quantile(rois, 1 - a))]


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------


def load_bets(d_from: str, d_to: str) -> pd.DataFrame:
    """1行=1組合せ: race_id, bet_type, combination, odds(複勝・拡連複は下限), ret(100円あたりの払戻), 人気順位, 条件。"""
    from src.features.build_interim import read_interim

    a, b = pd.Timestamp(d_from).date(), pd.Timestamp(d_to).date()
    races = read_interim("races", a, b)
    races["race_id"] = races["race_id"].astype(str)
    ok = races["is_valid_result"].astype("boolean").fillna(False) & ~races["has_refund"].astype("boolean").fillna(False)
    races = races[ok.astype(bool)]
    ent = read_interim("entries", a, b)
    ent["race_id"] = ent["race_id"].astype(str)
    pv = ent.groupby("race_id")["pv_exhibition_time"].apply(lambda s: bool(s.notna().all() and len(s) == 6))
    attrs = pd.DataFrame({
        "race_id": races["race_id"],
        "date": pd.to_datetime(races["date"]),
        "stadium": races["stadium_number"].astype(int),
        "time": np.where(pd.to_datetime(races["closed_at"]).dt.hour >= 17, "ナイター(17時以降)", "昼"),
        "grade": races["grade_number"].map(lambda g: {"top": "SG・G1", "g2g3": "G2・G3", "general": "一般"}.get(
            grade_bucket(None if pd.isna(g) else int(g)))),
    })
    attrs["season"] = attrs["date"].dt.month.map(SEASON)
    attrs["preview"] = attrs["race_id"].map(pv).map({True: "直前情報あり", False: "直前情報なし"}).fillna("直前情報なし")

    odds = read_interim("odds", a, b)
    odds["race_id"] = odds["race_id"].astype(str)
    odds = odds[odds["race_id"].isin(set(attrs["race_id"]))]
    odds["o"] = odds["odds"].fillna(odds["odds_lower"]).astype(float)
    odds = odds[odds["o"] > 0]
    pay = read_interim("payouts", a, b)
    pay["race_id"] = pay["race_id"].astype(str)
    pay = pay[pay["combination"].notna()].groupby(["race_id", "bet_type", "combination"], as_index=False)["amount"].sum()
    df = odds.merge(pay, on=["race_id", "bet_type", "combination"], how="left")
    df["ret"] = df["amount"].fillna(0).astype(float)
    df["rank"] = df.groupby(["race_id", "bet_type"])["o"].rank(method="first").astype(int)
    df["odds_band"] = odds_band(df["o"].to_numpy())
    df["pop_band"] = pop_band(df["rank"].to_numpy())
    # 艇番: 単勝・複勝はその艇、ほかは1着(順不同は小さい番号)の艇
    df["boat"] = df["combination"].str.slice(0, 1).astype(int)
    df = df.merge(attrs, on="race_id", how="left")
    return df[["race_id", "date", "bet_type", "combination", "o", "ret", "rank", "odds_band", "pop_band", "boat",
               "stadium", "time", "grade", "season", "preview"]]


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def cell_table(df: pd.DataFrame, keys: list[str], with_ci: bool = True) -> pd.DataFrame:
    """keys ごとの回収率(全組合せ100円)・件数(レース数・点数)・区間。"""
    rows = []
    for k, g in df.groupby(keys, sort=False, observed=True):
        per_race = g.groupby("race_id")["ret"].agg(["sum", "size"])
        stake = per_race["size"].to_numpy(float) * 100
        ret = per_race["sum"].to_numpy(float)
        k = k if isinstance(k, tuple) else (k,)
        row = dict(zip(keys, k))
        row.update({"races": int(len(per_race)), "bets": int(len(g)), "roi": float(ret.sum() / stake.sum()),
                    "hit_rate": float((g["ret"] > 0).mean())})
        if with_ci:
            row["roi_ci95"] = roi_ci(stake, ret)
        rows.append(row)
    return pd.DataFrame(rows)


DIMENSIONS = ("stadium", "time", "grade", "season", "boat", "preview")


def discover(first: pd.DataFrame, second: pd.DataFrame, band: str = "pop_band", top_n: int = 30) -> dict:
    """前半で回収率の高いマス目(券種 × 人気帯 × 条件1つ)を選び、後半で再現するかを見る。

    選び方: 前半で MIN_RACES 以上のマス目のうち、区間の下限の高い順に top_n。
    再現: 後半の回収率が「その券種・人気帯の後半全体の回収率」を上回り、かつ 1.0 以上。
    多重比較: 調べたマス目の数 K で割り引いた区間(水準 1−0.05/K)の下限も前半について出す。
    """
    tested = []
    for d in DIMENSIONS:
        t = cell_table(first, ["bet_type", band, d], with_ci=False)
        t = t[t["races"] >= MIN_RACES].rename(columns={d: "value"}).assign(dimension=d)
        tested.append(t)
    tested = pd.concat(tested, ignore_index=True)
    K = len(tested)
    # まず点推定で絞ってから区間を計算する(計算量を抑える)
    pre = tested.sort_values("roi", ascending=False).head(top_n * 3)
    base2 = cell_table(second, ["bet_type", band], with_ci=False).set_index(["bet_type", band])["roi"]
    cands = []
    for r in pre.itertuples():
        g1 = first[(first["bet_type"] == r.bet_type) & (first[band] == getattr(r, band)) & (first[r.dimension] == r.value)]
        g2 = second[(second["bet_type"] == r.bet_type) & (second[band] == getattr(r, band)) & (second[r.dimension] == r.value)]
        c1 = _ci_of(g1)
        c1b = _ci_of(g1, level=1 - 0.05 / K)
        roi2, ci2, n2 = (_roi_of(g2), _ci_of(g2), g2["race_id"].nunique()) if len(g2) else (None, None, 0)
        b2 = float(base2.get((r.bet_type, getattr(r, band)), np.nan))
        cands.append({"bet_type": r.bet_type, "band": getattr(r, band), "dimension": r.dimension, "value": _py(r.value),
                      "first": {"races": int(r.races), "roi": r.roi, "roi_ci95": c1, "roi_ci_bonferroni": c1b},
                      "second": {"races": int(n2), "roi": roi2, "roi_ci95": ci2, "band_average": b2},
                      "replicated": bool(roi2 is not None and n2 >= 30 and roi2 >= 1.0 and roi2 > b2)})
    cands = sorted(cands, key=lambda c: -(c["first"]["roi_ci95"][0] if c["first"]["roi_ci95"] else -9))[:top_n]
    return {"tested_cells": K, "candidates": cands}


def _roi_of(g: pd.DataFrame) -> float:
    return float(g["ret"].sum() / (100 * len(g)))


def _ci_of(g: pd.DataFrame, level: float = 0.95):
    pr = g.groupby("race_id")["ret"].agg(["sum", "size"])
    return roi_ci(pr["size"].to_numpy(float) * 100, pr["sum"].to_numpy(float), level=level)


def _py(v):
    return int(v) if isinstance(v, (np.integer,)) else v


# ---------------------------------------------------------------------------
# モデルの確率と市場の確率のずれ(後知恵の参考値)
# ---------------------------------------------------------------------------


def model_vs_market(model_id: str = "pre_20261106_7d3a") -> dict:
    """モデルの test 期間(学習・選択に使っていない)で、モデルの確率 / 市場の確率 の比の帯ごとの回収率。

    市場の確率 = (1/オッズ) をレース・券種ごとに合計1に正規化したもの。単勝・2連単・3連単。
    """
    from src.model import combinations as cb
    from src.model.backtest import p_win_matrix
    from src.model.data import features_path, filter_period, load_features
    from src.model.lgbm import gammas_from_meta, load_model
    from src.model.report import predict_p

    booster, meta = load_model(model_id)
    per = meta["periods"]
    part = filter_period(load_features(features_path(meta["variant"])), per["test_from"], per["test_to"])
    preds = part[["race_id", "entry_number"]].assign(p_win=predict_p(part, booster, meta), race_id=part["race_id"].astype(str))
    rid, pw = p_win_matrix(preds)
    g2, g3 = gammas_from_meta(meta)
    bets = load_bets(per["test_from"], per["test_to"])
    out = {"model_id": model_id, "period": {"from": per["test_from"], "to": per["test_to"]}, "bet_types": {}}
    for bt in ("win", "exacta", "trifecta"):
        P = cb.combination_prob_matrix(pw, bt, g2, g3)
        labels = cb.combination_labels(bt)
        mp = pd.DataFrame({"race_id": np.repeat(rid, len(labels)), "combination": np.tile(labels, len(rid)),
                           "p_model": P.reshape(-1)})
        b = bets[bets["bet_type"] == bt].merge(mp, on=["race_id", "combination"])
        inv = 1 / b["o"]
        b["p_market"] = inv / inv.groupby(b["race_id"]).transform("sum")
        b["ratio"] = b["p_model"] / b["p_market"]
        edges = [0, 0.5, 0.8, 1.0, 1.25, 1.5, 2.0, np.inf]
        b["ratio_band"] = pd.cut(b["ratio"], edges, right=False, labels=[f"{edges[i]:g}〜{edges[i + 1]:g}" for i in range(len(edges) - 1)])
        t = cell_table(b, ["ratio_band"])
        out["bet_types"][bt] = t.to_dict("records")
    return out


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


def build(d_from: str = "2026-01-01", d_to: str = "2026-10-31") -> tuple[dict, dict]:
    df = load_bets(d_from, d_to)
    overall = cell_table(df, ["bet_type"])
    by_odds = cell_table(df, ["bet_type", "odds_band"])
    by_pop = cell_table(df, ["bet_type", "pop_band"])
    first = df[(df["date"] >= FIRST_HALF[0]) & (df["date"] <= FIRST_HALF[1])]
    second = df[(df["date"] >= SECOND_HALF[0]) & (df["date"] <= SECOND_HALF[1])]
    disc = discover(first, second, "pop_band")
    mvm = model_vs_market()
    report = {
        "period": {"from": d_from, "to": d_to}, "races": int(df["race_id"].nunique()),
        "note": "確定オッズ(締切後にしかわからない)での参考値。全組合せを100円ずつ買ったとした回収率。"
                "実際の判断には締切前オッズでの確認が必要。",
        "overall": overall.to_dict("records"), "by_odds_band": by_odds.to_dict("records"),
        "by_popularity": by_pop.to_dict("records"), "discovery": disc, "model_vs_market": mvm,
    }
    # 公開用: オッズの数値・オッズ帯は入れず、人気順位の帯・回収率・件数だけ
    public = {"note": "全組合せを100円ずつ買ったとした回収率(結果の払戻金から計算)。人気は確定オッズでの順位(締切後にわかるもの。後知恵を含む参考値)。",
              "period": report["period"],
              "by_popularity": [{"bet_type": r["bet_type"], "popularity": r["pop_band"], "races": r["races"],
                                 "bets": r["bets"], "roi": round(r["roi"], 4),
                                 "roi_ci95": [round(x, 4) for x in r["roi_ci95"]] if r["roi_ci95"] else None,
                                 "level": sample_level(r["races"])} for r in report["by_popularity"]]}
    return report, public


def render(r: dict) -> str:
    L = ["# 回収率の地図(市場のゆがみ探し)", "", "`python -m src.model.market_map` が自動で生成(手で編集しない)。", "",
         f"期間: {r['period']['from']}〜{r['period']['to']}、返還・中止を除く {r['races']} レース。", "",
         f"**{r['note']}**", "", "## 券種ごと(全組合せを買った場合)", "", "| 券種 | レース | 点数 | 回収率 | 95%区間 |", "|---|---|---|---|---|"]
    for x in r["overall"]:
        L.append(f"| {BET_JA[x['bet_type']]} | {x['races']} | {x['bets']} | {x['roi']:.3f} | [{x['roi_ci95'][0]:.3f}, {x['roi_ci95'][1]:.3f}] |")
    for key, col, title in (("by_odds_band", "odds_band", "オッズ帯"), ("by_popularity", "pop_band", "人気順位")):
        L += ["", f"## 券種 × {title}", "", f"| 券種 | {title} | 点数 | 回収率 | 95%区間 | 的中率 |", "|---|---|---|---|---|---|"]
        for x in sorted(r[key], key=lambda x: (BET_TYPES.index(x["bet_type"]), x[col])):
            ci = x["roi_ci95"]
            L.append(f"| {BET_JA[x['bet_type']]} | {x[col]} | {x['bets']} | {x['roi']:.3f} | "
                     f"[{ci[0]:.3f}, {ci[1]:.3f}] | {x['hit_rate']:.4f} |")
    d = r["discovery"]
    L += ["", "## 前半(1〜6月)で見つけて、後半(7〜10月)で確かめた", "",
          f"調べたマス目(券種 × 人気帯 × 条件1つ、前半 {MIN_RACES} レース以上): {d['tested_cells']}。"
          "区間の下限の高い順に上位を並べる。再現 = 後半の回収率が 1.0 以上で、その券種・人気帯の後半平均より高い。", "",
          "| 券種 | 人気帯 | 条件 | 前半 レース | 前半 回収率 [95%] | 前半 [割り引いた区間] | 後半 レース | 後半 回収率 [95%] | 後半 平均 | 再現 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for c in d["candidates"]:
        f, s = c["first"], c["second"]
        fmt = lambda ci: "-" if not ci else f"[{ci[0]:.2f}, {ci[1]:.2f}]"  # noqa: E731
        L.append(f"| {BET_JA[c['bet_type']]} | {c['band']} | {c['dimension']}={c['value']} | {f['races']} | {f['roi']:.3f} {fmt(f['roi_ci95'])} | "
                 f"{fmt(f['roi_ci_bonferroni'])} | {s['races']} | {'-' if s['roi'] is None else f'{s['roi']:.3f}'} {fmt(s['roi_ci95'])} | "
                 f"{s['band_average']:.3f} | {'○' if c['replicated'] else '×'} |")
    m = r["model_vs_market"]
    L += ["", f"## モデルの確率 / 市場の確率(確定オッズ。後知恵の参考値。{m['period']['from']}〜{m['period']['to']}、{m['model_id']})", "",
          "| 券種 | 比の帯 | 点数 | 回収率 | 95%区間 |", "|---|---|---|---|---|"]
    for bt, rows in m["bet_types"].items():
        for x in rows:
            ci = x["roi_ci95"]
            L.append(f"| {BET_JA[bt]} | {x['ratio_band']} | {x['bets']} | {x['roi']:.3f} | "
                     f"{'-' if not ci else f'[{ci[0]:.3f}, {ci[1]:.3f}]'} |")
    L += ["", "- 締切前オッズ(spec/13。2026-11-07 から記録中)で同じ集計をし直してから判断する。", ""]
    text = "\n".join(L)
    for w in FORBIDDEN_WORDS:
        assert w not in text, w
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="回収率の地図")
    parser.add_argument("--from", dest="d_from", default="2026-01-01")
    parser.add_argument("--to", dest="d_to", default="2026-10-31")
    args = parser.parse_args()
    report, public = build(args.d_from, args.d_to)
    (REPORTS_DIR / "market_map.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (REPORTS_DIR / "market_map_public.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS_DIR / "market_map.md").write_text(render(report), encoding="utf-8")
    print(f"→ {REPORTS_DIR / 'market_map.md'}")


if __name__ == "__main__":
    main()
