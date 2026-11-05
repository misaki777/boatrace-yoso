"""締切前オッズの共通テストデータ(合成HTMLと期待値)を作る開発用スクリプト(spec/07 9.5、C-6)。

公式サイトの実物のHTMLはリポジトリに置かない。手元(data/raw/official_odds/)の実物から
「オッズの表の構造」だけを切り出し、選手名を消し、オッズの数値を架空の値に置き換えて
tests/fixtures/official_odds/ に書き出す。Python のテストと中継 Worker(JS)のテストの両方が読む。

使い方(実物のHTMLが手元にあるときだけ動く):
    python -m src.collect.make_odds_fixtures \
        --odds3t data/raw/official_odds/2026/20261103/24-05_odds3t_T09.html.gz \
        --odds2tf data/raw/official_odds/2026/20261102/02-01_odds2tf_TC.html.gz \
        --oddstf data/raw/official_odds/2026/20261103/24-05_oddstf_T09.html.gz

架空の値は、組合せが値から読み取れる形にしてある(並びの取り違えをすぐ見つけるため):
    3連単 a-b-c → "abc.d"(d = (a*b+c) % 10)  例: 2-1-6 → "216.8"
    2連単 a-b   → "ab.a"                       例: 2-1 → "21.2"
    2連複 a=b   → "ab.5"
    単勝 a      → "a.a"(1号艇だけ "1.0")
    複勝 a      → "a.0-(a+1).a"
欠場艇(空欄)と、票の無い組合せ("0.0")の例も入れる。

組合せと値の対応は、解析(official_odds.py)の並びの式ではなく、表の中の艇番のセル
(列の位置、rowspan の2着、直前の艇番)から独立に求める(derive_combinations)。
"""

import argparse
import gzip
import json
import re
from pathlib import Path

from src.collect import official_odds as oo

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "official_odds"

_TD_RE = re.compile(r"<(tr)\b[^>]*>|</tr>|<td\b([^>]*)>(.*?)</td>", re.S)
_BOAT_RE = re.compile(r'class="[^"]*is-boatColor[1-6][^"]*"')


def derive_combinations(page: str, html: str) -> list[tuple[int, ...]]:
    """oddsPoint セルが文書の順にどの組合せかを、表の艇番のセルから求める(解析の式とは独立)。

    3連単: 列=1着。rowspan のある艇番セル=その列の2着、直前の艇番セル=3着。
    2連単・2連複: 列=1着(2連複は小さい方)、直前の艇番セル=相手。is-disabled は2つで1列。
    単勝・複勝: 直前の艇番(行の先頭のセルの文字)。
    """
    combos: list[tuple[int, ...]] = []
    second_by_col: dict[int, int] = {}
    col = 0
    disabled = 0
    last_boat: int | None = None
    for m in _TD_RE.finditer(html):
        if m.group(1) == "tr":
            col, disabled, last_boat = 0, 0, None
            continue
        if m.group(0) == "</tr>":
            continue
        attrs, text = m.group(2) or "", m.group(3) or ""
        if "oddsPoint" in attrs:
            if page == "odds3t":
                combos.append((col + 1, second_by_col[col], last_boat))
            elif page == "odds2tf":
                combos.append((col + 1 + disabled // 2, last_boat))
            else:
                combos.append((last_boat,))
            col += 1
            continue
        if "is-disabled" in attrs:
            disabled += 1
            continue
        boat = _BOAT_RE.search(attrs)
        if boat and text.strip().isdigit():
            if page == "odds3t" and "rowspan" in attrs:
                second_by_col[col] = int(text.strip())
            else:
                last_boat = int(text.strip())
    # 2連単・2連複はどちらも (列の艇, 相手)。前半30が2連単、後半15が2連複
    return combos


def synthetic_value(page: str, idx: int, combo: tuple[int, ...], absent: set[int], no_votes: set) -> str:
    if any(b in absent for b in combo):
        return ""
    if combo in no_votes:
        return "0.0"
    if page == "odds3t":
        a, b, c = combo
        return f"{a}{b}{c}.{(a * b + c) % 10}"
    if page == "odds2tf":
        a, b = combo
        return f"{a}{b}.{a}" if idx < 30 else f"{min(a, b)}{max(a, b)}.5"
    (a,) = combo
    if idx < 6:
        return "1.0" if a == 1 else f"{a}.{a}"
    return f"{a}.0-{a + 1}.{a}"


def expected_odds(page: str, values: list[str], combos: list[tuple[int, ...]]) -> dict:
    """セルの文字と組合せから、2.6 の odds の形の期待値を作る(解析の式を使わない)。"""
    if page == "odds3t":
        tri: dict = {str(b): {} for b in oo.BOATS}
        for (a, b, c), v in zip(combos, values):
            tri[str(a)].setdefault(str(b), {})[str(c)] = oo.to_odds(v)
        return {"trifecta": tri}
    if page == "odds2tf":
        ex: dict = {str(b): {} for b in oo.BOATS}
        qu: dict = {str(b): {} for b in oo.BOATS[:-1]}
        for i, ((a, b), v) in enumerate(zip(combos, values)):
            if i < 30:
                ex[str(a)][str(b)] = oo.to_odds(v)
            else:
                lo, hi = min(a, b), max(a, b)
                qu[str(lo)][str(hi)] = oo.to_odds(v)
        return {"exacta": ex, "quinella": qu}
    win = {str(a): oo.to_odds(v) for ((a,), v) in zip(combos[:6], values[:6])}
    place = {str(a): oo.to_range(v) for ((a,), v) in zip(combos[6:], values[6:])}
    return {"win": win, "place": place}


_DECOY = """<div class="table1 h-mt10">
<table>
<tbody>
<tr>
<td class="is-fs14 is-thColor8 is-fBold" colspan="2">締切予定時刻</td>
<td>10:15</td><td>10:40</td><td>11:05</td><td>11:30</td><td>11:55</td><td>12:20</td>
</tr>
</tbody>
</table>
</div>
"""


def synthesize(page: str, real_html: str, label_kind: str, absent: set[int], no_votes: set) -> tuple[str, dict]:
    """実物のHTMLから、合成HTMLと期待値(dict)を作る。"""
    start = real_html.index('<div class="tab4">')
    n_tables = {"odds3t": 1, "odds2tf": 2, "oddstf": 2}[page]
    end = start
    for _ in range(n_tables):
        end = real_html.index("</table>", end) + len("</table>")
    region = real_html[start:end] + "\n</div>\n"
    # 選手名を消す
    region = re.sub(r'(<th class="is-boatColor([1-6]) is-borderLeftNone"[^>]*>)[^<]*(</th>)',
                    lambda m: f"{m.group(1)}選手{m.group(2)}{m.group(3)}", region)
    region = re.sub(r'(<span class="is-fs18 is-fBold">)[^<]*(</span>)', lambda m: f"{m.group(1)}選手{m.group(2)}", region)
    # リンクの日付・場・R を架空にする
    region = re.sub(r"rno=\d+&amp;jcd=\d+&amp;hd=\d+", "rno=1&amp;jcd=01&amp;hd=20991231", region)
    # オッズ時点の表示を作り直す
    region = re.sub(r'<div class="tab4_refresh">.*?</div>|<p class="tab4_time">[^<]*</p>', "@@LABEL@@", region, count=1, flags=re.S)
    if label_kind == "final":
        label_html, label = '<p class="tab4_time">締切時オッズ</p>', "締切時オッズ"
    else:
        label_html = (
            '<div class="tab4_refresh">\n\t<p class="tab4_refreshText">\n\t\tオッズ更新時間\n\t\t10:40\n\t</p>\n'
            '\t<p class="tab4_refresh_refreshBtn"><a href="/owpc/pc/race/' + page
            + '?rno=1&amp;jcd=01&amp;hd=20991231" class="btn is-type1_2">更新<i class="is-refresh1"></i></a>\n\t</p>\n</div>'
        )
        label = "オッズ更新時間 10:40"
    region = region.replace("@@LABEL@@", label_html)
    combos = derive_combinations(page, region)
    values = [synthetic_value(page, i, c, absent, no_votes) for i, c in enumerate(combos)]
    it = iter(values)
    region = re.sub(r'(<td class="oddsPoint[^"]*">)[^<]*(</td>)', lambda m: f"{m.group(1)}{next(it)}{m.group(2)}", region)
    html = (
        "<!DOCTYPE html>\n<!-- 合成HTML(テスト用)。公式サイトのオッズの表の構造だけを写し、"
        "選手名を消し、数値は架空の値に置き換えたもの。src/collect/make_odds_fixtures.py で作成 -->\n"
        f"<html lang=\"ja\"><head><meta charset=\"UTF-8\"><title>synthetic {page}</title></head><body>\n"
        + _DECOY + region + "</body></html>\n"
    )
    expected = {
        "page": page,
        "odds_time_label": label,
        "is_final_odds": label_kind == "final",
        "odds": expected_odds(page, values, combos),
    }
    return html, expected


def break_html(html: str, how: str) -> str:
    """解析が失敗すべき例を作る。"""
    cells = list(re.finditer(r'<td class="oddsPoint[^"]*">[^<]*</td>', html))
    if how == "missing":
        last = cells[-1]
        return html[: last.start()] + html[last.end():]
    if how == "extra":
        last = cells[-1]
        return html[: last.end()] + "\n" + last.group(0) + html[last.end():]
    if how == "no_table":
        return re.sub(r'<td class="oddsPoint[^"]*">[^<]*</td>', "", html)
    raise ValueError(how)


PLAN = {
    # page: (締切前/締切時, 欠場艇, 票の無い組合せ)
    "odds3t": ("pre", {4}, {(1, 2, 3)}),
    "odds2tf": ("final", set(), set()),
    "oddstf": ("pre", {5}, {(3,)}),
}
BROKEN = {"odds3t": "missing", "odds2tf": "extra", "oddstf": "no_table"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="締切前オッズの合成HTMLと期待値を作る")
    for page in PLAN:
        parser.add_argument(f"--{page}", required=True, help=f"{page} の実物のHTML(.html か .html.gz)")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for page, (kind, absent, no_votes) in PLAN.items():
        src = Path(getattr(args, page))
        raw = gzip.open(src).read() if src.suffix == ".gz" else src.read_bytes()
        real = raw.decode("utf-8")
        # 実物で、独立に求めた並びと解析の結果が一致することを確かめる
        real_odds, _ = oo.parse_page(page, real)
        cells, _ = oo.extract_cells(real)
        assert expected_odds(page, cells, derive_combinations(page, real)) == real_odds, page
        html, expected = synthesize(page, real, kind, absent, no_votes)
        (args.out / f"{page}.synthetic.html").write_text(html, encoding="utf-8")
        (args.out / f"{page}.expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        (args.out / f"broken_{page}_{BROKEN[page]}.html").write_text(break_html(html, BROKEN[page]), encoding="utf-8")
        print(f"{page}: 書き出しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
