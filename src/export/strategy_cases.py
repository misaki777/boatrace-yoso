"""推奨買い目の設定(S6)の共通テストケース docs/data/sample/strategy_cases.json を作る(E-9、spec 07 4.1)。

Python(tests/export/test_strategy_rules.py)とフロント(docs/app/tests/)の両方が同じケースを読み、
「race(confidence と picks)に settings を適用した結果が expected と一致する」ことを確かめる。
make_sample から呼ばれる(サンプルの 2026-11-02 のレースも入力に使う)。
"""

from __future__ import annotations

import copy
from typing import Any

from src.export.build import iter_races
from src.export.strategy_rules import (
    BET_TYPE_ORDER, CONFIDENCE_ARA, CONFIDENCE_KATA, DEFAULT_SETTINGS, EV_FILTER_RANKS, K_RANGES,
    apply_settings, evaluate_picks, normalize_settings,
)

RULES_TEXT = [
    "券種ごとに(win → exacta → trifecta の順で出す)、picks の並び(確率の降順)を保ったまま次を行う。",
    "1. min_prob が null でなければ、prob < min_prob の組合せを除く(prob は JSON の小数4桁の値)。",
    "2. ev_filter が none でなければ、ev_rank が許可ランク(B_or_above: A・B、A_only: A)に無い組合せを除く。"
    " ev_rank が無い・null の組合せは、no_odds が prob_only なら残し、skip なら除く。",
    "3. 残りの上位 k 点(bet_types の値)を取る。",
    "4. skip_confidence が ara で confidence < 0.35、または chu_or_below で confidence < 0.50 なら、全券種を空にする(見送り)。",
    "stake は表示用で、選ぶ買い目には影響しない。",
    "settings が壊れている(未知の値・範囲外・型違い・オブジェクトでない)ときは default_settings に戻す(normalize_cases)。",
    "odds_cases(E-10): odds_input に手入力(manual: 券種→組合せ→オッズ)と Worker の応答(worker.odds: 07 2.6 の入れ子)を渡す。"
    " 組合せごとに ev_rank を (1) 手入力 → (2) Worker(その券種が odds にあれば。組合せの値が無ければ null)"
    " → (3) race の picks の ev_rank(公開JSON)→ (4) なし の順で決めてから、上の1〜4を行う。",
    "ev = prob(小数4桁のまま)× オッズ(小数1桁のまま。丸め直さない)。ランクは A: ev ≥ 1.20、B: ≥ 1.00、C: ≥ 0.80、D: それ未満。"
    " 複勝・拡連複のような下限・上限の形は下限を使う。",
    "expected_evaluations は、settings の券種の picks すべてについて、使ったオッズの出どころ(source: manual / worker /"
    " public / none)・odds・ev・ev_rank の期待値。ev は浮動小数で、差が 1e-9 未満なら一致とみなす。",
]


def _s(**kw: Any) -> dict[str, Any]:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(kw)
    return s


def _pick(c: str, p: float, rank: str | None | bool = False) -> dict[str, Any]:
    """rank=False は ev_rank のキー自体が無い(オッズ未取得)ことを表す。"""
    d: dict[str, Any] = {"combination": c, "prob": p}
    if rank is not False:
        d["ev_rank"] = rank
    return d


# 手で作ったレース(境界や組合せを確かめるため)
TRI = ["1-2-3", "1-3-2", "1-2-4", "1-4-2", "2-1-3", "1-3-4", "1-2-5", "2-1-4", "1-4-3", "1-5-2",
       "3-1-2", "1-2-6", "2-3-1", "1-3-5", "1-6-2", "4-1-2", "2-1-5", "1-5-3", "1-4-5", "3-2-1"]
TRI_P = [0.1012, 0.0821, 0.0634, 0.0511, 0.0498, 0.0402, 0.0350, 0.0300, 0.0300, 0.0255,
         0.0201, 0.0200, 0.0199, 0.0150, 0.0120, 0.0110, 0.0100, 0.0099, 0.0090, 0.0080]
TRI_R = ["C", "A", None, "B", "D", "A", "C", "B", "D", "A", "C", None, "B", "D", "A", "C", "B", "D", "C", "A"]
WIN = [("1", 0.5200, "C"), ("2", 0.1800, "B"), ("3", 0.1300, "A"), ("4", 0.0900, "D"), ("5", 0.0500, None),
       ("6", 0.0300, "A")]
EXA = [("1-2", 0.1600, "D"), ("1-3", 0.1200, "A"), ("2-1", 0.0800, "B"), ("1-4", 0.0700, "C"), ("3-1", 0.0500, "A"),
       ("1-5", 0.0400, None), ("2-3", 0.0300, "B"), ("4-1", 0.0250, "D"), ("1-6", 0.0200, "A"), ("3-2", 0.0150, "C")]


def _race(confidence: float, with_rank: bool = True) -> dict[str, Any]:
    def r(x):
        return x if with_rank else False
    return {
        "confidence": confidence,
        "picks": {
            "win": [_pick(c, p, r(k)) for c, p, k in WIN],
            "exacta": [_pick(c, p, r(k)) for c, p, k in EXA],
            "trifecta": [_pick(c, p, r(k)) for c, p, k in zip(TRI, TRI_P, TRI_R)],
        },
    }


RACE_KATA = _race(0.5200)
RACE_CHU = _race(0.4000)
RACE_ARA = _race(0.3400)
RACE_NO_ODDS = _race(0.5200, with_rank=False)
RACE_AT_ARA = _race(0.3500)
RACE_AT_KATA = _race(0.5000)
RACE_EMPTY_EXACTA = {"confidence": 0.45, "picks": {"win": RACE_KATA["picks"]["win"], "exacta": [],
                                                   "trifecta": RACE_KATA["picks"]["trifecta"][:3]}}

HANDMADE: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
    ("default", "既定の設定(3連単 上位5点)", RACE_KATA, _s()),
    ("trifecta_k1", "3連単 1点", RACE_KATA, _s(bet_types={"trifecta": 1})),
    ("trifecta_k20", "3連単 20点(picks の全部)", RACE_KATA, _s(bet_types={"trifecta": 20})),
    ("multi_bet_types", "単勝3点・2連単2点・3連単3点。出す順は win→exacta→trifecta", RACE_KATA,
     _s(bet_types={"trifecta": 3, "win": 3, "exacta": 2})),
    ("min_prob_2pct", "最低確率2%(0.0200 ちょうどは残る)", RACE_KATA, _s(bet_types={"trifecta": 20}, min_prob=0.02)),
    ("min_prob_5pct_fewer_than_k", "最低確率5%で、残りが k より少ない", RACE_KATA, _s(min_prob=0.05)),
    ("ev_b_prob_only", "ランクB以上。ev_rank=null は残す(prob_only)", RACE_KATA, _s(ev_filter="B_or_above")),
    ("ev_b_skip", "ランクB以上。ev_rank=null は除く(skip)", RACE_KATA, _s(ev_filter="B_or_above", no_odds="skip")),
    ("ev_a_only", "ランクAだけ", RACE_KATA, _s(ev_filter="A_only", no_odds="skip")),
    ("ev_a_no_odds_prob_only", "オッズ無しのレースでランクA・prob_only → 確率だけで選ぶ", RACE_NO_ODDS,
     _s(ev_filter="A_only")),
    ("ev_a_no_odds_skip", "オッズ無しのレースでランクA・skip → 見送り", RACE_NO_ODDS,
     _s(ev_filter="A_only", no_odds="skip")),
    ("no_odds_without_filter", "オッズ無しでも ev_filter=none なら影響しない", RACE_NO_ODDS, _s(no_odds="skip")),
    ("skip_ara_on_ara", "「荒」なら見送り。荒のレース → 空", RACE_ARA, _s(skip_confidence="ara")),
    ("skip_ara_on_chu", "「荒」なら見送り。中のレース → 通常どおり", RACE_CHU, _s(skip_confidence="ara")),
    ("skip_chu_on_chu", "「中」以下なら見送り。中のレース → 空", RACE_CHU, _s(skip_confidence="chu_or_below")),
    ("skip_chu_on_kata", "「中」以下なら見送り。堅のレース → 通常どおり", RACE_KATA, _s(skip_confidence="chu_or_below")),
    ("boundary_ara_0_35", "confidence=0.35 ちょうどは「中」(見送らない)", RACE_AT_ARA, _s(skip_confidence="ara")),
    ("boundary_kata_0_50", "confidence=0.50 ちょうどは「堅」(見送らない)", RACE_AT_KATA,
     _s(skip_confidence="chu_or_below")),
    ("combined", "最低確率2%・ランクB以上(skip)・3連単10点・単勝2点", RACE_KATA,
     _s(bet_types={"trifecta": 10, "win": 2}, min_prob=0.02, ev_filter="B_or_above", no_odds="skip")),
    ("combined_skip_wins", "条件で残っても、見送りの条件に当たれば空", RACE_ARA,
     _s(bet_types={"trifecta": 10, "win": 2}, min_prob=0.02, skip_confidence="ara")),
    ("empty_picks", "picks が空の券種は0点", RACE_EMPTY_EXACTA, _s(bet_types={"exacta": 5, "trifecta": 5})),
    ("stake_ignored", "1点あたりの金額は結果に影響しない", RACE_KATA, _s(stake=500)),
    ("min_prob_1pct_win", "単勝3点・最低確率1%", RACE_KATA, _s(bet_types={"win": 3}, min_prob=0.01)),
    ("min_prob_3pct_exacta_ev_a", "2連単10点・最低確率3%・ランクA(prob_only)", RACE_KATA,
     _s(bet_types={"exacta": 10}, min_prob=0.03, ev_filter="A_only")),
]

def _nest(flat: dict[str, float | None]) -> dict[str, Any]:
    """{"1-2-3": 9.2} → {"1": {"2": {"3": 9.2}}}(Worker の応答の形)。"""
    out: dict[str, Any] = {}
    for comb, v in flat.items():
        keys = comb.split("-")
        node = out
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = v
    return out


# 手で作ったレース(RACE_KATA)に当てる Worker のオッズ
W_TRI = {c: round(1.0 / p * f, 1) for c, p, f in zip(TRI, TRI_P, [0.9, 1.3, 1.0, 0.7, 1.25, 1.1, 0.95, 0.6, 1.5, 0.85,
                                                                   1.05, 2.0, 0.5, 1.21, 0.99, 0.79, 1.19, 3.0, 0.8, 1.0])}
W_WIN = {"1": 1.6, "2": 6.5, "3": 7.0, "4": 9.1, "5": 30.0, "6": None}    # 6号艇は欠場で空欄
W_EXA = {c: round(1.0 / p * 0.92, 1) for c, p, _ in EXA}
WORKER_FULL = {"odds": {"trifecta": _nest(W_TRI), "win": _nest(W_WIN), "exacta": _nest(W_EXA),
                        "place": {"1": {"lower_limit": 1.1, "upper_limit": 1.3}}}}
WORKER_TRI_ONLY = {"odds": {"trifecta": _nest(W_TRI)}}     # 2連単・単勝のページは失敗(odds に入らない)
# ランクの境界ちょうど(prob 0.0500 の 2-1-3 に、24.0 → 1.20、20.0 → 1.00、16.0 → 0.80)
BOUNDARY_RACE = {"confidence": 0.45, "picks": {"win": [], "exacta": [], "trifecta": [
    _pick("1-2-3", 0.0800), _pick("2-1-3", 0.0500), _pick("1-3-2", 0.0500), _pick("3-1-2", 0.0500),
    _pick("2-3-1", 0.0500)]}}

ODDS_CASES: list[tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any]]] = [
    ("odds_worker_b_skip", "Worker のオッズ(全券種)で ランクB以上・skip", RACE_NO_ODDS,
     _s(bet_types={"trifecta": 10}, ev_filter="B_or_above", no_odds="skip"), {"worker": WORKER_FULL}),
    ("odds_worker_a_prob_only", "Worker のオッズで ランクA・prob_only。単勝6号艇は空欄(null)→ prob_only で残る",
     RACE_NO_ODDS, _s(bet_types={"win": 3, "trifecta": 5}, ev_filter="A_only"), {"worker": WORKER_FULL}),
    ("odds_worker_null_skip", "Worker の空欄(null)は skip なら除く", RACE_NO_ODDS,
     _s(bet_types={"win": 3}, ev_filter="B_or_above", no_odds="skip"), {"worker": WORKER_FULL}),
    ("odds_worker_overrides_public", "公開JSONの ev_rank より Worker のオッズを優先", RACE_KATA,
     _s(bet_types={"trifecta": 5}, ev_filter="A_only", no_odds="skip"), {"worker": WORKER_FULL}),
    ("odds_worker_partial_falls_back", "Worker は3連単だけ。単勝・2連単は公開JSONの ev_rank を使う", RACE_KATA,
     _s(bet_types={"win": 3, "exacta": 3, "trifecta": 3}, ev_filter="B_or_above", no_odds="skip"),
     {"worker": WORKER_TRI_ONLY}),
    ("odds_worker_partial_no_public", "Worker は3連単だけ・公開の ev_rank も無い → 単勝・2連単はオッズなし(skip で除く)",
     RACE_NO_ODDS, _s(bet_types={"win": 3, "exacta": 3, "trifecta": 3}, ev_filter="B_or_above", no_odds="skip"),
     {"worker": WORKER_TRI_ONLY}),
    ("odds_manual_over_worker", "手入力が Worker より優先(1-2-3 を手入力で A にする)", RACE_KATA,
     _s(bet_types={"trifecta": 5}, ev_filter="A_only", no_odds="skip"),
     {"manual": {"trifecta": {"1-2-3": 15.0}}, "worker": WORKER_FULL}),
    ("odds_manual_over_public", "手入力だけ(Worker なし)。手入力した組合せ以外は公開JSONの ev_rank", RACE_KATA,
     _s(bet_types={"trifecta": 5}, ev_filter="B_or_above", no_odds="skip"),
     {"manual": {"trifecta": {"1-2-3": 8.0, "1-2-4": 30.0}}}),
    ("odds_manual_only_no_public", "オッズの無いレースで手入力だけ。手入力した組合せだけがランクを持つ", RACE_NO_ODDS,
     _s(bet_types={"trifecta": 5, "win": 1}, ev_filter="B_or_above", no_odds="skip"),
     {"manual": {"trifecta": {"1-2-4": 20.0, "1-4-2": 18.0}, "win": {"1": 1.9}}}),
    ("odds_boundary", "ランクの境界ちょうど(1.20 は A、1.00 は B、0.80 は C)", BOUNDARY_RACE,
     _s(bet_types={"trifecta": 5}, ev_filter="B_or_above", no_odds="skip"),
     {"manual": {"trifecta": {"1-2-3": 10.0, "2-1-3": 24.0, "1-3-2": 20.0, "3-1-2": 16.0, "2-3-1": 19.9}}}),
    ("odds_no_filter", "ev_filter=none ならオッズは選び方に影響しない", RACE_KATA, _s(), {"worker": WORKER_FULL}),
    ("odds_with_confidence_skip", "オッズで条件を満たしても、荒の見送りが優先", RACE_ARA,
     _s(skip_confidence="ara", ev_filter="B_or_above"), {"worker": WORKER_FULL}),
    ("odds_with_min_prob", "最低確率2%・ランクB以上(Worker)", RACE_KATA,
     _s(bet_types={"trifecta": 20}, min_prob=0.02, ev_filter="B_or_above", no_odds="skip"), {"worker": WORKER_FULL}),
]


NORMALIZE: list[tuple[str, str, Any]] = [
    ("not_object", "オブジェクトでない", "abc"),
    ("null", "null", None),
    ("empty_object", "空のオブジェクト → 既定で補う", {}),
    ("partial", "一部だけ → 足りないキーを既定で補う", {"min_prob": 0.02}),
    ("k_out_of_range", "単勝 k=4(範囲外)→ 既定", {"bet_types": {"win": 4}}),
    ("k_zero", "k=0 → 既定", {"bet_types": {"trifecta": 0}}),
    ("unknown_bet_type", "未対応の券種 → 既定", {"bet_types": {"trio": 3}}),
    ("empty_bet_types", "券種なし → 既定", {"bet_types": {}}),
    ("bad_min_prob", "選択肢に無い最低確率 → 既定", {"min_prob": 0.04}),
    ("bad_ev_filter", "未知の ev_filter → 既定", {"ev_filter": "C_or_above"}),
    ("bad_stake", "stake=150(100円単位でない)→ 既定", {"stake": 150}),
    ("valid", "正しい設定はそのまま", {"bet_types": {"win": 1, "trifecta": 10}, "min_prob": 0.03,
                                       "skip_confidence": "chu_or_below", "ev_filter": "A_only", "no_odds": "skip",
                                       "stake": 300}),
]


def _race_input(race: dict[str, Any]) -> dict[str, Any]:
    """サンプルのレースから、ルールに必要な項目(confidence と picks の combination・prob・ev_rank)だけを取り出す。"""
    pr = race["prediction"]
    picks = {}
    for bt, items in pr["picks"].items():
        picks[bt] = [{k: it[k] for k in ("combination", "prob", "ev_rank") if k in it} for it in items]
    return {"race_id": race["race_id"], "confidence": pr["confidence"], "picks": picks}


def build_cases(sample_day: dict[str, Any]) -> dict[str, Any]:
    """strategy_cases.json の中身。sample_day は公開JSONの形のサンプル(2026-11-02)。"""
    cases = []
    for cid, desc, race, settings in HANDMADE:
        cases.append({"id": cid, "description": desc, "race": copy.deepcopy(race), "settings": settings,
                      "expected": apply_settings(race, settings)})
    # サンプルの実際のレース(締切前オッズあり・なし、堅・中・荒)
    races = [r for r in iter_races(sample_day) if r["prediction"]]
    with_odds = [r for r in races if r["prediction"].get("odds_snapshot")
                 and r["prediction"]["odds_snapshot"]["status"] in ("ok", "partial")]
    partial = [r for r in with_odds if r["prediction"]["odds_snapshot"]["status"] == "partial"]
    no_odds = [r for r in races if not r["prediction"].get("odds_snapshot")]
    picked = [
        ("sample_ok_ev_b", "サンプル: オッズあり・3連単10点・ランクB以上", with_odds[0],
         _s(bet_types={"trifecta": 10}, ev_filter="B_or_above", no_odds="skip")),
        ("sample_ok_multi", "サンプル: オッズあり・単勝1点・2連単3点・ランクA(prob_only)", with_odds[1],
         _s(bet_types={"win": 1, "exacta": 3}, ev_filter="A_only")),
        ("sample_partial", "サンプル: 3連単だけ取れたレース(単勝・2連単は ev_rank=null)。単勝2点・3連単5点・ランクB以上(skip)",
         partial[0], _s(bet_types={"win": 2, "trifecta": 5}, ev_filter="B_or_above", no_odds="skip")),
        ("sample_no_odds_default", "サンプル: 締切前オッズなし・既定の設定", no_odds[0], _s()),
        ("sample_sample_strategy", "サンプル: サンプルの戦略(confidence_skip alpha=0.35)と同じ設定",
         races[0], _s(skip_confidence="ara")),
    ]
    for cid, desc, race, settings in picked:
        inp = _race_input(race)
        cases.append({"id": cid, "description": desc, "race": inp, "settings": settings,
                      "expected": apply_settings(inp, settings)})
    odds_cases = []
    for cid, desc, race, settings, odds_input in ODDS_CASES:
        ev = evaluate_picks(race, odds_input)
        odds_cases.append({
            "id": cid, "description": desc, "race": copy.deepcopy(race), "settings": settings,
            "odds_input": copy.deepcopy(odds_input),
            "expected": apply_settings(race, settings, odds_input),
            "expected_evaluations": {bt: ev.get(bt, []) for bt in BET_TYPE_ORDER if bt in settings["bet_types"]},
        })
    return {
        "schema_version": 1,
        "generated_by": "python -m src.export.make_sample(src/export/strategy_cases.py)",
        "rules": RULES_TEXT,
        "constants": {
            "bet_type_order": list(BET_TYPE_ORDER),
            "k_ranges": {k: list(v) for k, v in K_RANGES.items()},
            "min_prob_choices": [None, 0.01, 0.02, 0.03, 0.05],
            "skip_confidence_choices": ["none", "ara", "chu_or_below"],
            "ev_filter_choices": ["none", "B_or_above", "A_only"],
            "ev_filter_ranks": {k: list(v) for k, v in EV_FILTER_RANKS.items()},
            "no_odds_choices": ["prob_only", "skip"],
            "confidence_kata": CONFIDENCE_KATA,
            "confidence_ara": CONFIDENCE_ARA,
            "stake_range": [100, 1000, 100],
        },
        "default_settings": copy.deepcopy(DEFAULT_SETTINGS),
        "cases": cases,
        "odds_cases": odds_cases,
        "normalize_cases": [{"id": cid, "description": desc, "input": inp, "expected": normalize_settings(inp)}
                            for cid, desc, inp in NORMALIZE],
    }
