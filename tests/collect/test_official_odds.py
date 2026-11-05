"""src/collect/official_odds.py と raw_io の締切前オッズの関数のテスト(ネットワーク不要)。

解析のテストデータは tests/fixtures/official_odds/ の合成HTML(実物の構造を写し、選手名を消し、
数値を架空にしたもの。src/collect/make_odds_fixtures.py で作成)。実物のHTMLはリポジトリに置かない。
実物での確認は data/raw/official_odds/ に手元のファイルがあるときだけ行う(無ければ skip)。
"""

import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.collect import official_odds as oo
from src.collect import raw_io
from src.collect.fetch import JST, HttpResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "official_odds"
REAL_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "official_odds"
PAGES = ("odds3t", "odds2tf", "oddstf")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def synthetic(page: str) -> str:
    return fixture(f"{page}.synthetic.html")


def expected(page: str) -> dict:
    return json.loads(fixture(f"{page}.expected.json"))


# =====================================================================
# 解析(共通の合成HTML。中継 Worker の JS のテストも同じファイルを読む)
# =====================================================================


@pytest.mark.parametrize("page", PAGES)
def test_synthetic_matches_expected(page):
    odds, label = oo.parse_page(page, synthetic(page))
    exp = expected(page)
    assert odds == exp["odds"]
    assert label == exp["odds_time_label"]
    assert oo.is_final_label(label) is exp["is_final_odds"]


def test_synthetic_values_encode_combinations():
    """架空の値は組合せを表す(並びの取り違えを見つけるため)。"""
    tri = expected("odds3t")["odds"]["trifecta"]
    assert tri["2"]["1"]["6"] == 216.8
    assert tri["1"]["2"]["3"] is None  # 票の無い組合せ "0.0"
    assert tri["4"]["1"]["2"] is None and tri["1"]["4"]["2"] is None  # 欠場艇(空欄)
    two = expected("odds2tf")["odds"]
    assert two["exacta"]["2"]["1"] == 21.2
    assert two["quinella"]["1"]["2"] == 12.5 and two["quinella"]["5"]["6"] == 56.5
    tf = expected("oddstf")["odds"]
    assert tf["win"]["5"] is None and tf["place"]["5"] is None  # 欠場
    assert tf["place"]["1"] == {"lower_limit": 1.0, "upper_limit": 2.1}


def test_synthetic_has_both_labels():
    assert expected("odds2tf")["odds_time_label"] == "締切時オッズ"
    assert expected("odds3t")["odds_time_label"] == "オッズ更新時間 10:40"


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("broken_*.html")))
def test_broken_fixtures_raise(name):
    page = name.split("_")[1]
    with pytest.raises(oo.OddsParseError):
        oo.parse_page(page, fixture(name))


def test_broken_fixtures_cover_all_pages():
    assert {p.name.split("_")[1] for p in FIXTURES.glob("broken_*.html")} == set(PAGES)


def test_no_real_names_in_fixtures():
    """合成HTMLに選手名が残っていない(名前の欄は「選手」か「選手N」だけ)。"""
    import re

    for path in FIXTURES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'is-borderLeftNone"[^>]*>([^<]*)</th>|is-fs18 is-fBold">([^<]*)</span>', text):
            name = m.group(1) or m.group(2)
            assert re.fullmatch(r"選手\d?", name), (path.name, name)


def test_unverified_page_is_not_parsed():
    odds, label = oo.parse_page("odds3f", "<p class='tab4_refreshText'>オッズ更新時間 10:00</p>")
    assert odds is None and label == "オッズ更新時間 10:00"


@pytest.mark.parametrize(
    "text,expected_value", [("27.5", 27.5), ("1,234.5", 1234.5), ("0.0", None), ("-", None), ("", None), ("欠場", None)]
)
def test_to_odds(text, expected_value):
    assert oo.to_odds(text) == expected_value


def test_to_range():
    assert oo.to_range("1.0-1.0") == {"lower_limit": 1.0, "upper_limit": 1.0}
    assert oo.to_range("-") is None
    assert oo.to_range("") is None


# =====================================================================
# 実物のHTML(data/raw/ に手元のファイルがあるときだけ。無ければ skip)
# =====================================================================


def _real_files(page: str) -> list[Path]:
    return sorted(REAL_DIR.glob(f"*/*/*_{page}_T*.html.gz")) if REAL_DIR.exists() else []


@pytest.mark.parametrize("page", PAGES)
def test_real_html_order_matches_boat_cells(page):
    """実物で、解析の並びの式が、表の艇番のセルから独立に求めた並びと一致する。"""
    from src.collect import make_odds_fixtures as mf

    files = _real_files(page)
    if not files:
        pytest.skip("data/raw/official_odds/ に実物のHTMLが無い")
    for path in files:
        html = gzip.open(path).read().decode("utf-8")
        odds, label = oo.parse_page(page, html)
        cells, _ = oo.extract_cells(html)
        assert mf.expected_odds(page, cells, mf.derive_combinations(page, html)) == odds, path.name
        assert label, path.name


def test_real_html_toda_20261102_payouts():
    """2026-11-02 戸田1R(締切後に取得)の値が公式の払戻と一致する。"""
    day = REAL_DIR / "2026" / "20261102"
    p3, p2 = day / "02-01_odds3t_TC.html.gz", day / "02-01_odds2tf_TC.html.gz"
    if not (p3.exists() and p2.exists()):
        pytest.skip("手元に 2026-11-02 戸田1R の実物のHTMLが無い")
    odds3, label = oo.parse_page("odds3t", gzip.open(p3).read().decode("utf-8"))
    odds2, _ = oo.parse_page("odds2tf", gzip.open(p2).read().decode("utf-8"))
    assert odds3["trifecta"]["2"]["1"]["6"] == 27.5  # 払戻 2,750円
    assert odds2["exacta"]["2"]["1"] == 4.9  # 490円
    assert odds2["quinella"]["1"]["2"] == 2.7  # 270円
    assert label == "締切時オッズ"


# =====================================================================
# 取得(HTTP・待機・時計を差し替える)
# =====================================================================


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start
        self.sleeps: list[float] = []

    def now_fn(self) -> datetime:
        return self.now

    def sleep_fn(self, sec: float) -> None:
        self.sleeps.append(round(sec, 3))
        self.now += timedelta(seconds=sec)


class FakeHttp:
    """URLのページ名で fixture を返す。queue に入れた結果があれば先に返す。"""

    def __init__(self, clock: FakeClock, queue: list | None = None, latency: float = 10.0, default=None):
        self.clock = clock
        self.queue = list(queue or [])
        self.latency = latency
        self.default = default
        self.calls: list[str] = []
        self.call_times: list[tuple[datetime, datetime]] = []

    def __call__(self, url: str) -> HttpResult:
        self.calls.append(url)
        start = self.clock.now
        self.clock.now += timedelta(seconds=self.latency)
        self.call_times.append((start, self.clock.now))
        if self.queue:
            return self.queue.pop(0)
        if self.default is not None:
            return self.default
        page = url.split("/race/")[1].split("?")[0]
        if page not in PAGES:
            return HttpResult(status=200, body=b"<html></html>")
        return HttpResult(status=200, body=synthetic(page).encode("utf-8"))


DAY = datetime(2026, 11, 3, tzinfo=JST).date()


def write_program(raw_dir: Path, races: list[tuple[int, int, str]], day_source: str = "3日目") -> None:
    """openapi の当日ファイル(最小限)を書く。races: [(場, R, 'HH:MM')]。"""
    stadiums: dict = {}
    for sn, rn, hm in races:
        stadiums.setdefault(str(sn), {"races": {}})["races"][str(rn)] = {
            "date": DAY.isoformat(),
            "stadium_number": sn,
            "race_number": rn,
            "closed_at": f"{DAY.isoformat()} {hm}:00",
            "day_number_source": day_source,
            "racers": {},
        }
    body = json.dumps({"programs": {"stadiums": stadiums}}).encode()
    raw_io.write_raw_bytes("openapi", DAY, body, raw_dir)


def at(hm: str, sec: int = 0) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 3, h, m, sec, tzinfo=JST)


def run(tmp_path, argv, clock, http, refresh=None):
    lines: list[str] = []
    code = oo.main(
        argv,
        raw_dir=tmp_path,
        http_get=http,
        sleep_fn=clock.sleep_fn,
        now_fn=clock.now_fn,
        out=lines.append,
        refresh_program=refresh or (lambda: None),
    )
    return code, lines


def official_log(tmp_path) -> list[dict]:
    return [r for r in raw_io.read_fetch_log(tmp_path) if r.get("source") == "official_odds"]


def test_single_race_saves_html_json_and_log(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock)
    code, lines = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 0
    assert http.calls == [
        "https://www.boatrace.jp/owpc/pc/race/odds3t?rno=5&jcd=24&hd=20261103",
        "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno=5&jcd=24&hd=20261103",
        "https://www.boatrace.jp/owpc/pc/race/oddstf?rno=5&jcd=24&hd=20261103",
    ]
    day_dir = raw_io.official_odds_day_dir(DAY, tmp_path)
    assert (day_dir / "24-05_odds3t_T10.html.gz").exists()
    doc = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    assert doc["status"] == "ok"
    assert doc["timing"] == "T10"
    assert doc["closed_at"] == "2026-11-03T17:17:00+09:00"
    assert doc["fetched_at"] == "2026-11-03T17:07:10+09:00"
    assert doc["minutes_before_close"] == 9.8
    assert doc["odds_time_label"] == "オッズ更新時間 10:40"
    assert doc["is_final_odds"] is True  # 合成の odds2tf は「締切時オッズ」の表記
    assert set(doc["odds"]) == {"trifecta", "exacta", "quinella", "win", "place"}
    log = official_log(tmp_path)
    assert [r["page"] for r in log] == ["odds3t", "odds2tf", "oddstf"]
    assert all(r["status"] == 200 and r["elapsed_ms"] == 10000 and r["race_id"] == "20261103-24-05" for r in log)
    # 2回目は(T が変わっても)取らない。--force なら取り直す
    clock.now += timedelta(minutes=2)
    code, lines = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 0 and len(http.calls) == 3
    code, lines = run(tmp_path, ["--race", "20261103-24-05", "--force"], clock, http)
    assert len(http.calls) == 6


def test_interval_after_response_end(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock)
    run(tmp_path, ["--race", "20261103-24-05", "--sleep", "1.5"], clock, http)
    gaps = [(b[0] - a[1]).total_seconds() for a, b in zip(http.call_times, http.call_times[1:])]
    assert gaps == [1.5, 1.5]


@pytest.mark.parametrize("status", [403, 429])
def test_403_429_stops_for_the_day(tmp_path, status):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, queue=[HttpResult(status=status, error=f"HTTP {status}")])
    code, lines = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 1
    assert len(http.calls) == 1  # リトライしない
    stopped = raw_io.load_official_odds_stopped(DAY, tmp_path)
    assert str(status) in stopped["reason"]
    assert any(r.get("event") == "stopped" for r in official_log(tmp_path))
    # 止めた日は、ループも一括も動かない
    code, lines = run(tmp_path, ["--loop"], clock, http)
    assert code == 1 and len(http.calls) == 1
    code, lines = run(tmp_path, ["--window-minutes", "15"], clock, http)
    assert code == 1 and len(http.calls) == 1


def test_resume_removes_stopped_marker(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, queue=[HttpResult(status=403, error="HTTP 403")])
    run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    code, lines = run(tmp_path, ["--window-minutes", "15", "--resume"], clock, http)
    assert raw_io.load_official_odds_stopped(DAY, tmp_path) is None
    assert code == 0 and len(http.calls) == 4


def test_retry_once_on_5xx_then_success(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, queue=[HttpResult(status=503, error="HTTP 503")])
    code, _ = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 0
    assert len(http.calls) == 4  # odds3t を2回(リトライ1回) + 残り2ページ
    assert http.calls[0] == http.calls[1]
    assert (http.call_times[1][0] - http.call_times[0][1]).total_seconds() == oo.RETRY_WAIT_SEC


def test_404_not_retried_and_partial(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, queue=[HttpResult(status=404, error="HTTP 404")])
    code, _ = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 1
    assert len(http.calls) == 3
    doc = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    assert doc["status"] == "partial"
    assert "trifecta" not in doc["odds"] and "win" in doc["odds"]


def test_three_consecutive_failures_stop(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17"), (24, 6, "17:45")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, default=HttpResult(status=500, error="HTTP 500"))
    code, _ = run(tmp_path, ["--window-minutes", "60"], clock, http)
    assert code == 1
    assert len(http.calls) == 6  # 3ページ × (1回 + リトライ1回)で止まる。2レース目は取らない
    assert "3回続けて失敗" in raw_io.load_official_odds_stopped(DAY, tmp_path)["reason"]


def test_parse_error_counts_as_failure(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, default=HttpResult(status=200, body=b"<html>changed</html>"))
    code, _ = run(tmp_path, ["--race", "20261103-24-05"], clock, http)
    assert code == 1
    assert len(http.calls) == 3
    assert raw_io.load_official_odds_stopped(DAY, tmp_path) is not None
    # 解析できなくても生HTMLは残す(後で直せるように)
    assert (raw_io.official_odds_day_dir(DAY, tmp_path) / "24-05_odds3t_T10.html.gz").exists()


def test_daily_request_cap(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17"), (24, 6, "17:20")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock)
    code, _ = run(tmp_path, ["--window-minutes", "15", "--max-requests-per-day", "4"], clock, http)
    assert code == 1
    assert len(http.calls) == 3  # 2レース目は上限を超えるので始めない
    assert "上限" in raw_io.load_official_odds_stopped(DAY, tmp_path)["reason"]
    # 前回までの送信数は取得ログから数える
    fetcher = oo.OfficialOddsFetcher(raw_dir=tmp_path, now_fn=clock.now_fn)
    assert fetcher.requests_today() == 3


# =====================================================================
# 常駐ループ
# =====================================================================


def test_loop_fetches_at_n_minutes_before_and_marks_late(tmp_path):
    # 17:00 開始。24-04 は締切 17:01(2分前を過ぎている → late)、24-05 は 17:17、01-06 は 17:27
    write_program(tmp_path, [(24, 4, "17:01"), (24, 5, "17:17"), (1, 6, "17:27"), (19, 1, "17:25")], "3日目")
    clock = FakeClock(at("17:00"))
    http = FakeHttp(clock)
    refreshes = []
    code, lines = run(tmp_path, ["--loop", "--pages", "odds3t"], clock, http, refresh=lambda: refreshes.append(clock.now))
    assert code == 0
    assert raw_io.load_official_odds("20261103-24-04", raw_dir=tmp_path)["status"] == "late"
    doc5 = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    assert doc5["timing"] == "T05" and doc5["scheduled_minutes_before"] == 5
    assert doc5["fetched_at"] == "2026-11-03T17:12:10+09:00"  # 17:12 に取り始め、応答10秒
    # 19-01(17:25)は 17:20、01-06(17:27)は 17:22 に取る
    starts = [s for s, _ in http.call_times]
    assert starts == [at("17:12"), at("17:20"), at("17:22")]
    assert len(refreshes) == 1
    assert any(r.get("event") == "late" for r in official_log(tmp_path))


def test_loop_skips_canceled_day(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")], day_source="中止")
    clock = FakeClock(at("17:00"))
    http = FakeHttp(clock)
    code, _ = run(tmp_path, ["--loop"], clock, http)
    assert code == 0 and http.calls == []


def test_loop_only_today(tmp_path):
    clock = FakeClock(at("17:00"))
    code, _ = run(tmp_path, ["--loop", "--date", "2026-11-02"], clock, FakeHttp(clock))
    assert code == 2


def test_loop_refreshes_program_every_30_minutes(tmp_path):
    write_program(tmp_path, [(24, 5, "18:10")])
    clock = FakeClock(at("17:00"))
    http = FakeHttp(clock)
    refreshes = []
    run(tmp_path, ["--loop", "--pages", "odds3t"], clock, http, refresh=lambda: refreshes.append(clock.now))
    assert refreshes == [at("17:00"), at("17:30"), at("18:00")]
    assert [s for s, _ in http.call_times] == [at("18:05")]


# =====================================================================
# 一括取得モード(--window-minutes)
# =====================================================================


def test_batch_fetches_window_and_skips(tmp_path):
    write_program(
        tmp_path,
        [
            (24, 3, "16:40"),  # 直近 window 分より前に締切(前々回以前の範囲) → 触らない
            (24, 4, "17:01"),  # 締切2分前を過ぎた・記録なし → late
            (24, 5, "17:10"),  # 取得
            (1, 6, "17:14"),  # 取得済み → スキップ
            (19, 1, "17:15"),  # 取得
            (15, 6, "17:16"),  # 窓の外(17:00 + 15分 より後) → 次回
        ],
    )
    clock = FakeClock(at("17:00"))
    # 01-06 は取得済みにしておく
    raw_io.write_gzip_atomic(
        raw_io.official_odds_paths("20261103-01-06", "T05", None, tmp_path),
        json.dumps({"race_id": "20261103-01-06", "status": "ok", "fetched_at": "2026-11-03T17:09:00+09:00"}).encode(),
    )
    http = FakeHttp(clock)
    refreshes = []
    code, lines = run(tmp_path, ["--window-minutes", "15"], clock, http, refresh=lambda: refreshes.append(1))
    assert code == 0
    assert len(refreshes) == 1
    fetched = sorted({u.split("rno=")[1] for u in http.calls})
    assert fetched == ["1&jcd=19&hd=20261103", "5&jcd=24&hd=20261103"]
    assert raw_io.load_official_odds("20261103-24-03", raw_dir=tmp_path) is None
    late = raw_io.load_official_odds("20261103-24-04", raw_dir=tmp_path)
    assert late["status"] == "late" and late["timing"] == "TL"
    doc = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    assert doc["status"] == "ok" and doc["timing"] == "T10"  # 17:00 に取り始め、締切10分前
    assert raw_io.load_official_odds("20261103-15-06", raw_dir=tmp_path) is None
    assert any("完了(一括 15分)" in line and "ok=2" in line and "late=1" in line for line in lines)
    # もう一度起動すると、取得済みのレースは送らず、窓に入ってきた 15-06 だけを取る
    n = len(http.calls)
    code, _ = run(tmp_path, ["--window-minutes", "15"], clock, http)
    assert {u.split("rno=")[1] for u in http.calls[n:]} == {"6&jcd=15&hd=20261103"}


def test_batch_rechecks_late_while_running(tmp_path):
    # 1レース約35秒かかるので、2レース目は取り始める時点で2分前を過ぎる
    write_program(tmp_path, [(24, 5, "17:02:30"[:5]), (19, 1, "17:02")])
    clock = FakeClock(at("16:59", 50))
    http = FakeHttp(clock)
    run(tmp_path, ["--window-minutes", "10"], clock, http)
    assert raw_io.load_official_odds("20261103-19-01", raw_dir=tmp_path)["status"] == "ok"
    assert raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)["status"] == "late"


def test_output_has_no_odds_numbers(tmp_path):
    """画面への出力(Actions のログになる)にオッズの数値を出さない。"""
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock)
    code, lines = run(tmp_path, ["--window-minutes", "15"], clock, http)
    doc = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    values = set()
    for bet in doc["odds"].values():
        stack = [bet]
        while stack:
            v = stack.pop()
            if isinstance(v, dict):
                stack.extend(v.values())
            elif v is not None:
                values.add(v)
    text = "\n".join(lines)
    distinctive = {v for v in values if v >= 10 and v != int(v)}  # 22.5、137.2 など
    assert distinctive
    assert not any(f"{v}" in text for v in distinctive)


def test_window_minutes_range(tmp_path):
    clock = FakeClock(at("17:00"))
    code, _ = run(tmp_path, ["--window-minutes", "120"], clock, FakeHttp(clock))
    assert code == 2


# =====================================================================
# dry-run・引数
# =====================================================================


def test_dry_run_counts_and_no_access(tmp_path):
    write_program(tmp_path, [(24, 4, "17:01"), (24, 5, "17:17"), (1, 6, "17:27")])
    clock = FakeClock(at("17:00"))
    http = FakeHttp(clock)
    code, lines = run(tmp_path, ["--dry-run"], clock, http)
    assert code == 0 and http.calls == []
    assert any("17:12 20261103-24-05 締切17:17" in line for line in lines)
    assert any("late予定" in line for line in lines)
    assert any("2 レース × 3 ページ = 6 リクエスト" in line for line in lines)


def test_dry_run_stadium_and_pages_filter(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17"), (1, 6, "17:27")])
    clock = FakeClock(at("17:00"))
    code, lines = run(tmp_path, ["--dry-run", "--stadiums", "1", "--pages", "odds3t"], clock, FakeHttp(clock))
    assert any("1 レース × 1 ページ = 1 リクエスト" in line for line in lines)
    assert not any("24-05" in line for line in lines)


def test_race_ids_file(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17"), (1, 6, "17:27")])
    path = tmp_path / "ids.txt"
    path.write_text("20261103-01-06\n", encoding="utf-8")
    clock = FakeClock(at("17:00"))
    code, lines = run(tmp_path, ["--dry-run", "--race-ids-file", str(path)], clock, FakeHttp(clock))
    assert any("1 レース" in line for line in lines)


@pytest.mark.parametrize(
    "argv",
    [
        ["--loop", "--sleep", "0.5"],
        ["--loop", "--minutes-before", "2"],
        ["--loop", "--minutes-before", "16"],
        ["--loop", "--pages", "odds9"],
        ["--loop", "--stadiums", "25"],
        ["--race", "2026-11-03"],
        ["--loop", "--dry-run"],
        [],
    ],
)
def test_bad_args(tmp_path, argv, capsys):
    clock = FakeClock(at("17:00"))
    code, _ = run(tmp_path, argv, clock, FakeHttp(clock))
    assert code == 2


def test_missing_program_file(tmp_path):
    clock = FakeClock(at("17:00"))
    code, lines = run(tmp_path, ["--race", "20261103-24-05"], clock, FakeHttp(clock))
    assert code == 1 and "openapi" in lines[-1]


def test_unverified_page_saved_but_listed_as_unparsed(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock)
    code, _ = run(tmp_path, ["--race", "20261103-24-05", "--pages", "odds3t,odds3f"], clock, http)
    doc = raw_io.load_official_odds("20261103-24-05", raw_dir=tmp_path)
    assert doc["status"] == "ok" and doc["unparsed_pages"] == ["odds3f"]
    assert (raw_io.official_odds_day_dir(DAY, tmp_path) / "24-05_odds3f_T10.html.gz").exists()


# =====================================================================
# raw_io.load_official_odds
# =====================================================================


def _put(tmp_path, race_id, timing, fetched_at, status="ok"):
    doc = {"race_id": race_id, "status": status, "fetched_at": fetched_at, "timing": timing}
    raw_io.write_gzip_atomic(raw_io.official_odds_paths(race_id, timing, None, tmp_path), json.dumps(doc).encode())


def test_load_official_odds_latest_and_timing(tmp_path):
    rid = "20261103-02-01"
    assert raw_io.load_official_odds(rid, raw_dir=tmp_path) is None
    _put(tmp_path, rid, "T10", "2026-11-03T10:35:00+09:00")
    _put(tmp_path, rid, "T05", "2026-11-03T10:40:00+09:00")
    _put(tmp_path, rid, "TL", None, status="late")
    assert raw_io.load_official_odds(rid, raw_dir=tmp_path)["timing"] == "T05"
    assert raw_io.load_official_odds(rid, 10, raw_dir=tmp_path)["timing"] == "T10"
    assert raw_io.load_official_odds(rid, "TL", raw_dir=tmp_path)["status"] == "late"
    assert raw_io.load_official_odds(rid, 7, raw_dir=tmp_path) is None
    # 別のレースは混ざらない
    assert raw_io.load_official_odds("20261103-02-11", raw_dir=tmp_path) is None


def test_official_odds_paths_layout(tmp_path):
    p = raw_io.official_odds_paths("20261103-02-01", "T05", "odds3t", tmp_path)
    assert p == tmp_path / "official_odds" / "2026" / "20261103" / "02-01_odds3t_T05.html.gz"
    with pytest.raises(ValueError):
        raw_io.official_odds_paths("2026-11-03", "T05", None, tmp_path)


def test_saved_html_is_gzip_of_body(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")])
    clock = FakeClock(at("17:07"))
    run(tmp_path, ["--race", "20261103-24-05", "--pages", "oddstf"], clock, FakeHttp(clock))
    path = raw_io.official_odds_day_dir(DAY, tmp_path) / "24-05_oddstf_T10.html.gz"
    assert gzip.open(path).read().decode("utf-8") == synthetic("oddstf")


# =====================================================================
# 対象外(not_targeted)の記録
# =====================================================================


def test_not_targeted_recorded_for_filtered_races(tmp_path):
    write_program(tmp_path, [(24, 5, "17:10"), (1, 6, "17:12")])
    clock = FakeClock(at("17:00"))
    http = FakeHttp(clock)
    code, _ = run(tmp_path, ["--window-minutes", "15", "--stadiums", "24", "--pages", "odds3t"], clock, http)
    assert code == 0
    targets = raw_io.load_official_odds_targets(DAY, tmp_path)
    assert targets["targeted"] == ["20261103-24-05"]
    assert targets["not_targeted"] == ["20261103-01-06"]
    assert targets["pages"] == ["odds3t"]
    assert raw_io.official_odds_status("20261103-24-05", tmp_path) == "ok"
    assert raw_io.official_odds_status("20261103-01-06", tmp_path) == "not_targeted"
    # 後の起動で対象になれば not_targeted から外れる
    run(tmp_path, ["--window-minutes", "15", "--pages", "odds3t"], clock, http)
    targets = raw_io.load_official_odds_targets(DAY, tmp_path)
    assert targets["not_targeted"] == []
    assert raw_io.official_odds_status("20261103-01-06", tmp_path) == "ok"


def test_status_stopped_and_unknown(tmp_path):
    assert raw_io.official_odds_status("20261103-24-05", tmp_path) is None
    write_program(tmp_path, [(24, 5, "17:17"), (24, 6, "17:45")])
    clock = FakeClock(at("17:07"))
    http = FakeHttp(clock, queue=[HttpResult(status=403, error="HTTP 403")])
    run(tmp_path, ["--window-minutes", "60"], clock, http)
    assert raw_io.official_odds_status("20261103-24-06", tmp_path) == "stopped"


def test_canceled_recorded(tmp_path):
    write_program(tmp_path, [(24, 5, "17:17")], day_source="中止")
    clock = FakeClock(at("17:00"))
    run(tmp_path, ["--window-minutes", "30"], clock, FakeHttp(clock))
    assert raw_io.load_official_odds_targets(DAY, tmp_path)["canceled"] == ["20261103-24-05"]
