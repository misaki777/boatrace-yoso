"""src/collect/raw_io.py のテスト(ネットワーク不要)。"""

from datetime import date

import pytest

from src.collect import raw_io


def test_raw_path_layout(tmp_path):
    p = raw_io.raw_path("turnmark", date(2026, 11, 1), tmp_path)
    assert p == tmp_path / "turnmark" / "2026" / "20261101.json.gz"
    with pytest.raises(ValueError):
        raw_io.raw_path("unknown", date(2026, 11, 1), tmp_path)


@pytest.mark.parametrize("value", [date(2026, 11, 1), "2026-11-01", "20261101"])
def test_to_date_accepts_formats(value):
    assert raw_io.to_date(value) == date(2026, 11, 1)


def test_write_and_load_roundtrip(tmp_path):
    body = b'{"programs": {"stadiums": {"2": {"races": {}}}}}'
    raw_io.write_raw_bytes("openapi", date(2026, 11, 2), body, tmp_path)
    assert raw_io.load_raw("openapi", date(2026, 11, 2), tmp_path) == {"programs": {"stadiums": {"2": {"races": {}}}}}
    assert raw_io.load_raw("openapi", "20261102", tmp_path) is not None
    assert raw_io.load_raw("turnmark", date(2026, 11, 2), tmp_path) is None
    assert not list(tmp_path.rglob("*.tmp"))
    assert raw_io.list_raw_dates("openapi", tmp_path) == [date(2026, 11, 2)]
    assert raw_io.list_raw_dates("turnmark", tmp_path) == []


def test_load_raw_any_prefers_turnmark(tmp_path):
    d = date(2026, 10, 1)
    assert raw_io.load_raw_any(d, raw_dir=tmp_path) is None
    raw_io.write_raw_bytes("openapi", d, b'{"src": "o"}', tmp_path)
    assert raw_io.load_raw_any(d, raw_dir=tmp_path) == ("openapi", {"src": "o"})
    raw_io.write_raw_bytes("turnmark", d, b'{"src": "t"}', tmp_path)
    assert raw_io.load_raw_any(d, raw_dir=tmp_path) == ("turnmark", {"src": "t"})


def test_fetch_log_append_and_read(tmp_path):
    raw_io.append_fetch_log({"source": "turnmark", "date": "2026-10-01", "status": 200}, tmp_path)
    (tmp_path / raw_io.FETCH_LOG_NAME).open("a").write("broken line\n")
    raw_io.append_fetch_log({"source": "turnmark", "date": "2026-10-02", "status": 404}, tmp_path)
    assert [r["status"] for r in raw_io.read_fetch_log(tmp_path)] == [200, 404]
