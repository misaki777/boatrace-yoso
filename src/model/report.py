"""複数モデルの比較レポート(reports/model_comparison.md)。

CLI:
    python -m src.model.report --models pre_20261103_9c45 pre_20261104_db90 preview_20261104_0b0e \
        [--day 2026-11-04]

- 指標は valid と test の両方。戦略(S1〜S5)のパラメータは valid の回収率で選び、test はそのパラメータで1回だけ評価する。
- preview モデルは、直前情報が欠けるレース(pv_exhibition_time に欠損がある)を pre モデルの予想で置き換える
  (--fallback で使う pre の model_id を指定。既定は models/latest)。
- --day を付けると、その日の当日用特徴量(data/features/today/)と interim の結果で照合する(1日分なので参考値)。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.backtest import DEFAULT_GRID, LEAK_FREE, load_interim_table, run_backtest
from src.model.calibration import apply_rows, calibration_from_meta
from src.model.config import is_preview_variant, FEATURES_DIR, MODELS_DIR, REPORTS_DIR, Periods
from src.model.data import clean_race_mask, features_path, filter_period, load_features, normalize_frame, race_matrix
from src.model.evaluate import compute_metrics
from src.model.lgbm import gammas_from_meta, load_model, raw_scores, resolve_model_id

JST = timezone(timedelta(hours=9))
FAV_BANDS = [0.0, 0.3, 0.4, 0.5, 0.65, 1.0001]
PV_FLAG = "pv_exhibition_time"
_COLS = ["race_id", "date", "stadium_number", "entry_number", "finish_position", "is_win", "is_valid_result", "is_absent"]


def predict_p(df: pd.DataFrame, booster, meta: dict) -> np.ndarray:
    s = raw_scores(booster, df, meta)
    return apply_rows(df["race_id"].to_numpy(), df["entry_number"].to_numpy(), s, calibration_from_meta(meta))


def pv_missing_races(df: pd.DataFrame) -> set:
    """直前情報が欠けているレース(1艇でも pv_exhibition_time が欠損)。"""
    if PV_FLAG not in df.columns:
        return set()
    return set(df.loc[df[PV_FLAG].isna(), "race_id"])


def with_fallback(pre_pred: pd.DataFrame, pv_pred: pd.DataFrame, missing: set) -> pd.DataFrame:
    """直前情報が欠けたレースだけ pre の p_win に置き換える。"""
    m = pv_pred.merge(pre_pred[["race_id", "entry_number", "p_win"]], on=["race_id", "entry_number"],
                      how="left", suffixes=("", "_pre"))
    use_pre = m["race_id"].isin(missing) & m["p_win_pre"].notna()
    m["p_win"] = np.where(use_pre, m["p_win_pre"], m["p_win"])
    return m.drop(columns="p_win_pre")


def favorite_bands(df: pd.DataFrame) -> list[dict]:
    """本命(レース内で p_win 最大の艇)の確率帯ごとの、予測の平均と実際の1着率。"""
    d = df[clean_race_mask(df)]
    if d.empty:
        return []
    _, P = race_matrix(d, "p_win")
    _, F = race_matrix(d, "finish_position")
    fav = P.argmax(axis=1)
    pf = P[np.arange(len(P)), fav]
    hit = F[np.arange(len(F)), fav] == 1
    out = []
    for lo, hi in zip(FAV_BANDS[:-1], FAV_BANDS[1:]):
        m = (pf >= lo) & (pf < hi)
        if m.any():
            out.append({"band": f"{lo:.2f}-{min(hi, 1):.2f}", "n": int(m.sum()),
                        "mean_pred": float(pf[m].mean()), "actual": float(hit[m].mean())})
    m = pf >= 0.5
    out.append({"band": ">=0.50", "n": int(m.sum()), "mean_pred": float(pf[m].mean()) if m.any() else None,
                "actual": float(hit[m].mean()) if m.any() else None})
    return out


def strategy_table(preds: dict[str, pd.DataFrame], payouts, odds, gammas, n_boot: int) -> list[dict]:
    """S1〜S5(3連単)。パラメータは valid の回収率最大で選び、test はそれで評価する。"""
    rows = []
    for strategy, grid in DEFAULT_GRID.items():
        best, best_roi = None, -1.0
        for params in grid:
            s, _ = run_backtest(preds["valid"], payouts, strategy, "trifecta", params,
                                odds=odds if not LEAK_FREE[strategy] else None, gamma2=gammas[0], gamma3=gammas[1], n_boot=10)
            if s["roi"] is not None and s["bets"] >= 100 and s["roi"] > best_roi:
                best, best_roi = params, s["roi"]
        if best is None:
            continue
        t, _ = run_backtest(preds["test"], payouts, strategy, "trifecta", best,
                            odds=odds if not LEAK_FREE[strategy] else None, gamma2=gammas[0], gamma3=gammas[1], n_boot=n_boot)
        # S1 は k=5 の固定値も載せる(PWA の既定)
        rows.append({"strategy": strategy, "params": best, "valid_roi": best_roi, "test": t})
    s1, _ = run_backtest(preds["test"], payouts, "top_k", "trifecta", {"k": 5}, gamma2=gammas[0], gamma3=gammas[1], n_boot=n_boot)
    rows.insert(0, {"strategy": "top_k(固定 k=5)", "params": {"k": 5}, "valid_roi": None, "test": s1})
    return rows


def _f(v, fmt="{:.4f}"):
    return "-" if v is None else fmt.format(v)


def main() -> None:
    parser = argparse.ArgumentParser(description="モデル比較レポート")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--fallback", default="latest", help="preview の欠損レースに使う pre モデル")
    parser.add_argument("--day", default=None, help="当日照合する日付 YYYY-MM-DD")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR))
    parser.add_argument("--n-boot", type=int, default=1000)
    args = parser.parse_args()

    feats_by_variant: dict[str, pd.DataFrame] = {}

    def feats(v):
        if v not in feats_by_variant:
            feats_by_variant[v] = load_features(features_path(v))
        return feats_by_variant[v]

    fb_booster, fb_meta = load_model(resolve_model_id(args.fallback, args.models_dir, "pre"), args.models_dir)
    periods = Periods(**fb_meta["periods"])
    fb_pred = {}
    for period in ("valid", "test"):
        part = filter_period(feats("pre"), *periods.range_of(period))
        fb_pred[period] = part[_COLS].assign(p_win=predict_p(part, fb_booster, fb_meta))

    all_race_ids = pd.concat([filter_period(feats("pre"), periods.valid_from, periods.test_to)["race_id"]])
    payouts = load_interim_table("payouts", all_race_ids)
    odds = load_interim_table("odds", all_race_ids)
    odds = odds[odds["bet_type"] == "trifecta"]

    results = {}
    for mid in args.models:
        booster, meta = load_model(mid, args.models_dir)
        g = gammas_from_meta(meta)
        preds, metrics, missing_rate = {}, {}, {}
        for period in ("valid", "test"):
            part = filter_period(feats(meta["variant"]), *periods.range_of(period))
            p = part[_COLS].assign(p_win=predict_p(part, booster, meta))
            if is_preview_variant(meta["variant"]):
                miss = pv_missing_races(part)
                missing_rate[period] = len(miss) / part["race_id"].nunique()
                p = with_fallback(fb_pred[period], p, miss)
            preds[period] = p
            m = compute_metrics(p, "p_win", *g)
            m["favorite_bands"] = favorite_bands(p)
            metrics[period] = m
        strategies = strategy_table(preds, payouts, odds, g, args.n_boot)
        results[mid] = {"meta": meta, "metrics": metrics, "strategies": strategies, "pv_missing_rate": missing_rate}
        print(f"{mid}: test win_logloss={metrics['test']['win_logloss']:.4f} trifecta_logloss={metrics['test']['trifecta_logloss']:.4f}")

    day = day_check(args, results, fb_booster, fb_meta) if args.day else None
    md = render(results, day, args.fallback, fb_meta["model_id"])
    out = Path(args.reports_dir) / "model_comparison.md"
    out.write_text(md, encoding="utf-8")
    js = {mid: {k: v for k, v in r.items() if k != "meta"} for mid, r in results.items()}
    (Path(args.reports_dir) / "model_comparison.json").write_text(
        json.dumps({"models": js, "day": day}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"→ {out}")


def day_check(args, results, fb_booster, fb_meta) -> dict:
    """1日分の当日予想を、interim の結果・払戻と照合する。"""
    from src.features.build_interim import read_interim

    d = pd.Timestamp(args.day)
    ymd = d.strftime("%Y%m%d")
    ent = read_interim("entries", d.date(), d.date())
    races = read_interim("races", d.date(), d.date())
    payouts = read_interim("payouts", d.date(), d.date())
    res = ent[["race_id", "entry_number", "finish_position", "is_absent"]].merge(
        races[["race_id", "is_valid_result"]], on="race_id")
    pre_today = pd.read_parquet(FEATURES_DIR / "today" / f"{ymd}_pre.parquet")
    fb = pre_today[["race_id", "entry_number"]].assign(p_win=predict_p(pre_today, fb_booster, fb_meta))
    out = {"date": args.day}
    for mid, r in results.items():
        meta = r["meta"]
        booster, _ = load_model(mid, args.models_dir)
        path = FEATURES_DIR / "today" / f"{ymd}_{meta['variant']}.parquet"
        today = pd.read_parquet(path)
        p = today[["race_id", "entry_number", "stadium_number"]].assign(p_win=predict_p(today, booster, meta))
        miss = None
        if is_preview_variant(meta["variant"]):
            m = pv_missing_races(today)
            miss = len(m) / today["race_id"].nunique()
            p = with_fallback(fb, p, m)
        p = normalize_frame(p.merge(res, on=["race_id", "entry_number"], how="inner"))
        p["is_win"] = (p["finish_position"] == 1).astype(float)
        p = p[p["finish_position"].notna().groupby(p["race_id"]).transform("any")]  # 結果が出たレースだけ
        g = gammas_from_meta(meta)
        m = compute_metrics(p, "p_win", *g)
        s1, _ = run_backtest(p, payouts, "top_k", "trifecta", {"k": 5}, gamma2=g[0], gamma3=g[1], n_boot=1000)
        out[mid] = {
            "races": m.get("races"), "win_logloss": m.get("win_logloss"), "top1_acc": m.get("top1_acc"),
            "trifecta_logloss": m.get("trifecta_logloss"), "favorite_bands": favorite_bands(p),
            "s1_top5": {k: s1[k] for k in ("bets", "stake", "return", "roi", "roi_ci95")}, "pv_missing_rate": miss,
        }
    return out


def render(results: dict, day: dict | None, fallback: str, fb_id: str) -> str:
    now = datetime.now(JST).isoformat(timespec="seconds")
    L = ["# モデル比較", "",
         f"`python -m src.model.report` が自動で生成(手で編集しない)。生成: {now}", "",
         "- 較正の方法・γ・戦略のパラメータは **valid だけ** で選んだ。test は選んだ後に1回評価しただけ。",
         f"- preview は、直前情報が欠けるレースを pre モデル({fb_id})の予想で置き換えて評価した。", ""]

    def desc(meta):
        c = calibration_from_meta(meta)
        g = meta.get("harville_gamma")
        return f"{meta['variant']} / 較正={c['method']} / γ={'なし' if g is None else f'{g['gamma2']},{g['gamma3']}'}"

    L += ["## モデル", "", "| model_id | 内容 |", "|---|---|"]
    L += [f"| {mid} | {desc(r['meta'])} |" for mid, r in results.items()]
    for period in ("valid", "test"):
        L += ["", f"## 指標({period})", "",
              "| model_id | win_logloss | win_brier | top1_acc | trifecta_logloss | trifecta_top10 | races | 直前欠損率 |",
              "|---|---|---|---|---|---|---|---|"]
        for mid, r in results.items():
            m = r["metrics"][period]
            L.append(f"| {mid} | {_f(m.get('win_logloss'))} | {_f(m.get('win_brier'))} | {_f(m.get('top1_acc'), '{:.3f}')} | "
                     f"{_f(m.get('trifecta_logloss'))} | {_f(m.get('trifecta_top10_hit'), '{:.3f}')} | {m.get('races')} | "
                     f"{_f(r['pv_missing_rate'].get(period), '{:.1%}')} |")
    L += ["", "## 本命の確率帯ごとの較正(test)", "", "予測=本命の p_win の平均、実際=本命が1着になった割合。", ""]
    bands = [b["band"] for b in next(iter(results.values()))["metrics"]["test"]["favorite_bands"]]
    L += ["| model_id | " + " | ".join(bands) + " |", "|---|" + "---|" * len(bands)]
    for mid, r in results.items():
        cells = {b["band"]: f"{_f(b['mean_pred'], '{:.3f}')} / {_f(b['actual'], '{:.3f}')} (n={b['n']})"
                 for b in r["metrics"]["test"]["favorite_bands"]}
        L.append(f"| {mid} | " + " | ".join(cells.get(b, "-") for b in bands) + " |")
    L += ["", "## 艇単位の較正(test、p_win の10分位。予測 / 実際)", ""]
    L += ["| model_id | " + " | ".join(f"D{i + 1}" for i in range(10)) + " |", "|---|" + "---|" * 10]
    for mid, r in results.items():
        L.append(f"| {mid} | " + " | ".join(f"{c['mean_pred']:.3f}/{c['actual']:.3f}" for c in r["metrics"]["test"]["calibration"]) + " |")
    L += ["", "## 戦略別の回収率(3連単、test。1点100円、ブートストラップ1000回の95%区間)", "",
          "S3・S4 は確定オッズを見て買う後知恵(楽観的な上限値)。", "",
          "| model_id | 戦略 | パラメータ(valid で選択) | valid 回収率 | test 点数 | test 回収率 | 95%区間 | 的中率(レース) |",
          "|---|---|---|---|---|---|---|---|"]
    for mid, r in results.items():
        for s in r["strategies"]:
            t = s["test"]
            ci = t["roi_ci95"]
            L.append(f"| {mid} | {s['strategy']}{'' if LEAK_FREE.get(s['strategy'].split('(')[0], True) else '(後知恵)'} | "
                     f"{json.dumps(s['params'])} | {_f(s['valid_roi'], '{:.3f}')} | {t['bets']} | {_f(t['roi'], '{:.3f}')} | "
                     f"{'-' if not ci else f'[{ci[0]:.3f}, {ci[1]:.3f}]'} | {_f(t['hit_rate'], '{:.3f}')} |")
    if day:
        L += ["", f"## 実運用の照合({day['date']}、1日分なので参考値)", "",
              "| model_id | races | win_logloss | top1_acc | trifecta_logloss | 本命≥0.50 予測/実際 (n) | S1 top5 回収率 [95%区間] | 直前欠損率 |",
              "|---|---|---|---|---|---|---|---|"]
        for mid, r in day.items():
            if mid == "date":
                continue
            fb = [b for b in r["favorite_bands"] if b["band"] == ">=0.50"]
            fbs = f"{_f(fb[0]['mean_pred'], '{:.3f}')} / {_f(fb[0]['actual'], '{:.3f}')} ({fb[0]['n']})" if fb else "-"
            s1 = r["s1_top5"]
            ci = s1["roi_ci95"]
            L.append(f"| {mid} | {r['races']} | {_f(r['win_logloss'])} | {_f(r['top1_acc'], '{:.3f}')} | {_f(r['trifecta_logloss'])} | "
                     f"{fbs} | {_f(s1['roi'], '{:.3f}')} {'-' if not ci else f'[{ci[0]:.2f}, {ci[1]:.2f}]'} | "
                     f"{_f(r['pv_missing_rate'], '{:.1%}')} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
