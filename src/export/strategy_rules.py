"""推奨買い目の設定(S6)の計算ルール(spec 07 4.2)。Python 版。

フロント(docs/app/)は同じルールを JavaScript で持ち、picks と ev_rank から端末で推奨買い目を
計算し直す。二つの実装がずれないよう、共通のテストケース docs/data/sample/strategy_cases.json を
このモジュールから作り、Python と JavaScript の両方のテストで使う(spec 07 4.1)。

設定(settings)の形(フロントの localStorage `settings.v1` と同じ形を推奨):
    {
      "bet_types": {"trifecta": 5},          # 券種 → 点数 k。単勝 1〜3、2連単 1〜10、3連単 1〜20
      "min_prob": null,                      # null / 0.01 / 0.02 / 0.03 / 0.05
      "skip_confidence": "none",             # "none" / "ara"(荒なら見送り)/ "chu_or_below"(中以下なら見送り)
      "ev_filter": "none",                   # "none" / "B_or_above"(ev_rank が A・B)/ "A_only"
      "no_odds": "prob_only",                # ev_filter を使うとき、ev_rank が無い組合せを
                                             #   "prob_only": 期待値の条件を使わずに残す / "skip": 除く
      "stake": 100                           # 表示用。計算には使わない
    }

適用の順序(券種ごと。券種は win → exacta → trifecta の順に並べて出す):
    1. picks の並び(確率の降順。同じ確率なら export の並びのまま)を保つ
    2. prob < min_prob の組合せを除く(prob は picks の小数4桁の値で比べる)
    3. ev_filter で除く(ev_rank が無い・null の組合せは no_odds に従う)
    4. 上位 k 点
    5. skip_confidence に当たるレースなら、全券種を除く(見送り)。confidence も JSON の小数4桁の値で比べる

数値のオッズを入力に含む場合(E-10、spec 07 9.6)。odds_input:
    {
      "manual": {"trifecta": {"1-2-3": 25.0}, ...},      # 手入力(S7)。券種 → 組合せ → オッズ(小数1桁)
      "worker": {"odds": {...07 2.6 の入れ子...}}       # 中継 Worker の応答(odds の部分だけ使う)
    }
組合せごとに、使う ev_rank を次の優先順位で決める:
    (1) 手入力にその組合せのオッズがある → ev = prob × オッズ でランクを付ける
    (2) Worker の odds にその券種がある(ページが取れた)→ その組合せの値でランク。値が無い(空欄)なら null
    (3) 公開JSONの ev_rank(あれば)
    (4) なし(ev_rank 無し)
ev = prob(picks の小数4桁)× オッズ(小数1桁のまま。丸め直さない)。ランクの境界は A ≥ 1.20、B ≥ 1.00、C ≥ 0.80、他は D。
"""

from __future__ import annotations

import copy
from typing import Any

from src.export.config import EV_RANK_BOUNDS, EV_RANK_LOWEST

BET_TYPE_ORDER = ("win", "exacta", "trifecta")
K_RANGES = {"win": (1, 3), "exacta": (1, 10), "trifecta": (1, 20)}
MIN_PROB_CHOICES = (None, 0.01, 0.02, 0.03, 0.05)
SKIP_CONFIDENCE_CHOICES = ("none", "ara", "chu_or_below")
EV_FILTER_CHOICES = ("none", "B_or_above", "A_only")
NO_ODDS_CHOICES = ("prob_only", "skip")
EV_FILTER_RANKS = {"B_or_above": ("A", "B"), "A_only": ("A",)}

# 堅さの区分(spec 05 3章「堅さの区分」。固定のしきい値)
CONFIDENCE_KATA = 0.50   # 堅: confidence >= 0.50
CONFIDENCE_ARA = 0.35    # 荒: confidence < 0.35(その間が 中)

DEFAULT_SETTINGS: dict[str, Any] = {
    "bet_types": {"trifecta": 5},
    "min_prob": None,
    "skip_confidence": "none",
    "ev_filter": "none",
    "no_odds": "prob_only",
    "stake": 100,
}


def confidence_class(confidence: float) -> str:
    """堅 / 中 / 荒 を "kata" / "chu" / "ara" で返す。"""
    if confidence >= CONFIDENCE_KATA:
        return "kata"
    if confidence >= CONFIDENCE_ARA:
        return "chu"
    return "ara"


def validate_settings(settings: dict[str, Any]) -> list[str]:
    """設定の値が 07 4.2 の選択肢の中にあるか。エラーのリスト(空なら正しい)。"""
    errors = []
    bts = settings.get("bet_types")
    if not isinstance(bts, dict) or not bts:
        errors.append("bet_types は1つ以上の券種が必要です")
    else:
        for bt, k in bts.items():
            if bt not in K_RANGES:
                errors.append(f"未対応の券種です: {bt}")
            elif isinstance(k, bool) or not isinstance(k, int) or not K_RANGES[bt][0] <= k <= K_RANGES[bt][1]:
                errors.append(f"{bt} の点数 {k!r} が範囲 {K_RANGES[bt]} の外です")
    if settings.get("min_prob") not in MIN_PROB_CHOICES:
        errors.append(f"min_prob は {MIN_PROB_CHOICES} のどれかです")
    if settings.get("skip_confidence") not in SKIP_CONFIDENCE_CHOICES:
        errors.append(f"skip_confidence は {SKIP_CONFIDENCE_CHOICES} のどれかです")
    if settings.get("ev_filter") not in EV_FILTER_CHOICES:
        errors.append(f"ev_filter は {EV_FILTER_CHOICES} のどれかです")
    if settings.get("no_odds") not in NO_ODDS_CHOICES:
        errors.append(f"no_odds は {NO_ODDS_CHOICES} のどれかです")
    stake = settings.get("stake", 100)
    if isinstance(stake, bool) or not isinstance(stake, int) or not 100 <= stake <= 1000 or stake % 100:
        errors.append("stake は 100〜1000 の100円単位です")
    return errors


def normalize_settings(settings: Any) -> dict[str, Any]:
    """読めない・壊れている設定は既定に戻す(07 4.2)。足りないキーは既定で補う。"""
    if not isinstance(settings, dict):
        return copy.deepcopy(DEFAULT_SETTINGS)
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    merged.update(settings)
    return merged if not validate_settings(merged) else copy.deepcopy(DEFAULT_SETTINGS)


def _rank(ev: float) -> str:
    for rank, lower in EV_RANK_BOUNDS:
        if ev >= lower:
            return rank
    return EV_RANK_LOWEST


def _worker_lookup(worker_odds: dict[str, Any], bet_type: str, combination: str) -> float | None:
    """Worker の入れ子の odds から1つの組合せのオッズを取り出す(複勝などの下限上限は下限)。"""
    node: Any = worker_odds.get(bet_type)
    for key in combination.replace("=", "-").split("-"):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict):
        node = node.get("lower_limit")
    if isinstance(node, bool) or not isinstance(node, (int, float)) or node <= 0:
        return None
    return float(node)


def evaluate_picks(prediction: dict[str, Any], odds_input: dict[str, Any] | None = None) -> dict[str, list]:
    """picks の各組合せに、使う ev_rank と、その出どころ・オッズ・ev を付けたもの(コピー)を返す。

    要素: {"combination", "prob", "ev_rank"(キーが無い = オッズなし), "source": manual/worker/public/none,
           "odds": float|None, "ev": float|None}(odds・ev は manual・worker のときだけ値がある)
    """
    manual = (odds_input or {}).get("manual") or {}
    worker = ((odds_input or {}).get("worker") or {}).get("odds") or {}
    out: dict[str, list] = {}
    for bt, items in prediction["picks"].items():
        rows = []
        for it in items:
            row = {"combination": it["combination"], "prob": it["prob"]}
            m = (manual.get(bt) or {}).get(it["combination"])
            if m is not None:
                ev = it["prob"] * float(m)
                row.update(source="manual", odds=float(m), ev=ev, ev_rank=_rank(ev))
            elif bt in worker:
                w = _worker_lookup(worker, bt, it["combination"])
                ev = it["prob"] * w if w is not None else None
                row.update(source="worker", odds=w, ev=ev, ev_rank=_rank(ev) if ev is not None else None)
            elif "ev_rank" in it:
                row.update(source="public", odds=None, ev=None, ev_rank=it["ev_rank"])
            else:
                row.update(source="none", odds=None, ev=None)
            rows.append(row)
        out[bt] = rows
    return out


def apply_settings(prediction: dict[str, Any], settings: dict[str, Any],
                   odds_input: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """prediction(confidence と picks。picks の要素は ev_rank を持つことがある)から推奨買い目を選ぶ。

    odds_input を渡すと、手入力・Worker のオッズで ev_rank を計算し直してから選ぶ(優先順位はモジュールの説明)。
    戻り値: [{"bet_type": ..., "combination": ...}, ...](券種の順、各券種の中は picks の順)
    """
    if odds_input:
        prediction = {"confidence": prediction["confidence"], "picks": evaluate_picks(prediction, odds_input)}
    errors = validate_settings(settings)
    if errors:
        raise ValueError("; ".join(errors))
    skip = settings["skip_confidence"]
    cls = confidence_class(float(prediction["confidence"]))
    if (skip == "ara" and cls == "ara") or (skip == "chu_or_below" and cls in ("ara", "chu")):
        return []
    min_prob = settings["min_prob"]
    ev_filter = settings["ev_filter"]
    allowed_ranks = EV_FILTER_RANKS.get(ev_filter)
    out: list[dict[str, str]] = []
    for bt in BET_TYPE_ORDER:
        if bt not in settings["bet_types"]:
            continue
        k = settings["bet_types"][bt]
        chosen = []
        for it in prediction["picks"].get(bt, []):
            if min_prob is not None and it["prob"] < min_prob:
                continue
            if allowed_ranks is not None:
                rank = it.get("ev_rank")
                if rank is None:
                    if settings["no_odds"] == "skip":
                        continue
                elif rank not in allowed_ranks:
                    continue
            chosen.append({"bet_type": bt, "combination": it["combination"]})
            if len(chosen) >= k:
                break
        out.extend(chosen)
    return out


def settings_for_strategy(strategy: dict[str, Any]) -> dict[str, Any] | None:
    """export の戦略(days の strategy)と同じ結果になる設定。対応する設定が無ければ None。

    - top_k(bet_type, k)                 → {bet_types: {bet_type: k}}
    - confidence_skip(alpha=0.35 のとき) → 上に skip_confidence="ara" を足したもの
    """
    bt, k = strategy.get("bet_type"), strategy.get("k")
    if bt not in K_RANGES or not isinstance(k, int) or not K_RANGES[bt][0] <= k <= K_RANGES[bt][1]:
        return None
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s["bet_types"] = {bt: k}
    if strategy["name"] == "top_k":
        return s
    if strategy["name"] == "confidence_skip" and float(strategy.get("alpha", -1)) == CONFIDENCE_ARA:
        s["skip_confidence"] = "ara"
        return s
    return None
