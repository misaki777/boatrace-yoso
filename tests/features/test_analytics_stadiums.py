"""A1 場の傾向(F-11)のテスト。期間を決め打ちしていないこと(期間の違う2つのデータで動く)。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, write_days
from src.common import analytics_rules as ar
from src.features.analytics.base import load_base
from src.features.analytics.stadiums import build_stadiums, main
from src.features.build_interim import build_interim
from src.features.raw_source import make_loader


@pytest.fixture
def long_interim(tmp_path):
    """1日目の内容(戸田4レース、うち有効2レース)を 2025-06-01 から 60日ごとに置いたデータ(約1年半)。"""
    raw_dir = tmp_path / "raw"
    days = [date(2025, 6, 1) + timedelta(days=60 * i) for i in range(9)] + [DAY1]
    # 件数を下限以上にするため、同じ日を多数入れる(毎日)
    days += [DAY1 - timedelta(days=i) for i in range(1, 40)]
    write_days(raw_dir, sorted(set(days)))
    out = tmp_path / "interim"
    build_interim(min(days), DAY1, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return out, sorted(set(days))


def test_period_from_data_short(tmp_path, interim_dir):
    base = load_base(interim_dir)
    assert (base.date_from, base.date_to) == (DAY1, DAY2)
    t = build_stadiums(base)
    s = t["summary"]
    assert set(s["period"]) == {"all", "365d"}
    assert (s["date_to"] == DAY2).all()
    # 件数が少ないので率は出ない(件数だけ)
    assert s["c1_win_rate"].isna().all() and (s["level"] == ar.SAMPLE_INSUFFICIENT).all()
    assert (s["n_races"] > 0).all()


def test_period_from_data_long(long_interim):
    interim, days = long_interim
    base = load_base(interim)
    assert (base.date_from, base.date_to) == (days[0], DAY1)
    t = build_stadiums(base)
    s = t["summary"].set_index(["period", "stadium_number"])
    # 全期間と直近365日で件数が違う(古い日は 365d に入らない)
    n_all, n_365 = s.loc[("all", 2), "n_races"], s.loc[("365d", 2), "n_races"]
    assert n_all > n_365
    assert s.loc[("365d", 2), "date_from"] == DAY1 - timedelta(days=364)
    # 1日あたり有効2レース(1R: 2号艇の差し、4R: 4号艇1着)
    assert n_all == 2 * len(days)
    c = t["course"].set_index(["period", "stadium_number", "course"])
    # 1コースの艇は 1R で2着、4R で2着 → 1着率0
    assert c.loc[("all", 2, 1), "win_rate"] == 0.0
    assert c.loc[("all", 2, 2), "win_rate"] == pytest.approx(0.5)
    lo, hi = c.loc[("all", 2, 2), ["win_lo", "win_hi"]]
    assert (lo, hi) == pytest.approx(ar.wilson_interval(len(days), 2 * len(days)))
    # 全国(0)の行は戸田と同じ(1場しかない)
    assert c.loc[("all", 0, 2), "n"] == c.loc[("all", 2, 2), "n"]
    k = t["condition"]
    allrow = k[(k["period"] == "all") & (k["stadium_number"] == 2) & (k["cond_type"] == "all")].iloc[0]
    shares = [allrow[f"share_{x}"] for x in ("nige", "sashi", "makuri", "makurisashi", "nuki", "megumare")]
    assert sum(shares) == pytest.approx(1.0)
    assert set(k["cond_type"]) >= {"all", "season", "wind"}
    assert set(k.loc[k["cond_type"] == "wind", "cond"]) <= set(ar.WIND_BUCKETS)
    # 万舟率は払戻金から(オッズは使わない)
    assert 0 <= s.loc[("all", 2), "manshu_rate"] <= 1


def test_no_odds_columns(long_interim):
    interim, _ = long_interim
    for df in build_stadiums(load_base(interim)).values():
        assert not [c for c in df.columns if "odds" in c]


def test_cli_writes_files(tmp_path, interim_dir):
    out = tmp_path / "a"
    assert main([], interim_dir=interim_dir, out_dir=out) == 0
    for name in ("course", "condition", "summary"):
        assert isinstance(pd.read_parquet(out / "stadiums" / f"{name}.parquet"), pd.DataFrame)
