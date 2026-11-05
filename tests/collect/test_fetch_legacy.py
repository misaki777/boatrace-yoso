"""src/collect/fetch_legacy.py のテスト(ネットワーク不要)。"""

import json
from datetime import date, datetime, time, timedelta

from src.collect import fetch_legacy, raw_io
from src.collect.fetch import JST, HttpResult


def payload(kind: str) -> bytes:
    races = [{"date": "2025-12-31", "stadium_number": s, "number": 1, "boats": []} for s in (1, 2)]
    return json.dumps({kind: races}).encode()


class Clock:
    def __init__(self, start):
        self.now = start
        self.sleeps = []

    def now_fn(self):
        return self.now

    def sleep_fn(self, sec):
        self.sleeps.append(sec)
        self.now += timedelta(seconds=sec)


class Http:
    def __init__(self, clock, latency=0.5, fail=False):
        self.clock, self.latency, self.fail, self.calls = clock, latency, fail, []

    def __call__(self, url):
        self.calls.append(url)
        self.clock.now += timedelta(seconds=self.latency)
        if self.fail:
            return HttpResult(status=500, error="HTTP 500")
        kind = url.split("/")[3]
        return HttpResult(status=200, body=payload(kind))


def run(tmp_path, argv, clock, http=None):
    http = http or Http(clock)
    lines = []
    code = fetch_legacy.main(argv, raw_dir=tmp_path, http_get=http, sleep_fn=clock.sleep_fn,
                             now_fn=clock.now_fn, out=lines.append)
    return code, http, lines


DAY = datetime(2026, 11, 5, 23, 0, tzinfo=JST)


def test_in_window_across_midnight():
    s, e = time(22), time(5)
    assert fetch_legacy.in_window(datetime(2026, 1, 1, 23, tzinfo=JST), s, e)
    assert fetch_legacy.in_window(datetime(2026, 1, 1, 4, 59, tzinfo=JST), s, e)
    assert not fetch_legacy.in_window(datetime(2026, 1, 1, 5, 0, tzinfo=JST), s, e)
    assert not fetch_legacy.in_window(datetime(2026, 1, 1, 12, tzinfo=JST), s, e)


def test_fetch_three_kinds_and_resume(tmp_path):
    clock = Clock(DAY)
    code, http, _ = run(tmp_path, ["--from", "2025-12-30", "--to", "2025-12-31"], clock)
    assert code == 0
    assert len(http.calls) == 6
    assert http.calls[0].endswith("/programs/v3/2025/20251231.json")  # 新しい日から
    assert all(s >= 1.5 for s in clock.sleeps)
    for kind in raw_io.LEGACY_KINDS:
        assert raw_io.load_legacy(kind, "2025-12-30", tmp_path)[kind][0]["stadium_number"] == 1
    assert (tmp_path / "legacy" / "results" / "2025" / "20251231.json.gz").exists()
    assert all(r["final"] for r in raw_io.read_fetch_log(tmp_path))
    assert not (tmp_path / "legacy" / fetch_legacy.PID_NAME).exists()
    code, http2, _ = run(tmp_path, ["--from", "2025-12-30", "--to", "2025-12-31"], clock)
    assert http2.calls == []


def test_waits_until_start_and_stops_at_stop(tmp_path):
    clock = Clock(datetime(2026, 11, 5, 21, 0, tzinfo=JST))
    # 1リクエスト = 0.5秒通信 + 1.5秒待ち。終了を 22:00:05 にすると数件で止まる
    code, http, lines = run(tmp_path, ["--from", "2025-12-01", "--to", "2025-12-31",
                                       "--start-at", "22:00", "--stop-at", "22:01"], clock)
    assert code == 0
    assert any("待ちます" in l for l in lines)
    assert any("終了時刻" in l for l in lines)
    assert 0 < len(http.calls) < 93
    first = raw_io.read_fetch_log(tmp_path)[0]["fetched_at"]
    assert first >= "2026-11-05T22:00"
    # 再開すると続きから(取得済みは送らない)
    done = {u for u in http.calls}
    clock2 = Clock(datetime(2026, 11, 6, 23, 0, tzinfo=JST))
    code, http2, _ = run(tmp_path, ["--from", "2025-12-01", "--to", "2025-12-31"], clock2)
    assert not done & set(http2.calls)
    assert len(http.calls) + len(http2.calls) == 93


def test_multiple_nights(tmp_path):
    clock = Clock(datetime(2026, 11, 5, 22, 0, tzinfo=JST))
    code, http, lines = run(tmp_path, ["--from", "2025-12-01", "--to", "2025-12-31", "--start-at", "22:00",
                                       "--stop-at", "22:01", "--nights", "5"], clock)
    assert code == 0
    assert len(http.calls) == 93
    assert any("全期間の取得が終わりました" in l for l in lines)


def test_max_requests_per_night(tmp_path):
    clock = Clock(DAY)
    code, http, lines = run(tmp_path, ["--from", "2025-12-01", "--to", "2025-12-31", "--max-requests", "10"], clock)
    assert len(http.calls) <= 10
    assert any("上限" in l for l in lines)


def test_stop_after_three_failed_days(tmp_path):
    clock = Clock(DAY)
    code, http, lines = run(tmp_path, ["--from", "2025-12-01", "--to", "2025-12-31"], clock, Http(clock, fail=True))
    assert code == 1
    assert len(http.calls) == 3 * 4  # 1日目の programs だけで失敗 → 次の日へ。3日で停止
    assert any("停止" in l for l in lines)


def test_args(tmp_path):
    clock = Clock(DAY)
    assert run(tmp_path, ["--sleep", "0.5"], clock)[0] == 2
    assert run(tmp_path, ["--start-at", "22:00"], clock)[0] == 2
    assert run(tmp_path, ["--to", "2026-01-01"], clock)[0] == 2
    code, http, lines = run(tmp_path, ["--from", "2025-12-31", "--to", "2025-12-31", "--dry-run"], clock)
    assert code == 0 and http.calls == [] and len(lines) == 4


def test_check_payload_legacy():
    from src.collect.fetch import check_payload
    assert check_payload("legacy_results", json.loads(payload("results"))) == []
    assert check_payload("legacy_results", {"programs": []})
