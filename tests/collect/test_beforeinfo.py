"""src/collect/beforeinfo.py のテスト(ネットワーク不要)。共通の合成HTML(tests/fixtures/beforeinfo/)を使う。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.collect import beforeinfo, raw_io
from src.collect.fetch import JST, HttpResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "beforeinfo"
CASES = ["not_posted", "partial", "ok", "flying", "absent", "maeduke"]


def load(name):
    html = (FIXTURES / f"{name}.synthetic.html").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    return html, expected


@pytest.mark.parametrize("name", CASES)
def test_fixture_matches_expected(name):
    html, expected = load(name)
    assert beforeinfo.parse_beforeinfo(html) == expected


def test_statuses():
    assert beforeinfo.parse_beforeinfo(load("not_posted")[0])["status"] == "not_posted"
    assert beforeinfo.parse_beforeinfo(load("partial")[0])["status"] == "partial"
    assert beforeinfo.parse_beforeinfo(load("absent")[0])["status"] == "ok"


def test_flying_is_negative_with_flag():
    b = beforeinfo.parse_beforeinfo(load("flying")[0])["boats"]
    assert b["3"]["start_timing"] == -0.02 and b["3"]["start_flying"] is True
    assert b["4"]["start_timing"] == 0.0 and b["4"]["start_flying"] is False


def test_maeduke_courses():
    b = beforeinfo.parse_beforeinfo(load("maeduke")[0])["boats"]
    assert [b[str(n)]["start_course"] for n in range(1, 7)] == [1, 2, 4, 5, 6, 3]


def test_absent_boat():
    b = beforeinfo.parse_beforeinfo(load("absent")[0])["boats"]
    assert b["4"]["absent"] is True and b["4"]["start_course"] is None
    assert b["5"]["start_course"] == 4


@pytest.mark.parametrize("text,expected", [(".03", (0.03, False)), ("F.03", (-0.03, True)), ("", (None, False)),
                                           ("0.10", (0.1, False))])
def test_parse_start_timing(text, expected):
    assert beforeinfo.parse_start_timing(text) == expected


def test_parse_parts_quantity():
    frag = '<ul><li><span class="label4">リング×２</span></li><li><span>電気</span></li></ul>'
    assert beforeinfo.parse_parts(frag) == [
        {"number_source": "リング", "number": 2, "quantity": 2},
        {"number_source": "電気", "number": 3, "quantity": None},
    ]


def test_broken_page_raises():
    with pytest.raises(beforeinfo.BeforeinfoParseError):
        beforeinfo.parse_beforeinfo("<html>メンテナンス中</html>")


def test_build_url():
    assert beforeinfo.build_url("20261106-02-01") == \
        "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno=1&jcd=02&hd=20261106"
    with pytest.raises(ValueError):
        beforeinfo.parse_race_id("2026-11-06-2-1")


def test_fetch_race_waits_and_saves(tmp_path):
    html, expected = load("ok")
    now = [datetime(2026, 11, 6, 10, 30, tzinfo=JST)]
    sleeps = []
    raw_io.append_fetch_log({"source": "beforeinfo", "fetched_at": (now[0] - timedelta(seconds=0.5)).isoformat()}, tmp_path)
    calls = []

    def http(url):
        calls.append(url)
        return HttpResult(status=200, body=html.encode())

    out = beforeinfo.fetch_race("20261106-02-01", raw_dir=tmp_path, http=http,
                                sleep_fn=lambda s: sleeps.append(round(s, 3)), now_fn=lambda: now[0])
    assert sleeps == [1.0]
    assert len(calls) == 1
    assert out["status"] == "ok" and out["boats"] == expected["boats"]
    saved = list((tmp_path / "beforeinfo" / "20261106").glob("02-01_*.json.gz"))
    assert len(saved) == 1
    assert raw_io.read_fetch_log(tmp_path)[-1]["parse_status"] == "ok"


def test_fetch_race_http_error(tmp_path):
    out = beforeinfo.fetch_race("20261106-02-01", raw_dir=tmp_path, http=lambda u: HttpResult(status=429, error="HTTP 429"),
                                sleep_fn=lambda s: None)
    assert out["status"] == "failed" and out["http_status"] == 429
