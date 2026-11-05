"""選手カードの集計(F-10)のテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features.build_interim import build_interim, read_interim
from src.features.history_state import build_state
from src.features.racer_cards import FORBIDDEN_CARD_COLUMNS, build_cards, build_day, card_columns
from src.features.raw_source import make_loader
from src.features.style import STYLE_ALPHA


@pytest.fixture
def day2(tmp_path, raw_dir, interim_dir):
    return build_day(DAY2, interim_dir=interim_dir, out_dir=tmp_path / "cards", load_raw=make_loader(raw_dir))


def test_files_and_columns(tmp_path, day2):
    cards, results = day2
    assert (tmp_path / "cards" / "20261102_cards.parquet").exists()
    assert (tmp_path / "cards" / "20261102_results.parquet").exists()
    assert list(cards.columns) == card_columns()
    assert cards["racer_number"].is_unique and len(cards) == 23
    for bad in FORBIDDEN_CARD_COLUMNS:
        assert bad not in cards.columns and bad not in results.columns


def test_card_values(interim_dir, day2):
    cards, _ = day2
    c = cards.set_index("racer_number").loc[5242]
    assert c["branch_name"] == "埼玉" and c["rank_number"] == 3
    # 戸田で 1日目 1R(2着)・5R(1着)。6R は中止なので数えない
    assert c["local365_n"] == 2 and c["local365_win_rate"] == pytest.approx(0.5)
    assert c["local365_top2_rate"] == pytest.approx(1.0)
    assert c["c1_n"] == 2 and c["c2_n"] == 0
    # 1着率 = 決まり手別の率の合計(縮小後)。過去に 2走1勝
    e = read_interim("entries", interim_dir=interim_dir)
    d1 = e[(e["date"] == DAY1) & e["place_number"].notna() & (e["rs_course_number"] == 1)]
    p = (d1["finish_position"] == 1).mean()
    assert c["c1_win_rate"] == pytest.approx((1 + STYLE_ALPHA * p) / (2 + STYLE_ALPHA), abs=1e-5)
    # 過去が無いコースは件数0。率は全体平均(過去にそのコースの出走が全体である場合)
    assert 0 <= c["f_rate"] <= 1


def test_results_lists(day2):
    _, results = day2
    r = results[results["racer_number"] == 5242]
    recent = r[r["kind"] == "recent"]
    series = r[r["kind"] == "series"]
    # 1日目の 1R・5R(6R は中止なので出走ではない)。同じ節(同じ場・タイトル・連続した日)
    assert list(recent["race_number"]) == [1, 5]
    assert list(series["race_number"]) == [1, 5]
    assert list(recent["finish_position"]) == [2, 1]
    # 決まり手は1着のときだけ
    assert pd.isna(recent.iloc[0]["technique_number"]) and recent.iloc[1]["technique_number"] == 2
    # 別の場(江戸川)の選手は今節の一覧が無い
    other = results[results["racer_number"].isin([4122, 5067])]
    assert (other["kind"] != "series").all()
    # D 日の結果は一覧に入らない
    assert (results["date"] < DAY2).all()


def test_recent_is_limited_to_ten(tmp_path, raw_dir):
    # 同じ選手が12日続けて出たら、直近10走だけ
    from datetime import timedelta
    import copy
    base = load_fixture("turnmark", DAY1)
    days = [DAY2 - timedelta(days=i) for i in range(12, 0, -1)]
    for d in days:
        r = copy.deepcopy(base)
        for st in r["programs"]["stadiums"].values():
            for race in st["races"].values():
                race["date"] = d.isoformat()
                race["closed_at"] = d.isoformat() + race["closed_at"][10:]
                for k in ("preview", "result", "odds"):
                    if isinstance(race.get(k), dict):
                        race[k]["date"] = d.isoformat()
        write_raw(raw_dir, "turnmark", d, r)
    out = tmp_path / "i"
    build_interim(days[0], DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    _, results = build_day(DAY2, interim_dir=out, out_dir=tmp_path / "c", load_raw=make_loader(raw_dir))
    rec = results[(results["racer_number"] == 5242) & (results["kind"] == "recent")]
    assert len(rec) == 10
    assert rec["date"].max() == DAY1


def test_leak_uses_only_previous_days(tmp_path, interim_dir, raw_dir, day2):
    """D 日の結果が履歴に混じっていても、カードは変わらない(D−1 までだけを使う)。"""
    cards, results = day2
    e, r = read_interim("entries", interim_dir=interim_dir), read_interim("races", interim_dir=interim_dir)
    raw = load_fixture("openapi", DAY2)
    from src.features import parse
    t = parse.parse_day(raw, "openapi", DAY2)
    assert (e["date"] == DAY2).any()  # 履歴に D 日の結果が入っている
    c2, r2 = build_cards(DAY2, t["entries"], t["races"], e, r)
    pd.testing.assert_frame_equal(cards, c2)
    pd.testing.assert_frame_equal(results, r2)


def test_state_gives_same_cards(tmp_path, interim_dir, raw_dir, day2):
    st = build_state(read_interim("entries", interim_dir=interim_dir),
                     read_interim("races", interim_dir=interim_dir), DAY2)
    c2, r2 = build_day(DAY2, interim_dir=interim_dir, out_dir=tmp_path / "s",
                       load_raw=make_loader(raw_dir), state=st)
    pd.testing.assert_frame_equal(day2[0], c2)
    pd.testing.assert_frame_equal(day2[1], r2)
