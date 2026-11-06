"""直前情報ページの共通テストデータ(合成HTMLと期待値)を作る開発用スクリプト(spec/12 3章、C-8)。

公式サイトの実物のHTMLはリポジトリに置かない。2026-11-06 に PC から取った実物
(data/raw/beforeinfo_samples/、git管理外)の「解析に使う部分の構造」(class 名・td の並び・入れ子)を
写したテンプレートに、架空の値(選手名・登録番号・数値)を入れて tests/fixtures/beforeinfo/ に書き出す。
Python のテスト(tests/collect/test_beforeinfo.py)と中継 Worker(JS)のテストの両方が読む。

期待値は解析関数を通さず、下の CASES の入力から直接作る(解析の誤りを期待値に写さないため)。

使い方:
    python -m src.collect.make_beforeinfo_fixtures
"""

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "beforeinfo"

# 1艇分の入力: weight, adj, exh, tilt, prop, parts
#   None は空欄(&nbsp;)。parts は部品交換のラベルの並び
BASE = {
    1: dict(weight=52.1, adj=0.0, exh=6.71, tilt=-0.5, prop="", parts=[]),
    2: dict(weight=53.2, adj=0.5, exh=6.82, tilt=0.0, prop="新", parts=[]),
    3: dict(weight=54.3, adj=0.0, exh=6.93, tilt=0.5, prop="", parts=["リング", "ピストン"]),
    4: dict(weight=51.4, adj=1.0, exh=6.64, tilt=-0.5, prop="", parts=[]),
    5: dict(weight=50.5, adj=2.0, exh=6.75, tilt=1.0, prop="", parts=["キャブ"]),
    6: dict(weight=55.6, adj=0.0, exh=6.86, tilt=0.0, prop="", parts=[]),
}
WEATHER = dict(weather=2, wind=17, wind_speed=0, wave=1, air=17.0, water=18.0, title="水面気象情報　10:31現在")
DEFAULT_START = [(1, ".11"), (2, ".12"), (3, ".13"), (4, ".14"), (5, ".15"), (6, ".16")]
PART_NUMBERS = {"ピストン": 1, "リング": 2, "電気": 3, "キャブ": 4, "シリンダ": 5, "シャフト": 6, "ギヤ": 7, "キャリボ": 8}


def _boats(**overrides):
    boats = {k: dict(v) for k, v in BASE.items()}
    for k, v in overrides.items():
        boats[int(k[1:])].update(v)
    return boats


CASES = {
    # 展示前: 体重・チルトは出ているが、展示タイムとスタート展示は空
    "not_posted": dict(boats=_boats(**{f"b{i}": dict(exh=None) for i in range(1, 7)}), start=[]),
    # 一部掲載: 展示タイムは出たが、スタート展示がまだ。6号艇は展示タイムも空
    "partial": dict(boats=_boats(b6=dict(exh=None)), start=[]),
    # 全部掲載(基本形)
    "ok": dict(boats=_boats(), start=DEFAULT_START),
    # フライング: 3号艇 F.02、5号艇 F.01
    "flying": dict(boats=_boats(), start=[(1, ".08"), (2, ".05"), (3, "F.02"), (4, ".00"), (5, "F.01"), (6, ".10")]),
    # 欠場: 4号艇は体重・チルト・展示タイムが空で、スタート展示に出てこない(残り5艇が1〜5コース)
    "absent": dict(
        boats=_boats(b4=dict(weight=None, adj=None, exh=None, tilt=None)),
        start=[(1, ".11"), (2, ".12"), (3, ".13"), (5, ".15"), (6, ".16")],
    ),
    # 前付け: 6号艇が3コースに入る(進入 1-2-6-3-4-5)
    "maeduke": dict(boats=_boats(), start=[(1, ".11"), (2, ".12"), (6, ".09"), (3, ".13"), (4, ".14"), (5, ".15")]),
}


def _fmt(v, suffix=""):
    return "&nbsp;" if v is None else f"{v}{suffix}"


def _fmt1(v):
    return "&nbsp;" if v is None else f"{v:.1f}"


def _racer_tbody(n: int, b: dict) -> str:
    toban = 9000 + n
    parts = "".join(f'\n                              <li><span class="label4 is-type1">{p}</span></li>' for p in b["parts"])
    exh = "&nbsp;" if b["exh"] is None else f"{b['exh']:.2f}"
    weight = "&nbsp;" if b["weight"] is None else f"{b['weight']:.1f}kg"
    return f"""                      <tbody class="is-fs12 ">
                        <tr>
                          <td class="is-boatColor{n} is-fs14" rowspan="4">{n}</td>
                          <td rowspan="4"><a href="/owpc/pc/data/racersearch/profile?toban={toban}"><img src="/racerphoto/{toban}.jpg" width="67" height="95" alt="" /></a></td>
                          <td class="is-fs18 is-fBold" rowspan="4"><a href="/owpc/pc/data/racersearch/profile?toban={toban}">テスト　選手{n}</a></td>
                          <td rowspan="2">{weight}</td>
                          <td rowspan="4">{exh}</td>
                          <td rowspan="4">{_fmt1(b['tilt'])}</td>
                          <td rowspan="4">{b['prop'] or '&nbsp;'}</td>
                          <td class="is-p5-5" rowspan="4">
                            <ul class="labelGroup1">{parts}
                            </ul>
                          </td>
                          <td>R</td>
                          <td class="is-boatColor3">7</td>
                        </tr>
                        <tr>
                          <td>進入</td>
                          <td>3</td>
                        </tr>
                        <tr>
                          <td rowspan="2">{_fmt1(b['adj'])}</td>
                          <td>ST</td>
                          <td>.19</td>
                        </tr>
                        <tr>
                          <td>着順</td>
                          <td class="is-fBold"><a href="/owpc/pc/race/raceresult?rno=7&amp;jcd=99&amp;hd=20990101">２</a></td>
                        </tr>
                      </tbody>
"""


def _start_rows(start: list) -> str:
    rows = []
    for i in range(6):
        if i < len(start):
            boat, st = start[i]
            inner = f"""
                          	<div class="table1_boatImage1">
                          		<span class="table1_boatImage1Number is-type{boat}">{boat}</span>
                            <span class="table1_boatImage1Line"><span class="table1_boatImage1Boat" style="left: 50%;"><img src="/static_extra/pc/images/img_boat2_{boat}.png" width="54" height="27" alt="" /></span></span>
                          		<span class="table1_boatImage1Time">{st}</span>
                          	</div>
                          """
        else:
            inner = "\n                          "
        rows.append(f"""                        <tr>
                          <td colspan="3">{inner}</td>
                        </tr>
""")
    return "".join(rows)


def build_html(case: dict) -> str:
    w = WEATHER
    racers = "".join(_racer_tbody(n, case["boats"][n]) for n in range(1, 7))
    return f"""<!DOCTYPE html>
<html lang="ja">
<!-- 合成データ(spec/12 3章)。公式サイトの直前情報ページの構造だけを写し、値はすべて架空 -->
<head><meta charset="UTF-8" /><title>直前情報(合成)</title></head>
<body>
<main>
              <div class="grid is-type3 h-clear">
                <div class="grid_unit">
                   <div class="table1">
                   <table class="is-w748">
                      <thead class="is-fs14">
                        <tr>
                          <th rowspan="2">枠</th>
                          <th rowspan="2">写真</th>
                          <th rowspan="2">ボートレーサー</th>
                          <th>体重</th>
                          <th rowspan="2">展示<br />タイム</th>
                          <th rowspan="2">チルト</th>
                          <th rowspan="2">プロペラ</th>
                          <th rowspan="2">部品交換</th>
                          <th colspan="2" rowspan="2">前走成績</th>
                        </tr>
                        <tr>
                          <th>調整重量</th>
                        </tr>
                      </thead>
{racers}                   </table>
                  </div>
                  <ul class="labelGroup1">
                    <li><span class="label4 is-type1">ピストン</span>ピストン</li>
                    <li><span class="label4 is-type1">リング</span>ピストンリング</li>
                  </ul>
                </div>
                <div class="grid_unit">
                  <div class="table1">
                   <table class="is-w238">
                      <thead>
                        <tr>
                          <th colspan="3">スタート展示</th>
                        </tr>
                        <tr class="is-thColor1">
                          <th>コース</th>
                          <th>並び</th>
                          <th>ST</th>
                        </tr>
                      </thead>
                      <tbody class="is-p10-0">
{_start_rows(case["start"])}                      </tbody>
                    </table>
                <div class="table1_water2"></div>
                  </div>
                  <div class="weather1">
                    <p class="weather1_title">{w['title']}</p>
                    <div class="weather1_body">
                      <div class="weather1_bodyUnit is-direction">
                        <p class="weather1_bodyUnitImage is-direction5"></p>
                        <div class="weather1_bodyUnitLabel">
                          <span class="weather1_bodyUnitLabelTitle">気温</span>
                          <span class="weather1_bodyUnitLabelData">{w['air']:.1f}℃</span>
                        </div>
                      </div>
                      <div class="weather1_bodyUnit is-weather">
                        <p class="weather1_bodyUnitImage is-weather{w['weather']}"></p>
                        <div class="weather1_bodyUnitLabel">
                          <span class="weather1_bodyUnitLabelTitle">曇り</span>
                        </div>
                      </div>
                      <div class="weather1_bodyUnit is-wind">
                        <div class="weather1_bodyUnitLabel">
                          <span class="weather1_bodyUnitLabelTitle">風速</span>
                          <span class="weather1_bodyUnitLabelData">{w['wind_speed']}m</span>
                        </div>
                      </div>
                      <div class="weather1_bodyUnit is-windDirection">
                        <p class="weather1_bodyUnitImage is-wind{w['wind']}"></p>
                      </div>
                      <div class="weather1_bodyUnit is-waterTemperature">
                        <div class="weather1_bodyUnitLabel">
                          <span class="weather1_bodyUnitLabelTitle">水温</span>
                          <span class="weather1_bodyUnitLabelData">{w['water']:.1f}℃</span>
                        </div>
                      </div>
                      <div class="weather1_bodyUnit is-wave">
                        <div class="weather1_bodyUnitLabel">
                          <span class="weather1_bodyUnitLabelTitle">波高</span>
                          <span class="weather1_bodyUnitLabelData">{w['wave']}cm</span>
                        </div>
                      </div>
                    </div>
                    <div class="weather1_stand">スタンド</div>
                  </div>
                </div>
              </div>
</main>
</body>
</html>
"""


def build_expected(case: dict) -> dict:
    """入力から直接期待値を作る(解析関数は使わない)。"""
    boats = {}
    absent = set()
    for n, b in case["boats"].items():
        boats[str(n)] = {
            "racer_number": 9000 + n,
            "exhibition_time": b["exh"],
            "tilt_adjustment": b["tilt"],
            "weight": b["weight"],
            "weight_adjustment": b["adj"],
            "propeller_new": b["prop"] == "新",
            "parts": [{"number_source": p, "number": PART_NUMBERS[p], "quantity": None} for p in b["parts"]],
            "start_course": None,
            "start_timing": None,
            "start_flying": False,
            "absent": False,
        }
        if b["weight"] is None and b["tilt"] is None:
            absent.add(n)
    for course, (boat, st) in enumerate(case["start"], start=1):
        flying = st.startswith("F")
        v = float(st.lstrip("F"))
        boats[str(boat)].update(start_course=course, start_timing=-v if flying else v, start_flying=flying)
    for n in absent:
        if boats[str(n)]["start_course"] is None:
            boats[str(n)]["absent"] = True
    entrants = [b for b in boats.values() if not b["absent"]]
    any_posted = any(b["exhibition_time"] is not None or b["start_course"] is not None for b in entrants)
    complete = all(b["exhibition_time"] is not None and b["start_course"] is not None for b in entrants)
    w = WEATHER
    return {
        "status": "ok" if complete else ("partial" if any_posted else "not_posted"),
        "weather": {
            "weather_number": w["weather"], "wind_direction_number": w["wind"], "wind_speed": w["wind_speed"],
            "wave_height": w["wave"], "air_temperature": w["air"], "water_temperature": w["water"], "as_of": "10:31現在",
        },
        "boats": boats,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        (OUT_DIR / f"{name}.synthetic.html").write_text(build_html(case), encoding="utf-8")
        (OUT_DIR / f"{name}.expected.json").write_text(
            json.dumps(build_expected(case), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(OUT_DIR / f"{name}.synthetic.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
