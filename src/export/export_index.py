"""index.json と performance.json を作り直す(E-3)。

    python -m src.export.export_index [--out docs/data] [--generated-at ...]

- live: docs/data/days/*.json の推奨買い目の結果を集計する(days だけから作るので冪等)。
- backtest: models/latest の採用モデルについて、
    reports/eval/<model_id>/metrics.json(test 期間の LightGBM の指標)と
    reports/backtest/<model_id>/<戦略>_test/summary.json(戦略ごとの回収率)から作る。
  無ければ backtest は null。
- 書き出しの順: performance.json → index.json(index は最後。spec 04 2章)。
- ローカルJSON(docs/data-local/)の days があれば、そちらにも同じ形の index.json・performance.json を書く
  (PWA のローカル表示は data-local/ を丸ごと読む。spec 07 5章)。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.export.aggregate import build_index, build_performance
from src.export.build import JST, to_jst_iso
from src.export.config import DOCS_DATA_DIR
from src.export.io_util import read_json, write_json
from src.export.schema import validate_index_against_days

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

# performance.backtest.metrics に出す指標(spec 04 5章の例 + 補助指標)
METRIC_KEYS = ("win_logloss", "win_brier", "top1_acc", "trifecta_logloss", "trifecta_top10_hit")
# 戦略の summary.json を読む順(PWA での並び順)
STRATEGY_ORDER = ("top_k", "confidence_skip", "prob_threshold", "ev_final", "ev_final_haircut")


def load_days(out_dir: Path) -> list[dict[str, Any]]:
    return [read_json(p) for p in sorted((Path(out_dir) / "days").glob("*.json"))]


def _r(x: Any, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def backtest_run_to_public(run: dict[str, Any]) -> dict[str, Any]:
    """backtest の summary.json の runs[] の1件を、performance.backtest.strategies[] の形にする。

    集計項目の定義は spec 04 5章にそろえる:
    - races = 推奨を1点以上買ったレースの数(summary の bet_races)
    - hit_rate = 1点以上的中したレース ÷ races(summary の hit_rate も同じ定義。src/model/backtest.py)
    - 投資が0なら roi は null
    """
    bet_races = int(run.get("bet_races", run.get("races", 0)) or 0)
    stake = int(run.get("stake", 0) or 0)
    ci = run.get("roi_ci95")
    return {
        "name": run["strategy"],
        "params": {"bet_type": run["bet_type"], **run.get("params", {})},
        "roi": _r(run.get("roi")) if stake > 0 else None,
        "roi_ci95": [_r(ci[0]), _r(ci[1])] if ci and stake > 0 else None,
        "hit_rate": _r(run.get("hit_rate")) if bet_races else None,
        "leak_free": bool(run.get("leak_free")),
        "races": bet_races,
        "bets": int(run.get("bets", 0) or 0),
        "max_losing_streak": run.get("max_losing_streak"),
        "max_drawdown_yen": int(run["max_drawdown"]) if run.get("max_drawdown") is not None else None,
        "is_baseline": bool(run.get("is_baseline", str(run["strategy"]).startswith("baseline"))),
    }


def load_backtest(models_dir: Path = MODELS_DIR, reports_dir: Path = REPORTS_DIR) -> dict[str, Any] | None:
    latest = Path(models_dir) / "latest"
    if not latest.exists():
        return None
    model_id = latest.read_text(encoding="utf-8").strip()
    bt_dir = Path(reports_dir) / "backtest" / model_id
    summaries = []
    for p in sorted(bt_dir.glob("*_test/summary.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        summaries.append(s)
    if not summaries and not (bt_dir / "performance_test.json").exists():
        return None
    order = {n: i for i, n in enumerate(STRATEGY_ORDER)}
    summaries.sort(key=lambda s: (order.get(s.get("strategy"), len(order)), s.get("strategy", "")))
    strategies = [backtest_run_to_public(run) for s in summaries for run in s.get("runs", [])]

    metrics: dict[str, Any] = {}
    calibration: list[dict[str, Any]] = []
    period = None
    mpath = Path(reports_dir) / "eval" / model_id / "metrics.json"
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding="utf-8"))
        test = (m.get("results") or {}).get("test") or {}
        row = next((v for k, v in test.items() if k.startswith("LightGBM") and model_id in k), None)
        if row:
            metrics = {k: _r(row[k], 4) for k in METRIC_KEYS if row.get(k) is not None}
            calibration = _calibration_from_eval(row.get("calibration") or [])
        per = m.get("periods") or {}
        if per.get("test_from") and per.get("test_to"):
            period = {"from": per["test_from"], "to": per["test_to"]}
    if period is None:
        meta_path = Path(models_dir) / model_id.split("_", 1)[0] / model_id / "meta.json"
        per = json.loads(meta_path.read_text(encoding="utf-8")).get("periods", {}) if meta_path.exists() else {}
        period = {"from": per.get("test_from", ""), "to": per.get("test_to", "")}
    perf_path = bt_dir / "performance_test.json"
    if perf_path.exists():
        # M-11 の集計済み(較正・戦略比較(基準2つを含む)・月別)をそのまま使う。数値は丸めるだけ
        pt = json.loads(perf_path.read_text(encoding="utf-8"))
        return {"model_id": model_id, "period": period, "metrics": metrics,
                "strategies": [_m11_strategy(x) for x in pt.get("strategies", [])],
                "calibration": [_round_cal(x) for x in pt.get("calibration", [])],
                "by_month": [{"month": m["month"], "races": int(m["races"]), "roi": _r(m.get("roi")),
                              "roi_ci95": [_r(v) for v in m["roi_ci95"]] if m.get("roi_ci95") else None}
                             for m in pt.get("by_month", [])],
                "sample_note": pt.get("sample_note")}
    by_month = []
    for s in summaries:
        for run in s.get("runs", []):
            if run["strategy"] == "top_k" and run["bet_type"] == "trifecta" and run.get("params", {}).get("k") == 5:
                by_month = [{"month": m["month"], "races": int(m.get("races", 0)), "roi": _r(m.get("roi")),
                             "roi_ci95": [_r(x) for x in m["roi_ci95"]] if m.get("roi_ci95") else None}
                            for m in run.get("by_month", [])]
    return {"model_id": model_id, "period": period, "metrics": metrics, "strategies": strategies,
            "calibration": calibration, "by_month": by_month}


def _m11_strategy(x: dict[str, Any]) -> dict[str, Any]:
    from src.model.summary import DEFAULT_STRATEGIES

    spec = next((d for d in DEFAULT_STRATEGIES if d["name"] == x["name"]), {})
    bet_type = spec.get("bet_type") or x.get("bet_type")
    if bet_type is None:   # 一覧に無い戦略(ev_final など)は表示名の先頭から決める
        lab = x.get("short_label") or ""
        bet_type = next((k for k, v in (("trifecta", "3連単"), ("exacta", "2連単"), ("win", "単勝"))
                         if lab.startswith(v)), "trifecta")
    params = {"bet_type": bet_type, **(x.get("params") or {})}
    ci = x.get("roi_ci95")
    return {
        "name": x["name"], "short_label": _short(x, params), "params": params,
        "roi": _r(x.get("roi")), "roi_ci95": [_r(v) for v in ci] if ci else None,
        "hit_rate": _r(x.get("hit_rate")), "leak_free": bool(x.get("leak_free", True)),
        "races": int(x.get("bet_races", x.get("races", 0))), "bets": int(x.get("bets", 0)),
        "max_losing_streak": x.get("max_losing_streak"), "max_drawdown_yen": x.get("max_drawdown_yen"),
        "is_baseline": bool(x.get("is_baseline")),
    }


def _short(x: dict[str, Any], params: dict[str, Any]) -> str:
    """M の short_label が 14 文字(04 4章)を超えるときは、export の規則で作り直す。"""
    from src.export.build import SHORT_LABEL_MAX, strategy_short_label

    lab = x.get("short_label")
    if lab and len(lab) <= SHORT_LABEL_MAX:
        return lab
    return strategy_short_label(x["name"], params)


def _round_cal(x: dict[str, Any]) -> dict[str, Any]:
    return {"bin": x["bin"], "n": int(x["n"]), "pred_mean": _r(x["pred_mean"]), "actual": _r(x["actual"]),
            "ci95": [_r(v) for v in x["ci95"]] if x.get("ci95") else None}


def _calibration_from_eval(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """reports/eval の較正(bin, mean_pred, actual, n)を 04 8.2 の形にする。区間は Wilson。"""
    from src.export.perf_stats import wilson_ci

    out = []
    for r in rows:
        n = int(r.get("n", 0))
        if n <= 0:
            continue
        actual = float(r["actual"])
        b = r.get("bin")
        label = b if isinstance(b, str) else f"{int(b) * 10}-{int(b) * 10 + 10}"
        ci = r.get("ci95") or wilson_ci(round(actual * n), n)
        out.append({"bin": label, "n": n, "pred_mean": _r(r.get("mean_pred", r.get("pred_mean"))),
                    "actual": _r(actual), "ci95": [_r(ci[0]), _r(ci[1])]})
    return out


def _export_one(out_dir: Path, generated_at: str, backtest: dict[str, Any] | None) -> tuple[Path, Path]:
    days = load_days(out_dir)
    index = build_index(days, generated_at)
    errors = validate_index_against_days(index, {d["date"]: d for d in days})
    if errors:
        raise ValueError("index と days が一致しません: " + "; ".join(errors))
    performance = build_performance(days, generated_at, backtest)
    perf_path = Path(out_dir) / "performance.json"
    index_path = Path(out_dir) / "index.json"
    write_json(perf_path, performance, kind="performance")
    write_json(index_path, index, kind="index")   # index は最後
    return index_path, perf_path


def export_index(out_dir: Path = DOCS_DATA_DIR, generated_at: str | None = None,
                 models_dir: Path = MODELS_DIR, reports_dir: Path = REPORTS_DIR,
                 local_dir: Path | None | str = "auto") -> tuple[Path, Path]:
    """公開JSONの index.json・performance.json を作り直す(ローカルJSONの days があれば、そちらも)。

    戻り値は公開JSONの (index, performance) のパス。
    """
    from src.export.export_day import local_dir_for

    generated_at = generated_at or to_jst_iso(datetime.now(JST))
    backtest = load_backtest(models_dir, reports_dir)
    ldir = local_dir_for(out_dir) if local_dir == "auto" else local_dir
    if ldir is not None and any((Path(ldir) / "days").glob("*.json")):
        _export_one(Path(ldir), generated_at, backtest)
    return _export_one(Path(out_dir), generated_at, backtest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="docs/data の index.json と performance.json を作り直す")
    ap.add_argument("--out", type=Path, default=DOCS_DATA_DIR)
    ap.add_argument("--generated-at", default=None, help="テスト用。既定は現在時刻(JST)")
    ap.add_argument("--no-local", action="store_true", help="ローカルJSON(docs/data-local/)を更新しない")
    args = ap.parse_args(argv)
    for p in export_index(args.out, args.generated_at, local_dir=None if args.no_local else "auto"):
        print(f"書き出し: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
