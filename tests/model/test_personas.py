"""仮想の購入者10人: 規則どおりに買うこと、過去の結果で金額を変えないこと。"""

import inspect
from pathlib import Path

import pandas as pd
import pytest

from src.model import personas as ps

IDS = ["win_fav", "boat1", "in_nige", "app", "box3", "quinella3", "steady", "upset", "outer_ana", "select10",
       "boat1_underdog"]


def _data(n=60):
    rows, pay = [], []
    for r in range(n):
        rid = f"202611{2 + r // 12:02d}-01-{r % 12 + 1:02d}"
        p = [0.55, 0.2, 0.1, 0.08, 0.05, 0.02] if r % 3 == 0 else (
            [0.30, 0.25, 0.2, 0.1, 0.1, 0.05] if r % 3 == 1 else [0.4, 0.3, 0.1, 0.1, 0.05, 0.05])
        for e in range(1, 7):
            rows.append({"race_id": rid, "entry_number": e, "p_win": p[e - 1], "is_valid_result": True,
                         "is_absent": False, "finish_position": e})
        pay += [(rid, "win", "1", 150, None), (rid, "exacta", "1-2", 500, None), (rid, "quinella", "1=2", 300, None),
                (rid, "trifecta", "1-2-3", 1200, None)]
    return pd.DataFrame(rows), pd.DataFrame(pay, columns=["race_id", "bet_type", "combination", "amount", "label"])


def _table(preds, pay, tmp, outer=None, strategies=None):
    return {r["id"]: r for r in ps.personas_table(preds, pay, 1000, tmp, ("day", "week"), outer, strategies)}


def test_ten_personas(tmp_path):
    preds, pay = _data()
    outer = pd.Series({rid: (4 if i % 2 == 0 else 0) for i, rid in enumerate(sorted(preds["race_id"].unique()))})
    rows = _table(preds, pay, tmp_path, outer)
    assert list(rows) == IDS and [p["name"] for p in ps.PERSONAS][:3] == ["単勝さん", "1号艇さん", "イン逃げさん"]
    assert rows["win_fav"]["roi"] == pytest.approx(1.5) and rows["boat1"]["is_baseline"]
    # イン逃げさん: 1,000円を4点(100円単位)→ 300・300・200・200円。確率の高い 1-2-3 は300円で的中
    assert rows["in_nige"]["stake"] == 60000 and rows["in_nige"]["return"] == 60 * 300 * 12
    assert rows["app"]["stake"] == 60000                         # 既定: 3連単上位5点
    # 1,000円を6点: 100円ずつ配り、余り4単位を確率の高い順に → 的中の 1-2-3(確率最大)は200円
    assert rows["box3"]["races_bought"] == 60 and rows["box3"]["return"] == 60 * 200 * 12
    assert rows["quinella3"]["races_bought"] == 60
    assert rows["steady"]["races_bought"] == 20 and rows["upset"]["races_bought"] == 20
    assert rows["outer_ana"]["races_bought"] == 30               # 対象の艇がいるレースだけ
    assert rows["select10"]["races_bought"] == 50                # 5日 × 10レース
    for r in rows.values():
        assert "最適" not in r["description"] and "おすすめ" not in r["description"]
        assert r["stake"] == r["races_bought"] * 1000             # 予算は常に1,000円


def test_box_and_outer_combos(tmp_path):
    preds, pay = _data(3)
    ctx = ps._Ctx(ps._valid_with_results(preds), tmp_path, pd.Series({"20261102-01-01": 5}), None)
    box = ps._select("box3", ctx, 0, {})
    assert sorted(c for _, c, _ in box) == sorted(["1-2-3", "1-3-2", "2-1-3", "2-3-1", "3-1-2", "3-2-1"])
    oa = ps._select("outer_ana", ctx, 0, {})
    assert [c for _, c, _ in oa] == ["1=5", "2=5"]            # 5号艇と、5号艇を除く上位2艇
    ctx2 = ps._Ctx(ps._valid_with_results(preds), tmp_path, pd.Series({"20261102-01-01": 1}), None)
    assert [c for _, c, _ in ps._select("outer_ana", ctx2, 0, {})] == ["1=2", "1=3"]  # 上位に入っていれば次の艇
    ctx3 = ps._Ctx(ps._valid_with_results(preds), tmp_path, pd.Series({"20261102-01-01": -1}), None)
    assert ps._select("outer_ana", ctx3, 0, {}) == []         # 直前情報なしは買わない


def test_app_follows_strategy_and_records_changes(tmp_path):
    preds, pay = _data(24)
    strats = {"2026-11-02": {"name": "top_k", "bet_type": "trifecta", "k": 5},
              "2026-11-03": {"name": "top_k", "bet_type": "exacta", "k": 2}}
    r = ps.race_results(ps._valid_with_results(preds), pay, "app", 1000, tmp_path, None, strats)
    assert set(r["stake"]) == {1000.0}
    ch = ps.strategy_changes(strats)
    assert [c["date"] for c in ch] == ["2026-11-02", "2026-11-03"]
    days = tmp_path / "days"
    days.mkdir()
    (days / "20261104.json").write_text('{"strategy": {"name": "top_k", "bet_type": "win", "k": 1, "description": "x"}}')
    got = ps.app_strategies(["2026-11-04", "2026-11-05"], days)
    assert got["2026-11-04"]["bet_type"] == "win" and got["2026-11-05"] == ps.DEFAULT_APP_STRATEGY


def test_outer_top_boats():
    rows = []
    for rid, st, ex in (("a", [.1, .1, .1, .05, .2, .2], [6.8] * 3 + [6.6, 6.7, 6.7]),   # 4号艇がともに1位
                        ("b", [.1, .1, .1, .05, .2, .2], [6.8] * 3 + [6.7, 6.6, 6.7]),   # 別の艇 → 0
                        ("c", [.1, .1, .1, None, .2, .2], [6.8] * 6)):                  # 直前情報なし → -1
        for e in range(1, 7):
            rows.append({"race_id": rid, "entry_number": e, "pv_course_number": e, "pv_start_timing": st[e - 1],
                         "pv_exhibition_time": ex[e - 1]})
    assert ps.outer_top_boats(pd.DataFrame(rows)).to_dict() == {"a": 4, "b": 0, "c": -1}


def test_canceled_refund_and_no_past_results(tmp_path):
    preds, pay = _data(12)
    preds.loc[preds["race_id"] == "20261102-01-01", "is_valid_result"] = False
    preds.loc[(preds["race_id"] == "20261102-01-02") & (preds["entry_number"] == 1), "is_absent"] = True
    w = _table(preds, pay, tmp_path)["boat1"]
    assert w["races_bought"] == 11 and w["return"] == 1500 * 10 + 1000
    for f in (ps.race_results, ps.personas_table, ps.build_personas, ps._select):
        names = " ".join(inspect.signature(f).parameters)
        for bad in ("profit", "streak", "balance", "loss", "previous"):
            assert bad not in names
    # 外れが続いても購入額は1,000円のまま
    preds2, pay2 = _data(24)
    r = ps.race_results(ps._valid_with_results(preds2), pay2[pay2["race_id"] > "20261103"], "win_fav", 1000, tmp_path)
    assert set(r["stake"]) == {1000.0}


def test_choose_live_version():
    base = {"entry_number": range(1, 7), "p_win": 1 / 6}
    pre = pd.concat([pd.DataFrame({**base, "race_id": r, "version": "pre"}) for r in ("a", "b")])
    pv = pd.DataFrame({**base, "race_id": "a", "version": "preview"})
    got = ps.choose_live_version(pd.concat([pre, pv]))
    assert got.groupby("race_id")["version"].first().to_dict() == {"a": "preview", "b": "pre"}


def _win_odds(flags: dict) -> pd.DataFrame:
    """flags: race_id → True なら1号艇は2番人気、False なら1番人気。"""
    rows = []
    for rid, under in flags.items():
        for e in range(1, 7):
            o = (3.0 if e == 1 else (1.5 if e == 2 else 10.0)) if under else (1.5 if e == 1 else 10.0)
            rows.append({"race_id": rid, "bet_type": "win", "combination": str(e), "odds": o})
    return pd.DataFrame(rows)


def test_underdog_flags():
    f = ps.underdog_flags(_win_odds({"a": True, "b": False}))
    assert f.to_dict() == {"a": True, "b": False}
    # 1番人気が同じオッズで並んで1号艇を含むなら「1番人気」
    tie = pd.DataFrame([{"race_id": "c", "bet_type": "win", "combination": str(e), "odds": 2.0 if e <= 2 else 9.0}
                        for e in range(1, 7)])
    assert ps.underdog_flags(tie).to_dict() == {"c": False}


def test_underdog_persona(tmp_path):
    preds, pay = _data(24)
    pay = pd.concat([pay, pd.DataFrame([(r, "place", "1", 110, None) for r in preds["race_id"].unique()],
                                       columns=pay.columns)])
    rids = sorted(preds["race_id"].unique())
    flags = ps.underdog_flags(_win_odds({r: i % 2 == 0 for i, r in enumerate(rids[:20])}))  # 残り4レースはオッズなし
    rows = {r["id"]: r for r in ps.personas_table(preds, pay, 1000, tmp_path, ("day",), None, None, underdog=flags,
                                                  only=["boat1_underdog"])}
    u = rows["boat1_underdog"]
    assert u["races_bought"] == 10 and u["stake"] == 10000          # 1番人気でない10レースだけ、1,000円ずつ
    assert u["return"] == 10 * (5 * 150 + 5 * 110)                  # 単勝500円(150円/100円)+複勝500円(110円/100円)
    assert u["parts"]["win"]["stake"] == 5000 and u["parts"]["place"]["stake"] == 5000
    assert u["parts"]["place"]["return"] == 10 * 550
    # オッズの数値は出力に入らない
    import json

    text = json.dumps(rows, ensure_ascii=False, default=str)
    assert "odds" not in text and "1.5" not in text and "3.0" not in text


def test_underdog_without_odds_buys_nothing(tmp_path):
    preds, pay = _data(12)
    rows = {r["id"]: r for r in ps.personas_table(preds, pay, 1000, tmp_path, ("day",), only=["boat1_underdog"])}
    assert rows["boat1_underdog"]["races_bought"] == 0


def test_output_keys_have_no_odds_word(tmp_path):
    preds, pay = _data(12)
    rows = ps.personas_table(preds, pay, 1000, tmp_path, ("day",))

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    assert not [k for k in keys(rows) if "odds" in str(k)]  # 出力のキー名にもオッズを入れない


def test_history_cache_missing_message(tmp_path):
    (tmp_path / "latest").write_text("pre_20990101_aaaa\n")
    (tmp_path / "pre" / "pre_20990101_aaaa").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="--build-history-cache"):
        ps.load_history_cache(tmp_path)
    assert ps.history_cache_dir(tmp_path) == tmp_path / "pre" / "pre_20990101_aaaa" / "personas_history"


def test_build_personas_does_not_read_features():
    # Actions には学習用の特徴量が無い: build_personas は特徴量を読まない(キャッシュだけ)
    import inspect

    src = inspect.getsource(ps.build_personas)
    assert "load_features(" not in src and "predict_p(" not in src and "load_history_cache(" in src
