"""テスト用の小さな生JSONを作るスクリプト(開発時に1回だけ手で実行する)。

    .venv/bin/python tests/features/fixtures/make_fixtures.py

data/raw の 2026-11-02 の openapi(raw_io.load_raw で読む)から数レースを抜き出して作る。
- 選手名は伏せる(登録番号はそのまま。過去集計のキーになるため)
- 2026-11-01 分は turnmark 形式とし、オッズは架空の値(Harville で作った確率から計算)
- 想定するケース: 通常、失格あり、同着1着、中止(着順がすべて null)、
  フライング返還、欠場、5艇しかいないレース
"""

from __future__ import annotations

import copy
import itertools
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.collect import raw_io  # noqa: E402

OUT = Path(__file__).resolve().parent

# 架空オッズの元にする各艇の強さ(枠番順)と控除率
STRENGTH = [0.40, 0.18, 0.15, 0.12, 0.09, 0.06]
TAKEOUT = 0.25


def _anonymize(race: dict) -> None:
    for sec in (race, race.get("preview") or {}, race.get("result") or {}):
        for r in (sec.get("racers") or {}).values():
            if r.get("name") is not None:
                r["name"] = f"選手{r['entry_number']}"


def _set_date(race: dict, ymd: str) -> None:
    race["date"] = ymd
    race["closed_at"] = ymd + race["closed_at"][10:]
    for k in ("preview", "result"):
        if race.get(k):
            race[k]["date"] = ymd


def _clear_result(race: dict) -> None:
    res = race["result"]
    for k in list(res):
        if k not in ("date", "stadium_number", "race_number", "racers", "payouts", "refunds"):
            res[k] = None
    for r in res["racers"].values():
        for k in list(r):
            if k != "entry_number":
                r[k] = None
    res["payouts"] = {}
    res["refunds"] = []


def _harville(order: tuple[int, ...]) -> float:
    p, rest = 1.0, 1.0
    for b in order:
        s = STRENGTH[b - 1]
        p *= s / rest
        rest -= s
    return p


def _odds(p: float) -> float:
    return round(max(1.0, (1 - TAKEOUT) / p), 1)


def _nest(out: dict, boats: tuple[int, ...], value) -> None:
    node = out
    for b in boats[:-1]:
        node = node.setdefault(str(b), {})
    node[str(boats[-1])] = value


def fake_odds(stadium: int, race: int, ymd: str) -> dict:
    """turnmark の実データと同じ形式(艇番の入れ子 dict)の架空オッズ。"""
    boats = range(1, 7)
    out: dict = {"date": ymd, "stadium_number": stadium, "race_number": race}
    for k in ("trifecta", "trio", "exacta", "quinella", "quinella_place", "win", "place"):
        out[k] = {}
    for b in boats:
        out["win"][str(b)] = _odds(_harville((b,)))
        p_top2 = sum(_harville((b, o)) + _harville((o, b)) for o in boats if o != b)
        o = _odds(p_top2)
        out["place"][str(b)] = {"lower_limit": o, "upper_limit": round(o * 1.6, 1)}
    for t in itertools.permutations(boats, 2):
        _nest(out["exacta"], t, _odds(_harville(t)))
    for a, b in itertools.combinations(boats, 2):
        _nest(out["quinella"], (a, b), _odds(_harville((a, b)) + _harville((b, a))))
        p_qp = sum(_harville(t) for t in itertools.permutations(boats, 3) if a in t and b in t)
        o = _odds(p_qp)
        _nest(out["quinella_place"], (a, b), {"lower_limit": o, "upper_limit": round(o * 1.5, 1)})
    for t in itertools.permutations(boats, 3):
        _nest(out["trifecta"], t, _odds(_harville(t)))
    for t in itertools.combinations(boats, 3):
        _nest(out["trio"], t, _odds(sum(_harville(x) for x in itertools.permutations(t))))
    return out


def _drop_boat(race: dict, entry: str) -> None:
    for sec in (race, race["preview"], race["result"]):
        sec["racers"].pop(entry, None)


def main() -> None:
    src = raw_io.load_raw("openapi", date(2026, 11, 2))
    if src is None:
        raise SystemExit("data/raw に 2026-11-02 の openapi がありません")
    st = src["programs"]["stadiums"]

    def pick(s: str, r: str) -> dict:
        race = copy.deepcopy(st[s]["races"][r])
        _anonymize(race)
        return race

    # --- 1日目(2026-11-01, turnmark 形式) ---
    day1: dict[str, dict] = {}
    base = pick("2", "1")
    r1 = copy.deepcopy(base)                       # 通常のレース
    r4 = pick("2", "4")                            # 失格(不完走)あり
    r5 = copy.deepcopy(base)                       # 同着1着
    r5["race_number"] = 5
    for k in ("preview", "result"):
        r5[k]["race_number"] = 5
    for en, pl in zip("123456", (1, 1, 3, 4, 5, 6)):
        r5["result"]["racers"][en]["place_number"] = pl
        r5["result"]["racers"][en]["place_number_source"] = str(pl)
    r6 = copy.deepcopy(base)                       # 中止(着順がすべて null)
    r6["race_number"] = 6
    for k in ("preview", "result"):
        r6[k]["race_number"] = 6
    _clear_result(r6)
    for race in (r1, r4, r5, r6):
        _set_date(race, "2026-11-01")
        race["day_number"] = race["day_number"] - 1
        race["day_number_source"] = f"{race['day_number']}日目"
        if race is not r6:
            race["odds"] = fake_odds(2, race["race_number"], "2026-11-01")
        day1[str(race["race_number"])] = race

    # --- 2日目(2026-11-02, openapi 形式) ---
    s2_r1 = pick("2", "1")                         # 1日目の1Rと同じ選手(過去集計のテスト用)
    s2_r12 = pick("2", "12")                       # フライング返還(place 14, refunds)
    s2_r2 = pick("2", "2")                         # 5艇しかいない
    _drop_boat(s2_r2, "6")
    s3_r2 = pick("3", "2")                         # 欠場(place 16, refunds)
    day2 = {"2": {"races": {"1": s2_r1, "2": s2_r2, "12": s2_r12}},
            "3": {"races": {"2": s3_r2}}}

    files = {
        OUT / "turnmark" / "2026" / "20261101.json": {"programs": {"stadiums": {"2": {"races": day1}}}},
        OUT / "openapi" / "2026" / "20261102.json": {"programs": {"stadiums": day2}},
    }
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(path, path.stat().st_size)


if __name__ == "__main__":
    main()
