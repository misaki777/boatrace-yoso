"""tokens.json から tokens.css を生成する(標準ライブラリのみ)。

使い方: python design/visual/tools/build_tokens.py
"""
import json
from pathlib import Path

VISUAL_DIR = Path(__file__).resolve().parent.parent
COMMON_GROUPS = ["font", "size", "weight", "leading", "space", "radius", "tap", "badge", "bar", "duration"]


def theme_lines(theme: dict, indent: str) -> list[str]:
    """テーマ(色・影)を CSS 変数の行にする。"""
    lines = [f"{indent}--c-{k}: {v};" for k, v in theme["color"].items()]
    lines += [f"{indent}--shadow-{k}: {v};" for k, v in theme["shadow"].items()]
    return lines


def build(tokens: dict) -> str:
    out = [
        "/* boatrace-yoso デザイントークン",
        "   このファイルは tools/build_tokens.py が tokens.json から生成する。直接編集しないこと。",
        "   テーマ: 既定は端末設定(prefers-color-scheme)に従う。",
        "   <html data-theme=\"light\"> / <html data-theme=\"dark\"> で強制できる(S6 設定用)。 */",
        "",
        ":root {",
        "  color-scheme: light dark;",
    ]
    for group in COMMON_GROUPS:
        for k, v in tokens[group].items():
            out.append(f"  --{group}-{k}: {v};")
    out.append("")
    out.append("  /* ライト(既定) */")
    out += theme_lines(tokens["themes"]["light"], "  ")
    out.append("}")
    out.append("")
    out.append("@media (prefers-color-scheme: dark) {")
    out.append("  :root:not([data-theme=\"light\"]) {")
    out += theme_lines(tokens["themes"]["dark"], "    ")
    out.append("  }")
    out.append("}")
    out.append("")
    out.append(":root[data-theme=\"light\"] { color-scheme: light; }")
    out.append(":root[data-theme=\"dark\"] {")
    out.append("  color-scheme: dark;")
    out += theme_lines(tokens["themes"]["dark"], "  ")
    out.append("}")
    out.append("")
    return "\n".join(out)


def main() -> None:
    tokens = json.loads((VISUAL_DIR / "tokens.json").read_text(encoding="utf-8"))
    light, dark = tokens["themes"]["light"], tokens["themes"]["dark"]
    # ライトとダークでキーが揃っているかを確認する
    for part in ("color", "shadow"):
        diff = set(light[part]) ^ set(dark[part])
        if diff:
            raise SystemExit(f"{part} のキーがライト/ダークで不一致: {sorted(diff)}")
    (VISUAL_DIR / "tokens.css").write_text(build(tokens), encoding="utf-8")
    print("tokens.css を生成しました")


if __name__ == "__main__":
    main()
