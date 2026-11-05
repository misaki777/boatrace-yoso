"""src/common/codes.py のテスト(ネットワーク不要)。"""

import pytest

from src.common import codes


def test_stadium_tables_cover_all_24():
    assert sorted(codes.STADIUM_NAMES) == list(range(1, 25))
    assert sorted(codes.STADIUM_PREFECTURE) == list(range(1, 25))
    assert codes.STADIUM_NAMES[2] == "戸田"
    assert codes.STADIUM_NAMES[24] == "大村"
    # 所在地はすべて正しい都道府県コード
    assert all(p in codes.PREFECTURE_NAMES for p in codes.STADIUM_PREFECTURE.values())
    assert codes.PREFECTURE_NAMES[codes.STADIUM_PREFECTURE[1]] == "群馬県"
    assert codes.PREFECTURE_NAMES[codes.STADIUM_PREFECTURE[12]] == "大阪府"


def test_grade_pg1_is_6_and_level_order():
    assert codes.GRADE_NAMES[6] == "PG1"
    assert codes.GRADE_NAMES[5] == "一般"
    order = sorted(codes.GRADE_LEVEL, key=codes.GRADE_LEVEL.get, reverse=True)
    assert [codes.GRADE_NAMES[g] for g in order] == ["SG", "PG1", "G1", "G2", "G3", "一般"]


def test_rank_names():
    assert codes.RANK_NAMES == {1: "A1", 2: "A2", 3: "B1", 4: "B2"}


def test_prefecture_short_name():
    assert len(codes.PREFECTURE_NAMES) == 47
    assert codes.prefecture_short_name(11) == "埼玉"
    assert codes.prefecture_short_name(13) == "東京"
    assert codes.prefecture_short_name(27) == "大阪"
    assert codes.prefecture_short_name(26) == "京都"
    assert codes.prefecture_short_name(1) == "北海道"
    assert codes.prefecture_short_name(46) == "鹿児島"
    assert codes.prefecture_short_name(None) is None
    assert codes.prefecture_short_name(99) is None


@pytest.mark.parametrize(
    "direction, expected",
    [
        (3, "tail"), (5, "tail"), (7, "tail"),
        (11, "head"), (13, "head"), (15, "head"),
        (17, "none"),
        (1, "cross"), (2, "cross"), (8, "cross"), (10, "cross"), (16, "cross"),
        (None, None),
    ],
)
def test_wind_class(direction, expected):
    assert codes.wind_class(direction) == expected


def test_wind_class_unknown_raises():
    with pytest.raises(ValueError):
        codes.wind_class(18)


def test_place_helpers():
    for p in range(1, 7):
        assert codes.is_finish(p)
        assert codes.finish_position(p) == p
        assert not codes.is_disqualified(p)
        assert not codes.is_absent(p)
    for p in range(7, 14):
        assert not codes.is_finish(p)
        assert codes.finish_position(p) is None
        assert codes.is_disqualified(p)
        assert not codes.is_absent(p)
    for p in (14, 15, 16):
        assert codes.is_absent(p)
        assert not codes.is_disqualified(p)
    for p in (99, None):
        assert not codes.is_finish(p)
        assert not codes.is_disqualified(p)
        assert not codes.is_absent(p)
        assert codes.finish_position(p) is None
    # 14 は「14着」ではなくフライング
    assert codes.PLACE_NAMES[14] == "フライング欠場"


def test_technique_and_weather():
    assert codes.TECHNIQUE_NAMES[2] == "差し"
    assert codes.WEATHER_NAMES[2] == "曇り"
    assert codes.WIND_DIRECTION_NAMES[17] == "無風"


def test_bet_types():
    assert set(codes.BET_TYPE_NAMES) == {
        "win", "place", "exacta", "quinella", "quinella_place", "trifecta", "trio",
    }
    assert codes.ORDERED_BET_TYPES <= set(codes.BET_TYPE_NAMES)


def test_race_id_roundtrip():
    rid = codes.make_race_id("2026-11-02", 2, 1)
    assert rid == "20261102-02-01"
    assert codes.make_race_id("20261102", 24, 12) == "20261102-24-12"
    assert codes.parse_race_id(rid) == ("20261102", 2, 1)


@pytest.mark.parametrize("args", [("2026-1-2", 1, 1), ("20261102", 0, 1), ("20261102", 25, 1), ("20261102", 1, 13)])
def test_make_race_id_invalid(args):
    with pytest.raises(ValueError):
        codes.make_race_id(*args)


def test_parse_race_id_invalid():
    with pytest.raises(ValueError):
        codes.parse_race_id("20261102_02_01")
