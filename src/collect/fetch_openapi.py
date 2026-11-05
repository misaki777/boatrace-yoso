"""(互換用の薄いラッパー)openapi の1日分を取得する。新しいコードは src.collect.fetch を使うこと。

使い方:
    python -m src.collect.fetch_openapi            # 今日(JST)
    python -m src.collect.fetch_openapi 2026-10-30 # 指定日

保存先・スキップ・待機・ログは src.collect.fetch と同じ
(data/raw/openapi/YYYY/YYYYMMDD.json.gz)。
"""

import sys

from src.collect import fetch


def main(argv: list[str]) -> int:
    args = ["--source", "openapi"]
    if len(argv) > 1:
        args += ["--date", argv[1]]
    return fetch.main(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
