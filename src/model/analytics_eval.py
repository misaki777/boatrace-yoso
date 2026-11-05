"""分析タブ用の計算関数(spec/11)。M-15 モデルの弱点分析(A4)と M-17 週ごとのまとめ(A7)。

- **実運用の確定版の予想だけ**を使う(data/predictions/YYYYMMDD_{version}.parquet。spec/08 5.1)。
  バックテストの値はここに混ぜない(シミュレーターの「検証」は src/model/simulator.py)。
- 計算の関数は M、集計の実行と JSON 化は E(export_analytics.py)。E は build_model_weakness() と
  build_weekly() の戻り値をそのまま JSON に書けば spec/11 の形になる。
- 件数の扱い(spec/11 1.3): 200 以上は通常、30〜199 は「参考」、30 未満は率を出さず件数だけ。
  定数は src/common/analytics_rules.py(PM)を優先し、無ければ同じ値の既定を使う。
- オッズの数値はどの集計にも入れない。払戻(結果の払戻金)は使ってよい。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.config import MODELS_DIR, PREDICTIONS_DIR

# ---------------------------------------------------------------------------
# 共通ルール(spec/11 1.3。src/common/analytics_rules.py を使う。値の変更は PM 経由)
# ---------------------------------------------------------------------------
from src.common.analytics_rules import (  # noqa: E402
    BOOTSTRAP_ITERATIONS,
    SAMPLE_INSUFFICIENT,
    bootstrap_roi_interval,
    prob_band3,
    prob_band10,
    rate_or_none,
    sample_level,
    wilson_interval,
    wind_bucket,
)

OVERCONF_THRESHOLD = 0.50  # 推しすぎの追跡: 本命の予測が50%以上のレース
VERSIONS = ("pre", "preview")


def rate_cell(k: int, n: int, pred_sum: float | None = None) -> dict:
    """件数つきの率。件数不足(30 未満)は率・区間・予測平均を出さない。"""
    lvl = sample_level(n)
    cell = {"n": int(n), "level": lvl, "actual": rate_or_none(k, n), "ci95": None, "pred_mean": None, "diff": None}
    if lvl == SAMPLE_INSUFFICIENT:
        return cell
    ci = wilson_interval(k, n)
    cell["ci95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
    if pred_sum is not None:
        cell["pred_mean"] = round(pred_sum / n, 4)
        cell["diff"] = round(pred_sum / n - k / n, 4)
    return cell


def roi_interval(stake_by_race, return_by_race) -> list[float] | None:
    """回収率の95%区間(共通ルール: レース単位のブートストラップ200回・seed 固定)。"""
    ci = bootstrap_roi_interval(list(map(float, stake_by_race)), list(map(float, return_by_race)))
    return [round(ci[0], 4), round(ci[1], 4)] if ci else None


# ---------------------------------------------------------------------------
# 実運用データの読み込み
# ---------------------------------------------------------------------------


def load_live(d_from: date | str, d_to: date | str, pred_dir: str | Path = PREDICTIONS_DIR,
              versions=VERSIONS) -> pd.DataFrame:
    """確定版の予想と結果を結合した、1行=1艇の表。

    列: race_id, date, version(予想ファイルの版), model_id, entry_number, p_win,
        finish_position, is_absent, is_valid_result, stadium_number, technique_number,
        weather_number, wind_bucket(共通ルールの風の区分)。結果がまだ無いレースも含む(結果の列が null)。
    """
    from src.features.build_interim import read_interim

    d_from, d_to = pd.Timestamp(d_from), pd.Timestamp(d_to)
    frames = []
    for d in pd.date_range(d_from, d_to, freq="D"):
        for v in versions:
            f = Path(pred_dir) / f"{d:%Y%m%d}_{v}.parquet"
            if f.exists():
                p = pd.read_parquet(f, columns=["race_id", "entry_number", "model_id", "p_win"])
                frames.append(p.assign(version=v, date=d))
    if not frames:
        return pd.DataFrame(columns=["race_id", "date", "version", "model_id", "entry_number", "p_win"])
    live = pd.concat(frames, ignore_index=True)
    live["race_id"] = live["race_id"].astype(str)
    ent = read_interim("entries", d_from.date(), d_to.date())[["race_id", "entry_number", "finish_position", "is_absent"]]
    races = read_interim("races", d_from.date(), d_to.date())
    races = races[["race_id", "is_valid_result", "stadium_number", "technique_number", "pv_weather_number",
                   "pv_wind_direction_number", "pv_wind_speed"]].rename(columns={"pv_weather_number": "weather_number"})
    races["wind_bucket"] = [wind_bucket(None if pd.isna(d) else int(d), None if pd.isna(v) else float(v))
                            for d, v in zip(races["pv_wind_direction_number"], races["pv_wind_speed"])]
    ent["race_id"] = ent["race_id"].astype(str)
    races["race_id"] = races["race_id"].astype(str)
    live = live.merge(ent, on=["race_id", "entry_number"], how="left").merge(
        races.drop(columns=["pv_wind_direction_number", "pv_wind_speed"]), on="race_id", how="left")
    return live


def favorite_table(live: pd.DataFrame) -> pd.DataFrame:
    """1行=1レース×版。本命(p_win 最大)の確率と、1着になったか。結果が確定したきれいなレースだけ。

    きれい = 結果が有効、欠場なし、6艇、1着がちょうど1艇(evaluate と同じ基準)。
    """
    from src.model.data import clean_race_mask

    rows = []
    for v, g in live.groupby("version"):
        g = g.copy()
        g["is_valid_result"] = g["is_valid_result"].astype("boolean").fillna(False).astype(bool)
        g["is_absent"] = g["is_absent"].astype("boolean").fillna(False).astype(bool)
        g = g[clean_race_mask(g)]
        if g.empty:
            continue
        g = g.sort_values(["race_id", "p_win"], ascending=[True, False])
        fav = g.groupby("race_id").head(1).set_index("race_id")
        winner_p = g[g["finish_position"] == 1].set_index("race_id")["p_win"]
        fav = fav.assign(version=v, p_fav=fav["p_win"], fav_won=(fav["finish_position"] == 1),
                         p_winner=winner_p.reindex(fav.index))
        rows.append(fav.reset_index())
    if not rows:
        return pd.DataFrame()
    t = pd.concat(rows, ignore_index=True)
    t["logloss"] = -np.log(np.clip(t["p_winner"].astype(float), 1e-15, 1))
    t["fav_entry"] = t["entry_number"]
    t["prob_band"] = t["p_fav"].map(prob_band10)
    t["band3"] = t["p_fav"].map(prob_band3)
    iso = pd.to_datetime(t["date"]).dt.isocalendar()
    t["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    t["month"] = pd.to_datetime(t["date"]).dt.strftime("%Y-%m")
    return t[["race_id", "date", "week", "month", "version", "model_id", "stadium_number", "technique_number",
              "weather_number", "wind_bucket", "fav_entry", "p_fav", "fav_won", "p_winner", "logloss", "prob_band",
              "band3"]]


def _group_rows(t: pd.DataFrame, key: str) -> list[dict]:
    out = []
    for k, g in t.groupby(key, dropna=False):
        cell = rate_cell(int(g["fav_won"].sum()), len(g), float(g["p_fav"].sum()))
        cell["key"] = None if pd.isna(k) else (int(k) if isinstance(k, (int, np.integer, float)) and float(k).is_integer() else str(k))
        cell["logloss"] = round(float(g["logloss"].mean()), 4) if cell["level"] != SAMPLE_INSUFFICIENT else None
        out.append(cell)
    return out


# ---------------------------------------------------------------------------
# M-15 モデルの弱点分析(A4)
# ---------------------------------------------------------------------------

BREAKDOWN_KEYS = {
    "stadium": "stadium_number",
    "prob_band": "prob_band",
    "technique": "technique_number",
    "weather": "weather_number",
    "wind": "wind_bucket",
}


def condition_breakdown(fav: pd.DataFrame) -> dict:
    """条件別の外れ方: 版ごと × (場・確率帯・決まり手・天候・風)の本命1着率・予測平均・差・logloss・件数。"""
    out = {}
    for v, g in fav.groupby("version"):
        out[v] = {name: _group_rows(g, col) for name, col in BREAKDOWN_KEYS.items()}
        out[v]["all"] = _group_rows(g.assign(_all="all"), "_all")
    return out


def ece_of(live_part: pd.DataFrame) -> tuple[float | None, list[dict]]:
    """艇単位の p_win の較正(10帯)と ECE(10 2.5 と同じ定義)。"""
    from src.model.summary import calibration_from_predictions

    d = live_part.copy()
    d["is_valid_result"] = d["is_valid_result"].astype("boolean").fillna(False).astype(bool)
    d["is_absent"] = d["is_absent"].astype("boolean").fillna(False).astype(bool)
    bins = calibration_from_predictions(d)
    n = sum(b["n"] for b in bins)
    ece = sum(b["n"] * abs(b["pred_mean"] - b["actual"]) for b in bins) / n if n else None
    return ece, bins


def _bins_public(bins: list[dict]) -> list[dict]:
    """較正の帯を公開用にする(件数不足の帯は率を出さない)。"""
    out = []
    for b in bins:
        k = round(b["actual"] * b["n"])
        cell = rate_cell(k, b["n"], b["pred_mean"] * b["n"])
        cell["bin"] = b["bin"]
        out.append(cell)
    return out


def calibration_trend(live: pd.DataFrame, freq: str = "week") -> list[dict]:
    """週ごと(freq='week')・月ごと(freq='month')の ECE と確率帯ごとの予測・実際。版ごと。"""
    d = live.copy()
    if freq == "week":
        iso = pd.to_datetime(d["date"]).dt.isocalendar()
        d["period"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    else:
        d["period"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m")
    out = []
    for (v, per), g in d.groupby(["version", "period"]):
        ece, bins = ece_of(g)
        races = sum(b["n"] for b in bins) // 6
        lvl = sample_level(races)
        out.append({"version": v, "period": per, "races": races, "level": lvl,
                    "ece": round(ece, 4) if ece is not None and lvl != SAMPLE_INSUFFICIENT else None,
                    "bins": _bins_public(bins) if lvl != SAMPLE_INSUFFICIENT else []})
    return out


def overconfidence_trend(fav: pd.DataFrame, threshold: float = OVERCONF_THRESHOLD) -> list[dict]:
    """推しすぎの推移: 本命の予測が threshold 以上のレースの、予測の平均と実際の1着率(週ごと・版ごと)。"""
    out = []
    for (v, wk), g in fav[fav["p_fav"] >= threshold].groupby(["version", "week"]):
        cell = rate_cell(int(g["fav_won"].sum()), len(g), float(g["p_fav"].sum()))
        cell.update({"version": v, "week": wk, "models": sorted(g["model_id"].astype(str).unique())})
        out.append(cell)
    return out


def model_switches(models_dir: str | Path = MODELS_DIR) -> list[dict]:
    """モデルの切り替え日(models/history.json の採用された行)。推移のグラフの縦線に使う。"""
    p = Path(models_dir) / "history.json"
    if not p.exists():
        return []
    hist = json.loads(p.read_text(encoding="utf-8"))
    return [{"date": (h.get("approved_at") or h["date"])[:10], "variant": h["variant"], "model_id": h["candidate"]}
            for h in hist if h.get("adopted")]


def build_model_weakness(d_from, d_to, pred_dir=PREDICTIONS_DIR, models_dir=MODELS_DIR) -> dict:
    """model_weakness.json の中身(実運用の確定版だけ)。"""
    live = load_live(d_from, d_to, pred_dir)
    fav = favorite_table(live)
    return {
        "source": "live",
        "period": {"from": str(pd.Timestamp(d_from).date()), "to": str(pd.Timestamp(d_to).date())},
        "races": {v: int((fav["version"] == v).sum()) for v in VERSIONS} if len(fav) else {},
        "conditions": condition_breakdown(fav) if len(fav) else {},
        "calibration_weekly": calibration_trend(live, "week") if len(fav) else [],
        "calibration_monthly": calibration_trend(live, "month") if len(fav) else [],
        "overconfidence_weekly": overconfidence_trend(fav) if len(fav) else [],
        "model_switches": model_switches(models_dir),
        "notes": {"overconfidence_threshold": OVERCONF_THRESHOLD},
    }


# ---------------------------------------------------------------------------
# M-17 週ごとのまとめ(A7)
# ---------------------------------------------------------------------------


# 券種別の回収率: 既定の戦略と基準(1号艇)
WEEKLY_STRATEGIES = [
    {"name": "model_win", "short_label": "単勝 本命1点", "bet_type": "win", "rule": "top_k", "params": {"k": 1}},
    {"name": "model_exacta", "short_label": "2連単 上位3点", "bet_type": "exacta", "rule": "top_k", "params": {"k": 3}},
    {"name": "model_trifecta", "short_label": "3連単 上位5点", "bet_type": "trifecta", "rule": "top_k", "params": {"k": 5}},
    {"name": "baseline_win1", "short_label": "1号艇 単勝", "bet_type": "win", "rule": "baseline_win1", "params": {},
     "is_baseline": True},
    {"name": "baseline_exacta1", "short_label": "1号艇から2連単 上位3点", "bet_type": "exacta",
     "rule": "baseline_first1_topk", "params": {"k": 3}, "is_baseline": True},
    {"name": "baseline_tri1_top5", "short_label": "1号艇から3連単 上位5点", "bet_type": "trifecta",
     "rule": "baseline_first1_topk", "params": {"k": 5}, "is_baseline": True},
]


def iso_week_range(week: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """'2026-W45' → (月曜, 日曜)。"""
    y, w = week.split("-W")
    mon = pd.Timestamp(date.fromisocalendar(int(y), int(w), 1))
    return mon, mon + pd.Timedelta(days=6)


def favorite_bands(fav: pd.DataFrame) -> dict:
    out = {}
    for v, g in fav.groupby("version"):
        out[v] = []
        for name in ("lt35", "35to50", "ge50"):
            b = g[g["band3"] == name]
            cell = rate_cell(int(b["fav_won"].sum()), len(b), float(b["p_fav"].sum()) if len(b) else None)
            cell["band"] = name
            out[v].append(cell)
    return out


def weekly_strategies(live: pd.DataFrame, payouts: pd.DataFrame, version: str = "pre") -> list[dict]:
    """券種別の回収率(既定の戦略と基準)。結果が出たレースだけ。件数の区分を付ける。"""
    from src.model.summary import DEFAULT_STRATEGIES, settle_strategy  # noqa: F401
    from src.model.summary import strategy_row

    d = live[(live["version"] == version)].copy()
    d["is_valid_result"] = d["is_valid_result"].astype("boolean").fillna(False).astype(bool)
    d["is_absent"] = d["is_absent"].astype("boolean").fillna(False).astype(bool)
    d = d[d["finish_position"].notna().groupby(d["race_id"]).transform("any")]
    if d.empty:
        return []
    rows = []
    for spec in WEEKLY_STRATEGIES:
        settled, n_races = settle_strategy(d, payouts, spec)
        r = strategy_row(settled, n_races, spec["name"], spec["short_label"], spec.get("is_baseline", False),
                         True, spec.get("params"), n_boot=10)
        by = settled.groupby("race_id")[["stake", "return"]].sum()
        lvl = sample_level(n_races)
        r.update({"level": lvl, "roi_ci95": roi_interval(by["stake"], by["return"])})
        r.pop("sample_note", None)
        if lvl == SAMPLE_INSUFFICIENT:
            r.update({"roi": None, "roi_ci95": None, "hit_rate": None})
        elif r["roi"] is not None:
            r["roi"] = round(r["roi"], 4)
            r["hit_rate"] = round(r["hit_rate"], 4) if r["hit_rate"] is not None else None
        rows.append(r)
    return rows


def tenkai_week(d_from, d_to, pred_dir=PREDICTIONS_DIR, versions=VERSIONS) -> dict:
    """展開予想の当たり具合: 多クラス logloss、最も確率の高い決まり手の的中率、基準(場の比率)との比較。"""
    from src.features.build_interim import read_interim
    from src.model import tenkai as tk

    d_from, d_to = pd.Timestamp(d_from), pd.Timestamp(d_to)
    hist = read_interim("races", None, d_to.date())
    hist = hist[hist["is_valid_result"].astype("boolean").fillna(False) & hist["technique_number"].notna()]
    hist = hist.assign(y=hist["technique_number"].astype(int) - 1, date=pd.to_datetime(hist["date"]),
                       race_id=hist["race_id"].astype(str))
    out = {}
    for v in versions:
        frames = [pd.read_parquet(f) for d in pd.date_range(d_from, d_to)
                  if (f := Path(pred_dir) / f"{d:%Y%m%d}_{v}_tenkai.parquet").exists()]
        if not frames:
            out[v] = {"races": 0, "level": "insufficient"}
            continue
        p = pd.concat(frames)
        p["race_id"] = p["race_id"].astype(str)
        m = p.merge(hist[["race_id", "y", "date", "stadium_number"]], on="race_id")
        n = len(m)
        lvl = sample_level(n)
        if lvl == SAMPLE_INSUFFICIENT:
            out[v] = {"races": n, "level": lvl}
            continue
        P = m[tk.PROB_COLUMNS].to_numpy()
        y = m["y"].to_numpy()
        prior = tk.baseline_overall(hist[hist["date"] < d_from])
        b1 = tk.baseline_stadium(hist, m, prior)
        top_hit = int((P.argmax(axis=1) == y).sum())
        out[v] = {"races": n, "level": lvl, "logloss": round(tk.multi_logloss(P, y), 4),
                  "baseline_stadium_logloss": round(tk.multi_logloss(b1, y), 4),
                  "top_hit": rate_cell(top_hit, n)}
    return out


def build_weekly(week: str, pred_dir=PREDICTIONS_DIR, with_previous: bool = True) -> dict:
    """weekly/YYYY-Www.json の中身(実運用の確定版だけ)。"""
    from src.features.build_interim import read_interim

    mon, sun = iso_week_range(week)
    live = load_live(mon, sun, pred_dir)
    fav = favorite_table(live)
    payouts = read_interim("payouts", mon.date(), sun.date())
    payouts["race_id"] = payouts["race_id"].astype(str)
    cal = {}
    for v in VERSIONS:
        part = live[live["version"] == v]
        if len(part):
            ece, bins = ece_of(part)
            races = sum(b["n"] for b in bins) // 6
            lvl = sample_level(races)
            cal[v] = {"races": races, "level": lvl,
                      "ece": round(ece, 4) if ece is not None and lvl != SAMPLE_INSUFFICIENT else None,
                      "bins": _bins_public(bins) if lvl != SAMPLE_INSUFFICIENT else []}
    out = {
        "week": week, "from": str(mon.date()), "to": str(sun.date()), "source": "live",
        "races": {v: int((fav["version"] == v).sum()) for v in VERSIONS} if len(fav) else {},
        "favorite": favorite_bands(fav) if len(fav) else {},
        "strategies": {v: weekly_strategies(live, payouts, v) for v in VERSIONS} if len(live) else {},
        "tenkai": tenkai_week(mon, sun, pred_dir),
        "calibration": cal,
    }
    if with_previous:
        prev_week = (mon - pd.Timedelta(days=7)).isocalendar()
        prev = build_weekly(f"{prev_week.year}-W{prev_week.week:02d}", pred_dir, with_previous=False)
        out["vs_previous_week"] = compare_weeks(out, prev)
    return out


def compare_weeks(cur: dict, prev: dict) -> dict:
    """前の週との差(主要な数値)。どちらかが件数不足なら null、どちらかが参考なら level=reference。"""
    def fav_all(w, v):
        rows = w.get("favorite", {}).get(v, [])
        n = sum(r["n"] for r in rows)
        k = sum((r["actual"] or 0) * r["n"] for r in rows if r["actual"] is not None)
        return n, (k / n if n and all(r["actual"] is not None for r in rows if r["n"]) else None)

    out = {"previous_week": prev["week"]}
    for v in VERSIONS:
        n1, a1 = fav_all(cur, v)
        n0, a0 = fav_all(prev, v)
        e1 = cur.get("calibration", {}).get(v, {}).get("ece")
        e0 = prev.get("calibration", {}).get(v, {}).get("ece")
        lvl = min((sample_level(n1), sample_level(n0)), key=["insufficient", "few", "normal"].index)
        ok = lvl != SAMPLE_INSUFFICIENT
        out[v] = {"level": lvl,
                  "fav_win_rate_diff": round(a1 - a0, 4) if ok and a1 is not None and a0 is not None else None,
                  "ece_diff": round(e1 - e0, 4) if ok and e1 is not None and e0 is not None else None,
                  "races": [n0, n1]}
    return out
