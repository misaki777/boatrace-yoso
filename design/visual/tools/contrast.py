"""tokens.json の contrast_checks を WCAG 2.x のコントラスト比で検証する(標準ライブラリのみ)。

使い方: python design/visual/tools/contrast.py [--md]
  --md を付けると README に貼る Markdown の表を出す。
基準: 文字 4.5:1(AA、通常サイズ)、非テキスト(枠・バー・アイコン)3:1(WCAG 2.1 1.4.11)。
"""
import json
import sys
from pathlib import Path

VISUAL_DIR = Path(__file__).resolve().parent.parent


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted([relative_luminance(a), relative_luminance(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main() -> None:
    tokens = json.loads((VISUAL_DIR / "tokens.json").read_text(encoding="utf-8"))
    md = "--md" in sys.argv
    failed = 0
    if md:
        print("| 組み合わせ | 基準 | ライト | ダーク |")
        print("|---|---|---|---|")
    for fg, bg, minimum, label in tokens["contrast_checks"]:
        cells = []
        for mode in ("light", "dark"):
            colors = tokens["themes"][mode]["color"]
            r = contrast_ratio(colors[fg], colors[bg])
            ok = r >= minimum
            failed += 0 if ok else 1
            cells.append((r, ok))
        if md:
            fmt = lambda c: f"{c[0]:.2f}" + ("" if c[1] else " **NG**")
            print(f"| {label} (`{fg}` / `{bg}`) | {minimum}:1 | {fmt(cells[0])} | {fmt(cells[1])} |")
        else:
            flag = lambda c: f"{c[0]:5.2f} {'OK' if c[1] else 'NG'}"
            print(f"{label:<34} >= {minimum:<3}  light {flag(cells[0])}  dark {flag(cells[1])}")
    if not md:
        print(f"\nNG: {failed} 件")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
