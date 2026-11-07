"""仮想の購入者ごとの「反省点」(利用者の要望。目標は回収率100%超)。personas.build_personas が各人に付ける。

1. 負けの内訳: 場・時間帯・堅さ・天候・風・買い目の順位・配当の大きさ・版ごとの収支。負けのうち何割がどこから来たか。
2. 改善の候補: 「買わない条件」「点数を減らす」「券種を変える」を、**過去の検証期間と実運用の両方で回収率が上がるもの
   だけ**選ぶ。件数(analytics_rules の区分)が「件数不足」なら出さない。「参考」の件数なら「参考」と付ける。
   - 1レースの予算(1,000円)は変えない。**賭け金を増やす・取り返す系の候補は作らない**(候補の種類にそもそも無い)。
   - 結果を見ないとわからない条件(配当の大きさ・決まり手)は、内訳には出すが「買わない条件」にはしない。
3. 文: 決まった型の短い日本語。「最適」「必ず」などは使わない(FORBIDDEN_WORDS でテストする)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common.analytics_rules import (
    SAMPLE_INSUFFICIENT,
    SAMPLE_NORMAL,
    sample_level,
    time_band,
    wind_bucket,
)
from src.common.codes import STADIUM_NAMES, WEATHER_NAMES
from src.model.simulator import allocate_bets

FORBIDDEN_WORDS = ("最適", "必ず", "おすすめ", "増額", "増や", "取り返", "倍に", "マーチン")
MIN_IMPROVE = 0.005  # 回収率の改善がこれ未満(0.5ポイント未満)の候補は出さない
MAX_CANDIDATES = 3
# 買わない条件にしてよい区分(予想の時点でわかるもの)
SKIP_DIMENSIONS = ("stadium", "time", "conf", "weather", "wind")
DIM_LABELS = {"stadium": "場", "time": "時間帯", "conf": "堅さ", "weather": "天候", "wind": "風", "rank": "買い目の順位",
              "payout": "配当の大きさ", "version": "版"}
_TIME = {"morning": "12時より前", "day": "12〜17時", "night": "17時以降"}
_CONF = {"strong": "「堅」", "mid": "「中」", "rough": "「荒」"}
_WIND = {"none": "無風", "tail": "追い風", "head": "向かい風", "cross": "横風", "weak": "弱", "mid": "中", "strong": "強"}
_VERSION = {"pre": "前日版", "preview": "直前版"}


def _wind_label(b: str | None) -> str | None:
    if b is None:
        return None
    if b == "none":
        return "無風"
    d, s = b.split("_")
    return f"{_WIND[d]}({_WIND[s]})"


def _payout_band(amount) -> str | None:
    if amount is None or pd.isna(amount):
        return None
    a = float(amount)
    return "3連単の配当 1,000円未満" if a < 1000 else ("3連単の配当 1,000〜3,000円" if a < 3000 else (
        "3連単の配当 3,000〜10,000円" if a < 10000 else "3連単の配当 10,000円以上(万舟)"))


def race_attributes(races: pd.DataFrame, payouts: pd.DataFrame, pfav: pd.Series,
                    version: pd.Series | None = None) -> pd.DataFrame:
    """race_id → 各区分のラベル(stadium, time, conf, weather, wind, payout, version)。

    races: interim の races(race_id, stadium_number, closed_at, pv_weather_number, pv_wind_direction_number, pv_wind_speed)
    pfav: race_id → 本命の確率(予想)。version: race_id → 'pre' / 'preview'(実運用だけ)。
    """
    r = races.copy()
    r["race_id"] = r["race_id"].astype(str)
    r = r.drop_duplicates("race_id").set_index("race_id")
    a = pd.DataFrame(index=pfav.index)
    a["stadium"] = r["stadium_number"].reindex(a.index).map(lambda x: STADIUM_NAMES.get(int(x)) if pd.notna(x) else None)
    ca = pd.to_datetime(r["closed_at"].reindex(a.index))
    a["time"] = [(_TIME[time_band(c)] if pd.notna(c) else None) for c in ca]
    a["conf"] = [(_CONF["strong"] if p >= 0.5 else (_CONF["mid"] if p >= 0.35 else _CONF["rough"])) for p in pfav]
    a["weather"] = r["pv_weather_number"].reindex(a.index).map(
        lambda x: WEATHER_NAMES.get(int(x)) if pd.notna(x) else None)
    a["wind"] = [_wind_label(wind_bucket(None if pd.isna(d) else int(d), None if pd.isna(s) else float(s)))
                 for d, s in zip(r["pv_wind_direction_number"].reindex(a.index), r["pv_wind_speed"].reindex(a.index))]
    tri = payouts[(payouts["bet_type"] == "trifecta") & payouts["combination"].notna()].copy()
    tri["race_id"] = tri["race_id"].astype(str)
    a["payout"] = tri.groupby("race_id")["amount"].max().reindex(a.index).map(_payout_band)
    if version is not None:
        a["version"] = version.reindex(a.index).map(_VERSION)
    return a


# ---------------------------------------------------------------------------
# 1. 負けの内訳
# ---------------------------------------------------------------------------


def _roi(stake, ret) -> float | None:
    s = float(np.sum(stake))
    return float(np.sum(ret)) / s if s > 0 else None


def loss_breakdown(res: pd.DataFrame, attrs: pd.DataFrame) -> list[dict]:
    """区分ごとに、負け(収支がマイナスの区分の損失)のうち、その区分が占める割合。買い目の順位も含む。"""
    b = res[res["bought"]].set_index("race_id").join(attrs, how="left")
    b["profit"] = b["return"] - b["stake"]
    out = []
    dims = [d for d in ("stadium", "time", "conf", "weather", "wind", "payout", "version") if d in b.columns]
    for d in dims:
        g = b.groupby(d).agg(races=("stake", "size"), stake=("stake", "sum"), ret=("return", "sum"), profit=("profit", "sum"))
        out += _dim_rows(d, g)
    bets = res.attrs.get("bets")
    if bets is not None and len(bets) and bets["rank"].max() > 1:
        bt = bets.assign(ret=bets["amount"] * bets["rate"])
        bt = bt[bt["amount"] > 0]
        g = bt.groupby("rank").agg(races=("race_id", "nunique"), stake=("amount", "sum"), ret=("ret", "sum"))
        g["profit"] = g["ret"] - g["stake"]
        g.index = [f"{int(i)}点目の買い目" for i in g.index]
        out += _dim_rows("rank", g)
    return out


def _dim_rows(dim: str, g: pd.DataFrame) -> list[dict]:
    loss = (-g["profit"]).clip(lower=0)
    total = float(loss.sum())
    stake_total = float(g["stake"].sum())
    rows = []
    if len(g) < 2:  # 区分が1つしかない(例: 堅実さんの「堅」)なら内訳にならない
        return rows
    for cat, row in g.iterrows():
        lvl = sample_level(int(row["races"]))
        rows.append({"dimension": dim, "category": str(cat), "races": int(row["races"]), "level": lvl,
                     "profit": int(round(row["profit"])),
                     "loss_share": round(float(loss[cat]) / total, 3) if total > 0 else 0.0,
                     "stake_share": round(float(row["stake"]) / stake_total, 3) if stake_total > 0 else 0.0,
                     "roi": round(row["ret"] / row["stake"], 4) if row["stake"] and lvl != SAMPLE_INSUFFICIENT else None})
    return rows


# ---------------------------------------------------------------------------
# 2. 改善の候補
# ---------------------------------------------------------------------------


def _skip_candidates(res: dict, attrs: dict) -> list[dict]:
    """「〜のレースを買わない」。両方の期間で、外すレース・残るレースとも件数不足でないこと。"""
    out = []
    b = {k: res[k][res[k]["bought"]].set_index("race_id").join(attrs[k], how="left") for k in ("history", "live")}
    cats = set()
    for d in SKIP_DIMENSIONS:
        for k in b:
            if d in b[k].columns:
                cats |= {(d, c) for c in b[k][d].dropna().unique()}
    for d, c in sorted(cats):
        nums = {}
        for k in ("history", "live"):
            x = b[k]
            if d not in x.columns:
                break
            drop = x[d] == c
            keep = x[~drop]
            nums[k] = {"before": _roi(x["stake"], x["return"]), "after": _roi(keep["stake"], keep["return"]),
                       "n_drop": int(drop.sum()), "n_keep": int(len(keep))}
        if len(nums) == 2:
            out.append({"kind": "skip", "dimension": d, "category": c, "action": f"{c}のレースを買わない",
                        "nums": nums, "n_min": min(min(v["n_drop"], v["n_keep"]) for v in nums.values())})
    return out


def _reduce_candidates(res: dict, budget: int) -> list[dict]:
    """「r点に絞る」。予算はそのまま、上位 r 点に均等に配り直す。"""
    bets = {k: res[k].attrs.get("bets") for k in ("history", "live")}
    if any(v is None or not len(v) for v in bets.values()):
        return []
    kmax = int(min(v["rank"].max() for v in bets.values()))
    out = []
    for r in range(1, kmax):
        nums = {}
        for k, bt in bets.items():
            before = _roi(bt["amount"], bt["amount"] * bt["rate"])
            sub = bt[bt["rank"] <= r].sort_values(["race_id", "rank"])
            stake = ret = 0.0
            for _, g in sub.groupby("race_id", sort=False):
                a = np.array(allocate_bets("equal", g["prob"].tolist(), budget), dtype=float)
                stake += a.sum()
                ret += float((a * g["rate"].to_numpy()).sum())
            races = int(sub["race_id"].nunique())
            nums[k] = {"before": before, "after": ret / stake if stake else None, "n_drop": races, "n_keep": races}
        out.append({"kind": "reduce", "points": r, "action": f"{r}点に絞る", "nums": nums,
                    "n_min": min(v["n_keep"] for v in nums.values())})
    return out


def _switch_candidates(pid: str, res_all: dict, labels: dict) -> list[dict]:
    """「同じレースで別の人の買い方に変える」(券種を変える)。その人が買ったレースだけで比べる。"""
    out = []
    for other, name in labels.items():
        if other == pid:
            continue
        nums = {}
        for k in ("history", "live"):
            me = res_all[k][pid]
            ot = res_all[k][other].set_index("race_id")
            races = me.loc[me["bought"], "race_id"]
            o = ot.reindex(races)
            if not o["bought"].fillna(False).all():
                break  # 相手がそのレースを買っていないなら比べない
            nums[k] = {"before": _roi(me.loc[me["bought"], "stake"], me.loc[me["bought"], "return"]),
                       "after": _roi(o["stake"], o["return"]), "n_drop": len(races), "n_keep": len(races)}
        if len(nums) == 2:
            out.append({"kind": "switch", "to": other, "action": f"同じレースで{name}の買い方に変える", "nums": nums,
                        "n_min": min(v["n_keep"] for v in nums.values())})
    return out


def _qualify(cands: list[dict]) -> list[dict]:
    """両方の期間で回収率が上がり、件数不足でないものだけ。改善の小さい方(両期間の小さい方)の大きい順。"""
    ok = []
    for c in cands:
        h, l = c["nums"]["history"], c["nums"]["live"]
        if None in (h["before"], h["after"], l["before"], l["after"]):
            continue
        gain = min(h["after"] - h["before"], l["after"] - l["before"])
        lvl = sample_level(int(c["n_min"]))
        if gain < MIN_IMPROVE or lvl == SAMPLE_INSUFFICIENT:
            continue
        ok.append({**c, "gain": round(gain, 4), "level": lvl})
    return sorted(ok, key=lambda c: -c["gain"])[:MAX_CANDIDATES]


# ---------------------------------------------------------------------------
# 3. 文
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def loss_text(rows: list[dict]) -> str | None:
    """負けの内訳から、最も偏りの大きい区分を1文にする(件数不足の区分は使わない)。"""
    # 買った額の割合より負けの割合がはっきり大きい区分だけ(買う量が多いから負けも多い、は言わない)
    cand = [r for r in rows if r["level"] != SAMPLE_INSUFFICIENT and r["loss_share"] >= 0.3 and r["profit"] < 0
            and r["dimension"] not in ("version", "payout") and r["loss_share"] - r["stake_share"] >= 0.1]
    if not cand:
        return None
    r = max(cand, key=lambda x: x["loss_share"] - x["stake_share"])
    share = max(1, int(round(r["loss_share"] * 10)))
    base = max(1, int(round(r["stake_share"] * 10)))
    unit = "買った額" if r["dimension"] == "rank" else "買ったレース"
    where = r["category"] if r["dimension"] in ("rank", "payout") else f"{r['category']}のレース"
    return (f"負けの約{share}割が{where}から({unit}では約{base}割)。"
            + ("(参考)" if r["level"] != SAMPLE_NORMAL else ""))


def candidate_text(c: dict) -> str:
    h, l = c["nums"]["history"], c["nums"]["live"]
    s = (f"{c['action']}と、過去 {_pct(h['before'])}→{_pct(h['after'])}、"
         f"実運用 {_pct(l['before'])}→{_pct(l['after'])}。")
    return s + ("(参考: 件数が少ない)" if c["level"] != SAMPLE_NORMAL else "")


def check_text(t: str) -> str:
    """使ってはいけない言葉が入っていないか確かめる(入っていれば ValueError)。"""
    for w in FORBIDDEN_WORDS:
        if w in t:
            raise ValueError(f"使ってはいけない言葉が入っています: {w}: {t}")
    return t


def reflections_for(pid: str, res_all: dict, attrs: dict, labels: dict, budget: int) -> dict:
    """1人分の反省点。res_all = {"history": {pid: race_results}, "live": {...}}、attrs = {"history": df, "live": df}。"""
    res = {k: res_all[k][pid] for k in ("history", "live")}
    lb = {k: loss_breakdown(res[k], attrs[k]) for k in ("history", "live")}
    cands = _qualify(_skip_candidates(res, attrs) + _reduce_candidates(res, budget)
                     + _switch_candidates(pid, res_all, labels))
    texts = []
    lt = loss_text(lb["live"]) or loss_text(lb["history"])
    if lt:
        texts.append(check_text(("実運用では" if loss_text(lb["live"]) else "過去の検証では") + lt))
    texts += [check_text(candidate_text(c)) for c in cands]
    if not cands:
        texts.append("過去と実運用の両方で回収率が上がる改善の候補は、今のところ見つかっていない。")
    return {"loss_breakdown": lb,
            "candidates": [{k: v for k, v in c.items() if k != "n_min"} for c in cands],
            "texts": texts,
            "note": "改善の候補は、過去の検証期間と実運用の両方で回収率が上がったものだけ。1レースの予算は変えない"}
