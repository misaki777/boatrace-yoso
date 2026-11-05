#!/usr/bin/env bash
# GitHub Actions から呼ぶ、ジョブごとの処理(状態の取得・保存とコミットはワークフロー側)。
# 公式サイト(boatrace.jp)へはアクセスしない。通信は src.collect.fetch の openapi 取得だけ。
#
# 毎日のジョブは生データの全履歴を持たない(CI-2。spec/10 2.4)。
# 当日の特徴量は「履歴の集計の状態 data/history/state_YYYYMMDD.parquet + 当日の出走表」から作る。
#
# 使い方:
#   scripts/ci_run.sh morning   [YYYY-MM-DD]   # 朝の準備(出走表 → 状態 → 特徴量 → pre 予想 → export)
#   scripts/ci_run.sh daytime   [YYYY-MM-DD]   # 日中の更新(openapi → interim → 直前版の予想 → export)
#   scripts/ci_run.sh finalize  [YYYY-MM-DD]   # 前日分の最終取り込みと状態を1日進める(既定: 昨日 JST)
#   DRY_RUN=1 scripts/ci_run.sh daytime        # コマンドを表示するだけ
set -euo pipefail

MODE="${1:?morning / daytime / finalize を指定してください}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python}"
HIST="data/history"

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

state_file() {  # YYYY-MM-DD → data/history/state_YYYYMMDD.parquet
  echo "$HIST/state_${1//-/}.parquet"
}

# 対象日の状態が無ければ、それより前で一番新しい状態から必要な日数だけ進める
# (前日確定のジョブが失敗した日の救済。進める日の interim が要る)
ensure_state() {
  local target="$1" want latest days
  want="$(state_file "$target")"
  if [ -f "$want" ]; then return 0; fi
  latest="$(ls "$HIST"/state_[0-9]*.parquet 2>/dev/null | awk -v w="$want" '$0 < w' | sort | tail -1 || true)"
  if [ -z "$latest" ]; then
    if [ -n "${DRY_RUN:-}" ]; then
      echo "+ (状態が無い: 実際の実行では失敗します。README の「初回の状態」を参照)"
      return 0
    fi
    echo "$want も、それより前の状態もありません(README の「初回の状態」を参照)" >&2
    return 1
  fi
  local from_ymd; from_ymd="$(basename "$latest" .parquet)"; from_ymd="${from_ymd#state_}"
  days=$(( ( $(date -d "$target" +%s) - $(date -d "$from_ymd" +%s) ) / 86400 ))
  echo "状態 $want が無いため、$latest から ${days} 日進めます"
  run "$PY" -m src.features.history_state advance --state "$latest" --days "$days"
}

# 展開予想(決まり手の確率)と選手カード。
# 選手カードは D−1 までの結果だけで決まるが、data/features は R2 に保存しないので、
# 日中も export(E-14)が読めるように毎回作り直す(数秒)。
tenkai_and_cards() {
  local v="$1"
  run "$PY" -m src.features.build_tenkai --variant "$v" --date "$D" --state "$(state_file "$D")"
  if [ "$v" = "pre" ]; then
    run "$PY" -m src.model.predict_tenkai --date "$D" --variant pre
  else
    run "$PY" -m src.model.predict_tenkai --date "$D" --variant preview --per-race-lock
  fi
  run "$PY" -m src.features.racer_cards --date "$D" --state "$(state_file "$D")"
}

morning() {
  # live_loop --prepare-only と同じ流れだが、特徴量は状態から作る(--state)。
  # predict は確定済みなら書かない(既存の仕様)ので、再実行しても予想は変わらない。
  run "$PY" -m src.collect.fetch --source openapi --date "$D"
  run "$PY" -m src.features.build_interim --from "$D" --to "$D"
  ensure_state "$D"
  run "$PY" -m src.features.build_features --variant pre --date "$D" --state "$(state_file "$D")"
  run "$PY" -m src.model.predict --date "$D" --variant pre
  tenkai_and_cards pre
  run "$PY" -m src.export.export_day --date "$D"
  run "$PY" -m src.export.export_index
  run "$PY" -m src.export.phase3b racers --date "$D"
  run "$PY" -m src.export.phase3b models
}

case "$MODE" in
  morning)
    morning
    ;;
  daytime)
    # 朝のジョブが失敗・遅延して pre 予想が無いときだけ、準備から行う
    if [ ! -f "data/predictions/${YMD}_pre.parquet" ]; then
      echo "pre 予想が無いため、準備から実行します"
      morning
      exit 0
    fi
    run "$PY" -m src.collect.fetch --source openapi --date "$D"
    run "$PY" -m src.features.build_interim --from "$D" --to "$D"
    ensure_state "$D"
    # 直前版の予想(preview)。レースごとに、直前情報が揃った締切前のレースだけを1回確定する(--per-race-lock)。
    # data/features は R2 に保存しないので毎回作り直す(preview の pre フォールバックが当日の pre 特徴量を読む)。
    # 締切時刻は interim の races から読むため、build_interim の後に行う。
    run "$PY" -m src.features.build_features --variant pre --date "$D" --state "$(state_file "$D")"
    run "$PY" -m src.features.build_features --variant preview --date "$D" --state "$(state_file "$D")"
    run "$PY" -m src.model.predict --date "$D" --variant preview --per-race-lock
    tenkai_and_cards preview
    run "$PY" -m src.export.export_day --date "$D"
    run "$PY" -m src.export.export_index
    run "$PY" -m src.export.phase3b racers --date "$D"
    ;;
  finalize)
    # openapi の前日ファイルは 0:00 ごろに更新され、fetch は翌日1時以降の取得を「確定」とみなす
    run "$PY" -m src.collect.fetch --source openapi --date "$D"
    run "$PY" -m src.features.build_interim --from "$D" --to "$D"
    # 状態を1日進める(state_D + D の結果 → state_{D+1})。state_D が無ければ古い状態から進める
    NEXT="$(date -d "$D +1 day" +%F)"
    if [ -f "$(state_file "$D")" ] || [ -n "${DRY_RUN:-}" ]; then
      run "$PY" -m src.features.history_state advance --state "$(state_file "$D")"
    else
      ensure_state "$NEXT"
    fi
    run "$PY" -m src.export.export_day --date "$D"
    run "$PY" -m src.export.export_index
    ;;
  *)
    echo "不明なモード: $MODE" >&2
    exit 2
    ;;
esac
