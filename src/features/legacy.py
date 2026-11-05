"""旧API(v3。2016-08-01〜2025-12-31)の生データを、v1(openapi / turnmark)と同じ形の dict に直す(F-7)。

v1 の形に直してから parse.parse_day に通すので、interim の列・型は v1 由来とまったく同じになる。
形式のメモは src/collect/LEGACY_FORMAT.md。主な読み替え:

| 旧API | v1 |
|---|---|
| number | race_number |
| racer_boat_number | entry_number |
| racer_number / racer_name | number / name |
| racer_class_number | rank_number |
| racer_branch_number / racer_birthplace_number | branch_number / birthplace_number |
| racer_age / racer_weight / racer_flying_count / racer_late_count | age / weight / flying_count / late_count |
| racer_average_start_timing | average_start_timing |
| racer_national_top_1_percent | national_win_rate(名前は top_1 だが値は勝率。実データで確認済み) |
| racer_national_top_{2,3}_percent | national_top_{2,3}_percent(local も同じ) |
| racer_assigned_motor_* / racer_assigned_boat_* | motor_* / boat_* |
| racer_course_number / racer_start_timing | course_number / start_timing |
| racer_place_number | place_number |
| day_label(全角数字。例「２日目」) | day_number_source(半角に直す) |

- day_number: 「初日」=1、「N日目」=N。「最終日」は旧APIに数値が無いので、ここでは null にし、
  build_interim が同じ場の前日の day_number + 1 で埋める(fill_final_day_number)。
- 直前情報の部品交換(parts)・プロペラ(propeller)は旧APIに無い → キーを作らない(interim では欠損)。
- 返還艇の一覧(refunds)は無い → 欠場は place_number(14〜16)だけで判定する。
- オッズは無い。
- 出走表の平均ST が無い選手は、v1 では 0.0、旧APIでは null。v1 に合わせて 0.0 にする
  (2026年以降の値を変えないため。実データで v1 の 0.0 は 1.6%、旧APIの null は約2%)。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import date

import pandas as pd

KINDS = ("programs", "previews", "results")

PROGRAM_KEYS = {
    "racer_class_number": "rank_number",
    "racer_branch_number": "branch_number",
    "racer_birthplace_number": "birthplace_number",
    "racer_age": "age",
    "racer_weight": "weight",
    "racer_flying_count": "flying_count",
    "racer_late_count": "late_count",
    "racer_average_start_timing": "average_start_timing",
    "racer_national_top_1_percent": "national_win_rate",
    "racer_national_top_2_percent": "national_top_2_percent",
    "racer_national_top_3_percent": "national_top_3_percent",
    "racer_local_top_1_percent": "local_win_rate",
    "racer_local_top_2_percent": "local_top_2_percent",
    "racer_local_top_3_percent": "local_top_3_percent",
    "racer_assigned_motor_number": "motor_number",
    "racer_assigned_motor_top_2_percent": "motor_top_2_percent",
    "racer_assigned_motor_top_3_percent": "motor_top_3_percent",
    "racer_assigned_boat_number": "boat_number",
    "racer_assigned_boat_top_2_percent": "boat_top_2_percent",
    "racer_assigned_boat_top_3_percent": "boat_top_3_percent",
}
PREVIEW_KEYS = {
    "racer_course_number": "course_number",
    "racer_start_timing": "start_timing",
    "racer_weight": "weight",
    "racer_weight_adjustment": "weight_adjustment",
    "racer_exhibition_time": "exhibition_time",
    "racer_tilt_adjustment": "tilt_adjustment",
}
RESULT_KEYS = {
    "racer_course_number": "course_number",
    "racer_start_timing": "start_timing",
    "racer_place_number": "place_number",
    "racer_number": "number",
    "racer_name": "name",
}
WEATHER_KEYS = ("wind_speed", "wind_direction_number", "wave_height", "weather_number",
                "air_temperature", "water_temperature")


def normalize_day_label(label: str | None) -> str | None:
    """全角数字などを半角にそろえる(「２日目」→「2日目」)。"""
    return unicodedata.normalize("NFKC", label) if isinstance(label, str) else label


def day_number_of(label: str | None) -> int | None:
    """「初日」=1、「N日目」=N。最終日・中止・順延などは None。"""
    s = normalize_day_label(label) or ""
    if s == "初日":
        return 1
    m = re.fullmatch(r"(\d+)日目", s)
    return int(m.group(1)) if m else None


def _boats(race: dict | None) -> list[dict]:
    return [b for b in (race or {}).get("boats") or [] if isinstance(b, dict)
            and b.get("racer_boat_number") is not None]


def _key(race: dict) -> tuple[int, int]:
    return int(race["stadium_number"]), int(race["number"])


def to_v1(programs: dict | None, previews: dict | None, results: dict | None) -> dict | None:
    """旧APIの3ファイル(1日分)を、v1 と同じ {"programs": {"stadiums": {...}}} の形にする。

    出走表が無い日は None。直前・結果が無いレースは、v1 の「未発表」と同じく値を null にする。
    """
    prog = (programs or {}).get("programs") or []
    if not prog:
        return None
    pv_by = {_key(r): r for r in (previews or {}).get("previews") or [] if r.get("number") is not None}
    rs_by = {_key(r): r for r in (results or {}).get("results") or [] if r.get("number") is not None}

    stadiums: dict[str, dict] = {}
    for p in prog:
        if p.get("number") is None:
            continue
        sn, rn = _key(p)
        label = normalize_day_label(p.get("day_label"))
        race = {
            "date": p.get("date"), "stadium_number": sn, "race_number": rn,
            "closed_at": p.get("closed_at"), "grade_number": p.get("grade_number"),
            "title": p.get("title"), "subtitle": p.get("subtitle"), "distance": p.get("distance"),
            "day_number_source": label, "day_number": day_number_of(label),
            "racers": {},
        }
        for b in _boats(p):
            en = int(b["racer_boat_number"])
            r = {"entry_number": en, "name": b.get("racer_name"), "number": b.get("racer_number")}
            r.update({v: b.get(k) for k, v in PROGRAM_KEYS.items()})
            # 平均STが無い選手(新人など)は、v1 では 0.0 が入っている。旧APIは null なので v1 に合わせる
            if r.get("average_start_timing") is None:
                r["average_start_timing"] = 0.0
            race["racers"][str(en)] = r

        pv = pv_by.get((sn, rn))
        race["preview"] = {"date": p.get("date"), "stadium_number": sn, "race_number": rn,
                           **{k: (pv or {}).get(k) for k in WEATHER_KEYS}, "racers": {}}
        for b in _boats(pv):
            en = int(b["racer_boat_number"])
            # parts / propeller は旧APIに無いので、キーを作らない(欠損として扱う)
            race["preview"]["racers"][str(en)] = {"entry_number": en,
                                                  **{v: b.get(k) for k, v in PREVIEW_KEYS.items()}}

        rs = rs_by.get((sn, rn))
        race["result"] = {"date": p.get("date"), "stadium_number": sn, "race_number": rn,
                          **{k: (rs or {}).get(k) for k in WEATHER_KEYS},
                          "technique_number": (rs or {}).get("technique_number"),
                          "racers": {}, "payouts": (rs or {}).get("payouts") or {}, "refunds": []}
        for b in _boats(rs):
            en = int(b["racer_boat_number"])
            race["result"]["racers"][str(en)] = {"entry_number": en,
                                                 **{v: b.get(k) for k, v in RESULT_KEYS.items()}}
        stadiums.setdefault(str(sn), {"races": {}})["races"][str(rn)] = race
    return {"programs": {"stadiums": stadiums}}


def make_legacy_loader(load_legacy: Callable[[str, date], dict | None]) -> Callable[[date], dict | None]:
    """raw_io.load_legacy(kind, date) を使って、1日分を v1 の形で返す関数を作る。"""

    def _load(target: date) -> dict | None:
        parts = {k: load_legacy(k, target) for k in KINDS}
        return to_v1(parts["programs"], parts["previews"], parts["results"])

    return _load


def fill_final_day_number(races: pd.DataFrame, previous: pd.DataFrame | None = None) -> pd.DataFrame:
    """「最終日」で day_number が null の行を、同じ場の前日の day_number + 1 で埋める。

    previous: 前の月の races(月の初日が最終日の節のため)。前日が無ければ null のまま。
    """
    need = (races["day_number"].isna() & (races["day_number_source"] == "最終日")
            & (races["source"] == "legacy")).fillna(False).astype(bool)
    if not need.any():
        return races
    pool = races[["stadium_number", "date", "day_number"]]
    if previous is not None and len(previous):
        pool = pd.concat([previous[["stadium_number", "date", "day_number"]], pool], ignore_index=True)
    daily = (pool.dropna(subset=["day_number"]).groupby(["stadium_number", "date"])["day_number"].max())
    races = races.copy()
    from datetime import timedelta

    def _prev(sn: int, d) -> float | None:
        # 前日が順延(day_number なし)のこともあるので、3日前までさかのぼる
        for k in (1, 2, 3):
            v = daily.get((sn, d - timedelta(days=k)))
            if v is not None and not pd.isna(v):
                return float(v)
        return None

    vals = pd.Series([_prev(int(sn), d) if n else None
                      for sn, d, n in zip(races["stadium_number"], races["date"], need)],
                     index=races.index, dtype="Float64")
    races.loc[need, "day_number"] = (vals[need] + 1).astype("Int8")
    return races
