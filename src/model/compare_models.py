"""モデルの比較(レース運びの特徴量 v2 の評価用)。reports/{name}.md と .json を出す。

CLI:
    python -m src.model.compare_models --name style_v2_comparison \
        --model pre_20261104_db90 --model pre_v2_20261105_6f88 \
        --model preview_20261104_0b0e:pre_20261104_db90 --model preview_v2_20261105_ef72:pre_v2_20261105_6f88 \
        --day 2026-11-04
    `model:fallback` は、直前情報が欠けたレースに使う pre 系モデル。

出すもの(valid と test。設定はすべて valid で決めたもので、test は評価だけ):
- win_logloss、trifecta_logloss(meta の γ)
- 本命の予想帯(80%以上・70%以上・60%以上)ごとの予測平均と実際の1着率
- 1号艇が「差し・まくり・まくり差し」で負けたレースでの、1号艇の p_win の平均(低いほど良い)
- S1(3連単上位5点)、単勝1点、2連複上位3点の回収率と95%区間(summary の関数)
- 特徴量の重要度(gain)上位
--day を付けると当日分も照合する(当日の予想はメモリ上で作るだけで、確定版の予想ファイルは触らない)。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.backtest import load_interim_table
from src.model.config import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, Periods, is_preview_variant
from src.model.data import clean_race_mask, features_path, filter_period, load_features, normalize_frame, race_matrix
from src.model.evaluate import compute_metrics
from src.model.lgbm import gammas_from_meta, load_model
from src.model.report import predict_p, pv_missing_races, with_fallback
from src.model.summary import compare_strategies

JST = timezone(timedelta(hours=9))
FAV_THRESHOLDS = (0.8, 0.7, 0.6)
INNER_LOSS_TECHNIQUES = {2: "差し", 3: "まくり", 4: "まくり差し"}
STRATEGIES = [
    {"name": "top_k", "short_label": "3連単 上位5点(S1)", "bet_type": "trifecta", "rule": "top_k", "params": {"k": 5}},
    {"name": "top_k_win", "short_label": "単勝 本命1点", "bet_type": "win", "rule": "top_k", "params": {"k": 1}},
    {"name": "top_k_quinella", "short_label": "2連複 上位3点", "bet_type": "quinella", "rule": "top_k", "params": {"k": 3}},
]
_COLS = ["race_id", "date", "stadium_number", "entry_number", "finish_position", "is_win", "is_valid_result", "is_absent"]


def favorite_thresholds(df: pd.DataFrame) -> list[dict]:
    """本命の p_win が閾値以上のレースでの、予測平均と実際の1着率(閾値は以上で累積)。"""
    d = df[clean_race_mask(df)]
    _, P = race_matrix(d, "p_win")
    _, F = race_matrix(d, "finish_position")
    fav = P.argmax(axis=1)
    pf = P[np.arange(len(P)), fav]
    hit = F[np.arange(len(F)), fav] == 1
    out = []
    for t in FAV_THRESHOLDS:
        m = pf >= t
        out.append({"threshold": t, "n": int(m.sum()), "pred_mean": float(pf[m].mean()) if m.any() else None,
                    "actual": float(hit[m].mean()) if m.any() else None})
    return out


def inner_loss(df: pd.DataFrame, technique: pd.Series) -> dict:
    """1号艇が差し・まくり系で負けたレースの1号艇 p_win の平均(決まり手別と合計)。"""
    d = df[clean_race_mask(df)]
    b1 = d[d["entry_number"] == 1].set_index("race_id")
    lost = b1[b1["finish_position"] != 1].join(technique.rename("tech"), how="inner")
    out = {}
    for code, name in INNER_LOSS_TECHNIQUES.items():
        g = lost[lost["tech"] == code]
        out[name] = {"n": int(len(g)), "p1_mean": float(g["p_win"].mean()) if len(g) else None}
    g = lost[lost["tech"].isin(INNER_LOSS_TECHNIQUES)]
    out["合計"] = {"n": int(len(g)), "p1_mean": float(g["p_win"].mean()) if len(g) else None}
    won = b1[b1["finish_position"] == 1]
    out["(参考)1号艇が勝ったレース"] = {"n": int(len(won)), "p1_mean": float(won["p_win"].mean()) if len(won) else None}
    return out


def evaluate_frame(p: pd.DataFrame, meta: dict, technique: pd.Series, payouts: pd.DataFrame, n_boot: int) -> dict:
    g2, g3 = gammas_from_meta(meta)
    m = compute_metrics(p, "p_win", g2, g3)
    strat = compare_strategies(p, payouts, None, STRATEGIES, g2, g3, n_boot)
    return {
        "races": m.get("races"), "win_logloss": m.get("win_logloss"), "trifecta_logloss": m.get("trifecta_logloss"),
        "top1_acc": m.get("top1_acc"), "favorite": favorite_thresholds(p), "inner_loss": inner_loss(p, technique),
        "strategies": [s for s in strat if s["name"] in {x["name"] for x in STRATEGIES}],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="モデル比較")
    parser.add_argument("--name", default="style_v2_comparison")
    parser.add_argument("--model", action="append", required=True, help="model_id または model_id:fallback_model_id")
    parser.add_argument("--day", default=None)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--n-boot", type=int, default=1000)
    args = parser.parse_args()

    from src.features.build_interim import read_interim

    specs = [(m.split(":")[0], m.split(":")[1] if ":" in m else None) for m in args.model]
    loaded = {}

    def get(mid):
        if mid not in loaded:
            loaded[mid] = load_model(mid, args.models_dir)
        return loaded[mid]

    feats_cache: dict[str, pd.DataFrame] = {}

    def feats(v):
        if v not in feats_cache:
            feats_cache[v] = load_features(features_path(v))
        return feats_cache[v]

    periods = Periods(**get(specs[0][0])[1]["periods"])
    races = read_interim("races", pd.Timestamp(periods.valid_from).date(), pd.Timestamp(periods.test_to).date())
    technique = races.set_index("race_id")["technique_number"]
    rids = filter_period(feats("pre"), periods.valid_from, periods.test_to)["race_id"]
    payouts = load_interim_table("payouts", rids)

    def model_preds(mid, fb, part_of):
        booster, meta = get(mid)
        part = part_of(meta["variant"])
        p = part[[c for c in _COLS if c in part.columns]].assign(p_win=predict_p(part, booster, meta))
        miss = None
        if is_preview_variant(meta["variant"]) and fb:
            fbb, fbm = get(fb)
            fpart = part_of(fbm["variant"])
            fpred = fpart[["race_id", "entry_number"]].assign(p_win=predict_p(fpart, fbb, fbm))
            missing = pv_missing_races(part)
            miss = len(missing) / part["race_id"].nunique()
            p = with_fallback(fpred, p, missing)
        return p, meta, miss

    results = {}
    for mid, fb in specs:
        r = {"fallback": fb, "periods": {}}
        for period in ("valid", "test"):
            p, meta, miss = model_preds(mid, fb, lambda v: filter_period(feats(v), *periods.range_of(period)))
            r["periods"][period] = evaluate_frame(p, meta, technique, payouts, args.n_boot)
            r["periods"][period]["pv_missing_rate"] = miss
        imp = meta.get("feature_importance_gain", {})
        total = sum(imp.values()) or 1.0
        r["importance_top"] = [{"feature": f, "share": v / total} for f, v in list(imp.items())[:15]]
        r["calibration"] = (meta.get("calibration") or {"method": "temperature"})["method"]
        r["harville_gamma"] = meta.get("harville_gamma")
        r["valid_cv"] = meta.get("calibration_selection", {}).get("cv_win_logloss")
        results[mid] = r
        t = r["periods"]["test"]
        print(f"{mid}: test win={t['win_logloss']:.4f} tri={t['trifecta_logloss']:.4f}")

    day = None
    if args.day:
        d = pd.Timestamp(args.day)
        ymd = d.strftime("%Y%m%d")
        ent = read_interim("entries", d.date(), d.date())
        dr = read_interim("races", d.date(), d.date())
        dpay = read_interim("payouts", d.date(), d.date())
        res = ent[["race_id", "entry_number", "finish_position", "is_absent"]].merge(
            dr[["race_id", "is_valid_result"]], on="race_id")
        day = {"date": args.day}
        for mid, fb in specs:
            def part_of(v):
                t = pd.read_parquet(FEATURES_DIR / "today" / f"{ymd}_{v}.parquet")
                return t.drop(columns=[c for c in ("finish_position", "is_win", "is_valid_result", "is_absent")
                                       if c in t.columns])
            p, meta, miss = model_preds(mid, fb, part_of)
            p = normalize_frame(p.merge(res, on=["race_id", "entry_number"], how="inner"))
            p["is_win"] = (p["finish_position"] == 1).astype(float)
            p = p[p["finish_position"].notna().groupby(p["race_id"]).transform("any")]
            e = evaluate_frame(p, meta, dr.set_index("race_id")["technique_number"], dpay, args.n_boot)
            e["pv_missing_rate"] = miss
            day[mid] = e

    out = REPORTS_DIR / f"{args.name}.json"
    out.write_text(json.dumps({"models": results, "day": day}, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS_DIR / f"{args.name}.md").write_text(render(results, day), encoding="utf-8")
    print(f"→ {REPORTS_DIR / (args.name + '.md')}")


def _f(v, fmt="{:.4f}"):
    return "-" if v is None else fmt.format(v)


def _block(L, title, rows: dict, with_imp=False):
    L += ["", f"## {title}", "",
          "| model | races | win_logloss | trifecta_logloss | 本命≥80% 予測/実際(n) | ≥70% | ≥60% | 1号艇が差し・まくり系で負けたときの1号艇 p_win(n) | 直前欠損率 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for mid, e in rows.items():
        fav = [f"{_f(x['pred_mean'], '{:.3f}')}/{_f(x['actual'], '{:.3f}')}({x['n']})" for x in e["favorite"]]
        il = e["inner_loss"]["合計"]
        L.append(f"| {mid} | {e['races']} | {_f(e['win_logloss'])} | {_f(e['trifecta_logloss'])} | " + " | ".join(fav)
                 + f" | {_f(il['p1_mean'], '{:.3f}')}({il['n']}) | {_f(e.get('pv_missing_rate'), '{:.1%}')} |")
    L += ["", "| model | 戦略 | 点数 | 回収率 | 95%区間 | 的中率(レース) | 最大連敗 |", "|---|---|---|---|---|---|---|"]
    for mid, e in rows.items():
        for s in e["strategies"]:
            ci = s["roi_ci95"]
            L.append(f"| {mid} | {s['short_label']} | {s['bets']} | {_f(s['roi'], '{:.3f}')} | "
                     f"{'-' if not ci else f'[{ci[0]:.3f}, {ci[1]:.3f}]'} | {_f(s['hit_rate'], '{:.3f}')} | {s['max_losing_streak']} |")
    L += ["", "1号艇が負けたレースの決まり手別(1号艇 p_win の平均、件数):", ""]
    for mid, e in rows.items():
        L.append(f"- {mid}: " + "、".join(f"{k} {_f(v['p1_mean'], '{:.3f}')}({v['n']})" for k, v in e["inner_loss"].items()))


def render(results: dict, day: dict | None) -> str:
    now = datetime.now(JST).isoformat(timespec="seconds")
    L = ["# レース運びの特徴量(v2)の比較", "",
         f"`python -m src.model.compare_models` が自動で生成(手で編集しない)。生成: {now}", "",
         "- 期間: train 2026-04〜08、valid 09、test 10。較正の方法と γ は valid だけで選んだ。test は評価だけ。",
         "- preview 系は、直前情報が欠けたレースを対応する pre 系モデルの予想で置き換えた。", "",
         "| model | 較正 | γ | valid の交差検証 win_logloss |", "|---|---|---|---|"]
    for mid, r in results.items():
        g = r["harville_gamma"]
        cv = r["valid_cv"]
        L.append(f"| {mid} | {r['calibration']} | {'なし' if g is None else f'{g['gamma2']},{g['gamma3']}'} | "
                 f"{'-' if not cv else ', '.join(f'{k} {v:.4f}' for k, v in cv.items())} |")
    for period in ("valid", "test"):
        _block(L, f"{period}", {mid: r["periods"][period] for mid, r in results.items()})
    if day:
        _block(L, f"実データ {day['date']}(1日分なので参考値)", {k: v for k, v in day.items() if k != "date"})
    L += ["", "## 特徴量の重要度(gain の割合、上位15)", ""]
    for mid, r in results.items():
        L.append(f"- {mid}: " + "、".join(f"{x['feature']} {x['share']:.1%}" for x in r["importance_top"]))
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
