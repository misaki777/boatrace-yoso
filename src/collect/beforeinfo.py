"""公式サイトの直前情報ページ(beforeinfo)の解析と、PC からの手動取得(spec/12、C-8)。

ページ: https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={R}&jcd={場2桁}&hd={YYYYMMDD}

使い方:
    python -m src.collect.beforeinfo --race 20261106-02-01          # 1レース取得して解析結果を表示・保存
    python -m src.collect.beforeinfo --html path/to/page.html       # 手元のHTMLを解析するだけ(アクセスなし)

公式サイトへのアクセスは PC から手動で、1レースずつ、前回のアクセスから1.5秒以上空ける。
GitHub Actions からは実行しない。日中の定期取得は Worker(R-4)が同じ解析を JS で行う。

解析の切り出し方(JS と共通。詳しくは src/collect/BEFOREINFO_FORMAT.md):
1. 選手の表: <table class="is-w748"> ... </table> の中の <tbody> が枠番1〜6の順に6個。
   各 tbody の <td> を順に並べると次の位置になる(前走成績の列は使わない):
     0 枠番 / 1 写真 / 2 名前(href の toban=登録番号) / 3 体重 "51.5kg" / 4 展示タイム / 5 チルト /
     6 プロペラ("新" なら交換) / 7 部品交換(<li> の並び) / 8 "R" / 9 前走R / 10 "進入" / 11 前走進入 /
     12 調整重量 / 13 "ST" / 14 前走ST / 15 "着順" / 16 前走着順
2. スタート展示: <table class="is-w238"> の tbody の <tr> が上から進入コース1〜6。
   各行の table1_boatImage1Number が艇番、table1_boatImage1Time が ST(".03"、F は "F.03")。未掲載の行は空。
3. 気象: <div class="weather1"> の中。is-weather{N} が天候コード、is-wind{N} が風向コード(17=無風)。
   weather1_bodyUnitLabelTitle と直後の weather1_bodyUnitLabelData の組で 気温/風速/水温/波高。
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

from src.collect import raw_io
from src.collect.fetch import JST, http_get, now_jst

URL_TEMPLATE = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd:02d}&hd={ymd}"
LOG_SOURCE = "beforeinfo"
MIN_INTERVAL_SEC = 1.5
BOATS = (1, 2, 3, 4, 5, 6)
# 部品交換の名前 → Open API の parts.number(凡例の並び順。turnmark の実データで確認)
PART_NUMBERS = {"ピストン": 1, "リング": 2, "電気": 3, "キャブ": 4, "シリンダ": 5, "シャフト": 6, "ギヤ": 7, "キャリボ": 8}


class BeforeinfoParseError(ValueError):
    """ページの形が想定と違う(表が見つからない等)。"""


# ---------------------------------------------------------------------
# 文字列の小道具(JS でも同じ処理にする)
# ---------------------------------------------------------------------

def _slice(html: str, start_marker: str, end_marker: str) -> str | None:
    """start_marker から、その後の最初の end_marker までを返す。無ければ None。"""
    i = html.find(start_marker)
    if i < 0:
        return None
    j = html.find(end_marker, i)
    return html[i:] if j < 0 else html[i:j + len(end_marker)]


def _text(fragment: str) -> str:
    """タグを除き、&nbsp; と空白を詰めた文字列。"""
    s = re.sub(r"<[^>]*>", "", fragment)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("　", " ")
    return re.sub(r"\s+", " ", s).strip()


def _number(text: str) -> float | None:
    """'51.5kg' '-0.5' '22.0℃' '4m' '5cm' → 数値。空なら None。"""
    m = re.search(r"-?(?:\d+(?:\.\d+)?|\.\d+)", text or "")
    return float(m.group()) if m else None


def _int_or_none(v: float | None) -> int | None:
    return None if v is None else int(round(v))


def parse_start_timing(text: str) -> tuple[float | None, bool]:
    """'.03' → (0.03, False)、'F.03' → (-0.03, True)、'' → (None, False)。"""
    t = (text or "").strip().upper()
    if not t:
        return None, False
    flying = t.startswith("F")
    v = _number(t.lstrip("FL"))
    if v is None:
        return None, flying
    return (-abs(v) if flying else v), flying


def parse_parts(fragment: str) -> list[dict]:
    """部品交換の <li> を Open API と同じ形 [{"number_source", "number", "quantity"}] にする。

    表記は "リング" または "リング×2"(数の表記は実物で未確認。×/x/✕ と全角数字を許す)。
    """
    parts = []
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", fragment, re.S):
        t = _text(li).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if not t:
            continue
        m = re.match(r"^(.*?)\s*(?:[×xX✕*]\s*(\d+))?$", t)
        name = m.group(1).strip()
        qty = int(m.group(2)) if m.group(2) else None
        parts.append({"number_source": name, "number": PART_NUMBERS.get(name), "quantity": qty})
    return parts


# ---------------------------------------------------------------------
# 解析の本体
# ---------------------------------------------------------------------

def _parse_racer_table(html: str) -> dict[str, dict]:
    table = _slice(html, '<table class="is-w748"', "</table>")
    if table is None:
        raise BeforeinfoParseError("選手の表(is-w748)が見つからない")
    bodies = re.findall(r"<tbody\b[^>]*>(.*?)</tbody>", table, re.S)
    if len(bodies) != 6:
        raise BeforeinfoParseError(f"選手の表の tbody が6個ではない: {len(bodies)}")
    boats = {}
    for body in bodies:
        tds = re.findall(r"<td\b[^>]*>(.*?)</td>", body, re.S)
        if len(tds) < 13:
            raise BeforeinfoParseError(f"選手の表の td が足りない: {len(tds)}")
        boat = _int_or_none(_number(_text(tds[0])))
        if boat not in BOATS:
            raise BeforeinfoParseError(f"枠番が読めない: {_text(tds[0])!r}")
        toban = re.search(r"toban=(\d+)", tds[2])
        boats[str(boat)] = {
            "racer_number": int(toban.group(1)) if toban else None,
            "exhibition_time": _number(_text(tds[4])),
            "tilt_adjustment": _number(_text(tds[5])),
            "weight": _number(_text(tds[3])),
            "weight_adjustment": _number(_text(tds[12])),
            "propeller_new": "新" in _text(tds[6]),
            "parts": parse_parts(tds[7]),
            "start_course": None,
            "start_timing": None,
            "start_flying": False,
            "absent": False,
        }
    return boats


def _parse_start_table(html: str) -> list[tuple[int, int, str]]:
    """スタート展示の (進入コース, 艇番, STの文字列) の並び。未掲載の行は含めない。"""
    table = _slice(html, '<table class="is-w238"', "</table>")
    if table is None:
        raise BeforeinfoParseError("スタート展示の表(is-w238)が見つからない")
    tbody = _slice(table, "<tbody", "</tbody>") or ""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", tbody, re.S)
    out = []
    for course, row in enumerate(rows, start=1):
        num = re.search(r'class="table1_boatImage1Number[^"]*">(.*?)</span>', row, re.S)
        if not num:
            continue
        st = re.search(r'class="table1_boatImage1Time[^"]*">(.*?)</span>', row, re.S)
        boat = _int_or_none(_number(_text(num.group(1))))
        if boat in BOATS:
            out.append((course, boat, _text(st.group(1)) if st else ""))
    return out


def _parse_weather(html: str) -> dict:
    block = _slice(html, '<div class="weather1"', 'class="weather1_stand"') or ""
    weather = re.search(r"is-weather(\d+)", block)
    wind_dir = re.search(r"is-wind(\d+)", block)
    pairs = dict(
        (_text(t), _text(d))
        for t, d in re.findall(
            r'weather1_bodyUnitLabelTitle">([^<]*)</span>\s*<span class="weather1_bodyUnitLabelData">([^<]*)</span>',
            block,
            re.S,
        )
    )
    title = re.search(r'class="weather1_title">(.*?)</p>', block, re.S)
    return {
        "weather_number": int(weather.group(1)) if weather else None,
        "wind_direction_number": int(wind_dir.group(1)) if wind_dir else None,
        "wind_speed": _int_or_none(_number(pairs.get("風速", ""))),
        "wave_height": _int_or_none(_number(pairs.get("波高", ""))),
        "air_temperature": _number(pairs.get("気温", "")),
        "water_temperature": _number(pairs.get("水温", "")),
        "as_of": _text(title.group(1)).replace("水面気象情報", "").strip() if title else None,
    }


def parse_beforeinfo(html: str) -> dict:
    """直前情報ページを解析する。

    戻り値: {"status", "weather": {...}, "boats": {"1": {...}, ...}}
    status:
      not_posted … 展示タイムもスタート展示もまだ1艇も載っていない
      partial    … 出走艇のうち、展示タイムかスタート展示が欠けている艇がある
      ok         … 出走艇すべてに展示タイムとスタート展示がある
    欠場(absent): 体重とチルトがどちらも空欄の艇(他の艇には載っている)。完全性の判定から外す。
    """
    boats = _parse_racer_table(html)
    for course, boat, st_text in _parse_start_table(html):
        b = boats[str(boat)]
        b["start_course"] = course
        b["start_timing"], b["start_flying"] = parse_start_timing(st_text)
    weather = _parse_weather(html)

    someone_has_weight = any(b["weight"] is not None or b["tilt_adjustment"] is not None for b in boats.values())
    for b in boats.values():
        if someone_has_weight and b["weight"] is None and b["tilt_adjustment"] is None and b["start_course"] is None:
            b["absent"] = True
    entrants = [b for b in boats.values() if not b["absent"]]
    has_any = any(b["exhibition_time"] is not None or b["start_course"] is not None for b in entrants)
    complete = all(b["exhibition_time"] is not None and b["start_course"] is not None for b in entrants)
    status = "ok" if (entrants and complete) else ("partial" if has_any else "not_posted")
    return {"status": status, "weather": weather, "boats": boats}


# ---------------------------------------------------------------------
# PC からの手動取得
# ---------------------------------------------------------------------

def parse_race_id(race_id: str) -> tuple[date, int, int]:
    m = re.fullmatch(r"(\d{8})-(\d{2})-(\d{2})", race_id)
    if not m:
        raise ValueError(f"race_id の形式が不正です(YYYYMMDD-SS-RR): {race_id}")
    return raw_io.to_date(m.group(1)), int(m.group(2)), int(m.group(3))


def build_url(race_id: str) -> str:
    d, jcd, rno = parse_race_id(race_id)
    return URL_TEMPLATE.format(rno=rno, jcd=jcd, ymd=d.strftime("%Y%m%d"))


def closed_at_of(race_id: str, raw_dir: Path | None = None) -> datetime | None:
    """手元の openapi 当日分から締切時刻を読む(無ければ None。取得はしない)。"""
    d, jcd, rno = parse_race_id(race_id)
    data = raw_io.load_raw("openapi", d, raw_dir)
    try:
        text = data["programs"]["stadiums"][str(jcd)]["races"][str(rno)]["closed_at"]
        return datetime.fromisoformat(text).replace(tzinfo=JST)
    except (TypeError, KeyError, ValueError):
        return None


def _last_beforeinfo_request(raw_dir: Path | None) -> datetime | None:
    last = None
    for r in raw_io.read_fetch_log(raw_dir):
        if r.get("source") == LOG_SOURCE:
            try:
                at = datetime.fromisoformat(r["fetched_at"])
            except (KeyError, TypeError, ValueError):
                continue
            last = at if last is None or at > last else last
    return last


def fetch_race(race_id: str, raw_dir: Path | None = None, http=http_get, sleep_fn=time.sleep, now_fn=now_jst,
               save_html: bool = False) -> dict:
    """1レース分を取得・解析し、data/raw/beforeinfo/YYYYMMDD/SS-RR_HHMMSS.json に保存して返す。"""
    url = build_url(race_id)
    last = _last_beforeinfo_request(raw_dir)
    if last is not None:
        elapsed = (now_fn() - last).total_seconds()
        if elapsed < MIN_INTERVAL_SEC:
            sleep_fn(MIN_INTERVAL_SEC - elapsed)
    result = http(url)
    fetched_at = now_fn()
    log = {"source": LOG_SOURCE, "race_id": race_id, "date": parse_race_id(race_id)[0].isoformat(), "url": url,
           "status": result.status, "fetched_at": fetched_at.isoformat(timespec="seconds")}
    base = (Path(raw_dir) if raw_dir is not None else raw_io.RAW_DIR) / "beforeinfo" / race_id[:8]
    if result.status != 200:
        log["error"] = result.error
        raw_io.append_fetch_log(log, raw_dir)
        return {"race_id": race_id, "fetched_at": log["fetched_at"], "status": "failed", "http_status": result.status}
    html = result.body.decode("utf-8", errors="replace")
    closed_at = closed_at_of(race_id, raw_dir)
    try:
        parsed = parse_beforeinfo(html)
    except BeforeinfoParseError as e:
        parsed = {"status": "failed", "error": str(e)}
    out = {
        "race_id": race_id,
        "fetched_at": log["fetched_at"],
        "minutes_before_close": round((closed_at - fetched_at).total_seconds() / 60, 1) if closed_at else None,
        "attempt": 1,
        **parsed,
    }
    stem = f"{race_id[9:]}_{fetched_at:%H%M%S}"
    raw_io.write_gzip_atomic(base / f"{stem}.json.gz", json.dumps(out, ensure_ascii=False).encode())
    if save_html:
        raw_io.write_gzip_atomic(base / f"{stem}.html.gz", result.body)
    log.update({"bytes": len(result.body), "sha256": hashlib.sha256(result.body).hexdigest(), "parse_status": out["status"]})
    raw_io.append_fetch_log(log, raw_dir)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="公式の直前情報ページを取得・解析する(PCから手動・1レースずつ)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--race", help="race_id(YYYYMMDD-SS-RR)")
    g.add_argument("--html", type=Path, help="手元のHTMLファイルを解析するだけ(アクセスなし)")
    p.add_argument("--save-html", action="store_true", help="HTML も data/raw/beforeinfo/ に保存する(git管理外)")
    args = p.parse_args(argv)
    if args.html:
        print(json.dumps(parse_beforeinfo(args.html.read_text(encoding="utf-8")), ensure_ascii=False, indent=1))
        return 0
    try:
        parse_race_id(args.race)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    out = fetch_race(args.race, save_html=args.save_html)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if out.get("http_status") in (403, 429):
        print("公式から 403/429 が返りました。今日はこれ以上アクセスしないでください。", file=sys.stderr)
    return 0 if out["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
