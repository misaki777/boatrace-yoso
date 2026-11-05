"""公式サイト(boatrace.jp)から締切前オッズを取得し、data/raw/official_odds/ に保存する。

使い方:
    python -m src.collect.official_odds --race 20261103-02-01            # 1レースだけ今すぐ取る(確認用)
    python -m src.collect.official_odds --date 2026-11-03 --loop         # 常駐ループ(当日分を順に取る)
    python -m src.collect.official_odds --loop --stadiums 2,12 --pages odds3t --minutes-before 10
    python -m src.collect.official_odds --dry-run --date 2026-11-03      # 取得予定を表示するだけ
    python -m src.collect.official_odds --window-minutes 15              # 一括取得(今から15分以内に締切の未取得分を取って終わる)

仕様は spec/07_official_odds_and_settings.md 2章。主な決まり:
- 同時接続は1本。応答が終わってから --sleep 秒(1.5秒以上が既定、1.0未満はエラー)空ける。
- 1レース1回、締切の N 分前(--minutes-before、既定5)に取る。締切2分前を過ぎたら取らずに late とする。
- リトライは 5xx・タイムアウト・接続エラーだけ、1回まで。
- HTTP 403・429、または3回続けて失敗したら、その日の取得をすべて止める(_stopped.json を置く。再開は --resume)。
- 1日のリクエスト数は --max-requests-per-day(既定600)まで(取得ログから当日分を数える)。
- 締切時刻は openapi の当日ファイル(data/raw/openapi/)の closed_at。ループ中は30分ごとに取り直す
  (一括取得モードは起動時に1回。既存の fetch の「10分以内なら取り直さない」に従う)。
- 画面への出力にはオッズの数値を出さない(件数・状態・時刻だけ)。公開リポジトリの Actions のログに出さないため。
- 終了コード: 0=正常、1=停止した/失敗があった、2=引数エラー
"""

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from src.collect import raw_io
from src.collect.fetch import JST, HttpResult, http_get, now_jst

SOURCE = "official_odds"
URL_TEMPLATE = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={ymd}"
DEFAULT_PAGES = ("odds3t", "odds2tf", "oddstf")
ALL_PAGES = ("odds3t", "odds2tf", "oddstf", "odds3f", "oddsk")
DEFAULT_MINUTES_BEFORE = 5
MIN_MINUTES_BEFORE, MAX_MINUTES_BEFORE = 3, 15
LATE_MINUTES = 2  # 締切のこの分数前を過ぎたら取らない
MIN_SLEEP_SEC = 1.0
DEFAULT_SLEEP_SEC = 1.5
DEFAULT_MAX_REQUESTS_PER_DAY = 600
RETRY_WAIT_SEC = 3.0  # リトライ前の待ち時間(1回だけ)
MAX_CONSECUTIVE_FAILURES = 3
PROGRAM_REFRESH = timedelta(minutes=30)  # openapi 当日分の取り直し間隔
BOATS = (1, 2, 3, 4, 5, 6)
NOT_TARGETED = "not_targeted"  # --stadiums・--race-ids-file の絞り込みで取らないレース
MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES = 3, 60  # 一括取得モードの窓


# =====================================================================
# HTML の解析
# =====================================================================


class OddsParseError(ValueError):
    """HTML の構造が想定と違う(セル数が違うなど)。間違った値を出さないために投げる。"""


class _OddsHTMLParser(HTMLParser):
    """td.oddsPoint の文字列と、オッズ時点の表示の文字列を文書の順に集める。

    オッズ時点の表示: 締切前は p.tab4_refreshText(「オッズ更新時間 17:07」。2026-11-03 に実物で確認)、
    締切後は p.tab4_time(「締切時オッズ」。PM の確認)。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.time_labels: list[str] = []
        self._cell: list[str] | None = None
        self._time: list[str] | None = None
        self._time_depth = 0

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag in ("td", "tr", "th") and self._cell is not None:
            # 閉じタグが省略されていた場合
            self._close_cell()
        if tag == "td" and "oddsPoint" in classes:
            self._cell = []
        if self._time is not None and tag == "p":
            self._time_depth += 1
        elif tag == "p" and ("tab4_time" in classes or "tab4_refreshText" in classes):
            self._time = []
            self._time_depth = 1

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._close_cell()
        if tag == "p" and self._time is not None:
            self._time_depth -= 1
            if self._time_depth <= 0:
                self.time_labels.append(" ".join("".join(self._time).split()))
                self._time = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._time is not None:
            self._time.append(data)

    def _close_cell(self):
        self.cells.append("".join(self._cell or []).strip())
        self._cell = None


def extract_cells(html: str) -> tuple[list[str], str | None]:
    """HTML から (oddsPoint セルの文字列の一覧, オッズ時点の表示) を返す。"""
    parser = _OddsHTMLParser()
    parser.feed(html)
    parser.close()
    if parser._cell is not None:
        parser._close_cell()
    label = parser.time_labels[0] if parser.time_labels else None
    return parser.cells, label


def to_odds(text: str) -> float | None:
    """'27.5' → 27.5。数値でないもの(空欄・'-'・'欠場' など)と 0.0 は None。

    締切前は票が入っていない組合せが '0.0' と表示される(2026-11-03 に実物で確認)。
    """
    t = text.replace(",", "").strip()
    try:
        value = float(t)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def to_range(text: str) -> dict | None:
    """'1.0-1.0' → {'lower_limit': 1.0, 'upper_limit': 1.0}。形が違えば None。"""
    parts = text.replace(",", "").strip().split("-")
    if len(parts) != 2:
        return None
    lower, upper = to_odds(parts[0]), to_odds(parts[1])
    if lower is None or upper is None:
        return None
    return {"lower_limit": lower, "upper_limit": upper}


def _others(*exclude: int) -> list[int]:
    return [b for b in BOATS if b not in exclude]


def _check_count(page: str, cells: list[str], expected: int) -> None:
    if len(cells) != expected:
        raise OddsParseError(f"{page}: oddsPoint のセル数が {len(cells)} です(想定 {expected})")


def parse_trifecta(cells: list[str]) -> dict:
    """3連単120セル。列=1着、行のブロック=2着、ブロック内の行=3着(spec/07 2.2)。"""
    _check_count("odds3t", cells, 120)
    out: dict = {str(b): {} for b in BOATS}
    idx = 0
    for b in range(5):
        for r in range(4):
            for first in BOATS:
                second = _others(first)[b]
                third = _others(first, second)[r]
                out[str(first)].setdefault(str(second), {})[str(third)] = to_odds(cells[idx])
                idx += 1
    return {"trifecta": out}


def parse_exacta_quinella(cells: list[str]) -> dict:
    """前半30セルが2連単、後半15セルが2連複。

    2連単: 行 r、列=1着。2着は1着以外を昇順に並べた r 番目。
    2連複: 左下の三角形。行=大きい方の艇(2〜6)、列=小さい方の艇(1〜行の艇-1)。
      文書の順は 1-2, 1-3, 2-3, 1-4, 2-4, 3-4, …(2026-11-03 大村5Rの実物で確認)。
    """
    _check_count("odds2tf", cells, 45)
    exacta: dict = {str(b): {} for b in BOATS}
    idx = 0
    for r in range(5):
        for first in BOATS:
            second = _others(first)[r]
            exacta[str(first)][str(second)] = to_odds(cells[idx])
            idx += 1
    quinella: dict = {str(b): {} for b in BOATS[:-1]}
    for second in BOATS[1:]:
        for first in range(1, second):
            quinella[str(first)][str(second)] = to_odds(cells[idx])
            idx += 1
    return {"exacta": exacta, "quinella": quinella}


def parse_win_place(cells: list[str]) -> dict:
    """前半6セルが単勝、後半6セルが複勝(下限-上限)。どちらも1〜6号艇の順。"""
    _check_count("oddstf", cells, 12)
    win = {str(b): to_odds(cells[i]) for i, b in enumerate(BOATS)}
    place = {str(b): to_range(cells[6 + i]) for i, b in enumerate(BOATS)}
    return {"win": win, "place": place}


# 解析の方法が実物で確かめられているページだけ parser を持つ。
# odds3f(3連複)・oddsk(拡連複)は取得と生HTMLの保存だけ行い、解析はしない(並びが未確認のため)。
PAGE_PARSERS: dict[str, Callable[[list[str]], dict] | None] = {
    "odds3t": parse_trifecta,
    "odds2tf": parse_exacta_quinella,
    "oddstf": parse_win_place,
    "odds3f": None,
    "oddsk": None,
}


def parse_page(page: str, html: str) -> tuple[dict | None, str | None]:
    """1ページ分の HTML を解析して (odds の dict, オッズ時点の表示) を返す。

    解析の方法が未確認のページは odds を None で返す。構造が違えば OddsParseError。
    """
    if page not in PAGE_PARSERS:
        raise ValueError(f"未知のページです: {page}")
    cells, label = extract_cells(html)
    parser = PAGE_PARSERS[page]
    if parser is None:
        return None, label
    return parser(cells), label


def is_final_label(label: str | None) -> bool:
    return bool(label) and "締切時" in label


# =====================================================================
# 対象レース(openapi の当日ファイルから)
# =====================================================================


@dataclass(frozen=True)
class RaceTarget:
    race_id: str
    stadium_number: int
    race_number: int
    closed_at: datetime

    def url(self, page: str) -> str:
        return URL_TEMPLATE.format(
            page=page, rno=self.race_number, jcd=self.stadium_number, ymd=self.race_id[:8]
        )


def make_race_id(target: date, stadium_number: int, race_number: int) -> str:
    return f"{target.strftime('%Y%m%d')}-{stadium_number:02d}-{race_number:02d}"


def parse_closed_at(text: str | None) -> datetime | None:
    """'2026-11-03 10:45:00'(JST)→ aware な datetime。"""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=JST)


def load_targets(
    target: date,
    raw_dir: Path | None = None,
    stadiums: set[int] | None = None,
    race_ids: set[str] | None = None,
) -> tuple[list[RaceTarget], list[tuple[str, str]]] | None:
    """openapi の当日ファイルから対象レースを締切順に返す。ファイルが無ければ None。

    戻り値: (対象, [(race_id, 対象外の理由)])
    """
    data = raw_io.load_raw("openapi", target, raw_dir)
    if data is None:
        return None
    stadium_map = ((data.get("programs") or {}).get("stadiums")) or {}
    targets: list[RaceTarget] = []
    skipped: list[tuple[str, str]] = []
    for s_key, stadium in stadium_map.items():
        races = (stadium or {}).get("races") or {}
        for r_key, race in races.items():
            if not isinstance(race, dict):
                continue
            sn = int(race.get("stadium_number") or s_key)
            rn = int(race.get("race_number") or r_key)
            race_id = make_race_id(target, sn, rn)
            if (stadiums and sn not in stadiums) or (race_ids is not None and race_id not in race_ids):
                skipped.append((race_id, NOT_TARGETED))
                continue
            day_source = race.get("day_number_source") or ""
            if "中止" in day_source or "順延" in day_source:
                skipped.append((race_id, f"中止・順延({day_source})"))
                continue
            closed_at = parse_closed_at(race.get("closed_at"))
            if closed_at is None:
                skipped.append((race_id, "締切時刻が無い"))
                continue
            targets.append(RaceTarget(race_id, sn, rn, closed_at))
    targets.sort(key=lambda t: (t.closed_at, t.race_id))
    return targets, skipped


def timing_label(minutes: int) -> str:
    return f"T{minutes:02d}"


# =====================================================================
# 取得
# =====================================================================


class StopForDay(Exception):
    """その日の取得をすべて止めるときに投げる(403/429、連続失敗、上限)。"""


class OfficialOddsFetcher:
    """締切前オッズの取得の本体。テストでは http_get / sleep_fn / now_fn を差し替える。"""

    def __init__(
        self,
        raw_dir: Path | None = None,
        pages: tuple[str, ...] = DEFAULT_PAGES,
        sleep_sec: float = DEFAULT_SLEEP_SEC,
        max_requests_per_day: int = DEFAULT_MAX_REQUESTS_PER_DAY,
        http_get: Callable[[str], HttpResult] = http_get,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = now_jst,
        out: Callable[[str], None] = print,
    ):
        if sleep_sec < MIN_SLEEP_SEC:
            raise ValueError(f"--sleep は {MIN_SLEEP_SEC} 秒以上にしてください: {sleep_sec}")
        self.raw_dir = raw_dir
        self.pages = tuple(pages)
        self.sleep_sec = sleep_sec
        self.max_requests_per_day = max_requests_per_day
        self.http_get = http_get
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.out = out
        self.requests_sent = 0
        self.consecutive_failures = 0
        self.any_failed = False
        self.stopped_reason: str | None = None
        self.last_request_at: datetime | None = None
        self.requests_by_day: dict[str, int] = {}
        for record in raw_io.read_fetch_log(raw_dir):
            try:
                at = datetime.fromisoformat(record["fetched_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if self.last_request_at is None or at > self.last_request_at:
                self.last_request_at = at
            if record.get("source") == SOURCE and record.get("page"):
                day = at.astimezone(JST).date().isoformat()
                self.requests_by_day[day] = self.requests_by_day.get(day, 0) + 1

    # ---- 補助 ----
    def requests_today(self) -> int:
        return self.requests_by_day.get(self.now_fn().date().isoformat(), 0)

    def _wait(self, min_sec: float) -> None:
        """前回の応答の終わりから min_sec 秒(かつ --sleep 秒以上)たつまで待つ。"""
        min_sec = max(min_sec, self.sleep_sec)
        if self.last_request_at is None:
            return
        elapsed = (self.now_fn() - self.last_request_at).total_seconds()
        if elapsed < min_sec:
            self.sleep_fn(min_sec - elapsed)

    def _log_event(self, event: str, race_id: str | None, target: date, **extra) -> None:
        record = {
            "source": SOURCE,
            "date": target.isoformat(),
            "race_id": race_id,
            "event": event,
            "logged_at": self.now_fn().isoformat(timespec="seconds"),
            **extra,
        }
        raw_io.append_fetch_log(record, self.raw_dir)

    def stop(self, reason: str, target: date) -> None:
        """その日の取得を止める。ログと _stopped.json に残す。"""
        self.stopped_reason = reason
        self.any_failed = True
        self._log_event("stopped", None, target, reason=reason)
        path = raw_io.official_odds_day_dir(target, self.raw_dir) / "_stopped.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"date": target.isoformat(), "stopped_at": self.now_fn().isoformat(timespec="seconds"), "reason": reason},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.out(f"停止: {reason}(その日の取得を止めました。再開は --resume)")

    # ---- 1ページの取得(リトライ1回) ----
    def _get_page(self, target: RaceTarget, page: str) -> HttpResult | None:
        """1ページを取る。成功なら HttpResult、失敗なら None。止めるべきときは StopForDay。"""
        url = target.url(page)
        day = target.race_id[:8]
        day_iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        for attempt in (1, 2):
            if self.requests_today() >= self.max_requests_per_day:
                raise StopForDay(f"1日のリクエスト上限({self.max_requests_per_day})に達しました")
            self._wait(self.sleep_sec if attempt == 1 else RETRY_WAIT_SEC)
            started = self.now_fn()
            result = self.http_get(url)
            finished = self.now_fn()
            self.last_request_at = finished
            self.requests_sent += 1
            today = finished.date().isoformat()
            self.requests_by_day[today] = self.requests_by_day.get(today, 0) + 1
            record = {
                "source": SOURCE,
                "date": day_iso,
                "race_id": target.race_id,
                "page": page,
                "url": url,
                "status": result.status,
                "fetched_at": finished.isoformat(timespec="seconds"),
                "elapsed_ms": int((finished - started).total_seconds() * 1000),
                "attempt": attempt,
                "bytes": len(result.body),
            }
            if result.error:
                record["error"] = result.error
            raw_io.append_fetch_log(record, self.raw_dir)
            if result.status == 200:
                return result
            if result.status in (403, 429):
                raise StopForDay(f"HTTP {result.status} が返りました({target.race_id} {page})")
            retryable = result.status is None or 500 <= result.status <= 599
            if retryable and attempt == 1:
                self.out(f"{target.race_id} {page}: {result.error} → リトライ")
                continue
            break
        return None

    def _record_failure(self, race_id: str, page: str, why: str) -> None:
        self.any_failed = True
        self.consecutive_failures += 1
        self.out(f"{race_id} {page}: 失敗 ({why})")
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise StopForDay(f"{MAX_CONSECUTIVE_FAILURES}回続けて失敗しました")

    # ---- 1レースの取得 ----
    def fetch_race(self, target: RaceTarget, timing: str, scheduled_minutes: int | None) -> dict:
        """設定したページを順に取り、生HTMLと解析JSONを保存する。解析JSONの dict を返す。

        途中で StopForDay になった場合も、取れた分は保存してから投げ直す。
        """
        if self.requests_today() + len(self.pages) > self.max_requests_per_day:
            raise StopForDay(f"1日のリクエスト上限({self.max_requests_per_day})を超えるため")
        odds: dict = {}
        page_info: dict = {}
        unparsed: list[str] = []
        first_fetched_at: datetime | None = None
        label: str | None = None
        stop: StopForDay | None = None
        for page in self.pages:
            try:
                result = self._get_page(target, page)
            except StopForDay as e:
                stop = e
                break
            if result is None:
                page_info[page] = {"ok": False, "error": "http"}
                try:
                    self._record_failure(target.race_id, page, "HTTP")
                except StopForDay as e:
                    stop = e
                    break
                continue
            fetched_at = self.last_request_at
            raw_io.write_gzip_atomic(raw_io.official_odds_paths(target.race_id, timing, page, self.raw_dir), result.body)
            try:
                parsed, page_label = parse_page(page, result.body.decode("utf-8"))
            except (OddsParseError, UnicodeDecodeError) as e:
                page_info[page] = {"ok": False, "error": f"parse: {e}", "fetched_at": fetched_at.isoformat(timespec="seconds")}
                try:
                    self._record_failure(target.race_id, page, f"解析エラー: {e}")
                except StopForDay as stop_e:
                    stop = stop_e
                    break
                continue
            self.consecutive_failures = 0
            if first_fetched_at is None:
                first_fetched_at = fetched_at
            if label is None:
                label = page_label
            page_info[page] = {
                "ok": True,
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "odds_time_label": page_label,
            }
            if parsed is None:
                unparsed.append(page)
            else:
                odds.update(parsed)
        n_ok = sum(1 for p in page_info.values() if p.get("ok"))
        if n_ok == len(self.pages):
            status = "ok"
        elif n_ok > 0:
            status = "partial"
        else:
            status = "failed"
        doc = {
            "race_id": target.race_id,
            "closed_at": target.closed_at.isoformat(timespec="seconds"),
            "fetched_at": first_fetched_at.isoformat(timespec="seconds") if first_fetched_at else None,
            "minutes_before_close": round((target.closed_at - first_fetched_at).total_seconds() / 60, 1)
            if first_fetched_at
            else None,
            "timing": timing,
            "scheduled_minutes_before": scheduled_minutes,
            "status": status,
            "odds_time_label": label,
            # どれか1ページでも「締切時」の表記なら締切後のオッズとみなす
            "is_final_odds": any(is_final_label(p.get("odds_time_label")) for p in page_info.values()),
            "pages_requested": list(self.pages),
            "pages": page_info,
            "unparsed_pages": unparsed,
            "odds": odds,
        }
        if n_ok > 0 or stop is None:
            self._write_json(target.race_id, timing, doc)
        self.out(
            f"{target.race_id} {timing}: {status} ページ {n_ok}/{len(self.pages)} "
            f"締切{doc['minutes_before_close']}分前 表示「{label}」"
        )
        if stop is not None:
            raise stop
        return doc

    def mark_late(self, target: RaceTarget, timing: str, scheduled_minutes: int | None) -> None:
        """取得の窓に間に合わなかったレースを late として記録する(取りに行かない)。"""
        doc = {
            "race_id": target.race_id,
            "closed_at": target.closed_at.isoformat(timespec="seconds"),
            "fetched_at": None,
            "minutes_before_close": None,
            "timing": timing,
            "scheduled_minutes_before": scheduled_minutes,
            "status": "late",
            "odds_time_label": None,
            "is_final_odds": False,
            "pages_requested": list(self.pages),
            "pages": {},
            "unparsed_pages": [],
            "odds": {},
        }
        self._write_json(target.race_id, timing, doc)
        self._log_event("late", target.race_id, target.closed_at.date(), timing=timing)
        self.out(f"{target.race_id} {timing}: late(締切{LATE_MINUTES}分前を過ぎたため取得しない)")

    def _write_json(self, race_id: str, timing: str, doc: dict) -> None:
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        raw_io.write_gzip_atomic(raw_io.official_odds_paths(race_id, timing, None, self.raw_dir), body)


# =====================================================================
# 実行モード
# =====================================================================


def manual_timing(closed_at: datetime, now: datetime) -> str:
    """手動取得の T ラベル。締切までの分(切り捨て)、締切後は 'TC'。"""
    minutes = (closed_at - now).total_seconds() / 60
    if minutes < 0:
        return "TC"
    return timing_label(min(int(minutes), 99))


def run_single(fetcher: OfficialOddsFetcher, target: RaceTarget, force: bool) -> int:
    """1レースだけ今すぐ取る(確認用。取得の窓は見ない)。"""
    now = fetcher.now_fn()
    timing = manual_timing(target.closed_at, now)
    # 同じレースは2回取らない(どの T でも、取れた記録があれば取り直さない)
    got = [d for d in raw_io.list_official_odds(target.race_id, fetcher.raw_dir) if d.get("status") in ("ok", "partial")]
    if got and not force:
        fetcher.out(f"{target.race_id}: 取得済みです({got[-1].get('timing')}。--force で取り直す)")
        return 0
    if timing == "TC":
        fetcher.out(f"注意: {target.race_id} は締切後です(締切時オッズを取ります)")
    day = target.closed_at.date()
    if raw_io.load_official_odds_stopped(day, fetcher.raw_dir) is not None:
        fetcher.out(f"{day} は取得を止めています(_stopped.json)。確かめてから --loop か --window-minutes の --resume で再開してください")
        return 1
    try:
        doc = fetcher.fetch_race(target, timing, None)
    except StopForDay as e:
        fetcher.stop(str(e), day)
        return 1
    return 0 if doc["status"] == "ok" else 1


def run_loop(
    fetcher: OfficialOddsFetcher,
    target_date: date,
    minutes_before: int,
    select: Callable[[], tuple[list[RaceTarget], list[tuple[str, str]]] | None],
    refresh_program: Callable[[], None],
    resume: bool = False,
) -> int:
    """当日分を締切の minutes_before 分前に順に取る。全レースを処理したら終わる。"""
    out = fetcher.out
    timing = timing_label(minutes_before)
    if not _check_stopped(fetcher, target_date, resume):
        return 1
    done: set[str] = set()
    next_refresh: datetime | None = None
    targets: list[RaceTarget] = []
    counts = {"ok": 0, "partial": 0, "failed": 0, "late": 0, "already": 0}
    while True:
        now = fetcher.now_fn()
        if next_refresh is None or now >= next_refresh:
            try:
                refresh_program()
            except Exception as e:  # 取り直しに失敗しても手元のファイルで続ける
                out(f"openapi の取り直しに失敗: {e}")
            next_refresh = fetcher.now_fn() + PROGRAM_REFRESH
            selected = select()
            if selected is None:
                out(f"{target_date} の openapi のファイルがありません")
                return 1
            targets = selected[0]
            record_targets(fetcher, target_date, selected)
        pending = []
        for t in targets:
            if t.race_id in done:
                continue
            if raw_io.official_odds_paths(t.race_id, timing, None, fetcher.raw_dir).exists():
                done.add(t.race_id)
                counts["already"] += 1
                continue
            pending.append(t)
        if not pending:
            break
        t = pending[0]  # 締切の早い順
        now = fetcher.now_fn()
        planned = t.closed_at - timedelta(minutes=minutes_before)
        late_at = t.closed_at - timedelta(minutes=LATE_MINUTES)
        if now > late_at:
            fetcher.mark_late(t, timing, minutes_before)
            done.add(t.race_id)
            counts["late"] += 1
            continue
        if now < planned:
            wake = min(planned, next_refresh)
            fetcher.sleep_fn(max((wake - now).total_seconds(), 1.0))
            continue
        try:
            doc = fetcher.fetch_race(t, timing, minutes_before)
        except StopForDay as e:
            fetcher.stop(str(e), target_date)
            break
        done.add(t.race_id)
        counts[doc["status"]] += 1
    out(
        f"完了: ok={counts['ok']} partial={counts['partial']} failed={counts['failed']} late={counts['late']} "
        f"取得済み={counts['already']} リクエスト数={fetcher.requests_sent}"
    )
    return 1 if fetcher.any_failed or fetcher.stopped_reason else 0


def record_targets(
    fetcher: OfficialOddsFetcher,
    target_date: date,
    selected: tuple[list[RaceTarget], list[tuple[str, str]]],
) -> None:
    """その日の取得対象・対象外を _targets.json に記録する(エクスポートの odds_snapshot.status 用)。

    起動のたびに前回までの記録と合わせる(一度でも対象になったレースは targeted のまま)。
    """
    old = raw_io.load_official_odds_targets(target_date, fetcher.raw_dir) or {}
    targeted = set(old.get("targeted", [])) | {t.race_id for t in selected[0]}
    canceled = set(old.get("canceled", [])) | {r for r, why in selected[1] if why != NOT_TARGETED}
    not_targeted = (set(old.get("not_targeted", [])) | {r for r, why in selected[1] if why == NOT_TARGETED}) - targeted
    doc = {
        "date": target_date.isoformat(),
        "updated_at": fetcher.now_fn().isoformat(timespec="seconds"),
        "pages": list(fetcher.pages),
        "targeted": sorted(targeted),
        "not_targeted": sorted(not_targeted),
        "canceled": sorted(canceled - targeted),
    }
    path = raw_io.official_odds_day_dir(target_date, fetcher.raw_dir) / "_targets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def has_any_odds_json(race_id: str, raw_dir: Path | None) -> bool:
    """そのレースの解析JSON(どの T でも、late も含む)が既にあるか。"""
    return bool(raw_io.list_official_odds(race_id, raw_dir))


def run_batch(
    fetcher: OfficialOddsFetcher,
    target_date: date,
    window_minutes: int,
    select: Callable[[], tuple[list[RaceTarget], list[tuple[str, str]]] | None],
    refresh_program: Callable[[], None],
    resume: bool = False,
) -> int:
    """一括取得モード: 1回起動して、今から window_minutes 分以内に締切を迎え、まだ取っていないレースを
    締切の早い順に取って終わる(定期実行用。10〜15分ごとに起動すれば全レースを覆える)。

    - 締切2分前を過ぎたレースは取らない。前回の起動から今回までの間に締切2分前を過ぎてしまい、
      まだ記録の無いレース(締切が直近 window_minutes 分以内)は late として記録する。
    - 取得済み(どの T でも解析JSONがある)レースは取り直さない。
    - T ラベルは、そのレースを取り始めた時点の締切までの分(切り捨て)。late の記録は 'TL'。
    - 上限・間隔・停止条件は常駐ループと同じ。画面への出力にオッズの数値は出さない(件数・状態だけ)。
    """
    out = fetcher.out
    if not _check_stopped(fetcher, target_date, resume):
        return 1
    try:
        refresh_program()
    except Exception as e:  # 取り直しに失敗しても手元のファイルで続ける
        out(f"openapi の取り直しに失敗: {e}")
    selected = select()
    if selected is None:
        out(f"{target_date} の openapi のファイルがありません")
        return 1
    record_targets(fetcher, target_date, selected)
    start = fetcher.now_fn()
    window_end = start + timedelta(minutes=window_minutes)
    late_from = start - timedelta(minutes=window_minutes)
    counts = {"ok": 0, "partial": 0, "failed": 0, "late": 0, "already": 0}
    for t in selected[0]:
        if t.closed_at > window_end:
            break  # 締切順なので、ここから先は次回以降
        if t.closed_at < late_from:
            continue  # 前々回以前の範囲(既に記録があるか、窓の外)
        if has_any_odds_json(t.race_id, fetcher.raw_dir):
            counts["already"] += 1
            continue
        now = fetcher.now_fn()
        if now > t.closed_at - timedelta(minutes=LATE_MINUTES):
            fetcher.mark_late(t, "TL", None)
            counts["late"] += 1
            continue
        timing = manual_timing(t.closed_at, now)
        try:
            doc = fetcher.fetch_race(t, timing, None)
        except StopForDay as e:
            fetcher.stop(str(e), target_date)
            break
        counts[doc["status"]] += 1
    out(
        f"完了(一括 {window_minutes}分): ok={counts['ok']} partial={counts['partial']} failed={counts['failed']} "
        f"late={counts['late']} 取得済み={counts['already']} リクエスト数={fetcher.requests_sent}"
    )
    return 1 if fetcher.any_failed or fetcher.stopped_reason else 0


def _check_stopped(fetcher: OfficialOddsFetcher, target_date: date, resume: bool) -> bool:
    """止めた日なら False(--resume なら印を消して True)。"""
    if raw_io.load_official_odds_stopped(target_date, fetcher.raw_dir) is None:
        return True
    if not resume:
        fetcher.out(f"{target_date} は取得を止めています。確かめてから --resume で再開してください")
        return False
    (raw_io.official_odds_day_dir(target_date, fetcher.raw_dir) / "_stopped.json").unlink()
    fetcher._log_event("resumed", None, target_date)
    fetcher.out("再開します(--resume)")
    return True


def dry_run(
    targets: list[RaceTarget],
    skipped: list[tuple[str, str]],
    pages: tuple[str, ...],
    minutes_before: int,
    now: datetime,
    max_requests_per_day: int,
    requests_today: int,
    out: Callable[[str], None],
) -> None:
    """取得予定(時刻・URL・件数)を表示する。アクセスはしない。"""
    n_fetch = 0
    for t in targets:
        planned = t.closed_at - timedelta(minutes=minutes_before)
        late = now > t.closed_at - timedelta(minutes=LATE_MINUTES)
        mark = "  (late予定: 締切2分前を過ぎている)" if late else ""
        out(f"{planned:%H:%M} {t.race_id} 締切{t.closed_at:%H:%M}{mark}")
        if not late:
            n_fetch += 1
            for p in pages:
                out(f"    {t.url(p)}")
    n_not_targeted = 0
    for race_id, why in skipped:
        if why == NOT_TARGETED:
            n_not_targeted += 1
        else:
            out(f"対象外 {race_id}: {why}")
    if n_not_targeted:
        out(f"絞り込みで対象外: {n_not_targeted} レース")
    n_req = n_fetch * len(pages)
    out(
        f"取得予定: {n_fetch} レース × {len(pages)} ページ = {n_req} リクエスト"
        f"(リトライを除く。上限 {max_requests_per_day}/日、本日の送信済み {requests_today})"
    )
    out(f"所要時間の目安: 1レース約{len(pages) * 11.5:.0f}秒、合計約{n_req * 11.5 / 60:.0f}分(応答10秒+間隔1.5秒で計算)")
    if requests_today + n_req > max_requests_per_day:
        out("注意: 上限を超えるため、途中で止まります。--pages や --stadiums で減らしてください")


# =====================================================================
# CLI
# =====================================================================


def parse_pages(text: str) -> tuple[str, ...]:
    pages = tuple(p.strip() for p in text.split(",") if p.strip())
    bad = [p for p in pages if p not in ALL_PAGES]
    if not pages or bad:
        raise argparse.ArgumentTypeError(f"--pages は {','.join(ALL_PAGES)} から選んでください: {text}")
    if len(set(pages)) != len(pages):
        raise argparse.ArgumentTypeError(f"--pages が重複しています: {text}")
    return pages


def parse_stadiums(text: str) -> set[int]:
    try:
        values = {int(s) for s in text.split(",") if s.strip()}
    except ValueError:
        raise argparse.ArgumentTypeError(f"--stadiums は場番号(1〜24)をカンマ区切りで: {text}")
    if not values or any(not 1 <= v <= 24 for v in values):
        raise argparse.ArgumentTypeError(f"--stadiums は場番号(1〜24)をカンマ区切りで: {text}")
    return values


def read_race_ids_file(path: str) -> set[str]:
    """1行1つの race_id、または race_id の JSON 配列のファイルを読む。"""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith("["):
        return {str(x) for x in json.loads(stripped)}
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公式サイトから締切前オッズを取得する")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--race", help="1レースだけ今すぐ取る(race_id。例: 20261103-02-01)")
    mode.add_argument("--loop", action="store_true", help="常駐ループ(当日分を締切前に順に取る)")
    mode.add_argument("--dry-run", action="store_true", help="取得予定を表示するだけ")
    mode.add_argument(
        "--window-minutes", type=int,
        help=f"一括取得: 今からこの分数以内に締切を迎える未取得のレースを取って終わる({MIN_WINDOW_MINUTES}〜{MAX_WINDOW_MINUTES})",
    )
    parser.add_argument("--date", type=raw_io.to_date, help="対象日(既定は今日)")
    parser.add_argument("--minutes-before", type=int, default=DEFAULT_MINUTES_BEFORE, help="締切の何分前に取るか(3〜15)")
    parser.add_argument("--pages", type=parse_pages, default=DEFAULT_PAGES, help=f"取るページ(既定 {','.join(DEFAULT_PAGES)})")
    parser.add_argument("--stadiums", type=parse_stadiums, help="対象の場番号(カンマ区切り。既定は全場)")
    parser.add_argument("--race-ids-file", help="対象のレースを絞るファイル(1行1つの race_id か JSON 配列)")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="リクエスト間の待機秒数(1.0以上)")
    parser.add_argument("--max-requests-per-day", type=int, default=DEFAULT_MAX_REQUESTS_PER_DAY)
    parser.add_argument("--resume", action="store_true", help="止めた日(_stopped.json)の取得を再開する")
    parser.add_argument("--force", action="store_true", help="--race で取得済みでも取り直す")
    args = parser.parse_args(argv)
    if args.sleep < MIN_SLEEP_SEC:
        parser.error(f"--sleep は {MIN_SLEEP_SEC} 秒以上にしてください")
    if not MIN_MINUTES_BEFORE <= args.minutes_before <= MAX_MINUTES_BEFORE:
        parser.error(f"--minutes-before は {MIN_MINUTES_BEFORE}〜{MAX_MINUTES_BEFORE} にしてください")
    if args.window_minutes is not None and not MIN_WINDOW_MINUTES <= args.window_minutes <= MAX_WINDOW_MINUTES:
        parser.error(f"--window-minutes は {MIN_WINDOW_MINUTES}〜{MAX_WINDOW_MINUTES} にしてください")
    if args.max_requests_per_day < 1:
        parser.error("--max-requests-per-day は1以上にしてください")
    if args.race:
        parts = args.race.split("-")
        if len(parts) != 3 or len(parts[0]) != 8 or not all(p.isdigit() for p in parts):
            parser.error(f"--race の形式が不正です: {args.race}")
        race_date = raw_io.to_date(parts[0])
        if args.date and args.date != race_date:
            parser.error("--date と --race の日付が違います")
        args.date = race_date
    return args


def default_refresh(raw_dir: Path | None, target: date, http_get, sleep_fn, now_fn, out) -> Callable[[], None]:
    """openapi の当日分を既存の fetch で取り直す関数を作る(10分以内なら取り直さない)。"""
    from src.collect.fetch import Fetcher

    def refresh() -> None:
        fetcher = Fetcher(raw_dir=raw_dir, http_get=http_get, sleep_fn=sleep_fn, now_fn=now_fn, out=out)
        if fetcher.skip_reason("openapi", target, now_fn().date()) is None:
            fetcher.fetch_one("openapi", target)

    return refresh


def main(
    argv: list[str] | None = None,
    raw_dir: Path | None = None,
    http_get: Callable[[str], HttpResult] = http_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = now_jst,
    out: Callable[[str], None] = print,
    refresh_program: Callable[[], None] | None = None,
) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return int(e.code or 0)
    today = now_fn().date()
    target_date = args.date or today
    race_ids = None
    if args.race_ids_file:
        try:
            race_ids = read_race_ids_file(args.race_ids_file)
        except (OSError, json.JSONDecodeError) as e:
            out(f"--race-ids-file を読めません: {e}")
            return 2

    fetcher = OfficialOddsFetcher(
        raw_dir=raw_dir,
        pages=args.pages,
        sleep_sec=args.sleep,
        max_requests_per_day=args.max_requests_per_day,
        http_get=http_get,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        out=out,
    )

    def select():
        return load_targets(target_date, raw_dir, args.stadiums, race_ids)

    if args.race:
        selected = load_targets(target_date, raw_dir, race_ids={args.race})
        if selected is None:
            out(f"{target_date} の openapi のファイルがありません(先に fetch --source openapi で取得)")
            return 1
        if not selected[0]:
            why = selected[1][0][1] if selected[1] else "出走表に無い"
            out(f"{args.race}: 取得できません({why})")
            return 1
        return run_single(fetcher, selected[0][0], args.force)

    if args.dry_run:
        selected = select()
        if selected is None:
            out(f"{target_date} の openapi のファイルがありません(先に fetch --source openapi で取得)")
            return 1
        now = now_fn() if target_date == today else datetime.combine(target_date, datetime.min.time(), JST)
        dry_run(
            selected[0], selected[1], args.pages, args.minutes_before, now,
            args.max_requests_per_day, fetcher.requests_today(), out,
        )
        return 0

    # --loop / --window-minutes
    if target_date != today:
        out("--loop と --window-minutes は当日だけ指定できます")
        return 2
    refresh = refresh_program or default_refresh(raw_dir, target_date, http_get, sleep_fn, now_fn, out)
    if args.window_minutes is not None:
        return run_batch(fetcher, target_date, args.window_minutes, select, refresh, resume=args.resume)
    return run_loop(fetcher, target_date, args.minutes_before, select, refresh, resume=args.resume)


if __name__ == "__main__":
    sys.exit(main())
