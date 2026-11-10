"""買うレースを絞る条件の探索(reports/race_filter.md)。

考え方:
- 締切前に分かる情報(モデルの p_win とそこから計算できる量、場、レース番号)だけでレースの条件を作る。
  確定オッズは使わない(締切後にしか分からないため)。
- 券種 × 点数(予想の上位K点、1点100円の定額)× 条件 の回収率を出す。
- 期間を前半と後半に分ける。条件のしきい値(分位点)は前半だけで決め、前半で良かった条件を後半で確かめる。
  試した条件の数はすべて記録する(たくさん試すと偶然良く見えるため)。
- 区間はレース単位のブートストラップ(200回、seed 固定、95%)。
- 賭け金は1点100円の定額。負けた後に増やす仕組みは入れない。公開用の出力は作らない(オッズの数値も出さない)。

CLI:
    python -m src.model.race_filter --preds <honest な予想の parquet> --split 2026-01-01 \
        --gamma2 0.7 --gamma3 0.5 [--live-from 2026-11-02 --live-to 2026-11-07]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.analytics_rules import MIN_COUNT_NORMAL
from src.model import combinations as cb
from src.model.backtest import STAKE, p_win_matrix, settle_bets
from src.model.config import REPORTS_DIR
from src.model.market_map import roi_ci

BET_JA = {"win": "単勝", "exacta": "2連単", "quinella": "2連複", "trifecta": "3連単", "trio": "3連複"}
# 券種ごとの点数
BET_KS: dict[str, tuple[int, ...]] = {
    "win": (1, 2, 3),
    "exacta": (1, 2, 3, 5, 10),
    "quinella": (1, 2, 3, 5, 10),
    "trifecta": (1, 2, 3, 5, 10),
    "trio": (1, 2, 3, 5, 10),
}
HI_QS = (0.5, 0.7, 0.8, 0.9)   # 「上位 x%」の条件に使う分位点(この値以上)
LO_QS = (0.1, 0.2, 0.3, 0.5)   # 「下位 x%」の条件に使う分位点(この値未満)
MIN_RACES = MIN_COUNT_NORMAL   # 前半でこれ未満の条件は選ばない
TOP_PER_CELL = 1               # 券種×点数ごとに前半で選ぶ条件の数(これに加えて前半で100%以上のもの)
NOTES = (
    "「候補」は全レースより明らかに良かったという意味で、後半で100%を超えたものは1つもない。候補の条件で買っても、"
    "長い目で見ると賭けたお金より払戻が少ない見込み(損の幅が小さくなるだけ)。",
    "後半の「全レース」と条件付きの回収率は同じ期間の部分集合どうしなので独立ではない。区間の下限が全レースを上回るのは目安。",
    "直前版(展示タイムなどを使う予想)と展開予想(逃げの確率など)は、学習に使っていない長い期間の予想が無いので条件に入れていない。"
    "1号艇の p_win と「1号艇が本命か」で代わりにしている。",
    "実運用の予想は別のモデル(本番の pre / preview)で、しきい値は長期用のモデルで決めたもの。件数も少ないので参考にとどめる。",
    "場の条件は24場それぞれを試しているので、単独の場が選ばれたものは偶然の可能性が高い(後半で多くが全レース並みに戻った)。",
)
FORBIDDEN_WORDS = ("最適", "必ず", "おすすめ", "増額", "増や")


# ---------------------------------------------------------------------------
# レースの特徴(締切前に分かるものだけ)
# ---------------------------------------------------------------------------


def race_table(preds: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """1行=1レースの条件用の表と、(レース数, 6) の p_win 行列を返す。

    preds: 1行=1艇(race_id, entry_number, p_win。任意で date, stadium_number)。
    """
    race_ids, pw = p_win_matrix(preds)
    srt = np.sort(pw, axis=1)[:, ::-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = -np.nansum(np.where(pw > 0, pw * np.log(pw), 0.0), axis=1)
    rid = pd.Series(race_ids).astype(str)
    rt = pd.DataFrame({
        "race_id": rid,
        "date": pd.to_datetime(rid.str.slice(0, 8), format="%Y%m%d"),
        "stadium": rid.str.slice(9, 11).astype(int),
        "race_number": rid.str.slice(12, 14).astype(int),
        "p_fav": srt[:, 0],
        "gap12": srt[:, 0] - srt[:, 1],
        "entropy": ent,
        "fav_is_1": pw.argmax(axis=1) == 0,
        "p_boat1": pw[:, 0],
    })
    return rt, pw


def topk_returns(race_ids: np.ndarray, pw: np.ndarray, payouts: pd.DataFrame, bet_type: str, kmax: int,
                 gamma2: float, gamma3: float, absent: pd.DataFrame | None = None) -> tuple[np.ndarray, np.ndarray]:
    """上位 kmax 点の (確率, 100円あたりの払戻) を (レース数, kmax) で返す。"""
    labels = np.array(cb.combination_labels(bet_type))
    probs = cb.combination_prob_matrix(pw, bet_type, gamma2, gamma3)
    k = min(kmax, len(labels))
    order = np.argsort(-probs, axis=1, kind="stable")[:, :k]
    P = np.take_along_axis(probs, order, axis=1)
    bets = pd.DataFrame({"race_id": np.repeat(np.asarray(race_ids).astype(str), k), "bet_type": bet_type,
                         "combination": labels[order].reshape(-1)})
    s = settle_bets(bets, payouts, absent)
    return P, s["return"].to_numpy(float).reshape(-1, k)


# ---------------------------------------------------------------------------
# 条件
# ---------------------------------------------------------------------------


@dataclass
class Condition:
    name: str        # 表示名
    column: str      # 使う列(all は全レース)
    op: str          # ">=", "<", "==", "all"
    value: float = 0.0

    def mask(self, df: pd.DataFrame) -> np.ndarray:
        if self.op == "all":
            return np.ones(len(df), dtype=bool)
        x = df[self.column].to_numpy()
        if self.op == ">=":
            return x >= self.value
        if self.op == "<":
            return x < self.value
        if self.op == "==":
            return x == self.value
        raise ValueError(self.op)


QUANT_COLS = {"p_fav": "本命の p_win", "gap12": "本命と2番手の差", "entropy": "確率のエントロピー",
              "p_boat1": "1号艇の p_win", "topsum": "上位K点の確率の合計"}


def make_conditions(first: pd.DataFrame, stadiums=range(1, 25)) -> list[Condition]:
    """条件の一覧。分位点のしきい値は前半(first)だけで決める(後半を見ない)。"""
    conds = [Condition("全レース", "", "all")]
    for col, ja in QUANT_COLS.items():
        if col not in first.columns:
            continue
        x = first[col].to_numpy(float)
        for q in HI_QS:
            conds.append(Condition(f"{ja}が上位{round((1 - q) * 100)}%(≥{np.quantile(x, q):.3f})", col, ">=",
                                   float(np.quantile(x, q))))
        for q in LO_QS:
            conds.append(Condition(f"{ja}が下位{round(q * 100)}%(<{np.quantile(x, q):.3f})", col, "<",
                                   float(np.quantile(x, q))))
    conds.append(Condition("1号艇が本命", "fav_is_1", "==", True))
    conds.append(Condition("1号艇が本命でない", "fav_is_1", "==", False))
    for lo, hi, ja in ((1, 4, "1〜4R"), (5, 8, "5〜8R"), (9, 12, "9〜12R")):
        conds.append(Condition(ja, "rband", "==", lo))
    for s in stadiums:
        conds.append(Condition(f"場{s:02d}", "stadium", "==", s))
    return conds


def add_rband(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(rband=np.select([df["race_number"] <= 4, df["race_number"] <= 8], [1, 5], 9))


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def summarize(ret: np.ndarray, k: int) -> dict:
    """ret はレースごとの払戻(円)。1レース k 点 × 100円。"""
    n = len(ret)
    stake = np.full(n, float(k * STAKE))
    out = {"races": int(n), "stake": int(stake.sum()), "return": int(round(ret.sum())),
           "roi": None, "ci95": None, "hit_rate": None}
    if n:
        out["roi"] = float(ret.sum() / stake.sum())
        out["hit_rate"] = float((ret > 0).mean())
        out["ci95"] = roi_ci(stake, ret)
    return out


def evaluate(rt: pd.DataFrame, pw: np.ndarray, payouts: pd.DataFrame, split: str, gamma2: float, gamma3: float,
             absent: pd.DataFrame | None = None) -> dict:
    """前半(split より前)で条件を選び、後半(split 以降)で確かめる。

    返り値: trials(試したすべての組の前半成績)、selected(選んだ組の前半・後半)、baseline、n_trials。
    """
    rt = add_rband(rt.reset_index(drop=True))
    first = (rt["date"] < pd.Timestamp(split)).to_numpy()
    trials, selected, baseline = [], [], {}
    for bt, ks in BET_KS.items():
        P, R = topk_returns(rt["race_id"].to_numpy(), pw, payouts, bt, max(ks), gamma2, gamma3, absent)
        for k in ks:
            ret = R[:, :k].sum(axis=1)
            df = rt.assign(topsum=P[:, :k].sum(axis=1))
            conds = make_conditions(df[first])
            cell = []
            for c in conds:
                m = c.mask(df)
                a, b = m & first, m & ~first
                n_a = int(a.sum())
                roi_a = float(ret[a].sum() / (n_a * k * STAKE)) if n_a else None
                row = {"bet_type": bt, "k": k, "condition": c.name, "rule": [c.column, c.op, c.value], "first_races": n_a, "first_roi": roi_a,
                       "_a": a, "_b": b}
                cell.append(row)
            base = cell[0]
            baseline[f"{bt}_k{k}"] = {"first": summarize(ret[base["_a"]], k), "second": summarize(ret[base["_b"]], k)}
            ok = [r for r in cell[1:] if r["first_races"] >= MIN_RACES and r["first_roi"] is not None]
            ok.sort(key=lambda r: -r["first_roi"])
            pick = ok[:TOP_PER_CELL] + [r for r in ok[TOP_PER_CELL:] if r["first_roi"] >= 1.0]
            for r in pick:
                sec = summarize(ret[r["_b"]], k)
                selected.append({"bet_type": bt, "k": k, "condition": r["condition"], "rule": r["rule"],
                                 "first": summarize(ret[r["_a"]], k), "second": sec,
                                 "baseline_second_roi": baseline[f"{bt}_k{k}"]["second"]["roi"],
                                 "is_candidate": is_candidate(sec, baseline[f"{bt}_k{k}"]["second"])})
            trials += [{kk: v for kk, v in r.items() if not kk.startswith("_")} for r in cell[1:]]
    return {"split": split, "n_trials": len(trials), "trials": trials, "selected": selected, "baseline": baseline}


def is_candidate(second: dict, base_second: dict) -> bool:
    """後半で「候補」とみなすか: 件数が200以上で、回収率が100%超、または区間の下限が全レースの回収率を上回る。"""
    if second["races"] < MIN_RACES or second["roi"] is None or second["ci95"] is None:
        return False
    return second["roi"] > 1.0 or (base_second["roi"] is not None and second["ci95"][0] > base_second["roi"])


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _ci(c) -> str:
    return "-" if not c else f"{c[0] * 100:.0f}〜{c[1] * 100:.0f}%"


def render(res: dict, info: dict) -> str:
    sel = res["selected"]
    cands = [s for s in sel if s["is_candidate"]]
    L = ["# 買うレースを絞る条件(race_filter)", "",
         "締切前に分かる情報(モデルの予想と場・レース番号)だけでレースを絞り、券種×点数ごとに回収率を見た結果。"
         "確定オッズは条件に使っていない。1点100円の定額(負けた後に賭け金を変える仕組みはない)。", "",
         "## データと方法", "",
         f"- 予想: {info.get('source', '')}", f"- 期間: {info.get('from')}〜{info.get('to')}、{info.get('races')}レース"
         f"(前半 〜{res['split']} の前日 / 後半 {res['split']}〜)",
         f"- Harville γ2={info.get('gamma2')}、γ3={info.get('gamma3')}",
         f"- 試した組(券種×点数×条件、全レースを除く): **{res['n_trials']}**。前半の件数が{MIN_RACES}未満の組は選ばない。",
         f"- 選び方: 券種×点数ごとに前半の回収率が最も高い条件{TOP_PER_CELL}つ + 前半で100%以上の条件。"
         "しきい値(分位点)は前半だけで決めた。",
         "- 候補の基準: 後半で200件以上、かつ「回収率が100%超」または「95%区間の下限が全レース(同じ券種・点数)の回収率を上回る」。",
         "- 区間: レース単位のブートストラップ200回(seed 固定)の95%。試した数が多いので、前半の数字は偶然の上振れを含む。",
         "", "## 結論", ""]
    if cands:
        over = [c for c in cands if c["second"]["roi"] > 1.0]
        L.append(f"候補は {len(cands)} 件(うち後半で100%を超えたもの {len(over)} 件)。"
                 f"前半の選び方で {res['n_trials']} 組を試している点に注意。")
    else:
        L.append("**候補はゼロ**。前半で良く見えた条件は、後半では全レースと見分けのつかない回収率に戻った。")
    L += ["", "## 全レースの回収率(条件なし)", "", "| 券種 | 点数 | 前半 件数 | 前半 回収率 [95%] | 後半 件数 | 後半 回収率 [95%] |",
          "|---|---|---|---|---|---|"]
    for key, b in res["baseline"].items():
        bt, k = key.rsplit("_k", 1)
        L.append(f"| {BET_JA[bt]} | {k} | {b['first']['races']} | {_pct(b['first']['roi'])} [{_ci(b['first']['ci95'])}] | "
                 f"{b['second']['races']} | {_pct(b['second']['roi'])} [{_ci(b['second']['ci95'])}] |")
    L += ["", "## 前半で選んだ条件と後半の成績", "",
          "| 券種 | 点数 | 条件 | 前半 件数 | 前半 回収率 [95%] | 後半 件数 | 後半 回収率 [95%] | 後半 全レース | 候補 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s in sel:
        f, g = s["first"], s["second"]
        L.append(f"| {BET_JA[s['bet_type']]} | {s['k']} | {s['condition']} | {f['races']} | {_pct(f['roi'])} [{_ci(f['ci95'])}] | "
                 f"{g['races']} | {_pct(g['roi'])} [{_ci(g['ci95'])}] | {_pct(s['baseline_second_roi'])} | "
                 f"{'○' if s['is_candidate'] else ''} |")
    if info.get("live"):
        L += ["", "## 参考: 実運用の予想(件数が少ないので判断には使わない)", "", info["live"]]
    if info.get("notes"):
        L += ["", "## 注意", ""] + [f"- {n}" for n in info["notes"]]
    return "\n".join(L) + "\n"


def prepare(preds: pd.DataFrame, payouts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """結果が確定し払戻があるレースだけに絞り、並べ替える。欠場艇の表(返還の計算用)も返す。"""
    if "is_valid_result" in preds.columns:
        ok = preds["is_valid_result"].astype("boolean").fillna(False).astype(bool)
        preds = preds[ok.groupby(preds["race_id"]).transform("all")]
    preds = preds.assign(race_id=preds["race_id"].astype(str))
    preds = preds[preds["race_id"].isin(set(payouts["race_id"].astype(str)))].sort_values(["race_id", "entry_number"])
    absent = None
    if "is_absent" in preds.columns:
        absent = preds.loc[preds["is_absent"].astype("boolean").fillna(False).astype(bool), ["race_id", "entry_number"]]
    return preds, absent


def run(preds: pd.DataFrame, payouts: pd.DataFrame, split: str, gamma2: float, gamma3: float) -> dict:
    """結果が確定したレースだけで evaluate する。"""
    preds, absent = prepare(preds, payouts)
    rt, pw = race_table(preds)
    return evaluate(rt, pw, payouts.assign(race_id=payouts["race_id"].astype(str)), split, gamma2, gamma3, absent)


def apply_rules(preds: pd.DataFrame, payouts: pd.DataFrame, rules: list[dict], gamma2: float, gamma3: float) -> list[dict]:
    """前半で決めた条件(しきい値は固定)を別の予想にそのまま当てはめて成績を出す(実運用の確認用)。"""
    preds, absent = prepare(preds, payouts)
    if preds.empty:
        return []
    rt, pw = race_table(preds)
    rt = add_rband(rt)
    out = []
    for r in rules:
        bt, k = r["bet_type"], r["k"]
        P, R = topk_returns(rt["race_id"].to_numpy(), pw, payouts.assign(race_id=payouts["race_id"].astype(str)),
                            bt, k, gamma2, gamma3, absent)
        df = rt.assign(topsum=P.sum(axis=1))
        col, op, val = r["rule"]
        m = Condition(r["condition"], col, op, val).mask(df)
        ret = R.sum(axis=1)
        out.append({"bet_type": bt, "k": k, "condition": r["condition"], "filtered": summarize(ret[m], k),
                    "all": summarize(ret, k)})
    return out


def render_live(rows: list[dict], label: str) -> str:
    L = [f"### {label}", "", "| 券種 | 点数 | 条件 | 件数 | 回収率 [95%] | 全レース 件数 | 全レース 回収率 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        f, a = r["filtered"], r["all"]
        L.append(f"| {BET_JA[r['bet_type']]} | {r['k']} | {r['condition']} | {f['races']} | {_pct(f['roi'])} [{_ci(f['ci95'])}] | "
                 f"{a['races']} | {_pct(a['roi'])} |")
    return "\n".join(L)


def main(argv=None) -> int:
    from src.model.backtest import load_interim_table

    ap = argparse.ArgumentParser(description="買うレースを絞る条件の探索")
    ap.add_argument("--preds", required=True)
    ap.add_argument("--split", required=True, help="後半の開始日(YYYY-MM-DD)")
    ap.add_argument("--gamma2", type=float, default=0.7)
    ap.add_argument("--gamma3", type=float, default=0.5)
    ap.add_argument("--source", default="")
    ap.add_argument("--live-from", default=None, help="実運用の予想で確かめる期間(任意)")
    ap.add_argument("--live-to", default=None)
    ap.add_argument("--out", default=str(Path(REPORTS_DIR) / "race_filter.md"))
    a = ap.parse_args(argv)
    preds = pd.read_parquet(a.preds)
    pay = load_interim_table("payouts", preds["race_id"].astype(str).unique())
    res = run(preds, pay, a.split, a.gamma2, a.gamma3)
    rid = preds["race_id"].astype(str)
    info = {"source": a.source or a.preds, "from": str(pd.to_datetime(rid.str.slice(0, 8).min()).date()),
            "to": str(pd.to_datetime(rid.str.slice(0, 8).max()).date()), "races": sum(b["first"]["races"] + b["second"]["races"] for b in list(res["baseline"].values())[:1]),
            "gamma2": a.gamma2, "gamma3": a.gamma3, "notes": NOTES}
    if a.live_from and a.live_to:
        from src.features.build_interim import read_interim
        from src.model.analytics_eval import load_live

        live = load_live(a.live_from, a.live_to)
        live = live[live["finish_position"].notna().groupby(live["race_id"]).transform("any")]
        lpay = read_interim("payouts", pd.Timestamp(a.live_from).date(), pd.Timestamp(a.live_to).date())
        rules = [s for s in res["selected"] if s["is_candidate"]] or res["selected"]
        parts = []
        for v in ("pre", "preview"):
            d = live[live["version"] == v]
            if len(d):
                parts.append(render_live(apply_rules(d, lpay, rules, a.gamma2, a.gamma3),
                                         f"{v}({a.live_from}〜{a.live_to})"))
        info["live"] = "\n\n".join(parts)
    Path(a.out).write_text(render(res, info), encoding="utf-8")
    Path(a.out).with_suffix(".json").write_text(json.dumps(
        {k: v for k, v in res.items() if k != "trials"}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
