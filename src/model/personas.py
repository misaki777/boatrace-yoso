"""仮想の購入者10人の成績(利用者の決定。分析タブ用)。エクスポート担当が build_personas() の戻り値を JSON にする。

10人(買い方は固定。どのレースでも同じ規則で買う):
 ① win_fav    単勝さん     : 全レース、モデルの本命の単勝1点
 ② boat1      1号艇さん    : 全レース、1号艇の単勝(比べるための基準)
 ③ in_nige    イン逃げさん : 全レース、1号艇1着固定の3連単 1-23-234 の4点(1-2-3、1-2-4、1-3-2、1-3-4)。モデルを使わない
 ④ app        アプリさん   : 全レース、アプリの推奨どおり(export の strategy の既定。その日の docs/data/days の strategy に従う。
                              既定が変わった日と内容を strategy_changes に記録する)。直前版が確定したレースは直前版の確率で
 ⑤ box3       ボックスさん : 全レース、モデルの1着確率の上位3艇の3連単ボックス6点
 ⑥ quinella3  2連複さん    : 全レース、2連複のモデルの上位3点
 ⑦ steady     堅実さん     : 「堅」(本命の予想1着率 0.50 以上)のレースだけ、2連単のモデルの上位3点
 ⑧ upset      荒れ狙いさん : 「荒」(本命の予想1着率 0.35 未満)のレースだけ、3連単のモデルの上位10点
 ⑨ outer_ana  外枠穴さん   : 外側3艇(展示の進入 4〜6コース)で、展示ST・展示タイムがともに1位の艇がいるレースだけ。
                              その艇と、モデルの1着確率の上位2艇(その艇を除いた上位2艇)との2連複2点。直前情報が無いレースは買わない
 ⑩ select10   厳選さん     : 1日のうち本命の確率が高い順に10レースだけ、3連単のモデルの上位5点
                              (その日の確定した予想だけで選ぶ。結果は見ない)

共通の規則:
- 1レースの予算は SIM_BUDGET_PER_RACE_YEN(1,000円)で固定。点数で均等に配る(simulator.allocate_bets の equal。
  100円単位、余りは確率の高い順)。
- **過去の収支・連敗・前のレースの結果は一切使わない**(負けたあとに増額しない)。
- 中止のレースは買わない(不参加)。欠場で返還になった買い目は、返還額を払戻に含める。
- 「最適」「おすすめ」という言葉は使わない。過去の参考値は後知恵を含むことを明記する。
"""

from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.analytics_rules import (
    SAMPLE_INSUFFICIENT,
    SIM_BUDGET_PER_RACE_YEN,
    bootstrap_roi_interval,
    sample_level,
)
from src.model import combinations as cb
from src.model.backtest import p_win_matrix, settle_bets
from src.model.config import MODELS_DIR, PREDICTIONS_DIR, ROOT
from src.model.simulator import _gammas_by_model, allocate_bets
from src.model.summary import max_drawdown_yen, max_losing_streak

CONF_STRONG = 0.50  # 「堅」(spec/05 3章)
CONF_ROUGH = 0.35   # 「荒」はこれ未満
SELECT_PER_DAY = 10
IN_NIGE_COMBOS = ("1-2-3", "1-2-4", "1-3-2", "1-3-4")
DEFAULT_APP_STRATEGY = {"name": "top_k", "bet_type": "trifecta", "k": 5, "short_label": "3連単 上位5点"}
DAYS_DIR = ROOT / "docs" / "data" / "days"

PERSONAS: list[dict] = [
    {"id": "win_fav", "name": "単勝さん", "description": "全レースで、モデルの本命の単勝を1点買う"},
    {"id": "boat1", "name": "1号艇さん", "description": "全レースで、1号艇の単勝を1点買う(比べるための基準)", "is_baseline": True},
    {"id": "in_nige", "name": "イン逃げさん",
     "description": "全レースで、1号艇が1着の3連単 1-2-3・1-2-4・1-3-2・1-3-4 の4点を買う(モデルを使わない)"},
    {"id": "app", "name": "アプリさん", "description": "全レースで、アプリが示す買い目(その日の既定の買い方)のとおりに買う"},
    {"id": "box3", "name": "ボックスさん", "description": "全レースで、1着の確率が高い3艇の3連単ボックス6点を買う"},
    {"id": "quinella3", "name": "2連複さん", "description": "全レースで、2連複の確率の上位3点を買う"},
    {"id": "steady", "name": "堅実さん", "description": "「堅」(本命の1着確率50%以上)のレースだけ、2連単の確率の上位3点を買う"},
    {"id": "upset", "name": "荒れ狙いさん", "description": "「荒」(本命の1着確率35%未満)のレースだけ、3連単の確率の上位10点を買う"},
    {"id": "outer_ana", "name": "外枠穴さん",
     "description": "外側(4〜6コース)で展示ST・展示タイムがともに1位の艇がいるレースだけ、その艇と1着確率の上位2艇との2連複2点を買う"},
    {"id": "select10", "name": "厳選さん", "description": "1日のうち本命の確率が高い10レースだけ、3連単の確率の上位5点を買う"},
]
HINDSIGHT_NOTE = "過去のデータで計算した参考値。買い方を結果を見てから選ぶと、たまたま良かったものを選びやすい(後知恵を含む)"


# ---------------------------------------------------------------------------
# 入力の準備
# ---------------------------------------------------------------------------


def choose_live_version(live: pd.DataFrame) -> pd.DataFrame:
    """load_live の結果(前日版・直前版の両方)から、レースごとに直前版を優先して1つの版だけを残す。"""
    pv = live[live["version"] == "preview"]
    pv_races = set(pv.groupby("race_id")["entry_number"].size().loc[lambda s: s == 6].index)
    keep = ((live["version"] == "preview") & live["race_id"].isin(pv_races)) | (
        (live["version"] == "pre") & ~live["race_id"].isin(pv_races))
    return live[keep].copy()


def outer_top_boats(entries: pd.DataFrame) -> pd.Series:
    """race_id → 外側(展示の進入 4〜6)で展示ST・展示タイムがともに1位の艇の枠番(いなければ 0)。

    entries: race_id, entry_number, pv_course_number, pv_start_timing, pv_exhibition_time(interim の entries)。
    6艇すべての展示タイムと展示STがあるレースだけを対象にし、無いレースは -1(直前情報なし → 買わない)。
    1位が同じ値で2艇以上いるときは「ともに1位」にしない(0)。フライング(負のST)は 0.00 として扱う。
    """
    e = entries.copy()
    e["race_id"] = e["race_id"].astype(str)
    e["st"] = pd.to_numeric(e["pv_start_timing"], errors="coerce").clip(lower=0)
    e["ex"] = pd.to_numeric(e["pv_exhibition_time"], errors="coerce")
    e["course"] = pd.to_numeric(e["pv_course_number"], errors="coerce").fillna(e["entry_number"].astype(float))
    g = e.groupby("race_id")
    complete = (g.size() == 6) & g["st"].apply(lambda x: x.notna().all()) & g["ex"].apply(lambda x: x.notna().all())
    out = pd.Series(-1, index=complete.index, dtype=int)
    out[complete] = 0
    outer = e[e["race_id"].isin(complete[complete].index) & (e["course"] >= 4)]
    for rid, h in outer.groupby("race_id"):
        a = h[h["st"] == h["st"].min()]
        b = h[h["ex"] == h["ex"].min()]
        if len(a) == 1 and len(b) == 1 and a["entry_number"].iloc[0] == b["entry_number"].iloc[0]:
            out[rid] = int(a["entry_number"].iloc[0])
    return out


def app_strategies(dates, days_dir: str | Path = DAYS_DIR) -> dict:
    """日付 → その日にアプリが使った既定の買い方(docs/data/days の strategy)。ファイルが無い日は既定値。"""
    out = {}
    for d in sorted(set(dates)):
        f = Path(days_dir) / f"{pd.Timestamp(d):%Y%m%d}.json"
        s = None
        if f.exists():
            try:
                s = json.loads(f.read_text(encoding="utf-8")).get("strategy")
            except (OSError, ValueError):
                s = None
        out[pd.Timestamp(d).strftime("%Y-%m-%d")] = {k: v for k, v in (s or DEFAULT_APP_STRATEGY).items()
                                                     if k in ("name", "bet_type", "k", "alpha", "theta", "short_label")}
    return out


def strategy_changes(strats: dict) -> list[dict]:
    """既定の買い方が変わった日と内容(グラフの印に使う)。最初の日も入れる。"""
    out, prev = [], None
    for d in sorted(strats):
        cur = strats[d]
        key = {k: v for k, v in cur.items() if k != "short_label"}
        if key != prev:
            out.append({"date": d, "strategy": cur})
            prev = key
    return out


class _Ctx:
    """1回の計算で共通に使う値(レースの並び、1着確率、組合せ確率のキャッシュ)。"""

    def __init__(self, preds: pd.DataFrame, models_dir: Path, outer: pd.Series | None, strategies: dict | None):
        self.race_ids, self.pw = p_win_matrix(preds)
        self.n = len(self.race_ids)
        self.pfav = self.pw.max(axis=1)
        self.dates = pd.to_datetime(pd.Series(self.race_ids).str.slice(0, 8), format="%Y%m%d").dt.strftime("%Y-%m-%d").to_numpy()
        mid = (preds.groupby("race_id", sort=False)["model_id"].first().reindex(self.race_ids).astype(str).to_numpy()
               if "model_id" in preds.columns else np.array(["?"] * self.n))
        self.mid = mid
        self.gam = _gammas_by_model(np.unique(mid), models_dir)
        self.outer = (outer.reindex(self.race_ids).fillna(-1).astype(int).to_numpy() if outer is not None
                      else np.full(self.n, -1))
        self.strategies = strategies or {}
        self._cache: dict[str, np.ndarray] = {}

    def probs(self, bt: str) -> np.ndarray:
        if bt not in self._cache:
            P = np.zeros((self.n, len(cb.combination_labels(bt))))
            for m in np.unique(self.mid):
                sel = self.mid == m
                P[sel] = cb.combination_prob_matrix(self.pw[sel], bt, *self.gam.get(m, (1.0, 1.0)))
            self._cache[bt] = P
        return self._cache[bt]

    def top(self, bt: str, i: int, k: int) -> list[tuple[str, float]]:
        P = self.probs(bt)[i]
        labels = cb.combination_labels(bt)
        order = np.argsort(-P, kind="stable")[:k]
        return [(labels[j], float(P[j])) for j in order]

    def prob_of(self, bt: str, i: int, combo: str) -> float:
        labels = cb.combination_labels(bt)
        return float(self.probs(bt)[i][labels.index(combo)])


# ---------------------------------------------------------------------------
# 10人の買い目(1レース分: [(券種, 組合せ, 確率)]。空なら買わない)
# ---------------------------------------------------------------------------


def _select(pid: str, ctx: _Ctx, i: int, extra: dict) -> list[tuple[str, str, float]]:
    pw = ctx.pw[i]
    if pid == "win_fav":
        e = int(np.argmax(pw)) + 1
        return [("win", str(e), float(pw[e - 1]))]
    if pid == "boat1":
        return [("win", "1", float(pw[0]))]
    if pid == "in_nige":
        return [("trifecta", c, ctx.prob_of("trifecta", i, c)) for c in IN_NIGE_COMBOS]
    if pid == "app":
        s = ctx.strategies.get(ctx.dates[i], DEFAULT_APP_STRATEGY)
        bt, k = s.get("bet_type", "trifecta"), int(s.get("k", 5))
        name = s.get("name", "top_k")
        if name == "confidence_skip" and ctx.pfav[i] < float(s.get("alpha", 0.35)):
            return []
        cand = ctx.top(bt, i, len(cb.combination_labels(bt)) if name == "prob_threshold" else k)
        if name == "prob_threshold":
            cand = [c for c in cand if c[1] >= float(s.get("theta", 0.05))][:k]
        return [(bt, c, p) for c, p in cand]
    if pid == "box3":
        top3 = (np.argsort(-pw, kind="stable")[:3] + 1).tolist()
        return [("trifecta", "-".join(map(str, pm)), ctx.prob_of("trifecta", i, "-".join(map(str, pm))))
                for pm in permutations(top3)]
    if pid == "quinella3":
        return [("quinella", c, p) for c, p in ctx.top("quinella", i, 3)]
    if pid == "steady":
        return [("exacta", c, p) for c, p in ctx.top("exacta", i, 3)] if ctx.pfav[i] >= CONF_STRONG else []
    if pid == "upset":
        return [("trifecta", c, p) for c, p in ctx.top("trifecta", i, 10)] if ctx.pfav[i] < CONF_ROUGH else []
    if pid == "outer_ana":
        x = int(ctx.outer[i])
        if x <= 0:
            return []
        partners = [int(e) + 1 for e in np.argsort(-pw, kind="stable") if int(e) + 1 != x][:2]
        out = []
        for y in partners:
            c = f"{min(x, y)}={max(x, y)}"
            out.append(("quinella", c, ctx.prob_of("quinella", i, c)))
        return out
    if pid == "select10":
        if i not in extra["select10"]:
            return []
        return [("trifecta", c, p) for c, p in ctx.top("trifecta", i, 5)]
    raise ValueError(pid)


def _select10_races(ctx: _Ctx) -> set[int]:
    """日ごとに本命の確率が高い順に10レース(同じ確率は race_id の順)。その日の予想だけで決める。"""
    df = pd.DataFrame({"i": np.arange(ctx.n), "date": ctx.dates, "pfav": ctx.pfav, "rid": ctx.race_ids})
    df = df.sort_values(["date", "pfav", "rid"], ascending=[True, False, True])
    return set(df.groupby("date").head(SELECT_PER_DAY)["i"])


def race_results(preds: pd.DataFrame, payouts: pd.DataFrame, pid: str, budget: int = SIM_BUDGET_PER_RACE_YEN,
                 models_dir: str | Path = MODELS_DIR, outer: pd.Series | None = None,
                 strategies: dict | None = None, ctx: _Ctx | None = None) -> pd.DataFrame:
    """その人のレースごとの購入額・払戻・的中(買わなかったレースは購入額0)。

    金額はそのレースの確率と予算だけで決める(過去の結果は使わない)。
    """
    ctx = ctx or _Ctx(preds, Path(models_dir), outer, strategies)
    extra = {"select10": _select10_races(ctx)} if pid == "select10" else {}
    rows = []
    for i in range(ctx.n):
        picks = _select(pid, ctx, i, extra)
        if not picks:
            continue
        amounts = allocate_bets("equal", [p for _, _, p in picks], budget)
        for rank, ((bt, c, pr), a) in enumerate(zip(picks, amounts), start=1):
            rows.append((ctx.race_ids[i], bt, c, a, rank, pr))
    base = pd.DataFrame({"race_id": ctx.race_ids, "stake": 0.0, "return": 0.0, "hit": False, "bought": False})
    if not rows:
        return base
    bets = pd.DataFrame(rows, columns=["race_id", "bet_type", "combination", "amount", "rank", "prob"])
    absent = None
    if "is_absent" in preds.columns:
        absent = preds.loc[preds["is_absent"].astype("boolean").fillna(False).astype(bool), ["race_id", "entry_number"]]
    s = settle_bets(bets[["race_id", "bet_type", "combination"]], payouts, absent)
    s["amount"] = bets["amount"].to_numpy(float)
    s["ret_yen"] = s["return"].to_numpy(float) / 100.0 * s["amount"]
    s["hit_paid"] = s["hit"] & (s["amount"] > 0)
    g = s.groupby("race_id").agg(stake=("amount", "sum"), ret=("ret_yen", "sum"), hit=("hit_paid", "any"))
    base = base.set_index("race_id")
    base.loc[g.index, "stake"] = g["stake"]
    base.loc[g.index, "return"] = g["ret"]
    base.loc[g.index, "hit"] = g["hit"]
    base["bought"] = base["stake"] > 0
    out = base.reset_index().sort_values("race_id")
    # 買い目ごとの詳細(反省点の分析で、点数を減らした場合の計算に使う)
    out.attrs["bets"] = pd.DataFrame({"race_id": s["race_id"].to_numpy(), "rank": bets["rank"].to_numpy(),
                                      "prob": bets["prob"].to_numpy(), "amount": s["amount"].to_numpy(),
                                      "rate": s["return"].to_numpy(float) / 100.0})
    return out


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def summarize_person(r: pd.DataFrame) -> dict:
    """買ったレースだけで、回収率と95%区間・的中率・最長連敗・最大の落ち込み。"""
    b = r[r["bought"]]
    n = int(len(b))
    stake, ret = float(b["stake"].sum()), float(b["return"].sum())
    lvl = sample_level(n)
    out = {"races_bought": n, "stake": int(stake), "return": int(round(ret)), "profit": int(round(ret - stake)),
           "level": lvl, "roi": None, "roi_ci95": None, "hit_rate": None,
           "max_losing_streak": max_losing_streak(b["hit"].to_numpy()),
           "max_drawdown_yen": max_drawdown_yen((b["return"] - b["stake"]).to_numpy())}
    if lvl != SAMPLE_INSUFFICIENT and stake > 0:
        ci = bootstrap_roi_interval(b["stake"].tolist(), b["return"].tolist())
        out.update({"roi": round(ret / stake, 4), "roi_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
                    "hit_rate": round(float(b["hit"].mean()), 4)})
    return out


def _by_period(r: pd.DataFrame, key: str) -> list[dict]:
    d = r.copy()
    dt = pd.to_datetime(d["race_id"].str.slice(0, 8), format="%Y%m%d")
    if key == "week":
        iso = dt.dt.isocalendar()
        d["period"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    elif key == "month":
        d["period"] = dt.dt.strftime("%Y-%m")
    else:
        d["period"] = dt.dt.strftime("%Y-%m-%d")
    g = d.groupby("period").agg(races_bought=("bought", "sum"), stake=("stake", "sum"), ret=("return", "sum")).reset_index()
    g["cum_profit"] = (g["ret"] - g["stake"]).cumsum()
    out = []
    for row in g.itertuples():
        n = int(row.races_bought)
        out.append({key: row.period, "races_bought": n, "stake": int(row.stake), "return": int(round(row.ret)),
                    "profit": int(round(row.ret - row.stake)), "cum_profit": int(round(row.cum_profit)),
                    "level": sample_level(n),
                    "roi": round(row.ret / row.stake, 4) if row.stake and sample_level(n) != SAMPLE_INSUFFICIENT else None})
    return out


def _valid_with_results(preds: pd.DataFrame) -> pd.DataFrame:
    if "is_valid_result" in preds.columns:
        ok = preds["is_valid_result"].astype("boolean").fillna(False).astype(bool)
        preds = preds[ok.groupby(preds["race_id"]).transform("all")]
    if "finish_position" in preds.columns:
        preds = preds[preds["finish_position"].notna().groupby(preds["race_id"]).transform("any")]
    return preds


def personas_table(preds: pd.DataFrame, payouts: pd.DataFrame, budget: int = SIM_BUDGET_PER_RACE_YEN,
                   models_dir: str | Path = MODELS_DIR, periods=("day", "week"), outer: pd.Series | None = None,
                   strategies: dict | None = None, return_details: bool = False):
    """10人それぞれの成績。preds は予想(結果の出ていないレース・中止は自動で除く)。

    outer: race_id → 外枠穴さんの対象の艇(outer_top_boats の戻り値)。None なら外枠穴さんは買わない。
    strategies: 日付 → アプリの既定の買い方(app_strategies の戻り値)。None なら既定値(3連単上位5点)。
    """
    preds = _valid_with_results(preds)
    ctx = _Ctx(preds, Path(models_dir), outer, strategies)
    out = []
    details = {}
    for p in PERSONAS:
        r = race_results(preds, payouts, p["id"], budget, models_dir, ctx=ctx)
        details[p["id"]] = r
        row = {k: p[k] for k in ("id", "name", "description")}
        row["is_baseline"] = bool(p.get("is_baseline", False))
        row["races_available"] = int(ctx.n)
        row.update(summarize_person(r))
        for per in periods:
            row[f"by_{per}"] = _by_period(r, per)
        out.append(row)
    if return_details:
        pf = pd.Series(ctx.pfav, index=ctx.race_ids, name="pfav")
        return out, details, pf
    return out


def build_personas(live_from, live_to, history_model_id: str = "pre_20261106_7d3a",
                   budget: int = SIM_BUDGET_PER_RACE_YEN, models_dir: str | Path = MODELS_DIR,
                   pred_dir: str | Path = PREDICTIONS_DIR, days_dir: str | Path = DAYS_DIR) -> dict:
    """10人の成績。live(実運用: 確定版の予想と実際の払戻)と history(過去の参考値)を分けて返す。

    live: レースごとに直前版があれば直前版、無ければ前日版(export と同じ)。日ごと・週ごと、アプリの既定の変更履歴つき。
    history: history_model_id の test 期間(学習にも選択にも使っていない期間)。月ごと・週ごと。
      アプリさんは今の既定の買い方で計算する。外枠穴さんは展示データのある期間だけ。
    """
    from src.features.build_interim import read_interim
    from src.model.analytics_eval import load_live
    from src.model.backtest import load_interim_table
    from src.model.data import features_path, filter_period, load_features
    from src.model.lgbm import load_model
    from src.model.report import predict_p

    lf, lt = pd.Timestamp(live_from), pd.Timestamp(live_to)
    live = choose_live_version(load_live(lf, lt, pred_dir))
    lpay = read_interim("payouts", lf.date(), lt.date())
    lpay["race_id"] = lpay["race_id"].astype(str)
    lent = read_interim("entries", lf.date(), lt.date())
    lraces = read_interim("races", lf.date(), lt.date())
    strats = app_strategies(pd.date_range(lf, lt), days_dir)
    out = {
        "budget_per_race_yen": budget,
        "rules": "1レースの予算は固定(均等に配る)。過去の収支・連敗で金額を変えない。中止は不参加、欠場の返還は払戻に含める",
        "personas": [{k: p[k] for k in ("id", "name", "description")} | {"is_baseline": bool(p.get("is_baseline"))}
                     for p in PERSONAS],
        "live": {"from": str(lf.date()), "to": str(lt.date()),
                 "versions_used": live.groupby("version")["race_id"].nunique().to_dict() if len(live) else {},
                 "app_strategy_changes": strategy_changes(strats),
                 "rows": []},
    }
    live_rows, live_det, live_pf = (personas_table(live, lpay, budget, models_dir, ("day", "week"), outer_top_boats(lent),
                                                   strats, return_details=True) if len(live) else ([], {}, pd.Series(dtype=float)))
    out["live"]["rows"] = live_rows
    booster, meta = load_model(history_model_id, models_dir)
    per = meta["periods"]
    part = filter_period(load_features(features_path(meta["variant"])), per["test_from"], per["test_to"])
    hpred = part[["race_id", "entry_number", "finish_position", "is_valid_result", "is_absent"]].assign(
        p_win=predict_p(part, booster, meta), model_id=meta["model_id"])
    hpay = load_interim_table("payouts", hpred["race_id"])
    hent = read_interim("entries", pd.Timestamp(per["test_from"]).date(), pd.Timestamp(per["test_to"]).date())
    hist_rows, hist_det, hist_pf = personas_table(hpred, hpay, budget, models_dir, ("month", "week"),
                                                  outer_top_boats(hent), None, return_details=True)
    out["history"] = {"from": per["test_from"], "to": per["test_to"], "model_id": meta["model_id"],
                      "note": HINDSIGHT_NOTE, "app_strategy": DEFAULT_APP_STRATEGY, "rows": hist_rows}

    # 反省点(過去の検証期間と実運用の両方で回収率が上がる候補だけ)
    if live_rows:
        from src.model.reflections import race_attributes, reflections_for

        hraces = read_interim("races", pd.Timestamp(per["test_from"]).date(), pd.Timestamp(per["test_to"]).date())
        lver = live.groupby("race_id")["version"].first()
        attrs = {"history": race_attributes(hraces, hpay, hist_pf),
                 "live": race_attributes(lraces, lpay, live_pf, lver)}
        res_all = {"history": hist_det, "live": live_det}
        labels = {p["id"]: p["name"] for p in PERSONAS}
        for row in out["live"]["rows"]:
            row["reflections"] = reflections_for(row["id"], res_all, attrs, labels, budget)
    return out
