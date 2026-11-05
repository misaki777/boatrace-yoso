"""src/collect/fetch.py のテスト(ネットワーク不要。HTTP・待機・時計を差し替える)。"""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

from src.collect import fetch, raw_io
from src.collect.fetch import JST, HttpResult


def make_payload(n_stadiums: int = 2, with_odds: bool = True) -> bytes:
    """最小限の正しい形のJSON(バイト列)。"""
    race = {"date": "2026-10-01", "racers": {}}
    if with_odds:
        race["odds"] = {"trifecta": {"1-2-3": 5.0}}
    stadiums = {str(i + 1): {"races": {"1": race, "2": race}} for i in range(n_stadiums)}
    return json.dumps({"programs": {"stadiums": stadiums}}, separators=(",", ":")).encode()


class FakeClock:
    """sleep で時間が進む時計。"""

    def __init__(self, start: datetime):
        self.now = start
        self.sleeps: list[float] = []

    def now_fn(self) -> datetime:
        return self.now

    def sleep_fn(self, sec: float) -> None:
        self.sleeps.append(round(sec, 3))
        self.now += timedelta(seconds=sec)


class FakeHttp:
    """URLごとに返す結果を順に返す。未登録のURLは200(正しいJSON)。"""

    def __init__(self, clock: FakeClock, responses: dict | None = None, default=None, latency: float = 0.1):
        self.clock = clock
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.default = default
        self.latency = latency
        self.calls: list[str] = []

    def __call__(self, url: str) -> HttpResult:
        self.calls.append(url)
        self.clock.now += timedelta(seconds=self.latency)
        queue = self.responses.get(url)
        if queue:
            return queue.pop(0)
        if self.default is not None:
            return self.default
        return HttpResult(status=200, body=make_payload())


NOW = datetime(2026, 11, 2, 20, 0, 0, tzinfo=JST)
TODAY = NOW.date()


def run(tmp_path, argv, clock=None, http=None):
    clock = clock or FakeClock(NOW)
    http = http or FakeHttp(clock)
    lines: list[str] = []
    code = fetch.main(
        argv, raw_dir=tmp_path, http_get=http, sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, out=lines.append
    )
    return code, clock, http, lines


def url(source, d):
    return fetch.build_url(source, d)


# ---- 引数 ----


def test_sleep_below_one_is_error(tmp_path):
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01", "--sleep", "0.5"])
    assert code == 2
    assert http.calls == []
    with pytest.raises(ValueError):
        fetch.Fetcher(raw_dir=tmp_path, sleep_sec=0.9)


def test_sleep_exactly_one_is_ok(tmp_path):
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01", "--sleep", "1.0"])
    assert code == 0
    assert len(http.calls) == 1


def test_date_and_range_conflict(tmp_path):
    code, *_ = run(tmp_path, ["--date", "2026-10-01", "--from", "2026-10-01"])
    assert code == 2


def test_max_requests_checked_before_start(tmp_path):
    code, _, http, lines = run(
        tmp_path, ["--source", "turnmark", "--from", "2026-10-01", "--to", "2026-10-05", "--max-requests", "3"]
    )
    assert code == 2
    assert http.calls == []


# ---- 期間取得・スキップ ----


def test_range_fetch_then_rerun_sends_nothing(tmp_path):
    argv = ["--source", "turnmark", "--from", "2026-10-01", "--to", "2026-10-03"]
    code, clock, http, _ = run(tmp_path, argv)
    assert code == 0
    assert len(http.calls) == 3
    # 1件目はログが無いので待たない。2件目以降は前回のレスポンスから1.5秒待つ
    assert clock.sleeps == [1.5, 1.5]
    for d in (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)):
        assert raw_io.load_raw("turnmark", d, raw_dir=tmp_path) is not None
    log = raw_io.read_fetch_log(tmp_path)
    assert [r["status"] for r in log] == [200, 200, 200]
    assert all(r["final"] for r in log)
    assert all(len(r["sha256"]) == 64 and r["bytes"] > 0 for r in log)
    assert not list(tmp_path.rglob("*.tmp"))

    # 2回目は0リクエスト
    code, _, http2, _ = run(tmp_path, argv)
    assert code == 0
    assert http2.calls == []


def test_saved_bytes_are_unchanged(tmp_path):
    import gzip

    body = make_payload(3)
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=200, body=body))
    run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    path = tmp_path / "turnmark" / "2026" / "20261001.json.gz"
    assert gzip.decompress(path.read_bytes()) == body


def test_interval_is_kept_across_runs_via_log(tmp_path):
    # 前回の実行の最後のリクエストが0.5秒前 → 最初のリクエストの前に1.0秒待つ
    raw_io.append_fetch_log(
        {"source": "turnmark", "date": "2026-09-01", "status": 200, "final": True,
         "fetched_at": (NOW - timedelta(seconds=0.5)).isoformat()},
        tmp_path,
    )
    code, clock, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"])
    assert code == 0
    assert clock.sleeps == [1.0]


def test_future_and_turnmark_today_are_skipped(tmp_path):
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--from", "2026-11-02", "--to", "2026-11-04"])
    assert code == 0
    assert http.calls == []
    code, _, http, _ = run(tmp_path, ["--source", "openapi", "--date", "2026-11-03"])
    assert http.calls == []


def test_past_404_is_skipped_unless_force(tmp_path):
    d = date(2026, 10, 1)
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=404, error="HTTP 404"))
    code, *_ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 0  # 404 は正常
    assert len(http.calls) == 1  # 404 はリトライしない
    assert raw_io.load_raw("turnmark", d, raw_dir=tmp_path) is None

    code, _, http2, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"])
    assert http2.calls == []
    code, _, http3, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01", "--force"])
    assert len(http3.calls) == 1


def test_turnmark_yesterday_404_is_retried_next_time(tmp_path):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=404, error="HTTP 404"))
    run(tmp_path, ["--source", "turnmark", "--date", "2026-11-01"], clock, http)
    code, _, http2, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-11-01"])
    assert len(http2.calls) == 1


def test_openapi_today_not_refetched_within_10_minutes(tmp_path):
    argv = ["--source", "openapi", "--date", "2026-11-02"]
    code, _, http, _ = run(tmp_path, argv)
    assert len(http.calls) == 1
    log = raw_io.read_fetch_log(tmp_path)
    assert log[-1]["final"] is False  # 当日分は確定でない

    # 9分後: スキップ(--force でも)
    for extra in ([], ["--force"]):
        code, _, http2, _ = run(tmp_path, argv + extra, clock=FakeClock(NOW + timedelta(minutes=9)))
        assert http2.calls == []
    # 11分後: 再取得
    code, _, http3, _ = run(tmp_path, argv, clock=FakeClock(NOW + timedelta(minutes=11)))
    assert len(http3.calls) == 1


def test_openapi_past_day_refetched_until_final(tmp_path):
    argv = ["--source", "openapi", "--date", "2026-11-01"]
    # 11/2 00:30 に取得 → まだ確定でない
    code, _, http, _ = run(tmp_path, argv, clock=FakeClock(datetime(2026, 11, 2, 0, 30, tzinfo=JST)))
    assert raw_io.read_fetch_log(tmp_path)[-1]["final"] is False
    # 11/2 20:00 に再取得 → 確定
    code, _, http2, _ = run(tmp_path, argv)
    assert len(http2.calls) == 1
    assert raw_io.read_fetch_log(tmp_path)[-1]["final"] is True
    # 確定後はスキップ
    code, _, http3, _ = run(tmp_path, argv)
    assert http3.calls == []


def test_is_final_rules():
    d = date(2026, 11, 1)
    assert fetch.is_final("turnmark", d, datetime(2026, 11, 1, 12, tzinfo=JST))
    assert not fetch.is_final("openapi", d, datetime(2026, 11, 2, 0, 59, tzinfo=JST))
    assert fetch.is_final("openapi", d, datetime(2026, 11, 2, 1, 0, tzinfo=JST))


# ---- リトライ ----


def test_retry_on_5xx_with_backoff(tmp_path):
    d = date(2026, 10, 1)
    clock = FakeClock(NOW)
    err = HttpResult(status=503, error="HTTP 503")
    http = FakeHttp(clock, {url("turnmark", d): [err, err]})
    code, clock, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 0
    assert len(http.calls) == 3
    assert clock.sleeps == [2.0, 4.0]  # 2秒・4秒
    log = raw_io.read_fetch_log(tmp_path)
    assert [r["status"] for r in log] == [503, 503, 200]


def test_retry_gives_up_after_three_retries(tmp_path):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=None, error="TimeoutError"))
    code, clock, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 1
    assert len(http.calls) == 4  # 初回 + リトライ3回
    assert clock.sleeps == [2.0, 4.0, 8.0]


@pytest.mark.parametrize("status", [400, 403, 410])
def test_other_4xx_not_retried(tmp_path, status):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=status, error=f"HTTP {status}"))
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 1
    assert len(http.calls) == 1


@pytest.mark.parametrize("retry_after,expected", [("10", 10.0), ("1000", 300.0)])
def test_429_respects_retry_after(tmp_path, retry_after, expected):
    d = date(2026, 10, 1)
    clock = FakeClock(NOW)
    err = HttpResult(status=429, headers={"Retry-After": retry_after}, error="HTTP 429")
    http = FakeHttp(clock, {url("turnmark", d): [err]})
    code, clock, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 0
    assert clock.sleeps == [expected]


def test_stop_after_three_consecutive_failed_days(tmp_path):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=500, error="HTTP 500"))
    code, _, http, lines = run(
        tmp_path, ["--source", "turnmark", "--from", "2026-10-01", "--to", "2026-10-06"], clock, http
    )
    assert code == 1
    assert len(http.calls) == 3 * 4
    assert any("停止" in line for line in lines)


def test_failure_counter_resets_on_success(tmp_path):
    clock = FakeClock(NOW)
    days = [date(2026, 10, i) for i in range(1, 6)]
    fail = [HttpResult(status=500, error="HTTP 500")] * 4
    # 1,2日目失敗 → 3日目成功 → 4,5日目失敗: 3日連続にはならないので最後まで行く
    http = FakeHttp(clock, {url("turnmark", days[i]): list(fail) for i in (0, 1, 3, 4)})
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--from", "2026-10-01", "--to", "2026-10-05"], clock, http)
    assert code == 1
    assert len(http.calls) == 4 * 4 + 1


def test_invalid_json_is_failure_and_not_saved(tmp_path):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=200, body=b"<html>oops"))
    code, _, http, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 1
    assert len(http.calls) == 1
    assert raw_io.load_raw("turnmark", date(2026, 10, 1), raw_dir=tmp_path) is None
    # 次回は再試行される
    code, _, http2, _ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"])
    assert len(http2.calls) == 1


# ---- auto ----


def test_auto_uses_openapi_for_today_and_fallback_for_404(tmp_path):
    d1, d2 = date(2026, 10, 31), date(2026, 11, 1)
    clock = FakeClock(NOW)
    http = FakeHttp(clock, {url("turnmark", d1): [HttpResult(status=404, error="HTTP 404")]})
    code, _, http, _ = run(tmp_path, ["--source", "auto", "--from", "2026-10-31", "--to", "2026-11-02"], clock, http)
    assert code == 0
    assert http.calls == [
        url("turnmark", d1),
        url("openapi", d1),
        url("turnmark", d2),
        url("openapi", TODAY),
    ]
    # 2回目: 10/31 は turnmark 404済み・openapi 確定済み → 0件。11/1 は turnmark 確定済み。当日は10分以内
    code, _, http2, _ = run(tmp_path, ["--source", "auto", "--from", "2026-10-31", "--to", "2026-11-02"])
    assert http2.calls == []


# ---- dry-run ----


def test_dry_run_prints_urls_without_access(tmp_path):
    code, _, http, lines = run(tmp_path, ["--source", "turnmark", "--from", "2026-10-01", "--to", "2026-10-02", "--dry-run"])
    assert code == 0
    assert http.calls == []
    assert any(url("turnmark", date(2026, 10, 1)) in line for line in lines)
    assert any(url("turnmark", date(2026, 10, 2)) in line for line in lines)
    assert not (tmp_path / raw_io.FETCH_LOG_NAME).exists()


# ---- 簡易チェック ----


def test_check_payload():
    assert fetch.check_payload("turnmark", json.loads(make_payload())) == []
    assert fetch.check_payload("openapi", json.loads(make_payload(with_odds=False))) == []
    assert fetch.check_payload("turnmark", json.loads(make_payload(with_odds=False)))
    assert fetch.check_payload("openapi", {"foo": 1})
    assert fetch.check_payload("openapi", json.loads(make_payload(n_stadiums=25)))


def test_warning_is_logged_but_saved(tmp_path):
    clock = FakeClock(NOW)
    http = FakeHttp(clock, default=HttpResult(status=200, body=make_payload(with_odds=False)))
    code, *_ = run(tmp_path, ["--source", "turnmark", "--date", "2026-10-01"], clock, http)
    assert code == 0
    assert raw_io.read_fetch_log(tmp_path)[-1]["warning"]
    assert raw_io.load_raw("turnmark", "2026-10-01", raw_dir=tmp_path) is not None


# ---- urllib のモック ----


class FakeResponse(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_http_get_with_mocked_urllib(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResponse(b'{"a": 1}')

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    result = fetch.http_get("https://example.invalid/x.json")
    assert result.status == 200 and result.body == b'{"a": 1}'
    assert seen == {"ua": fetch.USER_AGENT, "timeout": 30}


def test_http_get_maps_errors(monkeypatch):
    def raise_404(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", raise_404)
    assert fetch.http_get("https://example.invalid/x.json").status == 404

    def raise_timeout(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", raise_timeout)
    result = fetch.http_get("https://example.invalid/x.json")
    assert result.status is None and fetch.is_retryable(result)
