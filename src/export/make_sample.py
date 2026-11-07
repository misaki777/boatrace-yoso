"""フロント開発・デザイン確認用の架空サンプルを docs/data/sample/ に作る。

    python -m src.export.make_sample [--out docs/data/sample]

- 中身はすべて架空(場名・選手名・登録番号・成績・払戻・オッズ)。公式の生データは一切使わない。
- 乱数の seed を固定しているので、何度実行しても同じファイルになる(generated_at も固定値)。
- 標準ライブラリだけで動く(組合せ確率は src/export/probs.py の内部実装を使う)。

含まれる状態(2026-11-02 は「15:05 時点」の途中経過として作る):
- 2026-10-31, 2026-11-01: status=final(全レース結果あり、final_odds あり)
- 2026-11-02: status=in_progress
  - finished(的中 / 不的中 / 見送り)、preview(締切直前の直前予想)、scheduled(締切前)
  - canceled(はなみ 7R 以降が強風で中止)
  - no_prediction(結果だけあるレースと、締切前で予想の無いレース)
  - 欠場あり(推奨買い目に欠場艇を含み、返還を計上)
  - 3着が決まらないレース(order の3着が null、3連単が不成立で返還)
  - 前付け(展示の進入が枠番と違う)、F持ちの選手
  - 締切前オッズ(odds_snapshot): ok(締切前のレースを含む)、partial、late、not_targeted、未取得(null)
    と、ev_rank の A〜D・null。オッズの数値は架空(取得の設定は「締切10分前」を想定)
- 2026-10-31, 2026-11-01 は締切前オッズを取り始める前の日(odds_snapshot は null)。final_odds は ev_rank だけ

出力:
- docs/data/sample/        公開JSONの形(オッズ・期待値の数値なし)と strategy_cases.json
- docs/data/sample-local/  ローカルJSONの形(docs/data-local/ と同じ。odds・ev の数値あり。数値は架空)
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.export.aggregate import build_index, build_performance
from src.export.build import (
    JST, SCHEMA_VERSION, apply_odds, build_prediction, build_result, day_status, iter_races,
    strip_numeric_odds, summarize, to_jst_iso,
)
from src.export.io_util import write_json
from src.export.probs import combination_probs, normalize

DEFAULT_OUT = Path("docs/data/sample")
DEFAULT_LOCAL_OUT = Path("docs/data/sample-local")

MODEL = {"model_id": "pre_20261001_sample", "variant": "pre", "train_until": "2026-09-30"}
# サンプルでは「見送り」を出すため confidence_skip を使う(本番の既定は top_k。spec 06 U2)
STRATEGY = {
    "name": "confidence_skip", "bet_type": "trifecta", "k": 5, "alpha": 0.35,
    "short_label": "3連単 上位5点・荒れ見送り",
    "description": "1着確率の最大値が35%以上のレースだけ、3連単 確率上位5点",
}

# 架空の場名(場番号は 1〜24 の範囲で使うが、名前は実在の場と関係ない)
STADIUM_NAMES = {
    2: "かすみ", 4: "しおかぜ", 6: "はなみ", 9: "みさき", 12: "すみれ", 15: "ことひら",
    18: "あさひ", 20: "わかば", 22: "ひびき", 24: "しらなみ", 1: "あかぎ", 7: "みかわ",
    10: "えちぜん", 13: "むこがわ", 17: "いつくし", 21: "あしべ",
}
GRADE_TITLES = {
    "SG": "第{n}回 架空グランプリ",
    "PG1": "第{n}回 架空クイーンズ",
    "G1": "{name}周年記念 第{n}回",
    "G2": "第{n}回 {name}モーターボート大賞",
    "G3": "第{n}回 {name}企業杯",
    "一般": "第{n}回 {name}カップ",
}
# 1R の締切時刻(時, 分)と、レースの間隔(分)
SLOTS = {"morning": (8, 45), "day": (10, 45), "night": (15, 15)}
RACE_INTERVAL_MIN = 28

FAMILY = ["青木", "石井", "上野", "江口", "大西", "岡本", "片山", "川口", "北村", "久保", "小島", "斉藤",
          "坂口", "島田", "杉本", "高木", "田村", "中川", "西田", "野口", "橋本", "原田", "平野", "福田",
          "松尾", "宮崎", "森下", "山根", "吉岡", "渡部"]
GIVEN = ["一真", "海斗", "健太", "浩二", "翔", "大輔", "拓也", "直樹", "颯", "光", "真司", "裕介",
         "涼", "和也", "晴美", "彩", "美咲", "千夏", "陽菜", "優子"]
BRANCHES = ["群馬", "埼玉", "東京", "静岡", "愛知", "三重", "福井", "滋賀", "大阪", "兵庫", "徳島",
            "香川", "岡山", "広島", "山口", "福岡", "佐賀", "長崎"]
COURSE_WEIGHT = [1.9, 0.55, 0.45, 0.25, -0.2, -0.6]


# ---------------------------------------------------------------------------
# 架空データの部品
# ---------------------------------------------------------------------------

def make_racer_pool(rng: random.Random, size: int = 400) -> list[dict[str, Any]]:
    pool = []
    numbers = rng.sample(range(3100, 5400), size)
    for num in numbers:
        rank = rng.choices(["A1", "A2", "B1", "B2"], weights=[20, 20, 50, 10])[0]
        lo, hi = {"A1": (6.2, 7.9), "A2": (5.3, 6.4), "B1": (3.4, 5.5), "B2": (1.2, 3.5)}[rank]
        nwr = round(rng.uniform(lo, hi), 2)
        pool.append({
            "racer_name": f"{rng.choice(FAMILY)} {rng.choice(GIVEN)}",
            "racer_number": num,
            "rank": rank,
            "branch": rng.choice(BRANCHES),
            "national_win_rate": nwr,
            "avg_st": round(rng.uniform(0.13, 0.21), 2),
            "flying_count": 1 if rng.random() < 0.06 else 0,
        })
    return pool


def make_boats(rng: random.Random, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """6艇分の選手情報(spec 04 の boats[] の確率以外の列)。"""
    boats = []
    for entry, r in enumerate(members, start=1):
        boats.append({
            "entry": entry,
            "racer_name": r["racer_name"],
            "racer_number": r["racer_number"],
            "rank": r["rank"],
            "branch": r["branch"],
            "national_win_rate": r["national_win_rate"],
            "local_win_rate": round(max(0.0, r["national_win_rate"] + rng.uniform(-0.9, 0.9)), 2),
            "motor_top2": round(rng.uniform(22.0, 58.0), 1),
            "avg_st": r["avg_st"],
            "flying_count": r["flying_count"],
            "exhibition_time": None,
            "exhibition_course": None,
        })
    return boats


def model_p_win(rng: random.Random, boats: list[dict[str, Any]]) -> list[float]:
    """架空の「モデル」の1着確率(コース+勝率+モーター+ノイズの softmax)。"""
    scores = [COURSE_WEIGHT[i] + 0.45 * (b["national_win_rate"] - 5.0) + 0.012 * (b["motor_top2"] - 40)
              + rng.gauss(0, 0.3) for i, b in enumerate(boats)]
    m = max(scores)
    ex = [math.exp(s - m) for s in scores]
    t = sum(ex)
    return [e / t for e in ex]


def simulate_order(rng: random.Random, p: list[float], absent: set[int]) -> list[int]:
    """Plackett-Luce で着順(欠場を除いた全艇)を引く。"""
    remaining = [i + 1 for i in range(6) if (i + 1) not in absent]
    order = []
    while remaining:
        w = [p[b - 1] + 1e-6 for b in remaining]
        b = rng.choices(remaining, weights=w)[0]
        order.append(b)
        remaining.remove(b)
    return order


def fake_amount(rng: random.Random, prob: float) -> int:
    """控除率25%相当の払戻(100円あたり、10円単位)。"""
    raw = 75.0 / max(prob, 1e-4) * math.exp(rng.gauss(0, 0.35))
    return max(100, int(round(raw / 10.0)) * 10)


def technique_for(rng: random.Random, winner_course: int) -> str:
    if winner_course == 1:
        return "逃げ" if rng.random() < 0.97 else "恵まれ"
    if winner_course == 2:
        return rng.choices(["差し", "まくり", "抜き"], weights=[6, 3, 1])[0]
    return rng.choices(["まくり", "まくり差し", "差し", "抜き"], weights=[4, 4, 1, 1])[0]


def subtitle_for(race_number: int, day_number: int | None) -> str:
    if race_number == 12:
        return "ドリーム戦" if day_number == 1 else "選抜戦"
    if race_number == 11:
        return "特選"
    return "予選" if race_number <= 6 else "一般戦"


# ---------------------------------------------------------------------------
# 1日分
# ---------------------------------------------------------------------------

# 各日の場の構成: (場番号, 時間帯, グレード, 何日目)
DAY_PLANS: dict[str, list[tuple[int, str, str, int]]] = {
    "2026-10-31": [(1, "day", "一般", 2), (7, "night", "G3", 4), (10, "morning", "一般", 1),
                   (13, "day", "G1", 3), (21, "morning", "一般", 5)],
    "2026-11-01": [(1, "day", "一般", 3), (7, "night", "G3", 5), (10, "morning", "一般", 2),
                   (13, "day", "G1", 4), (17, "night", "PG1", 1), (21, "morning", "一般", 6)],
    "2026-11-02": [(2, "morning", "一般", 3), (4, "day", "G2", 2), (6, "day", "一般", 4),
                   (9, "day", "G3", 1), (12, "night", "SG", 2), (15, "night", "一般", 3),
                   (18, "morning", "一般", 6), (24, "night", "G1", 5)],
}
GENERATED_AT = {
    "2026-10-31": "2026-11-01T10:30:00+09:00",   # 翌日に確定オッズを反映した時刻
    "2026-11-01": "2026-11-02T10:30:00+09:00",
    "2026-11-02": "2026-11-02T15:05:00+09:00",   # 当日の途中経過
}
FINAL_DAYS = {"2026-10-31", "2026-11-01"}

# 2026-11-02 の特別なレース
CANCELED_FROM = (6, 7)              # (場, R): この R 以降が中止
NO_PREDICTION = {(9, 4), (24, 10)}   # 予想なし
ABSENT_RACE = (18, 5)               # 欠場あり
NO_THIRD_RACE = (4, 3)              # 3着が決まらない(失格)
PREVIEW_WINDOW_MIN = 45             # generated_at から何分以内に締め切るレースを直前予想にするか
FRONT_RUNNER_RACE = (12, 1)         # 展示で前付け(進入が枠番と違う)
# 締切前オッズ(2026-11-02 だけ)。締切の約10分前に取ったことにする
ODDS_MINUTES_BEFORE = 10
ODDS_NOT_TARGETED_STADIUM = 18      # 対象外の場(お気に入り以外を取らない設定を想定)
ODDS_LATE = {(2, 4), (9, 9)}        # 間に合わず未取得
ODDS_PARTIAL = {(4, 6)}             # 3連単のページだけ取れた


PREVIEW_MODEL_ID = "preview_20261001_sample"
# 架空の根拠(特徴量名, 表示ラベル, 値を出すか)
FAKE_FEATURES = [
    ("national_win_rate", "全国勝率", True), ("motor_top_2_percent", "モーター2連率", True),
    ("h_stadium_course_win_rate_365d", "この場・このコースの1着率(1年)", False),
    ("h_racer_course_win_rate", "このコースでの1着率", False), ("average_start_timing", "平均ST", True),
    ("r_inner_strength", "内側の艇との力の差", False), ("local_win_rate", "当地勝率", True),
]


def make_fake_reasons(race_id: str, boats: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """架空の根拠トップ3(寄与の数値は段階にしてから出す。build.make_reasons と同じ変換)。"""
    from src.export.build import make_reasons

    rng = random.Random(int(race_id.replace("-", "")) * 7)
    out = []
    for b in boats:
        feats = rng.sample(FAKE_FEATURES, 3)
        rows = []
        for name, label, has_value in feats:
            c = rng.gauss(0, 0.4)
            value = None
            if has_value:
                value = {"national_win_rate": b["national_win_rate"], "motor_top_2_percent": b["motor_top2"],
                         "average_start_timing": b["avg_st"], "local_win_rate": b["local_win_rate"]}[name]
            rows.append((label, c, value))
        rows.sort(key=lambda r: -abs(r[1]))
        out.append(make_reasons(rows))
    return out


def make_preview_info(race_id: str, boats: list[dict[str, Any]]) -> dict[str, Any]:
    rng = random.Random(int(race_id.replace("-", "")) * 11)
    return {
        "boats": [{"entry": b["entry"], "exhibition_time": b["exhibition_time"],
                   "exhibition_course": b["exhibition_course"], "exhibition_st": round(rng.uniform(0.05, 0.25), 2)}
                  for b in boats],
        "weather": rng.choice(["晴", "曇り", "雨"]),
        "wind_speed": float(rng.randint(0, 6)),
        "wind_class": rng.choice(["tail", "head", "cross", "none"]),
        "wave_height": float(rng.randint(0, 5)),
    }


def make_fake_tenkai(race_id: str, p_win: list[float], variant: str, locked_at: str, model_id: str,
                     boats: list[dict[str, Any]]) -> dict[str, Any]:
    """架空の展開予想(spec 10 3.2)。逃げは1号艇の1着確率に近い値にする。"""
    from src.export.phase3b import TENKAI_KEYS, round_to_one

    rng = random.Random(int(race_id.replace("-", "")) * 13)
    nige = min(0.9, p_win[0] * rng.uniform(0.9, 1.05))
    rest = [rng.uniform(0.6, 1.4) * w for w in (0.36, 0.30, 0.22, 0.09, 0.03)]
    vals = round_to_one([nige] + [(1 - nige) * x / sum(rest) for x in rest])
    c1 = next((b["entry"] for b in boats if b.get("exhibition_course") == 1), 1)
    base = {**dict(zip(TENKAI_KEYS, vals)), "course1_entry": c1, "version": variant, "model_id": model_id,
            "locked_at": locked_at}
    # 詳しい版(spec 10 3.4)。モデル担当の出力と同じ列の1行を作り、export と同じ関数で変換する
    import pandas as pd

    from src.export.phase3b import tenkai_detail_for_race

    entries = [b["entry"] for b in sorted(boats, key=lambda b: b.get("exhibition_course") or b["entry"])]
    who = {"c1_nige": vals[0], "c2_sashi": vals[1] * 0.6, "c2_makuri": vals[2] * 0.3, "c3_makuri": vals[2] * 0.3,
           "c3_makurisashi": vals[3] * 0.5, "c4_makuri": vals[2] * 0.4, "c4_makurisashi": vals[3] * 0.5,
           "c3_sashi": vals[1] * 0.2, "c4_sashi": vals[1] * 0.1, "c5_makuri": 0.005, "c5_makurisashi": 0.012,
           "c6_makuri": 0.004, "c6_makurisashi": 0.006}
    who["other"] = max(0.0, 1 - sum(who.values()))
    tops = [rng.uniform(0.6, 1.4) for _ in range(6)]
    row = {"race_id": race_id, "variant": variant, "headline": "",
           **{f"course{k}_entry": entries[k - 1] for k in range(1, 7)},
           **{f"p_who_{k}": v for k, v in who.items()},
           **{f"p_top_c{k}": tops[k - 1] / sum(tops) for k in range(1, 7)},
           **{f"p_faster_c{k}": rng.uniform(0.3, 0.6) for k in range(2, 7)}}
    p1 = max((v, k) for k, v in who.items() if k != "other")
    row["headline"] = f"本線: {entries[int(p1[1][1]) - 1]}号艇の{'逃げ' if 'nige' in p1[1] else 'まくり'}({p1[0] * 100:.0f}%)。"
    det = tenkai_detail_for_race(pd.DataFrame([row]), race_id)
    return {**base, **det}


SAMPLE_MODEL_HISTORY = [
    {"date": "2026-11-01", "variant": "pre", "candidate": "pre_20261101_sample", "adopted": True,
     "train_to": "2026-09-30", "valid": "2026-10",
     "metrics": {"win_logloss": {"candidate": 1.2041, "latest": 1.2102}, "top1_acc": {"candidate": 0.561, "latest": 0.557}},
     "reason": "基準 a〜e を満たした", "features": ["(公開しない)"]},
    {"date": "2026-10-01", "variant": "pre", "candidate": "pre_20261001_cand", "adopted": False,
     "train_to": "2026-08-31", "valid": "2026-09",
     "metrics": {"win_logloss": {"candidate": 1.2155, "latest": 1.2101}},
     "reason": "基準 a(logloss の改善)を満たさない"},
]


def make_sample_racers(day: dict[str, Any], pool: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    """その日の出走選手の架空カード(build_racers に、F-10 と同じ列の表を渡して作る)。"""
    import pandas as pd

    from src.export.phase3b import build_racers

    by_num = {r["racer_number"]: r for r in pool}
    seen: dict[int, int] = {}
    for st in day["stadiums"]:
        for race in st["races"]:
            for b in (race["prediction"] or {}).get("boats", []):
                seen.setdefault(b["racer_number"], st["stadium_number"])
    rows, res = [], []
    for num, stn in sorted(seen.items()):
        r = by_num[num]
        rng = random.Random(num)
        row = {"racer_number": num, "racer_name": r["racer_name"], "rank_number": ["A1", "A2", "B1", "B2"].index(r["rank"]) + 1,
               "branch_number": None, "branch_name": r["branch"], "stadium_number": stn,
               "national_win_rate": r["national_win_rate"], "national_top_2_percent": rng.uniform(15, 60),
               "national_top_3_percent": rng.uniform(30, 75), "local_win_rate": r["national_win_rate"] + rng.uniform(-1, 1),
               "local_top_2_percent": rng.uniform(10, 60), "local_top_3_percent": rng.uniform(25, 75),
               "average_start_timing": r["avg_st"], "flying_count": r["flying_count"], "late_count": 0,
               "local365_n": rng.randint(0, 30), "local365_win_rate": rng.uniform(0, 0.4),
               "local365_top2_rate": rng.uniform(0.1, 0.6), "local365_top3_rate": rng.uniform(0.2, 0.8),
               "f_rate": rng.uniform(0, 0.02), "late_rate": rng.uniform(0, 0.005), "maeduke_rate": rng.uniform(0, 0.2),
               # 入力にあっても出さない列(テストで確かめる)
               "age": 30, "weight": 52.0, "motor_number": 11}
        for k in range(1, 7):
            win = rng.uniform(0.0, 0.6 if k == 1 else 0.2)
            row.update({f"c{k}_n": rng.randint(0, 40), f"c{k}_win_rate": win, f"c{k}_nige": win * 0.9 if k == 1 else 0.0,
                        f"c{k}_sashi": 0.0 if k == 1 else win * 0.4, f"c{k}_makuri": 0.0 if k == 1 else win * 0.3,
                        f"c{k}_makurisashi": 0.0 if k <= 2 else win * 0.2, f"c{k}_second": rng.uniform(0.05, 0.3),
                        f"c{k}_third": rng.uniform(0.05, 0.3), f"c{k}_st_mean": rng.uniform(0.13, 0.21)})
        rows.append(row)
        for i in range(10):
            res.append({"racer_number": num, "kind": "recent", "date": datetime(2026, 10, 20 + i).date(),
                        "stadium_number": rng.randint(1, 24), "race_number": rng.randint(1, 12),
                        "course_number": rng.randint(1, 6), "place_number": rng.choice([1, 2, 3, 4, 5, 6, 6, 17]),
                        "finish_position": None, "technique_number": None})
        for i in range(2):
            res.append({"racer_number": num, "kind": "series", "date": datetime(2026, 10, 31 + i).date()
                        if i == 0 else datetime(2026, 11, 1).date(), "stadium_number": stn,
                        "race_number": rng.randint(1, 12), "course_number": rng.randint(1, 6),
                        "place_number": rng.randint(1, 6), "finish_position": None,
                        "technique_number": rng.choice([None, 1, 2, 3])})
    return build_racers(datetime(2026, 11, 2).date(), pd.DataFrame(rows), pd.DataFrame(res), generated_at)


def make_odds_snapshot(date: str, key: tuple[int, int], race_id: str, closed: datetime,
                       p_win: list[float], gen_dt: datetime) -> dict[str, Any] | None:
    """架空の締切前オッズ(spec 07 2.6 の解析JSONの形)。乱数はレースごとに別にして、他のデータに影響させない。"""
    if date != "2026-11-02":
        return None
    rng = random.Random(int(race_id.replace("-", "")))
    fetched = closed - timedelta(minutes=ODDS_MINUTES_BEFORE, seconds=rng.randint(5, 40))
    if fetched > gen_dt:
        return None      # まだ取っていない(odds_snapshot は null)
    base = {"race_id": race_id, "closed_at": to_jst_iso(closed)}
    if key[0] == ODDS_NOT_TARGETED_STADIUM:
        return {**base, "fetched_at": None, "status": "not_targeted", "pages": [], "odds": {}}
    if key in ODDS_LATE:
        return {**base, "fetched_at": None, "status": "late", "pages": [], "odds": {}}
    probs = combination_probs(p_win, use_model=False)

    def fake(prob: float) -> float:
        # 控除率25%に、締切前らしいばらつき(対数で σ=0.3)を足した値
        return max(1.0, round(75.0 / 100.0 / max(prob, 1e-4) * math.exp(rng.gauss(0, 0.3)), 1))

    odds: dict[str, Any] = {"trifecta": {}}
    for comb, p in probs["trifecta"].items():
        a, b, c = comb.split("-")
        odds["trifecta"].setdefault(a, {}).setdefault(b, {})[c] = fake(p)
    if key not in ODDS_PARTIAL:
        odds["exacta"] = {}
        for comb, p in probs["exacta"].items():
            a, b = comb.split("-")
            odds["exacta"].setdefault(a, {})[b] = fake(p)
        odds["win"] = {c: fake(p) for c, p in probs["win"].items()}
        # 1か所だけ欠けた値(空欄)を入れて、ev_rank=null の表示を確かめられるようにする
        if key == (2, 1):
            odds["win"]["6"] = None
    return {
        **base,
        "fetched_at": to_jst_iso(fetched),
        "minutes_before_close": round((closed - fetched).total_seconds() / 60.0, 1),
        "odds_time_label": "架空",
        "is_final_odds": False,
        "odds": odds,
    }


def make_day(date: str, pool: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    generated_at = GENERATED_AT[date]
    gen_dt = datetime.fromisoformat(generated_at)
    is_final = date in FINAL_DAYS
    y, m, d = (int(x) for x in date.split("-"))
    stadiums = []
    for st_no, slot, grade, day_number in DAY_PLANS[date]:
        name = STADIUM_NAMES[st_no]
        members_pool = rng.sample(pool, 48)   # その節に出ている選手
        title = GRADE_TITLES[grade].format(n=rng.randint(10, 70), name=name)
        h, mi = SLOTS[slot]
        races = []
        for rn in range(1, 13):
            closed = datetime(y, m, d, h, mi, tzinfo=JST) + timedelta(minutes=RACE_INTERVAL_MIN * (rn - 1))
            race_id = f"{date.replace('-', '')}-{st_no:02d}-{rn:02d}"
            boats = make_boats(rng, rng.sample(members_pool, 6))
            p_win = model_p_win(rng, boats)
            key = (st_no, rn)
            today = date == "2026-11-02"
            is_past = is_final or closed <= gen_dt
            canceled = today and st_no == CANCELED_FROM[0] and rn >= CANCELED_FROM[1]
            no_pred = today and key in NO_PREDICTION
            # 直前予想: 締切が generated_at の直前・直後のレース(締切前45分以内、締切後60分以内)
            near = timedelta(minutes=-60) <= closed - gen_dt <= timedelta(minutes=PREVIEW_WINDOW_MIN)
            use_preview = today and near and not canceled and not no_pred
            variant = "preview" if use_preview else "pre"
            if use_preview:
                for i, b in enumerate(boats):
                    b["exhibition_time"] = round(rng.uniform(6.62, 6.95), 2)
                    b["exhibition_course"] = i + 1
                if key == FRONT_RUNNER_RACE:
                    # 6号艇が前付けして3コースに入った
                    courses = [1, 2, 4, 5, 6, 3]
                    for b, c in zip(boats, courses):
                        b["exhibition_course"] = c
                predicted_at = to_jst_iso(closed - timedelta(minutes=12))
            else:
                predicted_at = f"{date}T07:30:00+09:00"
            prediction = None
            if not no_pred:
                prediction = build_prediction(
                    boats, p_win, variant=variant, predicted_at=predicted_at, strategy=STRATEGY,
                    use_model_combinations=False,
                    model_id=MODEL["model_id"] if variant == "pre" else PREVIEW_MODEL_ID,
                    locked_at=predicted_at, reasons=make_fake_reasons(race_id, boats))
                if date == "2026-11-02":   # 展開予想は Phase 3b から(過去日のファイルには無い)
                    prediction["tenkai"] = make_fake_tenkai(race_id, p_win, variant, predicted_at,
                                                            prediction["model_id"], boats)
                if not canceled:
                    apply_odds(prediction, make_odds_snapshot(date, key, race_id, closed, p_win, gen_dt))
            result = None
            if is_past and not canceled:
                result = make_result(rng, date, key, p_win, prediction, is_final)
            if canceled:
                state = "canceled"
            elif prediction is None:
                state = "no_prediction"
            elif result is not None:
                state = "finished"
            else:
                state = "preview" if variant == "preview" else "scheduled"
            races.append({
                "race_id": race_id,
                "race_number": rn,
                "closed_at": to_jst_iso(closed),
                "subtitle": subtitle_for(rn, day_number),
                "state": state,
                "prediction": prediction,
                "preview_info": make_preview_info(race_id, boats) if use_preview else None,
                "result": result,
            })
        stadiums.append({
            "stadium_number": st_no,
            "stadium_name": name,
            "title": title,
            "grade": grade,
            "day_number": day_number,
            "races": races,
        })
    stadiums.sort(key=lambda s: s["stadium_number"])
    day = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generated_at": generated_at,
        "status": "predicted",
        "model": MODEL,
        "strategy": STRATEGY,
        "stadiums": stadiums,
        "day_summary": None,
    }
    day["day_summary"] = summarize(iter_races(day))
    day["status"] = day_status(day, has_final_odds=is_final)
    return day


def make_result(rng: random.Random, date: str, key: tuple[int, int], p_win: list[float],
                prediction: dict[str, Any] | None, with_final_odds: bool) -> dict[str, Any]:
    recommended = prediction["recommended"] if prediction else None
    absent: set[int] = set()
    if date == "2026-11-02" and key == ABSENT_RACE:
        # 推奨買い目の1点目の3着の艇を欠場にする(返還の表示確認用)
        if recommended:
            absent = {int(recommended[0]["combination"].split("-")[2])}
        else:
            absent = {4}
    # 真の確率はモデルと同じとする(サンプルなので較正済みと仮定)
    order_all = simulate_order(rng, p_win, absent)
    order: list[int | None] = list(order_all[:3])
    no_third = date == "2026-11-02" and key == NO_THIRD_RACE
    if no_third:
        order[2] = None
    p_true = normalize(p_win, absent)
    probs = combination_probs(p_true, use_model=False)
    a, b, c = order_all[0], order_all[1], order_all[2]
    payouts: dict[str, list[dict[str, Any]]] = {
        "win": [{"combination": str(a), "amount": fake_amount(rng, probs["win"][str(a)])}],
        "exacta": [{"combination": f"{a}-{b}", "amount": fake_amount(rng, probs["exacta"][f"{a}-{b}"])}],
    }
    if no_third:
        payouts["trifecta"] = [{"combination": None, "amount": 100, "label": "不成立"}]
    else:
        payouts["trifecta"] = [{"combination": f"{a}-{b}-{c}",
                                "amount": fake_amount(rng, probs["trifecta"][f"{a}-{b}-{c}"])}]
    final_odds = None
    if with_final_odds and recommended is not None:
        final_odds = {}
        hit_amounts = {(bt, r["combination"]): r["amount"] for bt, rows in payouts.items() for r in rows}
        for rec in recommended:
            k = (rec["bet_type"], rec["combination"])
            if k in hit_amounts:
                final_odds[k] = hit_amounts[k] / 100.0   # 的中した組合せは 確定オッズ = 払戻/100
            else:
                final_odds[k] = round(fake_amount(rng, probs["trifecta"][rec["combination"]]) / 100.0, 1)
    return build_result(order=order, technique=technique_for(rng, a), absent_entries=sorted(absent),
                        payouts=payouts, recommended=recommended, final_odds=final_odds)


# ---------------------------------------------------------------------------
# performance の backtest(架空)
# ---------------------------------------------------------------------------

SAMPLE_BACKTEST_EXTRA = {
    "calibration": [
        {"bin": "0-10", "n": 9120, "pred_mean": 0.048, "actual": 0.045, "ci95": [0.041, 0.049]},
        {"bin": "10-20", "n": 6210, "pred_mean": 0.147, "actual": 0.139, "ci95": [0.131, 0.148]},
        {"bin": "20-30", "n": 3020, "pred_mean": 0.246, "actual": 0.252, "ci95": [0.237, 0.268]},
        {"bin": "30-40", "n": 1480, "pred_mean": 0.348, "actual": 0.331, "ci95": [0.307, 0.355]},
        {"bin": "40-50", "n": 1210, "pred_mean": 0.451, "actual": 0.462, "ci95": [0.434, 0.490]},
        {"bin": "50-60", "n": 980, "pred_mean": 0.548, "actual": 0.521, "ci95": [0.490, 0.552]},
        {"bin": "60-70", "n": 640, "pred_mean": 0.646, "actual": 0.659, "ci95": [0.621, 0.695]},
        {"bin": "70-80", "n": 310, "pred_mean": 0.738, "actual": 0.71, "ci95": [0.657, 0.757]},
        {"bin": "80-90", "n": 90, "pred_mean": 0.836, "actual": 0.811, "ci95": [0.718, 0.878]},
    ],
    "by_month": [{"month": "2026-10", "races": 4580, "roi": 0.78, "roi_ci95": [0.71, 0.86]}],
}
BASELINE_SAMPLE = [
    {"name": "baseline_win1", "params": {"bet_type": "win"}, "roi": 0.86, "roi_ci95": [0.82, 0.90], "hit_rate": 0.55,
     "leak_free": True, "is_baseline": True, "races": 4580, "bets": 4580, "max_losing_streak": 9,
     "max_drawdown_yen": 21300},
    {"name": "baseline_tri1_top5", "params": {"bet_type": "trifecta", "k": 5}, "roi": 0.74, "roi_ci95": [0.67, 0.81],
     "hit_rate": 0.27, "leak_free": True, "is_baseline": True, "races": 4580, "bets": 22900, "max_losing_streak": 23,
     "max_drawdown_yen": 154200},
]
BASELINE_LABELS = {"baseline_win1": "1号艇 単勝", "baseline_tri1_top5": "1号艇から3連単 上位5点"}

SAMPLE_BACKTEST = {
    "model_id": MODEL["model_id"],
    "period": {"from": "2026-10-01", "to": "2026-10-31"},
    "metrics": {"win_logloss": 1.312, "top1_acc": 0.512, "trifecta_top10_hit": 0.331},
    "strategies": [
        {"name": "top_k", "params": {"bet_type": "trifecta", "k": 5}, "roi": 0.81,
         "roi_ci95": [0.70, 0.93], "hit_rate": 0.17, "leak_free": True},
        {"name": "confidence_skip", "params": {"bet_type": "trifecta", "k": 5, "alpha": 0.35}, "roi": 0.88,
         "roi_ci95": [0.74, 1.03], "hit_rate": 0.21, "leak_free": True},
        {"name": "top_k", "params": {"bet_type": "exacta", "k": 3}, "roi": 0.79,
         "roi_ci95": [0.71, 0.87], "hit_rate": 0.38, "leak_free": True},
        {"name": "ev_final", "params": {"bet_type": "trifecta", "tau": 1.2}, "roi": 1.05,
         "roi_ci95": [0.82, 1.31], "hit_rate": 0.04, "leak_free": False},
        {"name": "ev_final_haircut", "params": {"bet_type": "trifecta", "tau": 1.2, "haircut": 0.85},
         "roi": 0.97, "roi_ci95": [0.74, 1.22], "hit_rate": 0.03, "leak_free": False},
    ],
}


def make_all(out_dir: Path, local_out_dir: Path | None = None) -> dict[str, Any]:
    """サンプル一式を書き出し、書いた内容を返す(テスト用)。

    days はローカルJSONの形で作り、公開用(out_dir)には数値を外したものを書く。
    戻り値の "days" は公開JSONの形、"local_days" はローカルJSONの形。
    """
    from src.export.strategy_cases import build_cases

    pool = make_racer_pool(random.Random(20261102))
    local_days = {}
    for i, date in enumerate(sorted(DAY_PLANS)):
        local_days[date] = make_day(date, pool, seed=1016 + i)   # 回収率が100%の上下に振れる seed を選んだ
    days = {date: strip_numeric_odds(day) for date, day in local_days.items()}
    generated_at = GENERATED_AT["2026-11-02"]
    index = build_index(days.values(), generated_at)
    bt = {**SAMPLE_BACKTEST, **SAMPLE_BACKTEST_EXTRA,
          "strategies": SAMPLE_BACKTEST["strategies"] + [{**b, "short_label": BASELINE_LABELS[b["name"]]}
                                                         for b in BASELINE_SAMPLE]}
    performance = build_performance(days.values(), generated_at, bt)
    cases = build_cases(days["2026-11-02"])
    targets = [(Path(out_dir), days, False)]
    if local_out_dir is not None:
        targets.insert(0, (Path(local_out_dir), local_days, True))
    for base, ds, local in targets:
        for date, day in ds.items():
            write_json(base / "days" / f"{date.replace('-', '')}.json", day, kind="day", pretty=True, local=local)
        write_json(base / "performance.json", performance, kind="performance", pretty=True)
        write_json(base / "index.json", index, kind="index", pretty=True)   # index は最後
    write_json(Path(out_dir) / "strategy_cases.json", cases, pretty=True)
    # Phase 3b: 選手カード(当日分だけ)とモデルの更新履歴
    from src.export.phase3b import build_models

    racers = make_sample_racers(local_days["2026-11-02"], pool, generated_at)
    models = build_models(SAMPLE_MODEL_HISTORY, generated_at)
    for base, _, _ in targets:
        write_json(base / "racers" / "20261102.json", racers, kind="racers")
        write_json(base / "models.json", models, kind="models", pretty=True)
    return {"index": index, "performance": performance, "days": days, "local_days": local_days,
            "strategy_cases": cases, "racers": racers, "models": models}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="docs/data/sample/ に架空のサンプルJSONを作る")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--local-out", type=Path, default=DEFAULT_LOCAL_OUT)
    args = ap.parse_args(argv)
    res = make_all(args.out, args.local_out)
    for date, day in res["days"].items():
        n = sum(1 for _ in iter_races(day))
        print(f"{date}: {day['status']} 場={len(day['stadiums'])} レース={n} summary={day['day_summary']}")
    print(f"書き出し先: {args.out}")


if __name__ == "__main__":
    main()
