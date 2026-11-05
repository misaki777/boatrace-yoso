#!/usr/bin/env bash
# GitHub Actions から呼ぶ、ジョブごとの処理(状態の取得・保存とコミットはワークフロー側)。
# 公式サイト(boatrace.jp)へはアクセスしない。通信は src.collect.fetch の openapi 取得だけ。
#
# 使い方:
#   scripts/ci_run.sh morning   [YYYY-MM-DD]   # 朝の準備(出走表 → 特徴量 → pre 予想 → export)
#   scripts/ci_run.sh daytime   [YYYY-MM-DD]   # 日中の更新(openapi → interim → export)
#   scripts/ci_run.sh finalize  [YYYY-MM-DD]   # 前日分の最終取り込み(既定: 昨日 JST)
#   DRY_RUN=1 scripts/ci_run.sh daytime        # コマンドを表示するだけ
set -euo pipefail

MODE="${1:?morning / daytime / finalize を指定してください}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python}"

if [ "$MODE" = "finalize" ]; then
  D="${2:-$(TZ=Asia/Tokyo date -d yesterday +%F)}"
else
  D="${2:-$(TZ=Asia/Tokyo date +%F)}"
fi
YMD="${D//-/}"
echo "mode=$MODE date=$D"

run() {
  echo "+ $*"
  if [ -z "${DRY_RUN:-}" ]; then "$@"; fi
}

case "$MODE" in
  morning)
    # live_loop の準備(fetch → build_interim → build_features pre → predict pre → export_day → export_index)。
    # predict は確定済みなら書かない(既存の仕様)ので、再実行しても予想は変わらない。
    run "$PY" -m scripts.live_loop --prepare-only --no-odds --date "$D"
    ;;
  daytime)
    # 朝のジョブが失敗・遅延して pre 予想が無いときだけ、準備から行う
    if [ ! -f "data/predictions/${YMD}_pre.parquet" ]; then
      echo "pre 予想が無いため、準備から実行します"
      run "$PY" -m scripts.live_loop --prepare-only --no-odds --date "$D"
      exit 0
    fi
    run "$PY" -m src.collect.fetch --source openapi --date "$D"
    run "$PY" -m src.features.build_interim --from "$D" --to "$D"
    # 直前版の予想(preview)。レースごとに、直前情報が揃った締切前のレースだけを1回確定する(--per-race-lock)。
    # data/features は R2 に保存しないので毎回作り直す(preview の pre フォールバックが当日の pre 特徴量を読む)。
    # 締切時刻は interim の races から読むため、build_interim の後に行う。
    run "$PY" -m src.features.build_features --variant pre --date "$D"
    run "$PY" -m src.features.build_features --variant preview --date "$D"
    run "$PY" -m src.model.predict --date "$D" --variant preview --per-race-lock
    run "$PY" -m src.export.export_day --date "$D"
    run "$PY" -m src.export.export_index
    ;;
  finalize)
    # openapi の前日ファイルは 0:00 ごろに更新され、fetch は翌日1時以降の取得を「確定」とみなす
    run "$PY" -m src.collect.fetch --source openapi --date "$D"
    run "$PY" -m src.features.build_interim --from "$D" --to "$D"
    run "$PY" -m src.export.export_day --date "$D"
    run "$PY" -m src.export.export_index
    ;;
  *)
    echo "不明なモード: $MODE" >&2
    exit 2
    ;;
esac
