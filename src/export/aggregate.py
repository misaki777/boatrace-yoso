"""index.json と performance.json の組み立て(標準ライブラリのみ)。

入力は days/YYYYMMDD.json の中身(dict)のリスト。集計は days の値だけから行うので、
days を作り直せば index / performance も同じ値で作り直せる(冪等)。
"""

from __future__ import annotations

from typing import Any, Iterable

from src.export.build import SCHEMA_VERSION, STAKE_PER_BET, compute_outcome, iter_races, roi, strategy_short_label
from src.export.perf_stats import calibration_bins, sample_note, strategy_stats

# 出典(spec 04 1章)
SOURCES = [
    {"name": "Boatrace Open API", "url": "https://github.com/boatraceopenapi/api", "license": "MIT"},
    {"name": "Turnmark API", "url": "https://github.com/turnmark/api", "license": "MIT"},
]


def build_index(days: Iterable[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    """days(順不同)から index.json を作る。dates は新しい順。"""
    ordered = sorted(days, key=lambda d: d["date"], reverse=True)
    dates = []
    for d in ordered:
        dates.append({
            "date": d["date"],
            "file": f"days/{d['date'].replace('-', '')}.json",
            "status": d["status"],
            "stadium_count": len(d["stadiums"]),
            "race_count": sum(1 for _ in iter_races(d)),
            "summary": d["day_summary"],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "latest_date": dates[0]["date"] if dates else None,
        "sources": SOURCES,
        "dates": dates,
    }


def strategy_label(strategy: dict[str, Any]) -> str:
    """戦略を短い文字列にする。例: top_k(trifecta,k=5)"""
    params = [strategy["bet_type"]] + [f"{k}={v}" for k, v in strategy.items()
                                       if k not in ("name", "bet_type", "description", "short_label")]
    return f"{strategy['name']}({','.join(params)})"


def _race_counts(day: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """(推奨があり結果の出たレース数, 的中したレース数, bets, hits, stake, return)"""
    races = hit_races = bets = hits = stake = ret = 0
    for r in iter_races(day):
        res = r.get("result")
        oc = res.get("recommended_outcome") if res else None
        if not oc or oc["bets"] == 0:
            continue
        races += 1
        hit_races += 1 if oc["hits"] > 0 else 0
        bets += oc["bets"]
        hits += oc["hits"]
        stake += oc["stake"]
        ret += oc["return"]
    return races, hit_races, bets, hits, stake, ret


def build_live(days: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """実運用の集計(performance.live)。

    - races: 推奨買い目が1点以上あり、結果が出たレースの数(見送り・中止は数えない)
    - hit_rate: 推奨が1点以上的中したレースの割合(hits / races ではなく、レース単位)
    - 戦略が日によって違う場合は、最新日の戦略名を表示する(集計は各日の推奨の結果そのまま)
    """
    ordered = sorted((d for d in days if d.get("day_summary") is not None), key=lambda d: d["date"])
    if not ordered:
        return None
    total = dict(races=0, hit_races=0, bets=0, hits=0, stake=0, ret=0)
    months: dict[str, dict[str, int]] = {}
    by_day = []
    cum_stake = cum_ret = 0
    for d in ordered:
        races, hit_races, bets, hits, stake, ret = _race_counts(d)
        for k, v in zip(("races", "hit_races", "bets", "hits", "stake", "ret"),
                        (races, hit_races, bets, hits, stake, ret)):
            total[k] += v
        m = months.setdefault(d["date"][:7], dict(races=0, stake=0, ret=0))
        m["races"] += races
        m["stake"] += stake
        m["ret"] += ret
        cum_stake += stake
        cum_ret += ret
        by_day.append({"date": d["date"], "stake": stake, "return": ret, "roi": roi(stake, ret),
                       "cum_roi": roi(cum_stake, cum_ret)})
    extra = live_details(ordered)
    return {
        "strategy": strategy_label(ordered[-1]["strategy"]),
        "strategy_label": ordered[-1]["strategy"].get("short_label")
        or strategy_short_label(ordered[-1]["strategy"]["name"], ordered[-1]["strategy"]),
        "from": ordered[0]["date"],
        "to": ordered[-1]["date"],
        "total": {
            "races": total["races"], "bets": total["bets"], "hits": total["hits"],
            "stake": total["stake"], "return": total["ret"], "roi": roi(total["stake"], total["ret"]),
            "hit_rate": round(total["hit_races"] / total["races"], 3) if total["races"] else None,
        },
        "by_month": [{"month": k, "races": v["races"], "stake": v["stake"], "return": v["ret"],
                      "roi": roi(v["stake"], v["ret"])} for k, v in sorted(months.items())],
        "by_day": by_day,
        **extra,
        "sample_note": sample_note(total["races"]),
    }


def build_performance(days: Iterable[dict[str, Any]], generated_at: str,
                      backtest: dict[str, Any] | None) -> dict[str, Any]:
    """performance.json を作る。backtest は reports/backtest の summary から作った dict(無ければ None)。

    backtest.strategies[] に short_label が無ければ、days と同じ規則で付ける。
    """
    if backtest is not None:
        backtest = dict(backtest)
        strategies = []
        for s in backtest.get("strategies", []):
            s = dict(s)
            s.setdefault("short_label", strategy_short_label(s["name"], s.get("params", {})))
            strategies.append(s)
        backtest["strategies"] = strategies
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "live": build_live(days),
        "backtest": backtest,
    }


# ---------------------------------------------------------------------------
# Phase 3a: 版ごと・モデル履歴・較正・戦略比較(04 8.2、08 5.3)
# ---------------------------------------------------------------------------

BASELINES = (
    ("baseline_win1", "1号艇 単勝"),
    ("baseline_tri1_top5", "1号艇から3連単 上位5点"),
)


def _finished(days):
    """(day, race) のうち、予想と結果のあるレース(確定版の予想だけ。08 5.1)。"""
    for d in days:
        for r in iter_races(d):
            if r.get("prediction") and r.get("result") and r["result"].get("recommended_outcome") is not None:
                yield d, r


def _baseline_bets(name: str, pr: dict) -> list[dict]:
    if name == "baseline_win1":
        return [{"bet_type": "win", "combination": "1"}]
    tri = [x for x in pr["picks"]["trifecta"] if x["combination"].startswith("1-")][:5]
    return [{"bet_type": "trifecta", "combination": x["combination"]} for x in tri]


def _row(outcome: dict) -> tuple[int, int, int, bool]:
    return outcome["bets"], outcome["stake"], outcome["return"], outcome["hits"] > 0


def live_details(days: list[dict]) -> dict:
    rows = list(_finished(days))
    by_version = {}
    for v in ("pre", "preview"):
        per = [_row(r["result"]["recommended_outcome"]) for _, r in rows
               if r["prediction"].get("version", r["prediction"]["variant"]) == v]
        st = strategy_stats(per) if any(x[0] for x in per) else None
        by_version[v] = st
    # モデルの履歴(日の古い順に、同じ model_id が続く区間)
    models: list[dict] = []
    prev_date = None
    for d in days:
        ids = sorted({r["prediction"].get("model_id") or (d.get("model") or {}).get("model_id")
                      for r in iter_races(d) if r.get("prediction")} - {None})
        for mid in ids:
            m = next((x for x in reversed(models) if x["model_id"] == mid), None)
            if m is not None and m["to"] == prev_date:
                m["to"] = d["date"]
            else:
                models.append({"model_id": mid, "from": d["date"], "to": d["date"]})
        prev_date = d["date"]
    # 較正(1着確率と、実際に1着になったか。1着が決まらないレース・欠場のあるレースは除く)
    pairs = []
    for _, r in rows:
        res = r["result"]
        if res["order"][0] is None or res["absent_entries"]:
            continue
        for b in r["prediction"]["boats"]:
            pairs.append((b["p_win"], b["entry"] == res["order"][0]))
    # 戦略比較: 実運用の推奨(各日の strategy)と、基準の2つ
    label = days[-1]["strategy"].get("short_label") or strategy_short_label(days[-1]["strategy"]["name"],
                                                                            days[-1]["strategy"])
    strategies = [{"name": days[-1]["strategy"]["name"], "short_label": label,
                   **strategy_stats([_row(r["result"]["recommended_outcome"]) for _, r in rows]),
                   "is_baseline": False}]
    for name, lab in BASELINES:
        per = []
        for _, r in rows:
            bets = _baseline_bets(name, r["prediction"])
            oc = compute_outcome(bets, r["result"]["payouts"], r["result"]["absent_entries"])
            per.append(_row(oc))
        strategies.append({"name": name, "short_label": lab, **strategy_stats(per), "is_baseline": True})
    return {"by_version": by_version, "models": models, "calibration": calibration_bins(pairs),
            "strategies": strategies}
