"""予想(data/predictions)と interim から days/YYYYMMDD.json を作る(E-2)。

    python -m src.export.export_day --date 2026-11-02 [--out docs/data] [--local-out docs/data-local]
    python -m src.export.export_day --date 2026-11-02 --odds-only     # 締切前オッズだけ反映する速い更新(E-8)

出力は2つ(spec 07 3.3):
- 公開JSON docs/data/days/YYYYMMDD.json: オッズ・期待値の数値(odds・ev)を含まない。ev_rank と取得時刻だけ
- ローカルJSON docs/data-local/days/YYYYMMDD.json(git 管理外): 同じスキーマに odds・ev の数値を足したもの
  (config.PUBLISH_ODDS_NUMERIC=True のときだけ、公開JSONにも数値を入れる)

入力(すべて git 管理外のローカルファイル。無いものは無いなりに出力する):
- data/predictions/YYYYMMDD_pre.parquet / YYYYMMDD_preview.parquet(spec 03 7章。1行=1艇)
    列: race_id, entry_number, model_id, variant, predicted_at, score, p_win, p_top2, p_top3
    同じレースに preview があれば preview を使う。
- data/interim/{races,entries,payouts,odds}/YYYY-MM.parquet(spec 02 1章)
- models/{variant}/{model_id}/meta.json(train_until の表示用。無ければ null)
- data/raw/official_odds/YYYY/YYYYMMDD/(締切前オッズの解析JSON。official_odds_input.py。無ければ odds_snapshot は null)

出すのは spec 04 の項目だけ。年齢・体重・チルト・特徴量・オッズ表全体などは読み込んでも出さない
(書き出す前に schema.assert_valid が check_forbidden も通す)。

冪等: 同じ入力なら generated_at 以外は同じ出力になる(並び順・丸めを固定している)。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import codes
from src.export import config
from src.export.build import (
    DEFAULT_STRATEGY, JST, SCHEMA_VERSION, apply_odds, build_prediction, build_result, day_status, iter_races,
    strip_numeric_odds, summarize, to_jst_iso, with_short_label,
)
from src.export.io_util import read_json, write_json
from src.export.official_odds_input import OFFICIAL_ODDS_DIR, load_day_snapshots, load_stopped

ROOT = Path(__file__).resolve().parents[2]
INTERIM_DIR = ROOT / "data" / "interim"
PREDICTIONS_DIR = ROOT / "data" / "predictions"
MODELS_DIR = ROOT / "models"
DOCS_DATA_DIR = config.DOCS_DATA_DIR
LOCAL_DATA_DIR = config.LOCAL_DATA_DIR

VARIANT_PRIORITY = ("preview", "pre")   # 同じレースに両方あれば先にある方を使う


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------

@dataclass
class DayInputs:
    """1日分の入力(pandas の表)。テストでは合成データを直接入れる。"""

    races: pd.DataFrame
    entries: pd.DataFrame
    predictions: pd.DataFrame                       # pre と preview を縦に積んだもの
    payouts: pd.DataFrame = field(default_factory=pd.DataFrame)
    odds: pd.DataFrame = field(default_factory=pd.DataFrame)
    model_meta: dict[str, dict[str, Any]] = field(default_factory=dict)   # model_id → meta.json
    odds_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)  # race_id → 締切前オッズ(07 2.6)
    odds_stopped: dict[str, Any] | None = None   # その日の取得を止めた記録(_stopped.json)。止めていなければ None
    # 根拠トップ3(M-10 の YYYYMMDD_{variant}_contrib.parquet を縦に積んだもの。列: race_id, entry_number,
    # rank, feature, contribution と、どの版のものかを表す variant)
    contribs: pd.DataFrame = field(default_factory=pd.DataFrame)


def _read_month_table(base: Path, table: str, d: Date) -> pd.DataFrame:
    """interim の1日分。特徴量担当の read_interim(型をそろえて読む)を使う。"""
    from src.features.build_interim import read_interim

    df = read_interim(table, d, d, interim_dir=base)
    if df.empty:
        return pd.DataFrame()
    ymd = d.strftime("%Y%m%d")
    return df[df["race_id"].astype(str).str.startswith(ymd)].reset_index(drop=True)


def load_inputs(d: Date, interim_dir: Path = INTERIM_DIR, predictions_dir: Path = PREDICTIONS_DIR,
                models_dir: Path = MODELS_DIR, official_odds_dir: Path = OFFICIAL_ODDS_DIR) -> DayInputs:
    # 確定版(08 5.1)をそのまま読む。p_win は parquet の値を使い、モデルから計算し直さない。
    # 直前版のファイルには、直前情報が欠けて前日モデルで予想した行(variant=pre)が混ざる。
    # 前日版の確定ファイルがあれば、その行は前日版のファイルの方を使う(同じレースの pre が2組にならないように)。
    preds, contribs = [], []
    files = {v: predictions_dir / f"{d:%Y%m%d}_{v}.parquet" for v in VARIANT_PRIORITY}
    for variant, path in files.items():
        if path.exists():
            df = pd.read_parquet(path)
            if variant == "preview" and files["pre"].exists():
                df = df[df["variant"] == "preview"]
            preds.append(df)
        cpath = predictions_dir / f"{d:%Y%m%d}_{variant}_contrib.parquet"
        if cpath.exists():
            c = pd.read_parquet(cpath)
            if "variant" not in c.columns:
                c["variant"] = variant
            contribs.append(c)
    predictions = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    meta: dict[str, dict[str, Any]] = {}
    if not predictions.empty:
        for model_id, variant in predictions[["model_id", "variant"]].drop_duplicates().itertuples(index=False):
            p = models_dir / str(variant) / str(model_id) / "meta.json"
            if p.exists():
                meta[str(model_id)] = json.loads(p.read_text(encoding="utf-8"))
    return DayInputs(
        races=_read_month_table(interim_dir, "races", d),
        entries=_read_month_table(interim_dir, "entries", d),
        predictions=predictions,
        contribs=pd.concat(contribs, ignore_index=True) if contribs else pd.DataFrame(),
        payouts=_read_month_table(interim_dir, "payouts", d),
        odds=_read_month_table(interim_dir, "odds", d),
        model_meta=meta,
        odds_snapshots=load_day_snapshots(d, official_odds_dir),
        odds_stopped=load_stopped(d, official_odds_dir),
    )


# ---------------------------------------------------------------------------
# 値の整形
# ---------------------------------------------------------------------------

def _val(v: Any) -> Any:
    """pandas の欠損(NA / NaN / NaT)を None にし、numpy の数値を Python の値にする。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        return v.item()
    return v


def _int(v: Any) -> int | None:
    v = _val(v)
    return None if v is None else int(v)


def _round(v: Any, nd: int) -> float | None:
    v = _val(v)
    if v is None:
        return None
    f = float(v)
    return round(f, nd) if math.isfinite(f) else None


def _str(v: Any) -> str | None:
    v = _val(v)
    return None if v is None else str(v)


def _datetime_iso(v: Any) -> str | None:
    """closed_at / predicted_at を JST の ISO 8601 にする(文字列・datetime・Timestamp のどれでもよい)。"""
    v = _val(v)
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace(" ", "T"))
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if isinstance(v, (int, float)):
        return None
    return to_jst_iso(v)


def _model_train_until(meta: dict[str, Any] | None) -> str | None:
    """meta.json から学習データの最終日を取り出す。train_until が無ければ periods.train_to を使う。"""
    if not meta:
        return None
    if meta.get("train_until"):
        return str(meta["train_until"])[:10]
    periods = meta.get("periods") or {}
    return str(periods["train_to"])[:10] if periods.get("train_to") else None


def harville_gamma(meta: dict[str, Any] | None) -> tuple[float, float]:
    """meta.json の γ(spec 03 M-3a)。harville_gamma → gamma2/gamma3 → 1.0 の順に探す。"""
    if not meta:
        return 1.0, 1.0
    hg = meta.get("harville_gamma")
    if isinstance(hg, dict):
        return float(hg.get("gamma2", 1.0)), float(hg.get("gamma3", 1.0))
    if "harville_gamma" in meta and hg is None:
        return 1.0, 1.0
    return float(meta.get("gamma2") or 1.0), float(meta.get("gamma3") or 1.0)


# reasons の value に出してよい特徴量(出走表に載っている値。04 1章・08 5.2)と、その丸め桁
REASON_VALUE_COLUMNS = {
    "national_win_rate": 2, "local_win_rate": 2, "national_top_2_percent": 1, "national_top_3_percent": 1,
    "local_top_2_percent": 1, "local_top_3_percent": 1, "motor_top_2_percent": 1, "motor_top_3_percent": 1,
    "boat_top_2_percent": 1, "boat_top_3_percent": 1, "average_start_timing": 2, "flying_count": 0,
}


def feature_label(name: str) -> str:
    """特徴量の表示ラベル(M-10 の src/model/feature_labels.py)。無ければ名前のまま。"""
    try:
        from src.model import feature_labels as fl  # type: ignore
    except Exception:  # noqa: BLE001
        return name
    fn = getattr(fl, "label_for", None)  # M-10
    if fn:
        return str(fn(name))
    return str(getattr(fl, "FEATURE_LABELS", {}).get(name, name))


def reasons_for_race(contrib: pd.DataFrame | None, ent_map: dict[int, dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """1レースの寄与(上位3)から、各艇の reasons。寄与の数値は段階(strength)にして捨てる。"""
    from src.export.build import make_reasons

    out = []
    for en in range(1, 7):
        rows = []
        if contrib is not None:
            g = contrib[contrib["entry_number"].astype(int) == en].sort_values("rank")
            for r in g.to_dict("records"):
                feat = str(r["feature"])
                raw = _val(r.get("value"))
                # 1号艇の「内側の艇の強さ」(値0。内側に艇がいない)は表示しない(M の推奨)
                if feat == "r_inner_strength" and en == 1 and (raw is None or float(raw) == 0.0):
                    continue
                label = _str(r.get("label")) or feature_label(feat)
                value = None
                if "value_public" in r:
                    # M-10 の形: value_public=False(年齢・体重・チルト・コード値など)は出さない
                    if bool(_val(r.get("value_public"))) and raw is not None:
                        value = round(float(raw), 3)
                elif feat in REASON_VALUE_COLUMNS:
                    nd = REASON_VALUE_COLUMNS[feat]
                    v = _val(ent_map[en].get(feat))
                    value = (int(v) if nd == 0 else round(float(v), nd)) if v is not None else None
                rows.append((label, float(r["contribution"]), value))
        out.append(make_reasons(rows[:3]))
    return out


def preview_info(race: dict[str, Any], ents: pd.DataFrame) -> dict[str, Any] | None:
    """直前情報の表示用(04 8.1、F13)。展示タイムも気象も無ければ None。"""
    if ents.empty:
        return None
    boats = []
    any_value = False
    for e in ents.sort_values("entry_number").to_dict("records"):
        b = {"entry": int(e["entry_number"]), "exhibition_time": _round(e.get("pv_exhibition_time"), 2),
             "exhibition_course": _int(e.get("pv_course_number")), "exhibition_st": _round(e.get("pv_start_timing"), 2)}
        any_value |= any(v is not None for k, v in b.items() if k != "entry")
        boats.append(b)
    weather = _int(race.get("pv_weather_number"))
    wdir = _int(race.get("pv_wind_direction_number"))
    info = {
        "boats": boats,
        "weather": codes.WEATHER_NAMES.get(weather) if weather is not None else None,
        "wind_speed": _round(race.get("pv_wind_speed"), 1),
        "wind_class": codes.wind_class(wdir) if wdir is not None else None,
        "wave_height": _round(race.get("pv_wave_height"), 1),
    }
    if not any_value and all(info[k] is None for k in ("weather", "wind_speed", "wind_class", "wave_height")):
        return None
    return info


def boat_info(e: dict[str, Any], variant: str) -> dict[str, Any]:
    """entries の1行から、公開する選手情報だけを取り出す(spec 04 4章)。"""
    preview = variant == "preview"
    return {
        "entry": int(e["entry_number"]),
        "racer_name": _str(e.get("racer_name")),
        "racer_number": _int(e.get("racer_number")),
        "rank": codes.RANK_NAMES.get(_int(e.get("rank_number"))),
        "branch": codes.prefecture_short_name(_int(e.get("branch_number"))),
        "national_win_rate": _round(e.get("national_win_rate"), 2),
        "local_win_rate": _round(e.get("local_win_rate"), 2),
        "motor_top2": _round(e.get("motor_top_2_percent"), 1),
        "avg_st": _round(e.get("average_start_timing"), 2),
        "flying_count": _int(e.get("flying_count")),
        "exhibition_time": _round(e.get("pv_exhibition_time"), 2) if preview else None,
        "exhibition_course": _int(e.get("pv_course_number")) if preview else None,
    }


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def _choose_predictions(predictions: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """race_id → そのレースで使う予想(6行。preview を優先)。6艇そろわない予想は使わない。"""
    if predictions.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for race_id, g in predictions.groupby("race_id", sort=True):
        for variant in VARIANT_PRIORITY:
            gv = g[g["variant"] == variant].sort_values("entry_number")
            if len(gv) == 6 and list(gv["entry_number"].astype(int)) == [1, 2, 3, 4, 5, 6] \
                    and gv["p_win"].notna().all():
                out[str(race_id)] = gv
                break
    return out


def snapshot_for_race(race_id: str, closed_at: Any, snapshots: dict[str, dict[str, Any]],
                      stopped: dict[str, Any] | None) -> dict[str, Any] | None:
    """そのレースの締切前オッズ。無く、その日の取得を止めていて、止めた後に締め切るレースなら stopped の印。"""
    snap = snapshots.get(race_id)
    if snap is not None or stopped is None:
        return snap
    closed = _datetime_iso(closed_at)
    at = stopped.get("stopped_at")
    if closed is not None and at:
        try:
            if datetime.fromisoformat(closed) <= datetime.fromisoformat(at).astimezone(JST):
                return None
        except ValueError:
            pass
    return {"race_id": race_id, "fetched_at": None, "status": "stopped", "pages": [], "odds": {}}


def _race_result(race: dict[str, Any], ents: pd.DataFrame, payouts: pd.DataFrame) -> dict[str, Any] | None:
    """結果に使う値(着順・決まり手・欠場・払戻)。結果が無ければ None。"""
    fin = {}
    for e in ents.itertuples(index=False):
        pos = _int(getattr(e, "finish_position", None))
        if pos is not None:
            fin.setdefault(pos, []).append(int(e.entry_number))
    has_payout = not payouts.empty
    if not fin and not has_payout:
        return None
    # 同着や失格で決まらない位置は null
    order = [fin[p][0] if len(fin.get(p, [])) == 1 else None for p in (1, 2, 3)]
    absent = sorted(int(e.entry_number) for e in ents.itertuples(index=False)
                    if bool(_val(getattr(e, "is_absent", None)) or False))
    pay: dict[str, list[dict[str, Any]]] = {}
    for p in payouts.sort_values(["bet_type", "combination"], na_position="last").itertuples(index=False):
        pay.setdefault(str(p.bet_type), []).append(
            {"combination": _str(p.combination), "amount": _int(p.amount), "label": _str(getattr(p, "label", None))})
    tech = _int(race.get("technique_number"))
    return {"order": order, "technique": codes.TECHNIQUE_NAMES.get(tech) if tech is not None else None,
            "absent_entries": absent, "payouts": pay}


def _is_canceled(race: dict[str, Any]) -> bool:
    """中止の判定。interim の is_canceled(True / False / NA)を使う。

    NA は「確定前で結果待ち」なので中止にしない。is_canceled 列が無い古い interim だけ day_status で判定する。
    """
    if "is_canceled" in race:
        return _val(race["is_canceled"]) is True or _val(race["is_canceled"]) == 1
    return (_str(race.get("day_status")) or "normal") != "normal"


def build_day(d: Date, inputs: DayInputs, *, generated_at: str,
              strategy: dict[str, Any] | None = None) -> dict[str, Any]:
    """1日分の days JSON(dict)を作る。書き出しはしない。"""
    strategy = with_short_label(strategy or DEFAULT_STRATEGY)
    date_str = d.isoformat()
    races = inputs.races.copy()
    if races.empty:
        raise ValueError(f"{date_str} の races が interim にありません")
    races = races.sort_values(["stadium_number", "race_number"]).reset_index(drop=True)
    entries_by_race = {str(k): g.sort_values("entry_number") for k, g in inputs.entries.groupby("race_id")} \
        if not inputs.entries.empty else {}
    payouts_by_race = {str(k): g for k, g in inputs.payouts.groupby("race_id")} if not inputs.payouts.empty else {}
    odds_map: dict[str, dict[tuple[str, str], float]] = {}
    if not inputs.odds.empty:
        o = inputs.odds[inputs.odds["bet_type"].isin(["win", "exacta", "trifecta"]) & inputs.odds["odds"].notna()]
        for r in o.itertuples(index=False):
            odds_map.setdefault(str(r.race_id), {})[(str(r.bet_type), str(r.combination))] = float(r.odds)
    has_final_odds = bool(odds_map)
    chosen = _choose_predictions(inputs.predictions)
    contrib_by_race: dict[tuple[str, str], pd.DataFrame] = {}
    if not inputs.contribs.empty:
        for (rid, v), g in inputs.contribs.groupby(["race_id", "variant"]):
            contrib_by_race[(str(rid), str(v))] = g

    stadiums: list[dict[str, Any]] = []
    by_stadium: dict[int, dict[str, Any]] = {}
    for race in races.to_dict("records"):
        race_id = str(race["race_id"])
        sn, rn = int(race["stadium_number"]), int(race["race_number"])
        st = by_stadium.get(sn)
        if st is None:
            grade = _int(race.get("grade_number"))
            st = {
                "stadium_number": sn,
                "stadium_name": codes.STADIUM_NAMES.get(sn, str(sn)),
                "title": _str(race.get("title")),
                "grade": codes.GRADE_NAMES.get(grade) if grade is not None else None,
                "day_number": _int(race.get("day_number")),
                "races": [],
            }
            by_stadium[sn] = st
            stadiums.append(st)
        ents = entries_by_race.get(race_id, pd.DataFrame())
        pred_rows = chosen.get(race_id)
        prediction = None
        if pred_rows is not None and len(ents) == 6:
            variant = str(pred_rows["variant"].iloc[0])
            ent_map = {int(e["entry_number"]): e for e in ents.to_dict("records")}
            boats = [boat_info(ent_map[i], variant) for i in range(1, 7)]
            p_win = [float(x) for x in pred_rows["p_win"]]
            total = sum(p_win)
            p_win = [x / total for x in p_win]   # 念のため合計1に揃える(predict 側で1e-6以内の保証あり)
            model_id = str(pred_rows["model_id"].iloc[0])
            g2, g3 = harville_gamma(inputs.model_meta.get(model_id))
            predicted_at = _datetime_iso(pred_rows["predicted_at"].iloc[0])
            locked_at = _datetime_iso(pred_rows["locked_at"].iloc[0]) if "locked_at" in pred_rows else None
            prediction = build_prediction(
                boats, p_win, variant=variant, predicted_at=predicted_at, strategy=strategy, gamma2=g2, gamma3=g3,
                model_id=model_id, locked_at=locked_at or predicted_at,
                reasons=reasons_for_race(contrib_by_race.get((race_id, variant)), ent_map)
                if contrib_by_race else None)
            apply_odds(prediction, snapshot_for_race(race_id, race.get("closed_at"), inputs.odds_snapshots,
                                                     inputs.odds_stopped))
        canceled = _is_canceled(race)
        result = None
        if not canceled:
            raw = _race_result(race, ents, payouts_by_race.get(race_id, pd.DataFrame()))
            if raw is not None:
                rec = prediction["recommended"] if prediction else None
                result = build_result(order=raw["order"], technique=raw["technique"],
                                      absent_entries=raw["absent_entries"], payouts=raw["payouts"],
                                      recommended=rec,
                                      final_odds=odds_map.get(race_id, {}) if has_final_odds else None)
        if canceled:
            state = "canceled"
        elif prediction is None:
            state = "no_prediction"
        elif result is not None:
            state = "finished"
        else:
            state = "preview" if prediction["variant"] == "preview" else "scheduled"
        st["races"].append({
            "race_id": race_id,
            "race_number": rn,
            "closed_at": _datetime_iso(race.get("closed_at")),
            "subtitle": _str(race.get("subtitle")),
            "state": state,
            "prediction": prediction,
            "preview_info": preview_info(race, ents),
            "result": result,
        })

    model = _day_model(inputs)
    day = {
        "schema_version": SCHEMA_VERSION,
        "date": date_str,
        "generated_at": generated_at,
        "status": "predicted",
        "model": model,
        "strategy": strategy,
        "stadiums": stadiums,
        "day_summary": None,
    }
    day["day_summary"] = summarize(iter_races(day))
    day["status"] = day_status(day, has_final_odds=has_final_odds)
    return day


def _day_model(inputs: DayInputs) -> dict[str, Any] | None:
    """その日の代表のモデル(pre を優先。無ければ preview)。予想が無ければ None。"""
    p = inputs.predictions
    if p.empty:
        return None
    for variant in ("pre", "preview"):
        g = p[p["variant"] == variant]
        if not g.empty:
            model_id = str(sorted(g["model_id"].astype(str).unique())[-1])
            return {"model_id": model_id, "variant": variant,
                    "train_until": _model_train_until(inputs.model_meta.get(model_id))}
    return None


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------

def local_dir_for(out_dir: Path) -> Path:
    """公開JSONの出力先に対応するローカルJSONの出力先(docs/data → docs/data-local)。"""
    out_dir = Path(out_dir)
    if out_dir.resolve() == DOCS_DATA_DIR.resolve():
        return LOCAL_DATA_DIR
    return out_dir.parent / f"{out_dir.name}-local"


def write_day(day_local: dict[str, Any], out_dir: Path, local_dir: Path | None) -> Path:
    """ローカルJSONの形の day を、ローカル(数値あり)と公開(数値なし)の2つに書き出す。公開の方のパスを返す。

    ローカルを先に書く(公開JSONは index と同じく後で更新する)。
    """
    name = f"days/{day_local['date'].replace('-', '')}.json"
    if local_dir is not None:
        write_json(Path(local_dir) / name, day_local, kind="day", local=True)
    if config.PUBLISH_ODDS_NUMERIC:
        public, public_local = day_local, True
    else:
        public, public_local = strip_numeric_odds(day_local), False
    path = Path(out_dir) / name
    write_json(path, public, kind="day", local=public_local)
    return path


def export_day(d: Date, out_dir: Path = DOCS_DATA_DIR, inputs: DayInputs | None = None,
               generated_at: str | None = None, strategy: dict[str, Any] | None = None,
               local_dir: Path | None | str = "auto") -> Path:
    """days/YYYYMMDD.json(公開とローカル)を検証してから書き出す。index.json は更新しない(export_index の役目)。

    local_dir: "auto" なら out_dir から決める(local_dir_for)。None ならローカルJSONを書かない。
    """
    inputs = inputs if inputs is not None else load_inputs(d)
    generated_at = generated_at or to_jst_iso(datetime.now(JST))
    day = build_day(d, inputs, generated_at=generated_at, strategy=strategy)
    ldir = local_dir_for(out_dir) if local_dir == "auto" else local_dir
    return write_day(day, out_dir, ldir)


def export_odds_only(d: Date, out_dir: Path = DOCS_DATA_DIR, local_dir: Path | None | str = "auto",
                     snapshots: dict[str, dict[str, Any]] | None = None,
                     generated_at: str | None = None) -> Path:
    """締切前オッズだけを反映する速い更新(--odds-only)。

    前回のローカルJSONを読み、各レースの odds_snapshot と ev_rank・odds・ev だけを入れ直す
    (予想・結果・集計はそのまま。interim や予想の parquet は読まない)。
    ev は picks の prob(小数4桁)× オッズ(小数1桁)で計算するので、通常の export_day と同じ値になる。
    ローカルJSONが無ければ通常の export_day を行う。
    """
    ldir = local_dir_for(out_dir) if local_dir == "auto" else local_dir
    path = Path(ldir) / "days" / f"{d:%Y%m%d}.json" if ldir is not None else None
    if path is None or not path.exists():
        return export_day(d, out_dir, generated_at=generated_at, local_dir=ldir)
    day = read_json(path)
    stopped = load_stopped(d) if snapshots is None else None
    snapshots = snapshots if snapshots is not None else load_day_snapshots(d)
    for race in iter_races(day):
        if race.get("prediction"):
            apply_odds(race["prediction"], snapshot_for_race(race["race_id"], race.get("closed_at"), snapshots,
                                                             stopped))
    day["generated_at"] = generated_at or to_jst_iso(datetime.now(JST))
    return write_day(day, out_dir, ldir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="予想と interim から days/YYYYMMDD.json(公開・ローカル)を作る")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", type=Path, default=DOCS_DATA_DIR, help="公開JSONの出力先")
    ap.add_argument("--local-out", type=Path, default=None,
                    help="ローカルJSONの出力先(既定: --out が docs/data なら docs/data-local、それ以外は <out>-local)")
    ap.add_argument("--no-local", action="store_true", help="ローカルJSONを書かない")
    ap.add_argument("--odds-only", action="store_true", help="締切前オッズだけを反映する(前回のローカルJSONを使う)")
    ap.add_argument("--generated-at", default=None, help="テスト用。既定は現在時刻(JST)")
    args = ap.parse_args(argv)
    d = Date.fromisoformat(args.date)
    local_dir: Path | None | str = None if args.no_local else (args.local_out or "auto")
    if args.odds_only:
        path = export_odds_only(d, args.out, local_dir=local_dir, generated_at=args.generated_at)
    else:
        path = export_day(d, args.out, generated_at=args.generated_at, local_dir=local_dir)
    print(f"書き出し: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
