"""旧API(boatraceopenapi.github.io の {programs,previews,results}/v3)を夜間に一括取得する(C-7)。

PC(WSL)でだけ実行する。GitHub Actions からは実行しない。
待機・スキップ・リトライ・3日連続失敗での停止・取得ログは src.collect.fetch と同じ(Fetcher を使う)。
1日分 = 3ファイル(programs / previews / results)。保存先は data/raw/legacy/{kind}/YYYY/YYYYMMDD.json.gz。

使い方:
    # 22:00〜05:00 の間だけ取得。時間外は 22:00 まで待ち、05:00 になったら止まる(--nights 晩まで繰り返す)
    python -m src.collect.fetch_legacy --from 2016-08-01 --to 2025-12-31 --start-at 22:00 --stop-at 05:00 --nights 3
    python -m src.collect.fetch_legacy --from 2025-12-30 --to 2025-12-31 --dry-run

- 新しい日付から古い日付へ取る(--order asc で逆)。途中で止めても、再実行で取得済みの日は飛ばして続きから取る。
- 止め方: kill $(cat data/raw/legacy/_fetch_legacy.pid)(保存は一時ファイル→rename なので壊れたファイルは残らない)
- 終了コード: 0=全部終わった / 時間切れで正常に止まった、1=失敗した日がある・連続失敗で停止、2=引数エラー
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

from src.collect import raw_io
from src.collect.fetch import (
    DEFAULT_SLEEP_SEC,
    MAX_CONSECUTIVE_FAILURES,
    MIN_SLEEP_SEC,
    Fetcher,
    HttpResult,
    StopFetching,
    build_url,
    date_range,
    http_get,
    now_jst,
)

LEGACY_FIRST_DATE = date(2016, 8, 1)
LEGACY_LAST_DATE = date(2025, 12, 31)  # 2026-01-01 以降は turnmark / openapi を使う(重ねない)
DEFAULT_MAX_REQUESTS = 11000  # 1晩あたりの上限(全期間 約1.03万リクエスト)
PID_NAME = "_fetch_legacy.pid"


def parse_hhmm(text: str) -> dtime:
    h, m = text.split(":")
    return dtime(int(h), int(m))


def in_window(now: datetime, start: dtime, stop: dtime) -> bool:
    """now が [start, stop) の中か。start > stop なら日をまたぐ(例: 22:00〜05:00)。"""
    t = now.time().replace(tzinfo=None)
    if start <= stop:
        return start <= t < stop
    return t >= start or t < stop


def next_start(now: datetime, start: dtime) -> datetime:
    """次に start の時刻になる日時(JST)。"""
    candidate = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class OutOfWindow(Exception):
    """取得してよい時間帯が終わった。"""


class LegacyRunner:
    def __init__(self, fetcher: Fetcher, start: dtime | None, stop: dtime | None, nights: int,
                 sleep_fn: Callable[[float], None], now_fn: Callable[[], datetime], out: Callable[[str], None]):
        self.fetcher = fetcher
        self.start = start
        self.stop = stop
        self.nights = nights
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.out = out

    def _windowed(self) -> bool:
        return self.start is not None and self.stop is not None

    def wait_for_window(self) -> None:
        """時間外なら次の開始時刻まで待つ。"""
        if not self._windowed() or in_window(self.now_fn(), self.start, self.stop):
            return
        until = next_start(self.now_fn(), self.start)
        self.out(f"{self.now_fn():%Y-%m-%d %H:%M} 時間外のため {until:%Y-%m-%d %H:%M} まで待ちます")
        while self.now_fn() < until:
            self.sleep_fn(min(60.0, (until - self.now_fn()).total_seconds()))

    def pending(self, dates: list[date], today: date) -> list[date]:
        """まだ取る必要のある日(3種類のどれかがスキップでない日)。"""
        return [d for d in dates
                if any(self.fetcher.skip_reason(s, d, today) is None for s in raw_io.LEGACY_SOURCES)]

    def run_night(self, dates: list[date], today: date) -> tuple[str, dict]:
        """1晩分を取得する。戻り値: ('done' | 'window' | 'cap' | 'stopped', 件数)。"""
        counts = {"ok": 0, "not_found": 0, "failed": 0}
        consecutive_failures = 0
        try:
            for d in dates:
                day_failed = False
                for src in raw_io.LEGACY_SOURCES:
                    if self.fetcher.skip_reason(src, d, today) is not None:
                        continue
                    if self._windowed() and not in_window(self.now_fn(), self.start, self.stop):
                        raise OutOfWindow()
                    if self.fetcher.requests_sent + 4 > self.fetcher.max_requests:
                        self.out("1晩のリクエスト上限に達したので止めます")
                        return "cap", counts
                    outcome = self.fetcher.fetch_one(src, d)
                    counts[outcome] += 1
                    if outcome == "failed":
                        day_failed = True
                        break  # その日の残りは次回
                if day_failed:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise StopFetching(f"{MAX_CONSECUTIVE_FAILURES}日続けて失敗したため停止します")
                else:
                    consecutive_failures = 0
        except OutOfWindow:
            self.out(f"{self.now_fn():%Y-%m-%d %H:%M} 終了時刻になったので止めます")
            return "window", counts
        except StopFetching as e:
            self.out(f"停止: {e}")
            return "stopped", counts
        return "done", counts

    def run(self, dates: list[date], today: date) -> int:
        any_failed = False
        for night in range(1, self.nights + 1):
            todo = self.pending(dates, today)
            if not todo:
                self.out("取得すべき日はありません(すべて取得済み)")
                return 1 if any_failed else 0
            self.wait_for_window()
            self.fetcher.requests_sent = 0  # --max-requests は1晩ごと
            self.out(f"{self.now_fn():%Y-%m-%d %H:%M} {night}晩目を開始: 残り {len(todo)} 日")
            status, counts = self.run_night(todo, today)
            any_failed = any_failed or counts["failed"] > 0
            self.out(f"{night}晩目: {status} 200={counts['ok']} 404={counts['not_found']} "
                     f"失敗={counts['failed']} リクエスト数={self.fetcher.requests_sent}")
            if status == "stopped":
                return 1
            if status == "done" and not self.pending(dates, today):
                self.out("全期間の取得が終わりました")
                return 1 if any_failed else 0
            if not self._windowed():
                break
        left = len(self.pending(dates, today))
        self.out(f"指定の晩数が終わりました。残り {left} 日(もう一度実行すると続きから取ります)")
        return 1 if any_failed else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="旧API(v3)を夜間に一括取得する(PCのみ)")
    p.add_argument("--from", dest="date_from", type=raw_io.to_date, default=LEGACY_FIRST_DATE)
    p.add_argument("--to", dest="date_to", type=raw_io.to_date, default=LEGACY_LAST_DATE)
    p.add_argument("--order", choices=["desc", "asc"], default="desc", help="desc=新しい日から(既定)")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC)
    p.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS, help="1晩あたりの上限")
    p.add_argument("--start-at", type=parse_hhmm, help="取得を始めてよい時刻(JST, HH:MM)")
    p.add_argument("--stop-at", type=parse_hhmm, help="取得をやめる時刻(JST, HH:MM)")
    p.add_argument("--nights", type=int, default=1, help="時間帯指定時、何晩まで続けるか")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    if a.sleep < MIN_SLEEP_SEC:
        p.error(f"--sleep は {MIN_SLEEP_SEC} 秒以上にしてください")
    if (a.start_at is None) != (a.stop_at is None):
        p.error("--start-at と --stop-at は両方指定してください")
    if a.date_from > a.date_to:
        p.error("--from が --to より後です")
    if a.date_from < LEGACY_FIRST_DATE or a.date_to > LEGACY_LAST_DATE:
        p.error(f"旧APIの期間は {LEGACY_FIRST_DATE}〜{LEGACY_LAST_DATE} です")
    if a.max_requests < 1 or a.nights < 1:
        p.error("--max-requests と --nights は1以上")
    return a


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
    dates = date_range(args.date_from, args.date_to)
    if args.order == "desc":
        dates.reverse()
    fetcher = Fetcher(raw_dir=raw_dir, sleep_sec=args.sleep, force=args.force, max_requests=args.max_requests,
                      http_get=http_get, sleep_fn=sleep_fn, now_fn=now_fn, out=out)
    runner = LegacyRunner(fetcher, args.start_at, args.stop_at, args.nights, sleep_fn, now_fn, out)
    if args.dry_run:
        n = 0
        for d in runner.pending(dates, today):
            for s in raw_io.LEGACY_SOURCES:
                if fetcher.skip_reason(s, d, today) is None:
                    out(f"{d} {s} {build_url(s, d)}")
                    n += 1
        out(f"取得予定: {n} リクエスト")
        return 0
    pid_path = (Path(raw_dir) if raw_dir else raw_io.RAW_DIR) / "legacy" / PID_NAME
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    try:
        return runner.run(dates, today)
    finally:
        pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
