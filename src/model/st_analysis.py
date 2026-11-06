"""展示ST(preview_v3)の効果の分析。reports/exhibition_st_v3.md と .json を出す。

CLI:
    python -m src.model.st_analysis --models preview_20261106_48b0 preview_v3_XXXX \
        --tenkai-reports reports/tenkai_preview_from2016-10-30.json reports/tenkai_preview_v3_from2016-10-30.json

(1) モデルの比較(test 期間。直前情報が全艇そろったレースだけで比べる。どのモデルも同じレース):
    win_logloss、2連単 logloss、3連単 logloss(meta の γ)、4〜6コース(展示の進入)の艇の2連対の予測と実際、
    そのうち「外側で展示ST1位」の艇・pv_outer_both_top の艇の2連対の予測と実際、
    回収率(単勝1点・2連単上位3点・3連単上位5点、95%区間)、新しい列の重要度。
(2) 払戻(= 確定オッズ × 100)による参考値: 2026年1〜10月と、10年分。
    「外側で展示ST1位の艇を2着以内に含む2連単(10点)・3連単(40点)を全部買う」と、全組合せを買う場合・
    外側で展示ST3位の艇で同じことをした場合を比べる。**確定オッズは締切後にしかわからないので後知恵の参考値**。
"""

from __future__ import annotations

import argparse
import json
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.analytics_rules import bootstrap_roi_interval
from src.model import combinations as cb
from src.model.config import REPORTS_DIR, Periods
from src.model.data import clean_race_mask, features_path, filter_period, load_features, race_matrix

NEW_V3 = ["r_pv_st_rank", "pv_st_flying", "r_pv_st_rank_group", "r_pv_st_diff_best", "r_pv_st_diff_inner",
          "r_pv_exhibition_rank_group", "pv_outer_both_top"]
_EPS = 1e-15


def _course(df: pd.DataFrame) -> pd.Series:
    """展示の進入コース(無ければ枠番)。"""
    c = pd.to_numeric(df.get("pv_course_number"), errors="coerce")
    return c.fillna(df["entry_number"].astype(float))


def _ll(P: np.ndarray, idx: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(idx)), idx], _EPS, 1))))


def model_block(mid: str, races: set, v3: pd.DataFrame, payouts: pd.DataFrame) -> dict:
    from src.model.lgbm import gammas_from_meta, load_model
    from src.model.report import predict_p
    from src.model.summary import compare_strategies

    booster, meta = load_model(mid)
    feats = load_features(features_path(meta["variant"]))
    periods = Periods(**meta["periods"])
    part = filter_period(feats, *periods.range_of("test"))
    part = part[part["race_id"].isin(races)].sort_values(["race_id", "entry_number"])
    p = part[["race_id", "entry_number", "finish_position", "is_valid_result", "is_absent", "stadium_number"]].assign(
        p_win=predict_p(part, booster, meta))
    g2, g3 = gammas_from_meta(meta)
    rid, P = race_matrix(p, "p_win")
    P = P / P.sum(axis=1, keepdims=True)
    _, F = race_matrix(p, "finish_position")
    w = (F == 1).argmax(axis=1)
    ok2 = ((F == 1).sum(1) == 1) & ((F == 2).sum(1) == 1)
    ok3 = ok2 & ((F == 3).sum(1) == 1)
    ex_lab = {lab: i for i, lab in enumerate(cb.combination_labels("exacta"))}
    tri_lab = {lab: i for i, lab in enumerate(cb.combination_labels("trifecta"))}
    pos = lambda row, k: int(np.argmax(row == k)) + 1  # noqa: E731
    ex_idx = np.array([ex_lab[f"{pos(r, 1)}-{pos(r, 2)}"] for r in F[ok2]])
    tri_idx = np.array([tri_lab[f"{pos(r, 1)}-{pos(r, 2)}-{pos(r, 3)}"] for r in F[ok3]])
    EX = cb.combination_prob_matrix(P[ok2], "exacta", g2, g3)
    TR = cb.combination_prob_matrix(P[ok3], "trifecta", g2, g3)
    fp = cb.finish_prob_matrix(P, g2, g3)

    # 艇ごとの2連対の予測と実際(外側 4〜6 コース、外側で展示ST1位、pv_outer_both_top)
    boat = p[["race_id", "entry_number"]].copy()
    r_i = pd.Index(rid).get_indexer(boat["race_id"])
    e_i = boat["entry_number"].to_numpy().astype(int) - 1
    boat["p_top2"] = fp["p_top2"][r_i, e_i]
    boat["top2"] = (F[r_i, e_i] <= 2).astype(float)
    boat = boat.merge(v3, on=["race_id", "entry_number"], how="left")
    groups = {
        "外側(4〜6コース)": boat["course"] >= 4,
        "外側で展示ST1位": (boat["course"] >= 4) & (boat["r_pv_st_rank_group"] == 1),
        "外側で展示ST3位": (boat["course"] >= 4) & (boat["r_pv_st_rank_group"] == 3),
        "外側で展示ST・展示タイムともに1位": boat["pv_outer_both_top"] == 1,
    }
    top2 = {k: {"n": int(m.sum()), "pred": float(boat.loc[m, "p_top2"].mean()), "actual": float(boat.loc[m, "top2"].mean())}
            for k, m in groups.items()}
    strategies = [
        {"name": "win1", "short_label": "単勝 本命1点", "bet_type": "win", "rule": "top_k", "params": {"k": 1}},
        {"name": "ex3", "short_label": "2連単 上位3点", "bet_type": "exacta", "rule": "top_k", "params": {"k": 3}},
        {"name": "tri5", "short_label": "3連単 上位5点", "bet_type": "trifecta", "rule": "top_k", "params": {"k": 5}},
    ]
    roi = [{k: r[k] for k in ("short_label", "bets", "roi", "roi_ci95", "hit_rate")}
           for r in compare_strategies(p, payouts, None, strategies, g2, g3, n_boot=1000) if r["name"] in {"win1", "ex3", "tri5"}]
    imp = meta.get("feature_importance_gain", {})
    tot = sum(imp.values()) or 1.0
    ranks = {f: i + 1 for i, f in enumerate(imp)}
    return {
        "model_id": mid, "races": int(len(rid)), "calibration": meta.get("calibration", {}).get("method"),
        "harville_gamma": meta.get("harville_gamma"),
        "win_logloss": _ll(P, w), "exacta_logloss": _ll(EX, ex_idx), "trifecta_logloss": _ll(TR, tri_idx),
        "top2": top2, "roi": roi,
        "new_feature_importance": {f: {"share": imp[f] / tot, "rank": ranks[f]} for f in NEW_V3 if f in imp},
        "new_features_total_share": sum(imp.get(f, 0) for f in NEW_V3) / tot,
    }


def payout_reference(v3: pd.DataFrame, d_from: str, d_to: str) -> dict:
    """払戻での参考値。外側で展示ST1位(または両方1位)の艇を2着以内に含む組合せを全部買った場合。"""
    from src.features.build_interim import read_interim

    pay = read_interim("payouts", pd.Timestamp(d_from).date(), pd.Timestamp(d_to).date())
    pay = pay[pay["bet_type"].isin(["exacta", "trifecta"]) & pay["combination"].notna()]
    pay["race_id"] = pay["race_id"].astype(str)
    d = v3[(v3["date"] >= pd.Timestamp(d_from)) & (v3["date"] <= pd.Timestamp(d_to))]
    d = d[d["is_valid_result"] & ~d["is_absent"].groupby(d["race_id"]).transform("any")]
    out = {}
    targets = {
        "外側で展示ST1位": d[(d["course"] >= 4) & (d["r_pv_st_rank_group"] == 1)],
        "外側で展示ST・展示タイムともに1位": d[d["pv_outer_both_top"] == 1],
        "(比較)外側で展示ST3位": d[(d["course"] >= 4) & (d["r_pv_st_rank_group"] == 3)],
    }
    # 外側で1位が同着(2艇以上)のレースは除く
    for name, t in targets.items():
        t = t[t.groupby("race_id")["entry_number"].transform("size") == 1]
        x = t.set_index("race_id")["entry_number"].astype(int)
        res = {"races": int(len(x))}
        for bt, n_combo in (("exacta", 10), ("trifecta", 40)):
            pp = pay[(pay["bet_type"] == bt) & pay["race_id"].isin(x.index)].copy()
            parts = pp["combination"].str.split("-")
            pp["x"] = pp["race_id"].map(x).astype(str)
            pp["hit"] = [xx in c[:2] for xx, c in zip(pp["x"], parts)]
            ret = pp[pp["hit"]].groupby("race_id")["amount"].sum().reindex(x.index, fill_value=0).astype(float)
            stake = np.full(len(x), 100.0 * n_combo)
            ci = bootstrap_roi_interval(stake.tolist(), ret.tolist())
            res[bt] = {"points_per_race": n_combo, "roi": float(ret.sum() / stake.sum()) if len(x) else None,
                       "roi_ci95": list(ci) if ci else None, "x_in_top2_rate": float((ret > 0).mean()) if len(x) else None}
        out[name] = res
    # 全組合せを買う場合(控除率の分だけ下がる)
    races = d["race_id"].unique()
    allr = {"races": int(len(races))}
    for bt, n_combo in (("exacta", 30), ("trifecta", 120)):
        ret = pay[(pay["bet_type"] == bt) & pay["race_id"].isin(races)]["amount"].sum()
        allr[bt] = {"points_per_race": n_combo, "roi": float(ret / (100.0 * n_combo * len(races))) if len(races) else None}
    out["(比較)全組合せ"] = allr
    return out


def tenkai_block(path: str) -> dict:
    r = json.loads(Path(path).read_text(encoding="utf-8"))
    t = r["periods"]["test"]["lgbm"]

    def ece(bins):
        n = sum(b["n"] for b in bins)
        return sum(b["n"] * abs(b["pred_mean"] - b["actual"]) for b in bins) / n if n else None

    return {"model_id": r["model_id"], "logloss_test": t["logloss"], "logloss_test_B1": r["periods"]["test"]["B1"]["logloss"],
            "logloss_valid": r["periods"]["valid"]["lgbm"]["logloss"],
            "ece_makuri": ece(t["calibration"]["makuri"]), "ece_makurisashi": ece(t["calibration"]["makurisashi"]),
            "class_mean": {k: t["class_mean"][k] for k in ("makuri", "makurisashi")}}


def main() -> None:
    parser = argparse.ArgumentParser(description="展示ST(v3)の分析")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--tenkai-reports", nargs="*", default=[])
    args = parser.parse_args()

    from src.model.backtest import load_interim_table

    cols = ["race_id", "date", "entry_number", "pv_course_number", "pv_exhibition_time", "r_pv_st_rank_group",
            "pv_outer_both_top", "is_valid_result", "is_absent"]
    v3 = load_features(features_path("preview_v3"))[cols]
    v3["course"] = _course(v3)
    v3["race_id"] = v3["race_id"].astype(str)
    # test 期間で、直前情報が全艇そろったきれいなレース(全モデル共通)
    first = Periods(**json.loads((Path("models") / "preview" / "preview_20261106_48b0" / "meta.json").read_text())["periods"]) \
        if (Path("models") / "preview" / "preview_20261106_48b0").exists() else None
    tf, tt = (first.test_from, first.test_to) if first else ("2026-10-01", "2026-10-31")
    t = v3[(v3["date"] >= tf) & (v3["date"] <= tt)]
    complete = t.groupby("race_id")["pv_exhibition_time"].apply(lambda s: s.notna().all() and len(s) == 6)
    races = set(complete[complete].index)
    payouts = load_interim_table("payouts", pd.Series(sorted(races)))
    out = {"test": {"from": tf, "to": tt, "races_with_full_preview": len(races)},
           "models": [model_block(m, races, v3[["race_id", "entry_number", "course", "r_pv_st_rank_group",
                                                 "pv_outer_both_top"]], payouts) for m in args.models],
           "tenkai": [tenkai_block(p) for p in args.tenkai_reports],
           "payout_reference_2026": payout_reference(v3, "2026-01-01", "2026-10-31"),
           "payout_reference_10y": payout_reference(v3, "2016-08-01", "2026-10-31")}
    (REPORTS_DIR / "exhibition_st_v3.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS_DIR / "exhibition_st_v3.md").write_text(render(out), encoding="utf-8")
    print(f"→ {REPORTS_DIR / 'exhibition_st_v3.md'}")


def render(o: dict) -> str:
    L = ["# 展示ST(preview_v3)の分析", "", "`python -m src.model.st_analysis` が自動で生成(手で編集しない)。", "",
         f"test: {o['test']['from']}〜{o['test']['to']}、直前情報が全艇そろったレース {o['test']['races_with_full_preview']}R(全モデル共通)。"
         "較正と γ は valid(9月)だけで選んだ。", "",
         "| model | win_logloss | 2連単 logloss | 3連単 logloss | 新しい列の重要度(合計) |", "|---|---|---|---|---|"]
    for m in o["models"]:
        L.append(f"| {m['model_id']} | {m['win_logloss']:.4f} | {m['exacta_logloss']:.4f} | {m['trifecta_logloss']:.4f} | "
                 f"{m['new_features_total_share']:.1%} |")
    L += ["", "### 2連対(2着以内)の予測と実際(艇単位)", "", "| model | 区分 | 件数 | 予測 | 実際 |", "|---|---|---|---|---|"]
    for m in o["models"]:
        for k, v in m["top2"].items():
            L.append(f"| {m['model_id']} | {k} | {v['n']} | {v['pred']:.3f} | {v['actual']:.3f} |")
    L += ["", "### 回収率(1点100円、95%区間)", "", "| model | 戦略 | 点数 | 回収率 | 区間 | 的中率 |", "|---|---|---|---|---|---|"]
    for m in o["models"]:
        for r in m["roi"]:
            ci = r["roi_ci95"]
            L.append(f"| {m['model_id']} | {r['short_label']} | {r['bets']} | {r['roi']:.3f} | [{ci[0]:.3f}, {ci[1]:.3f}] | {r['hit_rate']:.3f} |")
    for m in o["models"]:
        if m["new_feature_importance"]:
            L += ["", f"新しい列の重要度({m['model_id']}): " + "、".join(
                f"{f} {v['share']:.1%}(順位 {v['rank']})" for f, v in m["new_feature_importance"].items())]
    if o["tenkai"]:
        L += ["", "### 展開予想(test)", "", "| model | logloss | 基準 B1 | まくり ECE | まくり差し ECE | まくり 予測/実際 | まくり差し 予測/実際 |",
              "|---|---|---|---|---|---|---|"]
        for t in o["tenkai"]:
            cm = t["class_mean"]
            L.append(f"| {t['model_id']} | {t['logloss_test']:.4f} | {t['logloss_test_B1']:.4f} | {t['ece_makuri']:.4f} | "
                     f"{t['ece_makurisashi']:.4f} | {cm['makuri']['pred']:.3f}/{cm['makuri']['actual']:.3f} | "
                     f"{cm['makurisashi']['pred']:.3f}/{cm['makurisashi']['actual']:.3f} |")
    for key, title in (("payout_reference_2026", "2026年1〜10月"), ("payout_reference_10y", "10年分(2016-08〜2026-10)")):
        L += ["", f"### 払戻での参考値({title}。**確定オッズは締切後にしかわからないので後知恵の参考値**)", "",
              "その艇を2着以内に含む組合せを全部買った場合(2連単 10点・3連単 40点)。", "",
              "| 対象 | レース | 2連単 回収率 [区間] | 3連単 回収率 [区間] | その艇が2着以内の率 |", "|---|---|---|---|---|"]
        for name, r in o[key].items():
            ex, tr = r["exacta"], r["trifecta"]
            fmt = lambda x: "-" if not x or x.get("roi") is None else (f"{x['roi']:.3f}" + (f" [{x['roi_ci95'][0]:.3f}, {x['roi_ci95'][1]:.3f}]" if x.get("roi_ci95") else ""))  # noqa: E731
            L.append(f"| {name} | {r['races']} | {fmt(ex)} | {fmt(tr)} | {ex.get('x_in_top2_rate', '-') if isinstance(ex.get('x_in_top2_rate'), str) else ('-' if ex.get('x_in_top2_rate') is None else f'{ex['x_in_top2_rate']:.3f}')} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
