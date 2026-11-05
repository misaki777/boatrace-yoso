"""公開JSONの部品を組み立てる関数(標準ライブラリのみ)。

export_day(実データ)と make_sample(架空データ)の両方がこのモジュールを使う。
入力は素の Python の値(dict / list / float)で、pandas には依存しない。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from src.export.config import DEFAULT_ODDS_PAGES, EV_RANK_BOUNDS, EV_RANK_LOWEST, ODDS_PAGES
from src.export.probs import EXPORT_BET_TYPES, combination_probs, top2_top3

JST = timezone(timedelta(hours=9))

SCHEMA_VERSION = 1

# 確率上位として出す件数(spec 04 4章 picks)
PICK_LIMITS = {"win": 6, "exacta": 10, "trifecta": 20}

# 1点あたりの賭け金(円)。回収率はすべて 1点100円 と仮定したシミュレーション
STAKE_PER_BET = 100

# 既定の戦略(spec 06 U2: 3連単の上位5点を仮の既定とする)
DEFAULT_STRATEGY = {"name": "top_k", "bet_type": "trifecta", "k": 5, "short_label": "3連単 上位5点",
                    "description": "3連単 確率上位5点"}

# short_label の最大文字数(spec 04 4章。スペースも1文字と数える)
SHORT_LABEL_MAX = 14
BET_TYPE_LABELS = {"win": "単勝", "place": "複勝", "exacta": "2連単", "quinella": "2連複",
                   "quinella_place": "拡連複", "trifecta": "3連単", "trio": "3連複"}


def _num(x: Any) -> str:
    """表示用の数値(余計な0を付けない)。"""
    return f"{float(x):g}"


def strategy_short_label(name: str, params: dict[str, Any]) -> str:
    """戦略名とパラメータから短い表示名を作る(days の strategy、performance の live / backtest で共通)。

    例: top_k → 「3連単 上位5点」、confidence_skip → 「3連単 上位5点・荒れ見送り」、
        prob_threshold → 「3連単 確率5%以上」、ev_final → 「3連単 EV1.2以上」
    """
    bt = BET_TYPE_LABELS.get(params.get("bet_type", ""), str(params.get("bet_type", "")))
    if name == "top_k":
        label = f"{bt} 上位{params['k']}点"
    elif name == "confidence_skip":
        alpha = params.get("alpha")
        if alpha is None or float(alpha) == 0.35:   # 「荒」の境界(05 3章)と同じとき
            label = f"{bt} 上位{params['k']}点・荒れ見送り"
        else:
            label = f"{bt}{params['k']}点・本命{_num(float(alpha) * 100)}%以上"
    elif name == "prob_threshold":
        label = f"{bt} 確率{_num(float(params['theta']) * 100)}%以上"
    elif name == "ev_final":
        label = f"{bt} EV{_num(params['tau'])}以上"
    elif name == "ev_final_haircut":
        label = f"{bt} 補正EV{_num(params['tau'])}以上"
    else:
        label = f"{bt} {name}"
    return label[:SHORT_LABEL_MAX]


def with_short_label(strategy: dict[str, Any]) -> dict[str, Any]:
    """strategy の dict に short_label を足したもの(既にあればそのまま)。キーの順は description の前。"""
    if "short_label" in strategy:
        return dict(strategy)
    out = {k: v for k, v in strategy.items() if k != "description"}
    out["short_label"] = strategy_short_label(strategy["name"], strategy)
    if "description" in strategy:
        out["description"] = strategy["description"]
    return out


# ---------------------------------------------------------------------------
# 丸め・時刻
# ---------------------------------------------------------------------------

def round_prob(x: float) -> float:
    """確率は小数4桁(spec 04 1章)。"""
    return round(float(x), 4)


def round_odds(x: float) -> float:
    """オッズは小数1桁。"""
    return round(float(x), 1)


def required_odds(prob: float) -> float | None:
    """必要オッズ = 1/prob を小数1位で切り上げる(spec 03 5章)。prob が0以下なら None。"""
    if prob <= 0:
        return None
    # src/model/combinations.required_odds_display と同じ式(誤差で 20.0 が 20.1 にならないよう9桁で丸める)
    return math.ceil(round(10.0 / prob, 9)) / 10.0


def to_jst_iso(dt: datetime) -> str:
    """JST の ISO 8601(+09:00 付き、秒まで)にする。naive な datetime は JST とみなす。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST).replace(microsecond=0).isoformat()


def roi(stake: int, ret: int) -> float | None:
    """回収率(払戻/投資)。投資が0なら None。小数3桁。"""
    return round(ret / stake, 3) if stake > 0 else None


# ---------------------------------------------------------------------------
# 予想(prediction)
# ---------------------------------------------------------------------------

def sorted_picks(probs: dict[str, float], limit: int) -> list[dict[str, Any]]:
    """確率の降順(同じ確率なら組合せの文字列順)に上位 limit 件を出す。確率0は除く。"""
    items = [(c, p) for c, p in probs.items() if p > 0]
    items.sort(key=lambda cp: (-cp[1], cp[0]))
    return [
        {"combination": c, "prob": round_prob(p), "required_odds": required_odds(p)}
        for c, p in items[:limit]
    ]


def select_recommended(strategy: dict[str, Any], probs: dict[str, dict[str, float]],
                       confidence: float) -> list[dict[str, Any]]:
    """戦略に従って推奨買い目を選ぶ。0件(見送り)のこともある。

    対応する戦略(spec 03 6章のうち、当日に使えるリークなしのもの):
    - top_k:            bet_type の確率上位 k 点
    - prob_threshold:   bet_type で prob >= theta の組合せ
    - confidence_skip:  confidence >= alpha のレースだけ top_k
    """
    name = strategy["name"]
    bet_type = strategy["bet_type"]
    table = probs[bet_type]
    ranked = sorted(((c, p) for c, p in table.items() if p > 0), key=lambda cp: (-cp[1], cp[0]))
    if name == "top_k":
        chosen = ranked[: int(strategy["k"])]
    elif name == "prob_threshold":
        chosen = [(c, p) for c, p in ranked if p >= float(strategy["theta"])]
    elif name == "confidence_skip":
        # 公開JSONの confidence(小数4桁)で比べる(フロントの設定画面の計算と一致させるため)
        chosen = ranked[: int(strategy["k"])] if round_prob(confidence) >= float(strategy["alpha"]) else []
    else:
        raise ValueError(f"未対応の戦略です: {name}")
    return [
        {"bet_type": bet_type, "combination": c, "prob": round_prob(p), "required_odds": required_odds(p)}
        for c, p in chosen
    ]


def build_prediction(boats: Sequence[dict[str, Any]], p_win: Sequence[float], *, variant: str,
                     predicted_at: str, strategy: dict[str, Any],
                     use_model_combinations: bool = True, gamma2: float = 1.0,
                     gamma3: float = 1.0, model_id: str | None = None, locked_at: str | None = None,
                     reasons: Sequence[list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """1レース分の prediction を作る。

    boats: 枠番順の6艇。各要素は選手情報(entry, racer_name, racer_number, rank, branch,
           national_win_rate, local_win_rate, motor_top2, avg_st, flying_count,
           exhibition_time, exhibition_course)。確率の列はここで足す。
    p_win: 枠番順の1着確率(合計1)。
    """
    if len(boats) != 6 or len(p_win) != 6:
        raise ValueError("6艇分の情報と確率が必要です")
    probs = combination_probs(p_win, use_model=use_model_combinations, gamma2=gamma2, gamma3=gamma3)
    p_top2, p_top3 = top2_top3(probs["trifecta"])
    # p_rank: p_win の降順の順位(同じ値なら枠番の小さい方を上にする)
    order = sorted(range(6), key=lambda i: (-p_win[i], i))
    p_rank = [0] * 6
    for rank, i in enumerate(order, start=1):
        p_rank[i] = rank
    out_boats = []
    for i, b in enumerate(boats):
        row = dict(b)
        row["p_win"] = round_prob(p_win[i])
        row["p_top2"] = round_prob(p_top2[i])
        row["p_top3"] = round_prob(p_top3[i])
        row["p_rank"] = p_rank[i]
        if reasons is not None:
            row["reasons"] = list(reasons[i])
        out_boats.append(row)
    confidence = max(p_win)
    recommended = select_recommended(strategy, probs, confidence)
    return {
        "variant": variant,
        "version": variant,
        "model_id": model_id,
        "predicted_at": predicted_at,
        "locked_at": locked_at or predicted_at,
        "confidence": round_prob(confidence),
        "boats": out_boats,
        "picks": {bt: sorted_picks(probs[bt], PICK_LIMITS[bt]) for bt in EXPORT_BET_TYPES},
        "recommended": recommended,
        "skip_reason": skip_reason(strategy, confidence, recommended),
        "odds_snapshot": None,
    }


def skip_reason(strategy: dict[str, Any], confidence: float, recommended: list) -> str | None:
    """推奨が0件のときの理由(04 8.1、F12): low_confidence / low_prob / low_ev。推奨があれば None。"""
    if recommended:
        return None
    name = strategy["name"]
    if name == "confidence_skip":
        return "low_confidence"
    if name == "prob_threshold":
        return "low_prob"
    if name.startswith("ev"):
        return "low_ev"
    return None


# 寄与(logit 上の値)の絶対値を 1〜3 の段階にする境界。生の数値は公開しない(04 8.3)
REASON_STRENGTH_BOUNDS = ((0.5, 3), (0.2, 2))


def make_reasons(contribs: Sequence[tuple[str, float, Any]]) -> list[dict[str, Any]]:
    """[(表示ラベル, 寄与, 値)] → reasons。direction は寄与の符号、strength は絶対値の段階。"""
    out = []
    for label, c, value in contribs:
        strength = next((st for lo, st in REASON_STRENGTH_BOUNDS if abs(c) >= lo), 1)
        out.append({"label": label, "value": value, "direction": "up" if c >= 0 else "down", "strength": strength})
    return out


# ---------------------------------------------------------------------------
# 締切前オッズと期待値(spec 07 3章)
# ---------------------------------------------------------------------------

def ev_rank(ev: float | None) -> str | None:
    """期待値のランク A〜D(EV_RANK_BOUNDS)。ev が None なら None。"""
    if ev is None:
        return None
    for rank, lower in EV_RANK_BOUNDS:
        if ev >= lower:
            return rank
    return EV_RANK_LOWEST


def expected_value(prob: float, odds: float | None) -> float | None:
    """ev = prob × odds。prob は公開JSONの値(小数4桁)、odds は小数1桁に丸めた値を使う。

    丸めた値どうしで計算するので、ローカルJSONを読む側でも同じ値を再現できる。
    ランクは小数2桁に丸める前の値で決める。
    """
    if odds is None:
        return None
    return round_prob(prob) * round_odds(odds)


def flatten_odds(odds: dict[str, Any] | None) -> dict[tuple[str, str], float]:
    """spec 07 2.6 の入れ子の odds を {(券種, 組合せ): オッズ} にする。

    3連単・2連単・単勝は "1-2-3" / "1-2" / "1"。複勝・拡連複は下限(保守的にする。07 3.1)。
    数値でない値(欠場の空欄など)は捨てる。
    """
    out: dict[tuple[str, str], float] = {}
    if not odds:
        return out
    ordered = {"win", "place", "exacta", "trifecta"}

    def walk(bet_type: str, node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict) and ("lower_limit" in node or "upper_limit" in node):
            node = node.get("lower_limit")
        if isinstance(node, dict):
            for k, v in node.items():
                walk(bet_type, v, path + (str(k),))
            return
        if isinstance(node, bool) or not isinstance(node, (int, float)) or not math.isfinite(node) or node <= 0:
            return
        sep = "-" if bet_type in ordered else "="
        out[(bet_type, sep.join(path))] = float(node)

    for bt, node in odds.items():
        walk(str(bt), node, ())
    return out


def odds_snapshot_meta(snapshot: dict[str, Any]) -> dict[str, Any]:
    """締切前オッズの解析JSON(07 2.6)から、prediction.odds_snapshot に出す項目を作る。

    status: 入力に status があればそれを使う(収集担当がログから決める: ok / partial / late、
            export が足す stopped)。無ければ、オッズの数値が1つでもあれば ok、
            pages_requested(無ければ既定の3ページ)のうち欠けがあれば partial。
    pages:  取れたページの一覧。入力の pages が {ページ: {"ok": ...}} の dict(収集担当の形)なら ok のもの、
            リストならそのまま、無ければ入っている券種から推定する。
    """
    flat = flatten_odds(snapshot.get("odds"))
    bet_types = {bt for bt, _ in flat}
    pages = snapshot.get("pages")
    if isinstance(pages, dict):
        # 収集担当の形: {"odds3t": {"ok": true, "fetched_at": ...}, ...}
        pages = [p for p in ODDS_PAGES if (pages.get(p) or {}).get("ok")]
    if pages is None:
        pages = [p for p, bts in ODDS_PAGES.items() if bet_types & set(bts)]
    status = snapshot.get("status")
    if status is None:
        if not flat:
            status = "late"
        elif set(snapshot.get("pages_requested") or DEFAULT_ODDS_PAGES) - set(pages):
            status = "partial"
        else:
            status = "ok"
    mbc = snapshot.get("minutes_before_close")
    fetched = snapshot.get("fetched_at")
    if mbc is None and fetched and snapshot.get("closed_at"):
        delta = datetime.fromisoformat(snapshot["closed_at"]) - datetime.fromisoformat(fetched)
        mbc = delta.total_seconds() / 60.0
    return {
        "fetched_at": to_jst_iso(datetime.fromisoformat(fetched)) if fetched else None,
        "minutes_before_close": round(float(mbc), 1) if mbc is not None else None,
        "pages": list(pages),
        "status": status,
    }


def apply_odds(prediction: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    """prediction に締切前オッズを反映する(その場で書き換える。ローカルJSONの形)。

    - odds_snapshot を入れる(snapshot が None なら null のまま)
    - オッズの数値があるとき(status が ok / partial)、picks と recommended の各要素に
      ev_rank・odds・ev を足す。オッズが無い組合せは3つとも null。
      公開JSONでは strip_numeric_odds で odds・ev を外す。
    """
    prediction["odds_snapshot"] = None
    for items in list(prediction["picks"].values()) + [prediction["recommended"]]:
        for it in items:
            for k in ("ev_rank", "odds", "ev"):
                it.pop(k, None)
    if snapshot is None:
        return
    meta = odds_snapshot_meta(snapshot)
    prediction["odds_snapshot"] = meta
    if meta["status"] not in ("ok", "partial"):
        return
    flat = flatten_odds(snapshot.get("odds"))
    for bt, items in prediction["picks"].items():
        for it in items:
            _set_ev(it, flat.get((bt, it["combination"])))
    for it in prediction["recommended"]:
        _set_ev(it, flat.get((it["bet_type"], it["combination"])))


def _set_ev(item: dict[str, Any], odds: float | None) -> None:
    ev = expected_value(item["prob"], odds)
    item["ev_rank"] = ev_rank(ev)
    item["odds"] = round_odds(odds) if odds is not None else None
    item["ev"] = round(ev, 2) if ev is not None else None


NUMERIC_ODDS_KEYS = ("odds", "ev")


def strip_numeric_odds(day: dict[str, Any]) -> dict[str, Any]:
    """ローカルJSONの形の day から、オッズと期待値の数値(odds・ev)を外した公開JSONの形を返す(コピー)。"""
    import copy

    out = copy.deepcopy(day)
    for st in out["stadiums"]:
        for race in st["races"]:
            pr = race.get("prediction")
            if pr:
                for items in list(pr["picks"].values()) + [pr["recommended"]]:
                    for it in items:
                        for k in NUMERIC_ODDS_KEYS:
                            it.pop(k, None)
            rs = race.get("result")
            if rs and rs.get("final_odds"):
                for it in rs["final_odds"]:
                    for k in NUMERIC_ODDS_KEYS:
                        it.pop(k, None)
    return out


# ---------------------------------------------------------------------------
# 結果(result)
# ---------------------------------------------------------------------------

def compute_outcome(recommended: Iterable[dict[str, Any]], payouts: dict[str, list[dict[str, Any]]],
                    absent_entries: Iterable[int]) -> dict[str, int]:
    """推奨買い目を1点100円で買ったと仮定した投資・払戻。

    - 欠場艇を含む買い目は返還(100円を払戻として計上。hits には数えない)。
    - 払戻の combination が null の行は、その券種のすべての買い目に適用する
      (特払・不成立。amount は100円あたりの額)。
    - 返還した点数は refunded に入れる(spec 04 への追加項目。任意)。
    """
    absent = set(absent_entries)
    bets = hits = ret = refunded = 0
    for rec in recommended:
        bets += 1
        boats = {int(x) for x in rec["combination"].replace("=", "-").split("-")}
        if boats & absent:
            refunded += 1
            ret += STAKE_PER_BET
            continue
        rows = payouts.get(rec["bet_type"], [])
        special = [r for r in rows if r.get("combination") is None]
        matched = [r for r in rows if r.get("combination") == rec["combination"]]
        if matched:
            hits += 1
            ret += sum(int(r["amount"]) for r in matched)
        elif special:
            ret += sum(int(r["amount"]) for r in special)
    out = {"bets": bets, "hits": hits, "stake": bets * STAKE_PER_BET, "return": ret}
    if refunded:
        out["refunded"] = refunded
    return out


def build_result(*, order: Sequence[int | None], technique: str | None, absent_entries: Sequence[int],
                 payouts: dict[str, list[dict[str, Any]]], recommended: Sequence[dict[str, Any]] | None,
                 final_odds: dict[tuple[str, str], float] | None = None) -> dict[str, Any]:
    """1レース分の result を作る。

    payouts: 券種 → [{"combination", "amount", "label"(任意)}]。win / exacta / trifecta 以外は捨てる。
    recommended: 予想が無いレースは None(recommended_outcome も null になる)。
    final_odds: (bet_type, combination) → 確定オッズ。推奨買い目の分だけを取り出して入れる。
               各要素は ev_rank・odds・ev を持つ(ローカルJSONの形。公開JSONでは strip_numeric_odds で数値を外す)。
    """
    pub_payouts: dict[str, list[dict[str, Any]]] = {}
    for bt in EXPORT_BET_TYPES:
        rows = []
        for r in payouts.get(bt, []):
            item: dict[str, Any] = {"combination": r.get("combination"), "amount": int(r["amount"])}
            if r.get("label"):
                item["label"] = r["label"]
            rows.append(item)
        if rows:
            pub_payouts[bt] = rows
    outcome = None
    fo = None
    if recommended is not None:
        outcome = compute_outcome(recommended, pub_payouts, absent_entries)
        if final_odds is not None:
            fo = []
            for rec in recommended:
                odds = final_odds.get((rec["bet_type"], rec["combination"]))
                if odds is None:
                    continue
                ev = expected_value(rec["prob"], odds)
                fo.append({"bet_type": rec["bet_type"], "combination": rec["combination"],
                           "ev_rank": ev_rank(ev), "odds": round_odds(odds), "ev": round(ev, 2)})
    return {
        "order": list(order),
        "technique": technique,
        "absent_entries": sorted(int(x) for x in absent_entries),
        "payouts": pub_payouts,
        "recommended_outcome": outcome,
        "final_odds": fo,
    }


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def summarize(races: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """レース(days の races[] の要素)から day_summary / index の summary を作る。結果が無ければ None。

    - races_with_result: result が入っているレース数
    - bets / hits / stake / return: recommended_outcome の合計
    """
    n_result = bets = hits = stake = ret = 0
    for race in races:
        res = race.get("result")
        if res is None:
            continue
        n_result += 1
        oc = res.get("recommended_outcome")
        if not oc:
            continue
        bets += oc["bets"]
        hits += oc["hits"]
        stake += oc["stake"]
        ret += oc["return"]
    if n_result == 0:
        return None
    return {"races_with_result": n_result, "bets": bets, "hits": hits, "stake": stake,
            "return": ret, "roi": roi(stake, ret)}


def iter_races(day: dict[str, Any]):
    """days の JSON から全レースを順に返す。"""
    for st in day["stadiums"]:
        yield from st["races"]


def day_status(day: dict[str, Any], has_final_odds: bool) -> str:
    """predicted / in_progress / final を決める。

    final: 中止を除く全レースに結果があり、確定オッズを反映済み。
    """
    races = list(iter_races(day))
    with_result = [r for r in races if r.get("result") is not None]
    open_races = [r for r in races if r["state"] != "canceled" and r.get("result") is None]
    if not with_result:
        return "predicted"
    if not open_races and has_final_odds:
        return "final"
    return "in_progress"
