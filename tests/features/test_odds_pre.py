"""締切前オッズの記録 → interim の odds_pre(F-13b)のテスト。データは合成。"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2
from src.features.build_interim import build_interim, read_interim
from src.features.odds_pre import (
    ODDS_PRE_DTYPES, SOURCE, make_odds_record_loader, records_to_rows, win_popularity,
)
from src.features.raw_source import make_loader

ROOT = Path(__file__).resolve().parents[2]
ODDS_FIXTURES = ROOT / "tests" / "fixtures" / "official_odds"
RID = "20261102-02-01"


def record(race_id: str = RID, tf_status="ok", with_2tf=True) -> dict:
    """Worker が保存する形(workers/beforeinfo/src/odds.mjs)の合成データ。オッズは共通 fixture の期待値。"""
    tf = json.loads((ODDS_FIXTURES / "oddstf.expected.json").read_text(encoding="utf-8"))
    t2 = json.loads((ODDS_FIXTURES / "odds2tf.expected.json").read_text(encoding="utf-8"))
    pages = {"tf": {"status": "ok", "fetched_at": "2026-11-02T10:40:12+09:00", "minutes_before_close": 4.8,
                    "odds_time_label": tf["odds_time_label"], "is_final_odds": tf["is_final_odds"]}
             if tf_status == "ok" else tf_status}
    odds = dict(tf["odds"]) if tf_status == "ok" else {}
    if with_2tf:
        pages["2tf"] = {"status": "ok", "fetched_at": "2026-11-02T10:40:14+09:00", "minutes_before_close": 4.7,
                        "odds_time_label": t2["odds_time_label"], "is_final_odds": t2["is_final_odds"]}
        odds.update(t2["odds"])
    return {"race_id": race_id, "closed_at": "2026-11-02T10:45:00+09:00", "pages": pages, "odds": odds}


def write_record(raw_dir: Path, data: dict, gz: bool = False) -> None:
    ymd, sn, rn = data["race_id"].split("-")
    path = raw_dir / "odds_record" / ymd / f"{sn}-{rn}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False)
    if gz:
        with gzip.open(path.with_suffix(".json.gz"), "wt", encoding="utf-8") as f:
            f.write(body)
    else:
        path.write_text(body, encoding="utf-8")


def test_rows_and_types():
    df = records_to_rows({RID: record()})
    assert list(df.columns) == list(ODDS_PRE_DTYPES)
    counts = df["bet_type"].value_counts().to_dict()
    assert counts == {"win": 6, "place": 6, "exacta": 30, "quinella": 15}
    assert df["odds"].dtype == "float32" and str(df["fetched_at"].dt.tz) == "Asia/Tokyo"
    assert (df["source"] == SOURCE).all()
    w = df[df["bet_type"] == "win"].set_index("combination")
    assert w.loc["1", "odds"] == pytest.approx(1.0) and pd.isna(w.loc["3", "odds"])  # 欠場は null で行は残す
    p = df[df["bet_type"] == "place"].set_index("combination")
    assert p.loc["2", "odds_lower"] == pytest.approx(2.0) and p.loc["2", "odds_upper"] == pytest.approx(3.2)
    assert pd.isna(p.loc["2", "odds"])
    q = df[df["bet_type"] == "quinella"]
    assert all(c.split("=") == sorted(c.split("="), key=int) for c in q["combination"])
    # 券種ごとに、そのページの時刻・状態
    ex = df[df["bet_type"] == "exacta"].iloc[0]
    assert ex["minutes_before_close"] == pytest.approx(4.7) and bool(ex["is_final_odds"])
    assert not bool(w.iloc[0]["is_final_odds"])


def test_failed_page_has_no_rows():
    df = records_to_rows({RID: record(tf_status="error")})
    assert set(df["bet_type"]) == {"exacta", "quinella"}


def test_win_popularity():
    pop = win_popularity(records_to_rows({RID: record()})).set_index("entry_number")
    # 単勝 1.0, 2.2, null, 4.4, null, 6.6
    assert pop["popularity"].tolist() == [1, 2, pd.NA, 3, pd.NA, 4]
    assert str(pop["fetched_at"].dt.tz) == "Asia/Tokyo"
    assert pop.loc[1, "is_favorite"] == True  # noqa: E712
    assert pd.isna(pop.loc[3, "popularity"]) and pd.isna(pop.loc[3, "is_favorite"])
    assert pop.loc[6, "popularity"] == 4


def test_build_interim_writes_odds_pre(tmp_path, raw_dir):
    write_record(raw_dir, record())
    write_record(raw_dir, record("20261102-02-12", with_2tf=False), gz=True)
    (raw_dir / "odds_record" / "20261102" / "_state.json").write_text("{}", encoding="utf-8")  # 状態ファイルは読まない
    out = tmp_path / "i"
    reports = build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False,
                            load_odds_record=make_odds_record_loader(raw_dir))
    assert (out / "odds_pre" / "2026-11.parquet").exists()
    op = read_interim("odds_pre", interim_dir=out)
    assert op["race_id"].nunique() == 2 and len(op) == 57 + 12
    # 確定オッズ(turnmark の odds)とは別の表
    odds = read_interim("odds", interim_dir=out)
    assert set(odds["race_id"].str.slice(0, 8)) == {"20261101"}
    # 品質レポートには件数だけ(数値を出さない)
    q = reports[-1]["odds_pre"]
    assert q["rows"] == 69 and "odds" not in json.dumps(q).replace("odds_pre", "")
    # 期間で絞って読める
    assert len(read_interim("odds_pre", DAY2, DAY2, out)) == 69
    assert len(read_interim("odds_pre", DAY1, DAY1, out)) == 0


def test_not_public():
    """公開しない: 公開物(docs/)に odds_pre・記録の出どころが無く、interim は git の管理外。"""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/interim/" in gi
    docs = ROOT / "docs"
    for path in docs.rglob("*") if docs.exists() else []:
        if path.is_file() and path.suffix in (".json", ".js", ".html"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "odds_pre" not in text and "worker_record" not in text, path
