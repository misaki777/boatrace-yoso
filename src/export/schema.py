"""公開JSON(docs/data/)の構造検証。標準ライブラリだけで書く(spec 04 6章)。

使い方:
    errors = validate_day(obj)          # エラーの文字列のリスト(空なら合格)
    assert_valid("day", obj)            # 不合格なら SchemaError を投げる(書き出し前に必ず呼ぶ)

検証の範囲:
- 必須キー・型・値の範囲・並び順(spec 04 の「項目の定義」)
- 相互の整合(race_id と日付・場・R、state と prediction/result、day_summary とレースの合計 など)
- 公開してはいけない項目が無いこと(check_forbidden。spec 04 1章)

未知のキー(spec に無い追加項目)はエラーにしない(後方互換の追加は schema_version を上げない)。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

SCHEMA_VERSION = 1

BET_TYPES_EXPORTED = ("win", "exacta", "trifecta")
ALL_BET_TYPES = ("win", "place", "exacta", "quinella", "quinella_place", "trifecta", "trio")
GRADES = ("SG", "PG1", "G1", "G2", "G3", "一般")
RANKS = ("A1", "A2", "B1", "B2")
RACE_STATES = ("scheduled", "preview", "finished", "canceled", "no_prediction")
DAY_STATUSES = ("predicted", "in_progress", "final")
VARIANTS = ("pre", "preview")
PICK_LIMITS = {"win": 6, "exacta": 10, "trifecta": 20}
SHORT_LABEL_MAX = 14   # strategy.short_label などの最大文字数(スペースも1文字)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")
RACE_ID_RE = re.compile(r"^(\d{8})-(\d{2})-(\d{2})$")
COMB_RE = {
    "win": re.compile(r"^[1-6]$"),
    "exacta": re.compile(r"^[1-6]-[1-6]$"),
    "trifecta": re.compile(r"^[1-6]-[1-6]-[1-6]$"),
}

# 公開してはいけないキー(spec 04 1章)。どの階層にあってもエラーにする
FORBIDDEN_KEYS = {
    "age", "weight", "birthplace", "birthplace_number", "late_count",
    "tilt", "tilt_adjustment", "pv_tilt_adjustment", "propeller_new", "pv_propeller_new",
    "parts_count", "pv_parts_count", "weight_adjustment", "pv_weight_adjustment",
    "odds_table", "odds_lower", "odds_upper", "features", "score",
    "remarks", "refunds",
    # 寄与の生の数値(04 8.3)と、端末内だけに置く個人の記録(購入・印・メモ・予算。08 5.4)
    "contribution", "contrib", "contributions", "shap",
    "purchases", "purchase", "bought_at", "marks", "mark", "memo", "memos", "notes", "note", "budget",
}
# オッズ・期待値の数値のキー(spec 07 3.3)。公開JSONではどこにも出さない。
# ローカルJSON(docs/data-local/)では、picks・recommended・final_odds の要素の中だけ許す
NUMERIC_ODDS_KEYS = ("odds", "ev")
LOCAL_NUMERIC_PATH_RE = re.compile(
    r"\.(picks\.(win|exacta|trifecta)|recommended|final_odds)\[\d+\]\.(odds|ev)$")
EV_RANKS = ("A", "B", "C", "D")
SKIP_REASONS = ("low_confidence", "low_prob", "low_ev")
ODDS_PAGES = ("odds3t", "odds2tf", "oddstf", "odds3f", "oddsk")
ODDS_SNAPSHOT_STATUSES = ("ok", "partial", "late", "not_targeted", "stopped")


class SchemaError(ValueError):
    """検証エラー。errors に個々のメッセージを持つ。"""

    def __init__(self, kind: str, errors: list[str]):
        self.errors = errors
        head = "\n  ".join(errors[:20])
        more = f"\n  ...ほか {len(errors) - 20} 件" if len(errors) > 20 else ""
        super().__init__(f"{kind} のスキーマ検証に失敗しました({len(errors)} 件):\n  {head}{more}")


class _Checker:
    """パス付きでエラーを集める小さな道具。"""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    # --- 型 ---
    def obj(self, v: Any, path: str, required: tuple[str, ...]) -> bool:
        if not isinstance(v, dict):
            self.err(path, f"object が必要です(実際: {type(v).__name__})")
            return False
        ok = True
        for k in required:
            if k not in v:
                self.err(path, f"必須キー {k!r} がありません")
                ok = False
        return ok

    def arr(self, v: Any, path: str, nullable: bool = False) -> bool:
        if v is None and nullable:
            return False
        if not isinstance(v, list):
            self.err(path, f"array が必要です(実際: {type(v).__name__})")
            return False
        return True

    def string(self, v: Any, path: str, nullable: bool = False, choices: tuple[str, ...] | None = None,
               pattern: re.Pattern | None = None) -> bool:
        if v is None:
            if not nullable:
                self.err(path, "null は不可です")
            return False
        if not isinstance(v, str):
            self.err(path, f"文字列が必要です(実際: {type(v).__name__})")
            return False
        if choices is not None and v not in choices:
            self.err(path, f"{v!r} は {choices} のいずれかである必要があります")
            return False
        if pattern is not None and not pattern.match(v):
            self.err(path, f"{v!r} の形式が不正です")
            return False
        return True

    def integer(self, v: Any, path: str, nullable: bool = False, lo: int | None = None,
                hi: int | None = None) -> bool:
        if v is None:
            if not nullable:
                self.err(path, "null は不可です")
            return False
        if isinstance(v, bool) or not isinstance(v, int):
            self.err(path, f"整数が必要です(実際: {type(v).__name__})")
            return False
        if lo is not None and v < lo or hi is not None and v > hi:
            self.err(path, f"{v} が範囲 [{lo}, {hi}] の外です")
            return False
        return True

    def number(self, v: Any, path: str, nullable: bool = False, lo: float | None = None,
               hi: float | None = None) -> bool:
        if v is None:
            if not nullable:
                self.err(path, "null は不可です")
            return False
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.err(path, f"有限の数値が必要です(実際: {v!r})")
            return False
        if lo is not None and v < lo or hi is not None and v > hi:
            self.err(path, f"{v} が範囲 [{lo}, {hi}] の外です")
            return False
        return True

    def prob(self, v: Any, path: str) -> bool:
        return self.number(v, path, lo=0.0, hi=1.0)


# ---------------------------------------------------------------------------
# 共通部品
# ---------------------------------------------------------------------------

SUMMARY_KEYS = ("races_with_result", "bets", "hits", "stake", "return", "roi")


def _check_summary(c: _Checker, s: Any, path: str) -> None:
    """index の summary / days の day_summary。null 可。"""
    if s is None:
        return
    if not c.obj(s, path, SUMMARY_KEYS):
        return
    for k in ("races_with_result", "bets", "hits", "stake", "return"):
        c.integer(s[k], f"{path}.{k}", lo=0)
    c.number(s["roi"], f"{path}.roi", nullable=True, lo=0)
    if isinstance(s.get("hits"), int) and isinstance(s.get("bets"), int) and s["hits"] > s["bets"]:
        c.err(path, "hits が bets より多いです")
    if all(isinstance(s.get(k), int) for k in ("stake", "return")):
        expected = round(s["return"] / s["stake"], 3) if s["stake"] > 0 else None
        if s["roi"] != expected and not (expected is not None and isinstance(s["roi"], (int, float))
                                         and abs(s["roi"] - expected) < 1e-9):
            c.err(f"{path}.roi", f"return/stake と一致しません(期待値 {expected})")


def _check_odds_value(c: _Checker, prob: Any, req: Any, path: str) -> None:
    """required_odds が 1/prob を切り上げた値と矛盾しないか(丸め前の確率がわからないので幅を持たせる)。"""
    if not c.number(req, f"{path}.required_odds", lo=1.0):
        return
    if isinstance(prob, (int, float)) and prob > 0:
        # prob は4桁に丸めてあるので ±0.00005 の幅がある
        lo = 1.0 / (prob + 0.00005) - 0.1 - 1e-6
        hi = 1.0 / max(prob - 0.00005, 1e-9) + 0.1 + 1e-6
        if not lo <= req <= hi:
            c.err(f"{path}.required_odds", f"{req} が 1/prob({1 / prob:.2f}) と合いません")


def _check_short_label(c: _Checker, v: Any, path: str) -> None:
    """短い表示名(任意項目。2026-11-03 追加)。あれば空でない14文字以内の文字列。"""
    if c.string(v, path) and not 0 < len(v) <= SHORT_LABEL_MAX:
        c.err(path, f"1〜{SHORT_LABEL_MAX} 文字にします(実際: {len(v)} 文字)")


def _boats_of(comb: str) -> list[int]:
    return [int(x) for x in re.split(r"[-=]", comb)]


def _check_comb(c: _Checker, bet_type: str, comb: Any, path: str) -> bool:
    if not c.string(comb, path, pattern=COMB_RE.get(bet_type)):
        return False
    boats = _boats_of(comb)
    if len(set(boats)) != len(boats):
        c.err(path, f"{comb!r} に同じ枠番が重複しています")
        return False
    return True


# ---------------------------------------------------------------------------
# index.json
# ---------------------------------------------------------------------------

def validate_index(obj: Any) -> list[str]:
    c = _Checker()
    if not c.obj(obj, "$", ("schema_version", "generated_at", "latest_date", "sources", "dates")):
        return c.errors
    if obj["schema_version"] != SCHEMA_VERSION:
        c.err("$.schema_version", f"{SCHEMA_VERSION} である必要があります")
    c.string(obj["generated_at"], "$.generated_at", pattern=DATETIME_RE)
    c.string(obj["latest_date"], "$.latest_date", nullable=True, pattern=DATE_RE)
    if c.arr(obj["sources"], "$.sources"):
        for i, s in enumerate(obj["sources"]):
            p = f"$.sources[{i}]"
            if c.obj(s, p, ("name", "url", "license")):
                for k in ("name", "url", "license"):
                    c.string(s[k], f"{p}.{k}")
        if not obj["sources"]:
            c.err("$.sources", "出典が空です")
    if c.arr(obj["dates"], "$.dates"):
        prev = None
        for i, d in enumerate(obj["dates"]):
            p = f"$.dates[{i}]"
            if not c.obj(d, p, ("date", "file", "status", "stadium_count", "race_count", "summary")):
                continue
            if c.string(d["date"], f"{p}.date", pattern=DATE_RE):
                if prev is not None and d["date"] >= prev:
                    c.err(f"{p}.date", "dates は新しい順(重複なし)に並べる必要があります")
                prev = d["date"]
                c.string(d["file"], f"{p}.file", choices=(f"days/{d['date'].replace('-', '')}.json",))
            c.string(d["status"], f"{p}.status", choices=DAY_STATUSES)
            c.integer(d["stadium_count"], f"{p}.stadium_count", lo=0, hi=24)
            c.integer(d["race_count"], f"{p}.race_count", lo=0, hi=24 * 12)
            _check_summary(c, d["summary"], f"{p}.summary")
        if obj["dates"] and isinstance(obj["dates"][0], dict) and obj["latest_date"] != obj["dates"][0].get("date"):
            c.err("$.latest_date", "dates の先頭の日付と一致しません")
    c.errors.extend(check_forbidden(obj))
    return c.errors


# ---------------------------------------------------------------------------
# days/YYYYMMDD.json
# ---------------------------------------------------------------------------

BOAT_KEYS = ("entry", "racer_name", "racer_number", "rank", "branch", "national_win_rate", "local_win_rate",
             "motor_top2", "avg_st", "flying_count", "exhibition_time", "exhibition_course",
             "p_win", "p_top2", "p_top3", "p_rank")


def _check_ev_fields(c: _Checker, it: dict, q: str, local: bool, has_odds: bool) -> None:
    """picks・recommended の要素の ev_rank(公開・ローカル)と odds・ev(ローカルだけ)。"""
    if "ev_rank" in it:
        c.string(it["ev_rank"], f"{q}.ev_rank", nullable=True, choices=EV_RANKS)
        if not has_odds:
            c.err(q, "odds_snapshot の status が ok / partial でないのに ev_rank があります")
    if local:
        if "odds" in it or "ev" in it:
            if "ev_rank" not in it:
                c.err(q, "odds・ev があるときは ev_rank も必要です")
            c.number(it.get("odds"), f"{q}.odds", nullable=True, lo=1.0)
            c.number(it.get("ev"), f"{q}.ev", nullable=True, lo=0)
            if (it.get("odds") is None) != (it.get("ev") is None) or (it.get("ev") is None) != (it.get("ev_rank") is None):
                c.err(q, "odds・ev・ev_rank は、そろって値があるか、そろって null にします")
        elif "ev_rank" in it:
            c.err(q, "ローカルJSONでは ev_rank があるとき odds・ev も必要です")


def _check_odds_snapshot(c: _Checker, snap: Any, path: str) -> bool:
    """prediction.odds_snapshot(任意項目。null 可)。オッズの数値を使えるとき(ok / partial)True。"""
    if snap is None:
        return False
    if not c.obj(snap, path, ("fetched_at", "minutes_before_close", "pages", "status")):
        return False
    c.string(snap["fetched_at"], f"{path}.fetched_at", nullable=True, pattern=DATETIME_RE)
    c.number(snap["minutes_before_close"], f"{path}.minutes_before_close", nullable=True, lo=0, hi=24 * 60)
    if c.arr(snap["pages"], f"{path}.pages"):
        for j, pg in enumerate(snap["pages"]):
            c.string(pg, f"{path}.pages[{j}]", choices=ODDS_PAGES)
    ok = c.string(snap["status"], f"{path}.status", choices=ODDS_SNAPSHOT_STATUSES)
    if ok and snap["status"] in ("ok", "partial"):
        if snap["fetched_at"] is None:
            c.err(f"{path}.fetched_at", "オッズを取ったときは取得時刻が必要です")
        return True
    return False


def _check_preview_info(c: _Checker, pi: Any, path: str) -> None:
    if pi is None:
        return
    if not c.obj(pi, path, ("boats", "weather", "wind_speed", "wind_class", "wave_height")):
        return
    if c.arr(pi["boats"], f"{path}.boats"):
        for j, b in enumerate(pi["boats"]):
            q = f"{path}.boats[{j}]"
            if c.obj(b, q, ("entry", "exhibition_time", "exhibition_course", "exhibition_st")):
                c.integer(b["entry"], f"{q}.entry", lo=1, hi=6)
                c.number(b["exhibition_time"], f"{q}.exhibition_time", nullable=True, lo=5, hi=9)
                c.integer(b["exhibition_course"], f"{q}.exhibition_course", nullable=True, lo=1, hi=6)
                c.number(b["exhibition_st"], f"{q}.exhibition_st", nullable=True, lo=-1, hi=1)
    c.string(pi["weather"], f"{path}.weather", nullable=True)
    c.number(pi["wind_speed"], f"{path}.wind_speed", nullable=True, lo=0, hi=50)
    c.string(pi["wind_class"], f"{path}.wind_class", nullable=True)
    c.number(pi["wave_height"], f"{path}.wave_height", nullable=True, lo=0, hi=100)


def _check_prediction(c: _Checker, pr: Any, path: str, local: bool = False) -> None:
    if not c.obj(pr, path, ("variant", "predicted_at", "confidence", "boats", "picks", "recommended")):
        return
    c.string(pr["variant"], f"{path}.variant", choices=VARIANTS)
    c.string(pr["predicted_at"], f"{path}.predicted_at", pattern=DATETIME_RE)
    c.prob(pr["confidence"], f"{path}.confidence")
    has_odds = _check_odds_snapshot(c, pr.get("odds_snapshot"), f"{path}.odds_snapshot")
    # Phase 3a の追加項目(任意。過去日のファイルには無い)
    if "version" in pr:
        c.string(pr["version"], f"{path}.version", choices=VARIANTS)
    if "model_id" in pr:
        c.string(pr["model_id"], f"{path}.model_id", nullable=True)
    if "locked_at" in pr:
        c.string(pr["locked_at"], f"{path}.locked_at", pattern=DATETIME_RE)
    if "skip_reason" in pr:
        c.string(pr["skip_reason"], f"{path}.skip_reason", nullable=True, choices=SKIP_REASONS)
        if pr["skip_reason"] is not None and pr.get("recommended"):
            c.err(f"{path}.skip_reason", "推奨があるのに見送りの理由があります")
    p_wins: list[float] = []
    if c.arr(pr["boats"], f"{path}.boats"):
        boats = pr["boats"]
        if len(boats) != 6:
            c.err(f"{path}.boats", f"6艇必要です(実際: {len(boats)})")
        ranks = []
        for i, b in enumerate(boats):
            p = f"{path}.boats[{i}]"
            if not c.obj(b, p, BOAT_KEYS):
                continue
            if b["entry"] != i + 1:
                c.err(f"{p}.entry", f"枠番の順に並べる必要があります(期待: {i + 1})")
            c.string(b["racer_name"], f"{p}.racer_name", nullable=True)
            c.integer(b["racer_number"], f"{p}.racer_number", nullable=True, lo=1, hi=99999)
            c.string(b["rank"], f"{p}.rank", nullable=True, choices=RANKS)
            c.string(b["branch"], f"{p}.branch", nullable=True)
            c.number(b["national_win_rate"], f"{p}.national_win_rate", nullable=True, lo=0, hi=11)
            c.number(b["local_win_rate"], f"{p}.local_win_rate", nullable=True, lo=0, hi=11)
            c.number(b["motor_top2"], f"{p}.motor_top2", nullable=True, lo=0, hi=100)
            c.number(b["avg_st"], f"{p}.avg_st", nullable=True, lo=0, hi=1)
            c.integer(b["flying_count"], f"{p}.flying_count", nullable=True, lo=0, hi=9)
            c.number(b["exhibition_time"], f"{p}.exhibition_time", nullable=True, lo=5, hi=9)
            c.integer(b["exhibition_course"], f"{p}.exhibition_course", nullable=True, lo=1, hi=6)
            if pr.get("variant") == "pre" and (b["exhibition_time"] is not None or b["exhibition_course"] is not None):
                c.err(p, "前日予想(pre)では展示の値は null にします")
            for k in ("p_win", "p_top2", "p_top3"):
                c.prob(b[k], f"{p}.{k}")
            if all(isinstance(b[k], (int, float)) for k in ("p_win", "p_top2", "p_top3")):
                if not b["p_win"] <= b["p_top2"] + 1e-4 <= b["p_top3"] + 2e-4:
                    c.err(p, "p_win <= p_top2 <= p_top3 になっていません")
                p_wins.append(b["p_win"])
            c.integer(b["p_rank"], f"{p}.p_rank", lo=1, hi=6)
            if "reasons" in b and c.arr(b["reasons"], f"{p}.reasons"):
                if len(b["reasons"]) > 3:
                    c.err(f"{p}.reasons", "3件以下にします")
                for j, rsn in enumerate(b["reasons"]):
                    q = f"{p}.reasons[{j}]"
                    if c.obj(rsn, q, ("label", "value", "direction", "strength")):
                        c.string(rsn["label"], f"{q}.label")
                        c.number(rsn["value"], f"{q}.value", nullable=True)
                        c.string(rsn["direction"], f"{q}.direction", choices=("up", "down"))
                        c.integer(rsn["strength"], f"{q}.strength", lo=1, hi=3)
                        extra = set(rsn) - {"label", "value", "direction", "strength"}
                        if extra:
                            c.err(q, f"reasons に余計な項目があります(寄与の数値は出さない): {sorted(extra)}")
            ranks.append(b["p_rank"])
        if len(ranks) == 6 and sorted(ranks) != [1, 2, 3, 4, 5, 6]:
            c.err(f"{path}.boats", "p_rank が 1〜6 の並べ替えになっていません")
        if len(p_wins) == 6:
            if abs(sum(p_wins) - 1.0) > 1e-3:
                c.err(f"{path}.boats", f"p_win の合計が1になりません({sum(p_wins):.4f})")
            if isinstance(pr["confidence"], (int, float)) and abs(max(p_wins) - pr["confidence"]) > 1e-4:
                c.err(f"{path}.confidence", "p_win の最大値と一致しません")
    if c.obj(pr["picks"], f"{path}.picks", BET_TYPES_EXPORTED):
        for bt in BET_TYPES_EXPORTED:
            p = f"{path}.picks.{bt}"
            if not c.arr(pr["picks"][bt], p):
                continue
            items = pr["picks"][bt]
            if len(items) > PICK_LIMITS[bt]:
                c.err(p, f"{PICK_LIMITS[bt]} 件以下にします(実際: {len(items)})")
            prev = None
            seen = set()
            for j, it in enumerate(items):
                q = f"{p}[{j}]"
                if not c.obj(it, q, ("combination", "prob", "required_odds")):
                    continue
                if _check_comb(c, bt, it["combination"], f"{q}.combination"):
                    if it["combination"] in seen:
                        c.err(f"{q}.combination", "重複しています")
                    seen.add(it["combination"])
                if c.prob(it["prob"], f"{q}.prob"):
                    if prev is not None and it["prob"] > prev:
                        c.err(q, "確率の降順になっていません")
                    prev = it["prob"]
                _check_odds_value(c, it["prob"], it["required_odds"], q)
                _check_ev_fields(c, it, q, local, has_odds)
            if bt == "win" and len(items) == 6:
                total = sum(it.get("prob", 0) for it in items if isinstance(it, dict))
                if abs(total - 1.0) > 1e-3:
                    c.err(p, f"単勝の確率の合計が1になりません({total:.4f})")
    if c.arr(pr["recommended"], f"{path}.recommended"):
        seen = set()
        for j, it in enumerate(pr["recommended"]):
            q = f"{path}.recommended[{j}]"
            if not c.obj(it, q, ("bet_type", "combination", "prob", "required_odds")):
                continue
            if c.string(it["bet_type"], f"{q}.bet_type", choices=BET_TYPES_EXPORTED):
                _check_comb(c, it["bet_type"], it["combination"], f"{q}.combination")
            key = (it["bet_type"], it["combination"])
            if key in seen:
                c.err(q, "推奨買い目が重複しています")
            seen.add(key)
            c.prob(it["prob"], f"{q}.prob")
            _check_odds_value(c, it["prob"], it["required_odds"], q)
            _check_ev_fields(c, it, q, local, has_odds)


def _check_result(c: _Checker, rs: Any, path: str, has_prediction: bool, recommended: list | None,
                  local: bool = False) -> None:
    if not c.obj(rs, path, ("order", "technique", "absent_entries", "payouts", "recommended_outcome", "final_odds")):
        return
    if c.arr(rs["order"], f"{path}.order"):
        if len(rs["order"]) != 3:
            c.err(f"{path}.order", "1〜3着の3要素にします(決まらない位置は null)")
        vals = [x for x in rs["order"] if x is not None]
        for j, x in enumerate(rs["order"]):
            c.integer(x, f"{path}.order[{j}]", nullable=True, lo=1, hi=6)
        if len(set(vals)) != len(vals):
            c.err(f"{path}.order", "同じ枠番が重複しています")
    c.string(rs["technique"], f"{path}.technique", nullable=True)
    absent: list[int] = []
    if c.arr(rs["absent_entries"], f"{path}.absent_entries"):
        for j, x in enumerate(rs["absent_entries"]):
            if c.integer(x, f"{path}.absent_entries[{j}]", lo=1, hi=6):
                absent.append(x)
        if absent != sorted(set(absent)):
            c.err(f"{path}.absent_entries", "昇順・重複なしにします")
        if set(absent) & {x for x in rs.get("order", []) or [] if isinstance(x, int)}:
            c.err(f"{path}.absent_entries", "欠場艇が着順に入っています")
    if c.obj(rs["payouts"], f"{path}.payouts", ()):
        for bt, rows in rs["payouts"].items():
            p = f"{path}.payouts.{bt}"
            if bt not in BET_TYPES_EXPORTED:
                c.err(p, "公開する払戻は win / exacta / trifecta だけです")
                continue
            if not c.arr(rows, p):
                continue
            for j, r in enumerate(rows):
                q = f"{p}[{j}]"
                if not c.obj(r, q, ("combination", "amount")):
                    continue
                if r["combination"] is None:
                    c.string(r.get("label"), f"{q}.label")
                else:
                    _check_comb(c, bt, r["combination"], f"{q}.combination")
                c.integer(r["amount"], f"{q}.amount", lo=0)
    oc = rs["recommended_outcome"]
    if oc is None:
        if has_prediction:
            c.err(f"{path}.recommended_outcome", "予想があるレースでは null にできません")
    elif c.obj(oc, f"{path}.recommended_outcome", ("bets", "hits", "stake", "return")):
        q = f"{path}.recommended_outcome"
        for k in ("bets", "hits", "stake", "return"):
            c.integer(oc[k], f"{q}.{k}", lo=0)
        if "refunded" in oc:
            c.integer(oc["refunded"], f"{q}.refunded", lo=0)
        if isinstance(oc["bets"], int):
            if isinstance(oc["stake"], int) and oc["stake"] != oc["bets"] * 100:
                c.err(f"{q}.stake", "bets × 100 と一致しません")
            if isinstance(oc["hits"], int) and oc["hits"] + oc.get("refunded", 0) > oc["bets"]:
                c.err(q, "hits + refunded が bets を超えています")
            if recommended is not None and oc["bets"] != len(recommended):
                c.err(f"{q}.bets", f"推奨買い目の数({len(recommended)})と一致しません")
    fo = rs["final_odds"]
    if c.arr(fo, f"{path}.final_odds", nullable=True):
        rec_keys = {(r.get("bet_type"), r.get("combination")) for r in (recommended or []) if isinstance(r, dict)}
        for j, it in enumerate(fo):
            q = f"{path}.final_odds[{j}]"
            # 公開JSONは ev_rank だけ(2026-11-03 E-7)。ローカルJSONは odds・ev の数値も持つ
            if not c.obj(it, q, ("bet_type", "combination", "ev_rank") + (("odds", "ev") if local else ())):
                continue
            if (it["bet_type"], it["combination"]) not in rec_keys:
                c.err(q, "final_odds は推奨買い目の分だけにします")
            c.string(it["ev_rank"], f"{q}.ev_rank", choices=EV_RANKS)
            if local:
                c.number(it["odds"], f"{q}.odds", lo=1.0)
                c.number(it["ev"], f"{q}.ev", lo=0)


def validate_day(obj: Any, local: bool = False) -> list[str]:
    """days/YYYYMMDD.json。local=True はローカルJSON(docs/data-local/。オッズ・期待値の数値を許す)。"""
    c = _Checker()
    if not c.obj(obj, "$", ("schema_version", "date", "generated_at", "status", "model", "strategy",
                            "stadiums", "day_summary")):
        return c.errors
    if obj["schema_version"] != SCHEMA_VERSION:
        c.err("$.schema_version", f"{SCHEMA_VERSION} である必要があります")
    date_ok = c.string(obj["date"], "$.date", pattern=DATE_RE)
    ymd = obj["date"].replace("-", "") if date_ok else None
    c.string(obj["generated_at"], "$.generated_at", pattern=DATETIME_RE)
    c.string(obj["status"], "$.status", choices=DAY_STATUSES)
    m = obj["model"]
    if m is not None and c.obj(m, "$.model", ("model_id", "variant", "train_until")):
        c.string(m["model_id"], "$.model.model_id")
        c.string(m["variant"], "$.model.variant", choices=VARIANTS)
        c.string(m["train_until"], "$.model.train_until", nullable=True, pattern=DATE_RE)
    st = obj["strategy"]
    if c.obj(st, "$.strategy", ("name", "bet_type", "description")):
        c.string(st["name"], "$.strategy.name")
        c.string(st["bet_type"], "$.strategy.bet_type", choices=BET_TYPES_EXPORTED)
        c.string(st["description"], "$.strategy.description")
        if "short_label" in st:
            _check_short_label(c, st["short_label"], "$.strategy.short_label")

    all_races: list[dict] = []
    if c.arr(obj["stadiums"], "$.stadiums"):
        prev_st = 0
        for si, s in enumerate(obj["stadiums"]):
            sp = f"$.stadiums[{si}]"
            if not c.obj(s, sp, ("stadium_number", "stadium_name", "title", "grade", "day_number", "races")):
                continue
            if c.integer(s["stadium_number"], f"{sp}.stadium_number", lo=1, hi=24):
                if s["stadium_number"] <= prev_st:
                    c.err(f"{sp}.stadium_number", "場番号の昇順(重複なし)にします")
                prev_st = s["stadium_number"]
            c.string(s["stadium_name"], f"{sp}.stadium_name")
            c.string(s["title"], f"{sp}.title", nullable=True)
            c.string(s["grade"], f"{sp}.grade", nullable=True, choices=GRADES)
            c.integer(s["day_number"], f"{sp}.day_number", nullable=True, lo=1, hi=9)
            if not c.arr(s["races"], f"{sp}.races"):
                continue
            prev_r = 0
            for ri, r in enumerate(s["races"]):
                rp = f"{sp}.races[{ri}]"
                if not c.obj(r, rp, ("race_id", "race_number", "closed_at", "subtitle", "state",
                                     "prediction", "result")):
                    continue
                all_races.append(r)
                if c.integer(r["race_number"], f"{rp}.race_number", lo=1, hi=12):
                    if r["race_number"] <= prev_r:
                        c.err(f"{rp}.race_number", "R番号の昇順(重複なし)にします")
                    prev_r = r["race_number"]
                if c.string(r["race_id"], f"{rp}.race_id", pattern=RACE_ID_RE) and ymd:
                    expected = f"{ymd}-{s.get('stadium_number', 0):02d}-{r.get('race_number', 0):02d}"
                    if r["race_id"] != expected:
                        c.err(f"{rp}.race_id", f"{expected} である必要があります")
                if c.string(r["closed_at"], f"{rp}.closed_at", nullable=True, pattern=DATETIME_RE) and date_ok:
                    if not r["closed_at"].startswith(obj["date"]):
                        c.err(f"{rp}.closed_at", "その日の日付ではありません")
                c.string(r["subtitle"], f"{rp}.subtitle", nullable=True)
                c.string(r["state"], f"{rp}.state", choices=RACE_STATES)
                state = r["state"]
                pr, rs = r["prediction"], r["result"]
                if pr is not None:
                    _check_prediction(c, pr, f"{rp}.prediction", local)
                _check_preview_info(c, r.get("preview_info"), f"{rp}.preview_info")
                rec = pr.get("recommended") if isinstance(pr, dict) else None
                if rs is not None:
                    _check_result(c, rs, f"{rp}.result", pr is not None, rec if isinstance(rec, list) else None,
                                  local)
                # state と prediction / result の整合
                if state == "no_prediction" and pr is not None:
                    c.err(rp, "state=no_prediction のときは prediction を null にします")
                if state in ("scheduled", "preview", "finished") and pr is None:
                    c.err(rp, f"state={state} には prediction が必要です(無ければ no_prediction)")
                if state == "finished" and rs is None:
                    c.err(rp, "state=finished には result が必要です")
                if state in ("scheduled", "preview", "canceled") and rs is not None:
                    c.err(rp, f"state={state} では result を null にします")
                if state == "preview" and isinstance(pr, dict) and pr.get("variant") != "preview":
                    c.err(rp, "state=preview の prediction.variant は preview にします")
    _check_summary(c, obj["day_summary"], "$.day_summary")
    # day_summary はレースの合計と一致すること
    if not c.errors:
        from src.export.build import summarize  # 循環を避けるためここで読む
        expected = summarize(all_races)
        if obj["day_summary"] != expected:
            c.err("$.day_summary", f"レースの合計 {expected} と一致しません")
        if obj["status"] == "predicted" and expected is not None:
            c.err("$.status", "結果のあるレースがあるのに predicted になっています")
    c.errors.extend(check_forbidden(obj, local))
    return c.errors


# ---------------------------------------------------------------------------
# performance.json
# ---------------------------------------------------------------------------

def _check_strategy_stats(c: _Checker, s: Any, q: str) -> None:
    if not c.obj(s, q, ("races", "bets", "roi", "roi_ci95", "hit_rate", "max_losing_streak", "max_drawdown_yen")):
        return
    c.integer(s["races"], f"{q}.races", lo=0)
    c.integer(s["bets"], f"{q}.bets", lo=0)
    c.number(s["roi"], f"{q}.roi", nullable=True, lo=0)
    c.number(s["hit_rate"], f"{q}.hit_rate", nullable=True, lo=0, hi=1)
    c.integer(s["max_losing_streak"], f"{q}.max_losing_streak", nullable=True, lo=0)
    c.integer(s["max_drawdown_yen"], f"{q}.max_drawdown_yen", nullable=True, lo=0)
    ci = s["roi_ci95"]
    if ci is not None and c.arr(ci, f"{q}.roi_ci95") and (len(ci) != 2 or ci[0] > ci[1]):
        c.err(f"{q}.roi_ci95", "[下限, 上限] にします")


def _check_calibration(c: _Checker, rows: Any, path: str) -> None:
    if not c.arr(rows, path):
        return
    for i, r in enumerate(rows):
        q = f"{path}[{i}]"
        if c.obj(r, q, ("bin", "n", "pred_mean", "actual", "ci95")):
            c.string(r["bin"], f"{q}.bin", pattern=re.compile(r"^\d{1,2}0?-\d{2,3}$"))
            c.integer(r["n"], f"{q}.n", lo=1)
            c.prob(r["pred_mean"], f"{q}.pred_mean")
            c.prob(r["actual"], f"{q}.actual")


def _check_perf_3a(c: _Checker, lv: dict, path: str) -> None:
    if "calibration" in lv:
        _check_calibration(c, lv["calibration"], f"{path}.calibration")
    if "strategies" in lv and c.arr(lv["strategies"], f"{path}.strategies"):
        names = []
        for i, s in enumerate(lv["strategies"]):
            q = f"{path}.strategies[{i}]"
            if c.obj(s, q, ("name", "short_label", "is_baseline")):
                names.append(s["name"])
                _check_short_label(c, s["short_label"], f"{q}.short_label")
                _check_strategy_stats(c, s, q)
        for b in ("baseline_win1", "baseline_tri1_top5"):
            if b not in names:
                c.err(f"{path}.strategies", f"基準の戦略 {b} がありません")


def validate_performance(obj: Any) -> list[str]:
    c = _Checker()
    if not c.obj(obj, "$", ("schema_version", "generated_at", "live", "backtest")):
        return c.errors
    if obj["schema_version"] != SCHEMA_VERSION:
        c.err("$.schema_version", f"{SCHEMA_VERSION} である必要があります")
    c.string(obj["generated_at"], "$.generated_at", pattern=DATETIME_RE)
    lv = obj["live"]
    if lv is not None and c.obj(lv, "$.live", ("strategy", "from", "to", "total", "by_month", "by_day")):
        c.string(lv["strategy"], "$.live.strategy")
        if "strategy_label" in lv:
            _check_short_label(c, lv["strategy_label"], "$.live.strategy_label")
        _check_perf_3a(c, lv, "$.live")
        if "sample_note" in lv:
            c.string(lv["sample_note"], "$.live.sample_note", nullable=True, choices=("few",))
        if "by_version" in lv and c.obj(lv["by_version"], "$.live.by_version", ("pre", "preview")):
            for v in ("pre", "preview"):
                if lv["by_version"][v] is not None:
                    _check_strategy_stats(c, lv["by_version"][v], f"$.live.by_version.{v}")
        if "models" in lv and c.arr(lv["models"], "$.live.models"):
            for i, m in enumerate(lv["models"]):
                q = f"$.live.models[{i}]"
                if c.obj(m, q, ("model_id", "from", "to")):
                    c.string(m["model_id"], f"{q}.model_id")
                    c.string(m["from"], f"{q}.from", pattern=DATE_RE)
                    c.string(m["to"], f"{q}.to", pattern=DATE_RE)
        c.string(lv["from"], "$.live.from", pattern=DATE_RE)
        c.string(lv["to"], "$.live.to", pattern=DATE_RE)
        t = lv["total"]
        if c.obj(t, "$.live.total", ("races", "bets", "hits", "stake", "return", "roi", "hit_rate")):
            for k in ("races", "bets", "hits", "stake", "return"):
                c.integer(t[k], f"$.live.total.{k}", lo=0)
            c.number(t["roi"], "$.live.total.roi", nullable=True, lo=0)
            c.number(t["hit_rate"], "$.live.total.hit_rate", nullable=True, lo=0, hi=1)
        if c.arr(lv["by_month"], "$.live.by_month"):
            prev = None
            for i, mth in enumerate(lv["by_month"]):
                p = f"$.live.by_month[{i}]"
                if not c.obj(mth, p, ("month", "races", "stake", "return", "roi")):
                    continue
                if c.string(mth["month"], f"{p}.month", pattern=MONTH_RE):
                    if prev is not None and mth["month"] <= prev:
                        c.err(f"{p}.month", "古い順(重複なし)に並べます")
                    prev = mth["month"]
                for k in ("races", "stake", "return"):
                    c.integer(mth[k], f"{p}.{k}", lo=0)
                c.number(mth["roi"], f"{p}.roi", nullable=True, lo=0)
        if c.arr(lv["by_day"], "$.live.by_day"):
            prev = None
            for i, d in enumerate(lv["by_day"]):
                p = f"$.live.by_day[{i}]"
                if not c.obj(d, p, ("date", "stake", "return", "roi", "cum_roi")):
                    continue
                if c.string(d["date"], f"{p}.date", pattern=DATE_RE):
                    if prev is not None and d["date"] <= prev:
                        c.err(f"{p}.date", "古い順(重複なし)に並べます")
                    prev = d["date"]
                for k in ("stake", "return"):
                    c.integer(d[k], f"{p}.{k}", lo=0)
                c.number(d["roi"], f"{p}.roi", nullable=True, lo=0)
                c.number(d["cum_roi"], f"{p}.cum_roi", nullable=True, lo=0)
    bt = obj["backtest"]
    if isinstance(bt, dict):
        if "calibration" in bt:
            _check_calibration(c, bt["calibration"], "$.backtest.calibration")
        if "by_month" in bt and c.arr(bt["by_month"], "$.backtest.by_month"):
            for i, m in enumerate(bt["by_month"]):
                q = f"$.backtest.by_month[{i}]"
                if c.obj(m, q, ("month", "races", "roi", "roi_ci95")):
                    c.string(m["month"], f"{q}.month", pattern=MONTH_RE)
                    c.integer(m["races"], f"{q}.races", lo=0)
                    c.number(m["roi"], f"{q}.roi", nullable=True, lo=0)
    if bt is not None and c.obj(bt, "$.backtest", ("model_id", "period", "metrics", "strategies")):
        c.string(bt["model_id"], "$.backtest.model_id")
        if c.obj(bt["period"], "$.backtest.period", ("from", "to")):
            c.string(bt["period"]["from"], "$.backtest.period.from", pattern=DATE_RE)
            c.string(bt["period"]["to"], "$.backtest.period.to", pattern=DATE_RE)
        if c.obj(bt["metrics"], "$.backtest.metrics", ()):
            for k, v in bt["metrics"].items():
                c.number(v, f"$.backtest.metrics.{k}", nullable=True)
        if c.arr(bt["strategies"], "$.backtest.strategies"):
            for i, s in enumerate(bt["strategies"]):
                p = f"$.backtest.strategies[{i}]"
                if not c.obj(s, p, ("name", "params", "roi", "roi_ci95", "hit_rate", "leak_free")):
                    continue
                c.string(s["name"], f"{p}.name")
                if "short_label" in s:
                    _check_short_label(c, s["short_label"], f"{p}.short_label")
                c.obj(s["params"], f"{p}.params", ())
                c.number(s["roi"], f"{p}.roi", nullable=True, lo=0)
                c.number(s["hit_rate"], f"{p}.hit_rate", nullable=True, lo=0, hi=1)
                if not isinstance(s["leak_free"], bool):
                    c.err(f"{p}.leak_free", "真偽値が必要です")
                ci = s["roi_ci95"]
                if ci is not None and c.arr(ci, f"{p}.roi_ci95"):
                    if len(ci) != 2 or not all(c.number(x, f"{p}.roi_ci95[]", lo=0) for x in ci):
                        c.err(f"{p}.roi_ci95", "[下限, 上限] の2要素にします")
                    elif ci[0] > ci[1]:
                        c.err(f"{p}.roi_ci95", "下限 > 上限 になっています")
    c.errors.extend(check_forbidden(obj))
    return c.errors


# ---------------------------------------------------------------------------
# 公開してはいけない項目
# ---------------------------------------------------------------------------

def check_forbidden(obj: Any, local: bool = False) -> list[str]:
    """公開してはいけないキーがどの階層にも無いことを確かめる(spec 04 1章、07 1章)。

    - FORBIDDEN_KEYS に含まれるキー
    - `_source` で終わるキー
    - `odds`・`ev`(オッズと期待値の数値): 公開JSONではどこにも出さない。
      local=True(docs/data-local/)では picks・recommended・final_odds の要素の中だけ許す
    """
    errors: list[str] = []

    def walk(v: Any, path: str, parent_key: str | None) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                p = f"{path}.{k}"
                if k in FORBIDDEN_KEYS:
                    errors.append(f"{p}: 公開してはいけない項目です")
                elif k.endswith("_source") or k == "_source":
                    errors.append(f"{p}: _source 文字列は公開しません")
                elif k in NUMERIC_ODDS_KEYS:
                    if not local:
                        errors.append(f"{p}: オッズ・期待値の数値は公開JSONに出しません(ローカルJSONだけ)")
                    elif not LOCAL_NUMERIC_PATH_RE.search(p):
                        errors.append(f"{p}: {k} は picks・recommended・final_odds の要素の中だけで出します")
                walk(x, p, k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]", parent_key)

    walk(obj, "$", None)
    return errors


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

VALIDATORS: dict[str, Callable[[Any], list[str]]] = {
    "index": validate_index,
    "day": validate_day,
    "performance": validate_performance,
}


def assert_valid(kind: str, obj: Any, local: bool = False) -> None:
    """kind = index / day / performance。不合格なら SchemaError。local=True はローカルJSON(day だけ意味がある)。"""
    errors = validate_day(obj, local=local) if kind == "day" else VALIDATORS[kind](obj)
    if errors:
        raise SchemaError(kind, errors)


def validate_index_against_days(index: dict[str, Any], days: dict[str, dict[str, Any]]) -> list[str]:
    """index.json の各日付の概要が、days の中身と一致するかを確かめる。days: 日付 → day の JSON。"""
    from src.export.build import iter_races

    errors = []
    for i, d in enumerate(index.get("dates", [])):
        day = days.get(d["date"])
        p = f"$.dates[{i}]"
        if day is None:
            errors.append(f"{p}: days/{d['date']} が見つかりません")
            continue
        races = list(iter_races(day))
        if d["race_count"] != len(races):
            errors.append(f"{p}.race_count: days と一致しません({len(races)})")
        if d["stadium_count"] != len(day["stadiums"]):
            errors.append(f"{p}.stadium_count: days と一致しません({len(day['stadiums'])})")
        if d["status"] != day["status"]:
            errors.append(f"{p}.status: days と一致しません({day['status']})")
        if d["summary"] != day["day_summary"]:
            errors.append(f"{p}.summary: days の day_summary と一致しません")
    return errors
