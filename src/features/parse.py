"""1日分の生JSON(openapi / turnmark 形式)を interim の4テーブルに変換する。

ここは純粋関数だけを置く(ファイルの読み書きはしない)。学習用(build_interim)と
当日用(build_features --date)の両方がこの関数を通る。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from src.common import codes

TZ = "Asia/Tokyo"

WEATHER_KEYS = (
    "wind_speed", "wind_direction_number", "wave_height",
    "weather_number", "air_temperature", "water_temperature",
)

PROGRAM_RACER_KEYS = (
    "rank_number", "branch_number", "birthplace_number", "age", "weight",
    "flying_count", "late_count", "average_start_timing",
    "national_win_rate", "national_top_2_percent", "national_top_3_percent",
    "local_win_rate", "local_top_2_percent", "local_top_3_percent",
    "motor_number", "motor_top_2_percent", "motor_top_3_percent",
    "boat_number", "boat_top_2_percent", "boat_top_3_percent",
)

BET_TYPES = ("win", "place", "exacta", "quinella", "quinella_place", "trifecta", "trio")
# オッズが幅(下限〜上限)で出る券種
RANGE_ODDS_BET_TYPES = frozenset({"place", "quinella_place"})

# ---------------------------------------------------------------------------
# 列の型(契約: 02_features.md 1章)
# ---------------------------------------------------------------------------

RACES_DTYPES: dict[str, str] = {
    "race_id": "string",
    "date": "object",  # datetime.date(parquet では date32)
    "stadium_number": "int8",
    "race_number": "int8",
    "closed_at": "object",  # 後で tz 付き datetime に変換
    "grade_number": "Int8",
    "title": "string",
    "subtitle": "string",
    "distance": "Int16",
    "day_number": "Int8",
    "day_number_source": "string",
    "day_status": "string",
    **{f"pv_{k}": "Float32" for k in WEATHER_KEYS},
    **{f"rs_{k}": "Float32" for k in WEATHER_KEYS},
    "technique_number": "Int8",
    "n_entries": "int8",
    "has_dead_heat": "boolean",
    "is_valid_result": "boolean",
    "is_canceled": "boolean",
    "has_refund": "boolean",
    "source": "string",
}
# 気象のうちコード値は整数にする
for _k in ("wind_direction_number", "weather_number"):
    RACES_DTYPES[f"pv_{_k}"] = "Int8"
    RACES_DTYPES[f"rs_{_k}"] = "Int8"

ENTRIES_DTYPES: dict[str, str] = {
    "race_id": "string",
    "date": "object",
    "stadium_number": "int8",
    "race_number": "int8",
    "entry_number": "int8",
    "racer_number": "Int32",
    "racer_name": "string",
    "rank_number": "Int8",
    "branch_number": "Int8",
    "birthplace_number": "Int8",
    "age": "Int8",
    "weight": "Float32",
    "flying_count": "Int8",
    "late_count": "Int8",
    "average_start_timing": "Float32",
    "national_win_rate": "Float32",
    "national_top_2_percent": "Float32",
    "national_top_3_percent": "Float32",
    "local_win_rate": "Float32",
    "local_top_2_percent": "Float32",
    "local_top_3_percent": "Float32",
    "motor_number": "Int16",
    "motor_top_2_percent": "Float32",
    "motor_top_3_percent": "Float32",
    "boat_number": "Int16",
    "boat_top_2_percent": "Float32",
    "boat_top_3_percent": "Float32",
    "pv_course_number": "Int8",
    "pv_start_timing": "Float32",
    "pv_weight": "Float32",
    "pv_weight_adjustment": "Float32",
    "pv_exhibition_time": "Float32",
    "pv_tilt_adjustment": "Float32",
    "pv_propeller_new": "boolean",
    "pv_parts_count": "Int8",
    "rs_course_number": "Int8",
    "rs_start_timing": "Float32",
    "place_number": "Int8",
    "finish_position": "Int8",
    "is_absent": "boolean",
    "is_disqualified": "boolean",
}

PAYOUTS_DTYPES: dict[str, str] = {
    "race_id": "string",
    "bet_type": "string",
    "combination": "string",
    "amount": "Int32",
    "label": "string",
}

ODDS_DTYPES: dict[str, str] = {
    "race_id": "string",
    "bet_type": "string",
    "combination": "string",
    "odds": "float32",
    "odds_lower": "float32",
    "odds_upper": "float32",
}


# ---------------------------------------------------------------------------
# 小さな道具
# ---------------------------------------------------------------------------


def _get(d: Any, key: str) -> Any:
    """キーが無い場合と null を同じに扱う(00_overview.md 7章)。"""
    if not isinstance(d, dict):
        return None
    return d.get(key)


def _racers_by_entry(section: Any) -> dict[int, dict]:
    """racers の dict を枠番 → 選手の dict にする。"""
    racers = _get(section, "racers") or {}
    if isinstance(racers, list):
        items = racers
    else:
        items = list(racers.values())
    out: dict[int, dict] = {}
    for r in items:
        if not isinstance(r, dict):
            continue
        en = r.get("entry_number")
        if en is None:
            continue
        out[int(en)] = r
    return out


def day_status_of(day_number_source: str | None, day_number: Any) -> str:
    """day_number_source から 開催/中止/順延 を判定する。"""
    s = day_number_source or ""
    if "中止" in s:
        return "canceled"
    if "順延" in s:
        return "postponed"
    return "normal"


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_range_leaf(v: Any) -> bool:
    return isinstance(v, dict) and ("lower_limit" in v or "upper_limit" in v)


def _walk_odds(node: Any, path: tuple[str, ...]):
    """艇番の入れ子 dict をたどり、(艇番の並び, 末端の値) を返す。"""
    if isinstance(node, dict) and not _is_range_leaf(node):
        for k, v in node.items():
            yield from _walk_odds(v, path + (str(k),))
    else:
        yield path, node


def _odds_rows(race_id: str, odds_section: Any) -> list[dict]:
    """turnmark の odds を行にする。

    実データの形式(2026-11-01 の turnmark で確認):
      "odds": {"date": ..., "stadium_number": ..., "race_number": ...,
               "win": {"1": 1.3, ...},
               "exacta": {"1": {"2": 4.6, ...}, ...},
               "trifecta": {"1": {"2": {"3": 9.7, ...}}},
               "place": {"1": {"lower_limit": 1.0, "upper_limit": 1.0}, ...},
               "quinella_place": {"1": {"2": {"lower_limit": 1.6, "upper_limit": 1.9}}}, ...}
    順不同の券種(2連複・拡連複・3連複)は、艇番を昇順にして "=" でつなぐ。
    """
    rows: list[dict] = []
    if not isinstance(odds_section, dict):
        return rows
    for bet_type in BET_TYPES:
        node = odds_section.get(bet_type)
        if node is None:
            continue
        ordered = bet_type in codes.ORDERED_BET_TYPES
        for path, leaf in _walk_odds(node, ()):
            if not path:
                continue
            boats = list(path) if ordered else sorted(path, key=lambda x: int(x) if x.isdigit() else 99)
            row = {"race_id": race_id, "bet_type": bet_type,
                   "combination": ("-" if ordered else "=").join(boats),
                   "odds": None, "odds_lower": None, "odds_upper": None}
            if _is_range_leaf(leaf):
                row["odds_lower"] = _to_float(leaf.get("lower_limit"))
                row["odds_upper"] = _to_float(leaf.get("upper_limit"))
            elif bet_type in RANGE_ODDS_BET_TYPES:
                row["odds_lower"] = row["odds_upper"] = _to_float(leaf)
            else:
                row["odds"] = _to_float(leaf)
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def iter_races(raw: dict):
    """生JSONから (stadium_number, race_number, race_dict) を順に返す。"""
    stadiums = _get(_get(raw, "programs"), "stadiums") or {}
    for s_key, stadium in stadiums.items():
        races = _get(stadium, "races") or {}
        for r_key, race in races.items():
            if not isinstance(race, dict):
                continue
            sn = race.get("stadium_number", s_key)
            rn = race.get("race_number", r_key)
            yield int(sn), int(rn), race


def parse_day(raw: dict, source: str, target: date | None = None,
              is_final: bool | None = None) -> dict[str, pd.DataFrame]:
    """1日分の生JSONを {races, entries, payouts, odds} の DataFrame にする。

    target を渡した場合、レースの date がそれと違えば ValueError(日付の取り違え防止)。
    is_final: このファイルが確定済み(取り直し後)か。None なら turnmark=確定、openapi=未確定とみなす。
      is_canceled の判定に使う(未確定のファイルで結果が無いレースは「結果未着」と区別できないため NA)。
    """
    if is_final is None:
        is_final = source == "turnmark"
    race_rows: list[dict] = []
    entry_rows: list[dict] = []
    payout_rows: list[dict] = []
    odds_rows: list[dict] = []

    for sn, rn, race in iter_races(raw):
        d = date.fromisoformat(str(race["date"])[:10])
        if target is not None and d != target:
            raise ValueError(f"日付が一致しません: ファイル={target} レース={d} ({sn}場{rn}R)")
        race_id = codes.make_race_id(d.strftime("%Y%m%d"), sn, rn)
        preview = _get(race, "preview")
        result = _get(race, "result")
        pv_racers = _racers_by_entry(preview)
        rs_racers = _racers_by_entry(result)
        program_racers = _racers_by_entry(race)

        refunds = _get(result, "refunds") or []
        refund_entries = {int(x) for x in refunds if x is not None and str(x).isdigit()}

        places: list[int | None] = []
        for en in sorted(program_racers):
            p = program_racers[en]
            pv = pv_racers.get(en, {})
            rs = rs_racers.get(en, {})
            place = rs.get("place_number")
            place = int(place) if place is not None else None
            places.append(place)
            # 直前情報が無い艇(締切前・未発表)は、プロペラ・部品も null にする
            pv_has = pv.get("exhibition_time") is not None or pv.get("course_number") is not None
            parts = pv.get("parts")
            row = {
                "race_id": race_id, "date": d, "stadium_number": sn, "race_number": rn,
                "entry_number": en,
                "racer_number": p.get("number"),
                "racer_name": p.get("name"),
                **{k: p.get(k) for k in PROGRAM_RACER_KEYS},
                "pv_course_number": pv.get("course_number"),
                "pv_start_timing": pv.get("start_timing"),
                "pv_weight": pv.get("weight"),
                "pv_weight_adjustment": pv.get("weight_adjustment"),
                "pv_exhibition_time": pv.get("exhibition_time"),
                "pv_tilt_adjustment": pv.get("tilt_adjustment"),
                # キー自体が無い古いファイルでは欠損にする(null 値は「新品なし」= False)
                "pv_propeller_new": (pv["propeller"] is not None)
                if pv_has and "propeller" in pv else None,
                "pv_parts_count": (len(parts) if isinstance(parts, list) else 0)
                if pv_has and "parts" in pv else None,
                "rs_course_number": rs.get("course_number"),
                "rs_start_timing": rs.get("start_timing"),
                "place_number": place,
                "finish_position": codes.finish_position(place),
                "is_absent": codes.is_absent(place) or en in refund_entries,
                "is_disqualified": codes.is_disqualified(place),
            }
            entry_rows.append(row)

        has_result = any(p is not None for p in places)
        n_win = sum(1 for p in places if p == 1)
        finishes = [p for p in places if p is not None and 1 <= p <= 6]
        has_dead_heat = len(finishes) != len(set(finishes))
        n_entries = len(program_racers)
        dns = race.get("day_number_source")
        day_status = day_status_of(dns, race.get("day_number"))
        # 出走表がまるごと null(選手が1人も入っていない)のレースは、開催が無かったものとして中止扱い
        no_program = race.get("title") is None and all(
            p.get("number") is None for p in program_racers.values())
        if no_program and day_status == "normal":
            day_status = "canceled"
        # レース単位の中止。結果が無く、日単位で中止・順延か、確定済みのファイルなら中止。
        # 未確定のファイル(当日の openapi)で結果が無いものは、結果未着と区別できないので NA
        if has_result:
            is_canceled = False
        elif day_status != "normal" or is_final:
            is_canceled = True
        else:
            is_canceled = None
        race_rows.append({
            "race_id": race_id, "date": d, "stadium_number": sn, "race_number": rn,
            "closed_at": race.get("closed_at"),
            "grade_number": race.get("grade_number"),
            "title": race.get("title"),
            "subtitle": race.get("subtitle"),
            "distance": race.get("distance"),
            "day_number": race.get("day_number"),
            "day_number_source": dns,
            "day_status": day_status,
            **{f"pv_{k}": _get(preview, k) for k in WEATHER_KEYS},
            **{f"rs_{k}": _get(result, k) for k in WEATHER_KEYS},
            "technique_number": _get(result, "technique_number"),
            "n_entries": n_entries,
            "has_dead_heat": has_dead_heat,
            # 着順があり、6艇そろい、1着がちょうど1艇のレースだけを有効とする(1.5節)
            "is_valid_result": has_result and n_entries == 6 and n_win == 1,
            "is_canceled": is_canceled,
            "has_refund": len(refund_entries) > 0,
            "source": source,
        })

        payouts = _get(result, "payouts") or {}
        if isinstance(payouts, dict):
            for bet_type, items in payouts.items():
                for it in items or []:
                    if not isinstance(it, dict):
                        continue
                    payout_rows.append({
                        "race_id": race_id, "bet_type": bet_type,
                        "combination": it.get("combination"),
                        "amount": it.get("amount"), "label": it.get("label"),
                    })

        # オッズはレース直下(turnmark)。念のため result の下も見る
        odds_section = race.get("odds")
        if odds_section is None:
            odds_section = _get(result, "odds")
        odds_rows.extend(_odds_rows(race_id, odds_section))

    return {
        "races": _finalize(race_rows, RACES_DTYPES),
        "entries": _finalize(entry_rows, ENTRIES_DTYPES),
        "payouts": _finalize(payout_rows, PAYOUTS_DTYPES),
        "odds": _finalize(odds_rows, ODDS_DTYPES),
    }


def _finalize(rows: list[dict], dtypes: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(dtypes))
    return cast_frame(df, dtypes)


def cast_frame(df: pd.DataFrame, dtypes: dict[str, str]) -> pd.DataFrame:
    """契約どおりの列順・型にそろえる。"""
    df = df.reindex(columns=list(dtypes))
    for col, dt in dtypes.items():
        if col == "closed_at":
            s = pd.to_datetime(df[col], errors="coerce")
            if getattr(s.dt, "tz", None) is None:
                s = s.dt.tz_localize(TZ)
            df[col] = s
        elif col == "date":
            df[col] = pd.to_datetime(df[col]).dt.date if len(df) else df[col].astype("object")
        elif dt in ("float32",):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(dt)
        elif dt.startswith(("Int", "Float")) or dt in ("int8", "int16"):
            num = pd.to_numeric(df[col], errors="coerce")
            if dt.startswith("Int"):
                # 小数が混じる値(例: 体重)が整数列に来たら丸めずにエラーにしたいので、そのまま変換する
                df[col] = num.astype("Float64").astype(dt)
            else:
                df[col] = num.astype(dt)
        else:
            df[col] = df[col].astype(dt)
    return df
