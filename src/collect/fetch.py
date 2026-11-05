"""turnmark / openapi から日単位の生データを取得し、data/raw/ に保存する(収集CLI)。

使い方:
    python -m src.collect.fetch --source turnmark --from 2026-01-01 --to 2026-11-01
    python -m src.collect.fetch --source openapi  --date 2026-11-02
    python -m src.collect.fetch --source auto     --from 2026-10-25 --to 2026-11-02
    python -m src.collect.fetch --dry-run --from 2026-10-01 --to 2026-10-31

仕様は spec/01_collect.md。主な決まり:
- すべてのリクエスト(リトライ・404を含む)の間で --sleep 秒以上空ける。
  前回のリクエスト時刻は取得ログから読むので、続けて実行しても間隔が守られる。
- 取得済み(確定)・404済みの過去日はスキップ。当日の openapi は10分以内なら再取得しない。
- 終了コード: 0=正常(404を含む)、1=失敗した日がある/途中で停止、2=引数エラー
"""

import argparse
import hashlib
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.collect import raw_io

JST = timezone(timedelta(hours=9))
USER_AGENT = "boatrace-yoso/0.1 (personal use)"
TIMEOUT_SEC = 30
URL_TEMPLATES = {
    "turnmark": "https://turnmark.github.io/api/v1/{year}/{ymd}.json",
    "openapi": "https://boatraceopenapi.github.io/api/v1/{year}/{ymd}.json",
    # 旧API(v3)。取得は src/collect/fetch_legacy.py から(PC で夜間のみ)
    "legacy_programs": "https://boatraceopenapi.github.io/programs/v3/{year}/{ymd}.json",
    "legacy_previews": "https://boatraceopenapi.github.io/previews/v3/{year}/{ymd}.json",
    "legacy_results": "https://boatraceopenapi.github.io/results/v3/{year}/{ymd}.json",
}
MIN_SLEEP_SEC = 1.0
DEFAULT_SLEEP_SEC = 1.5
DEFAULT_MAX_REQUESTS = 400
BACKOFF_SEC = (2, 4, 8)  # リトライ前の待ち時間(最大3回)
RETRY_AFTER_MAX_SEC = 300
OPENAPI_REFETCH_MIN = timedelta(minutes=10)  # 当日 openapi の再取得間隔
OPENAPI_FINAL_HOUR = 1  # openapi は翌日1時(JST)以降の取得を確定とみなす
MAX_CONSECUTIVE_FAILURES = 3


def now_jst() -> datetime:
    return datetime.now(JST)


def build_url(source: str, target: date) -> str:
    return URL_TEMPLATES[source].format(year=target.year, ymd=target.strftime("%Y%m%d"))


@dataclass
class HttpResult:
    """1リクエストの結果。status が None なら通信エラー(タイムアウト等)。"""

    status: int | None
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    error: str | None = None


def http_get(url: str, timeout: float = TIMEOUT_SEC) -> HttpResult:
    """urllib で GET する。例外は投げずに HttpResult にまとめる。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return HttpResult(status=res.status, body=res.read(), headers=dict(res.headers.items()))
    except urllib.error.HTTPError as e:
        headers = dict(e.headers.items()) if e.headers is not None else {}
        return HttpResult(status=e.code, headers=headers, error=f"HTTP {e.code}")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        return HttpResult(status=None, error=f"{type(e).__name__}: {e}")


def is_retryable(result: HttpResult) -> bool:
    """タイムアウト・接続エラー・5xx・429 はリトライする。404 とその他の4xxはしない。"""
    if result.status is None:
        return True
    return result.status == 429 or 500 <= result.status <= 599


def retry_after_sec(result: HttpResult) -> float | None:
    """429 の Retry-After(秒数形式のみ対応)。上限300秒。"""
    if result.status != 429:
        return None
    for key, value in result.headers.items():
        if key.lower() == "retry-after":
            try:
                return min(float(value), RETRY_AFTER_MAX_SEC)
            except (TypeError, ValueError):
                return None
    return None


def is_final(source: str, target: date, fetched_at: datetime) -> bool:
    """取得時点でそのデータを確定とみなせるか。"""
    if source == "turnmark":
        return True
    if source.startswith("legacy_"):
        # 旧API: 対象日の翌々日以降に取得していれば確定
        return fetched_at.date() >= target + timedelta(days=2)
    threshold = datetime.combine(target + timedelta(days=1), datetime.min.time(), JST) + timedelta(
        hours=OPENAPI_FINAL_HOUR
    )
    return fetched_at >= threshold


def check_payload(source: str, data) -> list[str]:
    """保存前の簡易チェック。異常があれば warning の文字列を返す(保存はする)。"""
    warnings = []
    if source.startswith("legacy_"):
        kind = source.removeprefix("legacy_")
        races = data.get(kind) if isinstance(data, dict) else None
        if not isinstance(races, list):
            return [f"トップレベルが {{{kind}: [...]}} ではない"]
        n_stadiums = len({r.get("stadium_number") for r in races if isinstance(r, dict)})
        if not 1 <= n_stadiums <= 24:
            warnings.append(f"開催場数が範囲外: {n_stadiums}")
        return warnings
    programs = data.get("programs") if isinstance(data, dict) else None
    stadiums = programs.get("stadiums") if isinstance(programs, dict) else None
    if not isinstance(stadiums, dict):
        return ["トップレベルが {programs: {stadiums: {...}}} ではない"]
    n_stadiums = len(stadiums)
    if not 1 <= n_stadiums <= 24:
        warnings.append(f"開催場数が範囲外: {n_stadiums}")
    if source == "turnmark":
        n_races = 0
        n_odds = 0
        for stadium in stadiums.values():
            races = stadium.get("races") if isinstance(stadium, dict) else None
            if not isinstance(races, dict):
                continue
            for race in races.values():
                n_races += 1
                if isinstance(race, dict) and race.get("odds"):
                    n_odds += 1
        if n_races == 0 or n_odds / n_races < 0.5:
            warnings.append(f"odds のあるレースが5割未満: {n_odds}/{n_races}")
    return warnings


class StopFetching(Exception):
    """連続失敗やリクエスト上限で、以降の取得をやめるときに投げる。"""


class Fetcher:
    """取得の本体。テストでは http_get / sleep_fn / now_fn を差し替える。"""

    def __init__(
        self,
        raw_dir: Path | None = None,
        sleep_sec: float = DEFAULT_SLEEP_SEC,
        force: bool = False,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        http_get: Callable[[str], HttpResult] = http_get,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = now_jst,
        out: Callable[[str], None] = print,
    ):
        if sleep_sec < MIN_SLEEP_SEC:
            raise ValueError(f"--sleep は {MIN_SLEEP_SEC} 秒以上にしてください: {sleep_sec}")
        self.raw_dir = raw_dir
        self.sleep_sec = sleep_sec
        self.force = force
        self.max_requests = max_requests
        self.http_get = http_get
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.out = out
        self.requests_sent = 0
        # (source, 'YYYY-MM-DD') -> 最新のログ記録
        self.latest: dict[tuple[str, str], dict] = {}
        self.last_request_at: datetime | None = None
        for record in raw_io.read_fetch_log(raw_dir):
            self._remember(record)

    # ---- ログ ----
    def _remember(self, record: dict) -> None:
        key = (record.get("source"), record.get("date"))
        self.latest[key] = record
        try:
            at = datetime.fromisoformat(record["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return
        if self.last_request_at is None or at > self.last_request_at:
            self.last_request_at = at

    def _log(self, record: dict) -> None:
        raw_io.append_fetch_log(record, self.raw_dir)
        self._remember(record)

    # ---- スキップ判定 ----
    def skip_reason(self, source: str, target: date, today: date) -> str | None:
        """取得しない理由を返す。取得する場合は None。"""
        if target > today:
            return "未来の日付"
        if source == "turnmark" and target >= today:
            return "turnmark に当日分は無い"
        latest = self.latest.get((source, target.isoformat()))
        # 当日の openapi は10分以内に取得していれば再取得しない(--force でも守る)
        if source == "openapi" and target == today and latest is not None:
            try:
                last = datetime.fromisoformat(latest["fetched_at"])
                if self.now_fn() - last < OPENAPI_REFETCH_MIN:
                    return "当日分を10分以内に取得済み"
            except (KeyError, TypeError, ValueError):
                pass
        if self.force or latest is None:
            return None
        exists = raw_io.raw_path(source, target, self.raw_dir).exists()
        if exists and latest.get("status") == 200 and latest.get("final") and not latest.get("error"):
            return "確定済み"
        if latest.get("status") == 404 and target < today:
            # turnmark の前日分は公開待ちかもしれないので次回も試す
            if source == "turnmark" and target == today - timedelta(days=1):
                return None
            return "404 済み(--force で再試行)"
        return None

    # ---- 待機 ----
    def _wait(self, min_sec: float) -> None:
        """前回のリクエストから min_sec 秒(かつ --sleep 秒以上)たつまで待つ。"""
        min_sec = max(min_sec, self.sleep_sec)
        if self.last_request_at is None:
            return
        elapsed = (self.now_fn() - self.last_request_at).total_seconds()
        if elapsed < min_sec:
            self.sleep_fn(min_sec - elapsed)

    # ---- 1ソース・1日の取得 ----
    def fetch_one(self, source: str, target: date) -> str:
        """1日分を取得する。戻り値: 'ok' / 'not_found' / 'failed'。"""
        url = build_url(source, target)
        wait_sec = self.sleep_sec
        for attempt in range(len(BACKOFF_SEC) + 1):
            if self.requests_sent >= self.max_requests:
                raise StopFetching(f"リクエスト上限({self.max_requests})に達しました")
            self._wait(wait_sec)
            result = self.http_get(url)
            self.requests_sent += 1
            fetched_at = self.now_fn()
            self.last_request_at = fetched_at
            record = {
                "source": source,
                "date": target.isoformat(),
                "url": url,
                "status": result.status,
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "attempt": attempt + 1,
            }
            if result.status == 200:
                return self._handle_ok(source, target, result, record, fetched_at)
            record["error"] = result.error
            record["final"] = False
            self._log(record)
            if result.status == 404:
                self.out(f"{target} {source}: 404(データなし)")
                return "not_found"
            if not is_retryable(result) or attempt >= len(BACKOFF_SEC):
                self.out(f"{target} {source}: 失敗 ({result.error})")
                return "failed"
            wait_sec = retry_after_sec(result) or BACKOFF_SEC[attempt]
            self.out(f"{target} {source}: {result.error} → {wait_sec:.0f}秒後にリトライ")
        return "failed"  # ここには来ない

    def _handle_ok(self, source: str, target: date, result: HttpResult, record: dict, fetched_at: datetime) -> str:
        try:
            data = json.loads(result.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # JSONとして読めないものは失敗扱い。保存しない
            record.update({"bytes": len(result.body), "error": f"invalid json: {e}", "final": False})
            self._log(record)
            self.out(f"{target} {source}: JSONとして読めないため失敗")
            return "failed"
        warnings = check_payload(source, data)
        raw_io.write_raw_bytes(source, target, result.body, self.raw_dir)
        record.update(
            {
                "bytes": len(result.body),
                "sha256": hashlib.sha256(result.body).hexdigest(),
                "final": is_final(source, target, fetched_at),
            }
        )
        if warnings:
            record["warning"] = warnings
        self._log(record)
        msg = f"{target} {source}: 200 {len(result.body) / 1e6:.2f}MB"
        if warnings:
            msg += f" warning={warnings}"
        self.out(msg)
        return "ok"

    # ---- 計画と実行 ----
    def plan(self, source: str, dates: list[date], today: date) -> list[tuple[str, date]]:
        """実際に送る予定の (source, date) を返す(auto の404時の代替は既知のものだけ)。"""
        planned = []
        for d in dates:
            for src in self._sources_for(source, d, today):
                if self.skip_reason(src, d, today) is None:
                    planned.append((src, d))
                    if source == "auto":
                        break
                elif source == "auto" and src == "turnmark":
                    # turnmark が確定済みなら代替は不要。404済みなら openapi を見る
                    latest = self.latest.get(("turnmark", d.isoformat()))
                    if not (latest and latest.get("status") == 404):
                        break
        return planned

    @staticmethod
    def _sources_for(source: str, target: date, today: date) -> list[str]:
        if source != "auto":
            return [source]
        if target >= today:
            return ["openapi"]
        return ["turnmark", "openapi"]

    def run(self, source: str, dates: list[date], today: date) -> int:
        """取得を実行して終了コードを返す。"""
        counts = {"ok": 0, "not_found": 0, "failed": 0, "skipped": 0}
        consecutive_failures = 0
        any_failed = False
        try:
            for d in dates:
                day_failed = False
                for src in self._sources_for(source, d, today):
                    reason = self.skip_reason(src, d, today)
                    if reason is not None:
                        counts["skipped"] += 1
                        if source == "auto" and src == "turnmark":
                            latest = self.latest.get(("turnmark", d.isoformat()))
                            if latest and latest.get("status") == 404:
                                continue  # 404済みの過去日は openapi で代替
                        break
                    outcome = self.fetch_one(src, d)
                    counts[outcome] += 1
                    if outcome == "failed":
                        day_failed = True
                        break
                    if outcome == "not_found" and source == "auto" and src == "turnmark":
                        continue  # openapi で代替
                    break
                if day_failed:
                    any_failed = True
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise StopFetching(f"{MAX_CONSECUTIVE_FAILURES}日続けて失敗したため停止します")
                else:
                    consecutive_failures = 0
        except StopFetching as e:
            self.out(f"停止: {e}")
            any_failed = True
        self.out(
            f"完了: 200={counts['ok']} 404={counts['not_found']} 失敗={counts['failed']} "
            f"スキップ={counts['skipped']} リクエスト数={self.requests_sent}"
        )
        return 1 if any_failed else 0


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ボートレースの生データを取得する")
    parser.add_argument("--source", choices=["turnmark", "openapi", "auto"], default="auto")
    parser.add_argument("--date", type=raw_io.to_date, help="対象日(YYYY-MM-DD)")
    parser.add_argument("--from", dest="date_from", type=raw_io.to_date, help="開始日(含む)")
    parser.add_argument("--to", dest="date_to", type=raw_io.to_date, help="終了日(含む)")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="リクエスト間の待機秒数(1.0以上)")
    parser.add_argument("--force", action="store_true", help="取得済み・404済みも取り直す")
    parser.add_argument("--dry-run", action="store_true", help="取得予定のURLを表示するだけ")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    args = parser.parse_args(argv)
    if args.sleep < MIN_SLEEP_SEC:
        parser.error(f"--sleep は {MIN_SLEEP_SEC} 秒以上にしてください")
    if args.date and (args.date_from or args.date_to):
        parser.error("--date と --from/--to は同時に指定できません")
    if args.max_requests < 1:
        parser.error("--max-requests は1以上にしてください")
    return args


def main(
    argv: list[str] | None = None,
    raw_dir: Path | None = None,
    http_get: Callable[[str], HttpResult] = http_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = now_jst,
    out: Callable[[str], None] = print,
) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return int(e.code or 0)
    today = now_fn().date()
    if args.date:
        start = end = args.date
    else:
        start = args.date_from or args.date_to or today
        end = args.date_to or args.date_from or today
    if start > end:
        out("--from が --to より後になっています")
        return 2

    fetcher = Fetcher(
        raw_dir=raw_dir,
        sleep_sec=args.sleep,
        force=args.force,
        max_requests=args.max_requests,
        http_get=http_get,
        sleep_fn=sleep_fn,
        now_fn=now_fn,
        out=out,
    )
    dates = date_range(start, end)
    planned = fetcher.plan(args.source, dates, today)
    if args.dry_run:
        for src, d in planned:
            out(f"{d} {src} {build_url(src, d)}")
        out(f"取得予定: {len(planned)} リクエスト(リトライ・404時の代替を除く)")
        return 0
    if len(planned) > args.max_requests:
        out(f"取得予定 {len(planned)} 件が --max-requests {args.max_requests} を超えるため中止します")
        return 2
    return fetcher.run(args.source, dates, today)


if __name__ == "__main__":
    sys.exit(main())
