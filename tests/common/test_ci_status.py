"""scripts/ci_status.py のテスト。"""

from datetime import datetime

from scripts import ci_status

JST = ci_status.JST


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=JST)


def test_next_expected():
    assert ci_status.next_expected(_t("2026-11-05T14:04:31")) == _t("2026-11-05T14:34:00")
    assert ci_status.next_expected(_t("2026-11-05T07:41:10")) == _t("2026-11-05T08:04:00")
    assert ci_status.next_expected(_t("2026-11-05T23:40:00")) == _t("2026-11-06T01:12:00")
    assert ci_status.next_expected(_t("2026-11-06T01:15:00")) == _t("2026-11-06T07:41:00")


def test_build_status(tmp_path):
    (tmp_path / "latest").write_text("pre_x\n")
    s = ci_status.build_status("daytime", _t("2026-11-05T14:04:31.5"), tmp_path)
    assert s == {
        "schema_version": 1,
        "last_success_at": "2026-11-05T14:04:31+09:00",
        "job": "update",
        "next_expected_by": "2026-11-05T14:34:00+09:00",
        "models": {"pre": "pre_x"},
    }
