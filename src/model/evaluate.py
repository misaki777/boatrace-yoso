"""評価指標(spec/03 4章)と、B0・B1・LightGBM の比較表の生成(spec/03 8章)。

CLI:
    python -m src.model.evaluate --model-id <id|latest>    # B0・B1・LightGBM を valid/test で比較
    python -m src.model.evaluate --variant pre              # model-id なし: B0・B1 だけ
出力:
    reports/eval/{name}/metrics.json
    reports/model_comparison.md
    reports/backtest/{model_id}/predictions_{period}.parquet(backtest の入力。LightGBM のとき)
payouts が手に入るとき(data/interim/payouts か --payouts)は、S1 top_k(3連単)の回収率も出す。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.model import combinations as cb
from src.model.baselines import B0CourseRate, B1Logistic
from src.model.config import BOOTSTRAP_N, DEFAULT_PERIODS, MODELS_DIR, REPORTS_DIR, VARIANTS, Periods
from src.model.data import (
    clean_race_mask,
    exclusion_counts,
    features_path,
    filter_period,
    load_features,
    race_matrix,
)

JST = timezone(timedelta(hours=9))
_EPS = 1e-15

# 3連単の (1着, 2着, 3着)(0始まり)→ combination_labels("trifecta") の列番号
_TRIFECTA_INDEX = {
    tuple(int(x) - 1 for x in label.split("-")): i for i, label in enumerate(cb.combination_labels("trifecta"))
}


def _binary_logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def compute_metrics(
    df: pd.DataFrame,
    p_col: str = "p_win",
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> dict:
    """1行=1艇の表(race_id, entry_number, stadium_number, finish_position, is_valid_result, is_absent, p_col)から指標を出す。

    評価に使うのは clean_race_mask のレース(中止・欠場あり・同着1着・6艇そろわないレースは外す)。
    p_col はレース内で正規化し直してから使う。
    """
    excluded = exclusion_counts(df)
    d = df[clean_race_mask(df)]
    out: dict = {"exclusions": excluded}
    if d.empty:
        out["races"] = 0
        return out

    race_ids, P = race_matrix(d, p_col)
    P = P / P.sum(axis=1, keepdims=True)
    _, F = race_matrix(d, "finish_position")
    Y = (F == 1).astype(float)
    n = len(race_ids)
    winner = Y.argmax(axis=1)
    p_true = P[np.arange(n), winner]
    race_ll = -np.log(np.clip(p_true, _EPS, 1))

    out["races"] = int(n)
    out["win_logloss"] = float(race_ll.mean())
    out["win_brier"] = float(((P - Y) ** 2).sum(axis=1).mean())
    out["top1_acc"] = float((P.argmax(axis=1) == winner).mean())

    # 3連単: 1・2・3着がそれぞれちょうど1艇のレースだけ
    ok = np.all([(F == k).sum(axis=1) == 1 for k in (1, 2, 3)], axis=0)
    out["trifecta_races"] = int(ok.sum())
    if ok.any():
        F_ok = F[ok]
        cols = np.array(
            [_TRIFECTA_INDEX[tuple(int(np.where(row == k)[0][0]) for k in (1, 2, 3))] for row in F_ok]
        )
        T = cb.combination_prob_matrix(P[ok], "trifecta", gamma2, gamma3)
        p_tri = T[np.arange(len(cols)), cols]
        out["trifecta_logloss"] = float(-np.log(np.clip(p_tri, _EPS, 1)).mean())
        rank = (T > p_tri[:, None]).sum(axis=1)  # 自分より確率が高い組合せの数
        out["trifecta_top10_hit"] = float((rank < 10).mean())

    # 較正: 艇単位の p を10分位に分ける
    p_flat, y_flat = P.reshape(-1), Y.reshape(-1)
    bins = pd.qcut(p_flat, 10, labels=False, duplicates="drop")
    cal = pd.DataFrame({"bin": bins, "p": p_flat, "y": y_flat}).groupby("bin")
    out["calibration"] = [
        {"bin": int(b), "mean_pred": float(g["p"].mean()), "actual": float(g["y"].mean()), "n": int(len(g))}
        for b, g in cal
    ]

    # 場別(レース単位の win_logloss)とコース(枠番)別(艇単位の二値 logloss)
    stadium = d.groupby("race_id", sort=False)["stadium_number"].first().reindex(race_ids).to_numpy()
    out["by_stadium"] = {
        str(int(s)): {"races": int((stadium == s).sum()), "win_logloss": float(race_ll[stadium == s].mean())}
        for s in np.unique(stadium)
    }
    out["by_entry"] = {
        str(k + 1): {
            "win_rate": float(Y[:, k].mean()),
            "mean_pred": float(P[:, k].mean()),
            "binary_logloss": _binary_logloss(P[:, k], Y[:, k]),
        }
        for k in range(6)
    }
    return out


# ---------------------------------------------------------------------------
# 比較表
# ---------------------------------------------------------------------------

_TABLE_COLS = [
    ("win_logloss", "win_logloss", "{:.4f}"),
    ("win_brier", "win_brier", "{:.4f}"),
    ("top1_acc", "top1_acc", "{:.3f}"),
    ("trifecta_logloss", "trifecta_logloss", "{:.4f}"),
    ("trifecta_top10_hit", "trifecta_top10", "{:.3f}"),
    ("races", "races", "{:d}"),
]


def comparison_markdown(results: dict, title_note: str = "") -> str:
    """{period: {model_name: metrics}} から Markdown の比較表を作る。"""
    now = datetime.now(JST).isoformat(timespec="seconds")
    lines = [
        "# モデル比較",
        "",
        f"このファイルは `python -m src.model.evaluate` が自動で生成する(手で編集しない)。生成: {now}",
        "",
    ]
    if title_note:
        lines += [title_note, ""]
    for period, models in results.items():
        lines += [f"## {period}", ""]
        header = "| model | " + " | ".join(c[1] for c in _TABLE_COLS) + " | S1 top5 回収率 [95%区間] |"
        lines += [header, "|" + "---|" * (len(_TABLE_COLS) + 2)]
        for name, m in models.items():
            cells = []
            for key, _, fmt in _TABLE_COLS:
                v = m.get(key)
                cells.append(fmt.format(v) if v is not None else "-")
            roi = "-"
            for r in m.get("roi_simulation", []):
                if r["params"].get("k") == 5 and r["bet_type"] == "trifecta" and r["roi"] is not None:
                    ci = r["roi_ci95"]
                    roi = f"{r['roi']:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]"
            lines.append(f"| {name} | " + " | ".join(cells) + f" | {roi} |")
        if models:
            ex = next(iter(models.values())).get("exclusions", {})
            lines += ["", f"除外したレース: {json.dumps(ex, ensure_ascii=False)}", ""]
    lines += [
        "- win_logloss が主指標(小さいほど良い)。全艇1/6のときは log 6 = 1.7918。",
        "- LightGBM は B1 より win_logloss が良くなければ採用しない(spec/03 2.3章)。",
        "- 回収率は 3連単の確率上位5点を全レースで買ったとした値(1点100円、ブートストラップ1000回の95%区間)。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _roi_simulation(preds: pd.DataFrame, payouts: pd.DataFrame | None, n_boot: int) -> list[dict]:
    if payouts is None:
        return []
    from src.model.backtest import run_backtest

    runs = []
    for k in (1, 3, 5, 10):
        summary, _ = run_backtest(preds, payouts, "top_k", "trifecta", {"k": k}, n_boot=n_boot)
        summary.pop("by_month", None)
        runs.append(summary)
    return runs


def evaluate_all(
    features: pd.DataFrame,
    periods: Periods,
    lgbm: tuple | None = None,
    payouts: pd.DataFrame | None = None,
    n_boot: int = BOOTSTRAP_N,
) -> tuple[dict, dict[str, pd.DataFrame]]:
    """B0・B1(と LightGBM)を valid/test で評価する。

    Args:
        lgbm: (booster, meta) または None。
    Returns:
        ({period: {model: metrics}}, {period: 予想の表(1行=1艇)})
    """
    train = filter_period(features, *periods.range_of("train"))
    valid = filter_period(features, *periods.range_of("valid"))
    b0 = B0CourseRate().fit(train)
    b1 = B1Logistic().fit(train, valid)

    results: dict = {}
    preds_by_period: dict[str, pd.DataFrame] = {}
    for period in ("valid", "test"):
        part = filter_period(features, *periods.range_of(period))
        if part.empty:
            continue
        cols = [c for c in ("race_id", "date", "stadium_number", "entry_number", "finish_position",
                            "is_win", "is_valid_result", "is_absent") if c in part.columns]
        pred = part[cols].copy()
        pred["p_win_b0"] = b0.predict(part)
        pred["p_win_b1"] = b1.predict(part)
        model_cols = {"B0": "p_win_b0", "B1": "p_win_b1"}
        if lgbm is not None:
            from src.model.calibration import apply_rows, calibration_from_meta
            from src.model.lgbm import raw_scores

            booster, meta = lgbm
            pred["score"] = raw_scores(booster, part, meta)
            pred["p_win"] = apply_rows(
                pred["race_id"].to_numpy(), pred["entry_number"].to_numpy(), pred["score"].to_numpy(),
                calibration_from_meta(meta),
            )
            model_cols[f"LightGBM({meta['model_id']})"] = "p_win"
        results[period] = {}
        for name, col in model_cols.items():
            m = compute_metrics(pred, col)
            m["roi_simulation"] = _roi_simulation(pred.assign(p_win=pred[col]), payouts, n_boot)
            results[period][name] = m
        preds_by_period[period] = pred
    results["_baseline_params"] = {"B0": b0.params(), "B1": b1.params()}
    return results, preds_by_period


def main() -> None:
    parser = argparse.ArgumentParser(description="モデルの評価と比較表の作成")
    parser.add_argument("--model-id", default=None, help="LightGBM の model_id か latest。省略時は B0・B1 のみ")
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--features", default=None, help="特徴量の parquet(省略時は data/features/features_{variant}.parquet)")
    parser.add_argument("--payouts", default=None, help="payouts の parquet(省略時は data/interim/payouts を探す)")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR))
    parser.add_argument("--n-boot", type=int, default=BOOTSTRAP_N)
    args = parser.parse_args()

    lgbm = None
    periods = DEFAULT_PERIODS
    variant = args.variant
    name = f"baselines_{variant}"
    if args.model_id:
        from src.model.lgbm import load_model

        booster, meta = load_model(args.model_id, args.models_dir, args.variant)
        lgbm = (booster, meta)
        periods = Periods(**meta["periods"])
        variant = meta["variant"]
        name = meta["model_id"]

    feats = load_features(args.features or features_path(variant))
    payouts = None
    if args.payouts:
        payouts = pd.read_parquet(args.payouts)
    else:
        try:
            from src.model.backtest import load_interim_table

            payouts = load_interim_table("payouts", feats["race_id"])
        except FileNotFoundError:
            print("payouts が無いため、回収率シミュレーションは省略します")

    results, preds = evaluate_all(feats, periods, lgbm, payouts, args.n_boot)

    reports = Path(args.reports_dir)
    out_dir = reports / "eval" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "variant": variant, "periods": periods.to_dict(), "results": results}
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if lgbm is not None:
        bt_dir = reports / "backtest" / name
        bt_dir.mkdir(parents=True, exist_ok=True)
        for period, p in preds.items():
            p.to_parquet(bt_dir / f"predictions_{period}.parquet", index=False)

    table = {k: v for k, v in results.items() if not k.startswith("_")}
    note = f"variant: `{variant}`、期間: {json.dumps(periods.to_dict(), ensure_ascii=False)}"
    (reports / "model_comparison.md").write_text(comparison_markdown(table, note), encoding="utf-8")
    for period, models in table.items():
        for mname, m in models.items():
            print(f"[{period}] {mname:28s} win_logloss={m.get('win_logloss', float('nan')):.4f} "
                  f"top1_acc={m.get('top1_acc', float('nan')):.3f} races={m.get('races')}")
    print(f"→ {out_dir / 'metrics.json'}、{reports / 'model_comparison.md'}")


if __name__ == "__main__":
    main()
