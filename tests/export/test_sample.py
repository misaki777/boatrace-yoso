"""E-1: サンプル(docs/data/sample/)が仕様どおりで、スキーマの検証を通ることを確かめる。"""

import copy
from collections import Counter
from pathlib import Path

import pytest

from src.export import make_sample
from src.export.build import iter_races
from src.export.io_util import read_json
from src.export.schema import (
    check_forbidden, validate_day, validate_index, validate_index_against_days, validate_performance,
)

REPO = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO / "docs" / "data" / "sample"
SAMPLE_LOCAL_DIR = REPO / "docs" / "data" / "sample-local"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    res = make_sample.make_all(out / "sample", out / "sample-local")
    return out, res


def test_generated_passes_schema(generated):
    _, res = generated
    assert validate_index(res["index"]) == []
    assert validate_performance(res["performance"]) == []
    for day in res["days"].values():
        assert validate_day(day) == []
    assert validate_index_against_days(res["index"], res["days"]) == []


def test_committed_sample_is_up_to_date(generated):
    """docs/data/sample/ は make_sample の出力と同じ(seed 固定で冪等)。"""
    out, _ = generated
    files = sorted(p.relative_to(out) for p in out.rglob("*.json"))
    assert len(files) == 15, "サンプルが作られていません"
    committed = REPO / "docs" / "data"
    for rel in files:
        assert (committed / rel).exists(), f"{rel} がありません。python -m src.export.make_sample を実行してください"
        assert (committed / rel).read_text(encoding="utf-8") == (out / rel).read_text(encoding="utf-8"), \
            f"{rel} が古いです。python -m src.export.make_sample を実行してください"


def test_committed_sample_files_validate():
    index = read_json(SAMPLE_DIR / "index.json")
    assert validate_index(index) == []
    days = {}
    for d in index["dates"]:
        day = read_json(SAMPLE_DIR / d["file"])
        assert validate_day(day) == []
        days[d["date"]] = day
    assert validate_index_against_days(index, days) == []
    assert validate_performance(read_json(SAMPLE_DIR / "performance.json")) == []
    # ローカル用のサンプル(odds・ev の数値あり)
    lindex = read_json(SAMPLE_LOCAL_DIR / "index.json")
    for d in lindex["dates"]:
        assert validate_day(read_json(SAMPLE_LOCAL_DIR / d["file"]), local=True) == []


def test_sample_covers_all_states(generated):
    _, res = generated
    statuses = {d["status"] for d in res["days"].values()}
    assert {"in_progress", "final"} <= statuses
    today = res["days"]["2026-11-02"]
    states = Counter(r["state"] for r in iter_races(today))
    for s in ("scheduled", "preview", "finished", "canceled", "no_prediction"):
        assert states[s] > 0, f"state={s} がありません"
    races = list(iter_races(today))
    outcomes = [r["result"]["recommended_outcome"] for r in races
                if r["result"] and r["result"]["recommended_outcome"]]
    assert any(o["hits"] > 0 for o in outcomes), "的中がありません"
    assert any(o["bets"] > 0 and o["hits"] == 0 for o in outcomes), "不的中がありません"
    assert any(r["prediction"] and not r["prediction"]["recommended"] for r in races), "見送りがありません"
    assert any(r["result"] and r["result"]["absent_entries"] for r in races), "欠場がありません"
    assert any(o.get("refunded") for o in outcomes), "返還がありません"
    assert any(r["result"] and None in r["result"]["order"] for r in races), "着順が決まらないレースがありません"
    assert any(r["state"] == "no_prediction" and r["result"] is not None for r in races)
    assert any(r["state"] == "no_prediction" and r["result"] is None for r in races)
    # 直前予想: 展示の進入が枠番と違う艇がいる
    assert any(b["exhibition_course"] not in (None, b["entry"])
               for r in races if r["prediction"] for b in r["prediction"]["boats"])
    # 確定オッズは final の日にある
    final_day = res["days"]["2026-11-01"]
    assert any(r["result"]["final_odds"] for r in iter_races(final_day))
    # 締切前オッズ: ok(締切前のレースを含む)・partial・late・not_targeted・未取得(null)、ev_rank の A〜D と null
    snaps = Counter((r["prediction"]["odds_snapshot"] or {}).get("status") for r in races if r["prediction"])
    for st in ("ok", "partial", "late", "not_targeted", None):
        assert snaps[st] > 0, f"odds_snapshot={st} がありません"
    assert any(r["state"] in ("scheduled", "preview") and (r["prediction"]["odds_snapshot"] or {}).get("status") == "ok"
               for r in races if r["prediction"])
    ranks = Counter(it.get("ev_rank", "missing") for r in races if r["prediction"]
                    for items in r["prediction"]["picks"].values() for it in items)
    for rk in ("A", "B", "C", "D", None, "missing"):
        assert ranks[rk] > 0, f"ev_rank={rk} がありません"
    # F持ち
    assert any(b["flying_count"] > 0 for r in races if r["prediction"] for b in r["prediction"]["boats"])


def test_realistic_volume(generated):
    _, res = generated
    for day in res["days"].values():
        assert len(day["stadiums"]) >= 5
        for st in day["stadiums"]:
            assert [r["race_number"] for r in st["races"]] == list(range(1, 13))


def test_picks_win_sum_to_one(generated):
    _, res = generated
    for day in res["days"].values():
        for r in iter_races(day):
            if r["prediction"]:
                win = r["prediction"]["picks"]["win"]
                assert len(win) == 6
                assert abs(sum(x["prob"] for x in win) - 1.0) < 1e-3
                assert len(r["prediction"]["picks"]["exacta"]) == 10
                assert len(r["prediction"]["picks"]["trifecta"]) == 20


def test_no_forbidden_fields(generated):
    _, res = generated
    for obj in [res["index"], res["performance"], *res["days"].values()]:
        assert check_forbidden(obj) == []


def test_sample_outcome_hand_check(generated):
    """1レース分を手で照合する: 推奨の中に3連単の払戻の組合せがあれば、その額が return になる。"""
    _, res = generated
    checked = 0
    for r in iter_races(res["days"]["2026-11-01"]):
        rs, pr = r["result"], r["prediction"]
        if not pr or not pr["recommended"] or rs["absent_entries"]:
            continue
        tri = {p["combination"]: p["amount"] for p in rs["payouts"]["trifecta"]}
        expected = sum(tri.get(x["combination"], 0) for x in pr["recommended"])
        assert rs["recommended_outcome"]["return"] == expected
        assert rs["recommended_outcome"]["stake"] == 100 * len(pr["recommended"])
        checked += 1
    assert checked > 10


def test_validator_detects_broken_sample(generated):
    """サンプルを壊すと検証に落ちる(検証が空振りしていないことの確認)。"""
    _, res = generated
    day = copy.deepcopy(res["days"]["2026-11-02"])
    day["stadiums"][0]["races"][0]["prediction"]["boats"][0]["age"] = 30
    assert validate_day(day)
