#!/usr/bin/env bash
# 画面モックのスクリーンショットを shots/ に保存する(WSL から Windows の Chrome をヘッドレスで使う)。
# Playwright が無い環境向け。使い方: bash design/screens/tools/shoot.sh [state一覧ファイル]
# 新しいヘッドレスはウィンドウの最小幅があるため、tools/frame.html の iframe で幅を固定して撮る。
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME="${CHROME:-/mnt/c/Program Files/Google/Chrome/Application/chrome.exe}"
WIN_DIR=$(wslpath -w "$PWD")
BASE="file:///${WIN_DIR//\\//}"
mkdir -p shots

# 画面ファイル:state:高さ
LIST=(
  "s1-home:today:1000" "s1-home:today-closed:820" "s1-home:by-stadium:1000" "s1-home:past:860"
  "s1-home:loading:720" "s1-home:offline:720" "s1-home:error:720" "s1-home:no-today:720"
  "s2-race:before:1900" "s2-race:players:720" "s2-race:skip:1100" "s2-race:preview:1000"
  "s2-race:result-hit:1400" "s2-race:result-final:1000" "s2-race:absent:1500"
  "s2-race:canceled:720" "s2-race:no-prediction:720" "s2-race:closed-wait:720" "s2-race:loading:720"
  "s3-performance:normal:1300" "s3-performance:few:820" "s3-performance:error:720"
  "s4-dates:sheet:720" "s5-about:about:1000"
  "s1-home:local-custom:900" "s1-home:local-error:720"
  "s2-race:ev-public:1500" "s2-race:ev-local:1500" "s2-race:ev-custom:1500" "s2-race:odds-late:900"
  "s2-race:odds-pending:900" "s2-race:ev-custom-noodds:760" "s2-race:result-final-local:1000"
  "s3-performance:custom:900" "s5-about:about-local:1000"
  "s6-settings:default:2300" "s6-settings:custom:2100" "s6-settings:reset:720" "s6-settings:worker-badkey:800"
  "s2-race:fetch-ready:1000" "s2-race:fetch-loading:1000" "s2-race:fetch-ok:1100" "s2-race:fetch-cached:1100"
  "s1-home:stadium-focus:1000" "s1-home:gentle:760" "s2-race:p3a-before:2600" "s2-race:p3a-skip-reason:900"
  "s3-performance:p3a:2000" "s8-ledger:normal:2000" "s8-ledger:over:760" "s8-ledger:empty:760" "s9-marks:normal:900"
  "s1-home:stale:760" "s2-race:tenkai:900" "s2-race:racer-card:1300" "s2-race:racer-card-none:800" "s5-about:models:1000"
  "s10-analytics:stadium:1500" "s10-analytics:cube:1100" "s10-analytics:players:900" "s10-analytics:model:1500" "s10-analytics:sim:1200" "s10-analytics:you:900"
  "s2-race:tenkai-ext:1200" "s10-personas:live:2100" "s10-personas:backtest:2100"
  "s2-race:fetch-partial:1100" "s2-race:fetch-nokey:1000" "s2-race:fetch-errors:1500" "s2-race:source-manual:1100"
)
THEME="${THEME:-light}"
for item in "${LIST[@]}"; do
  IFS=: read -r page state h <<< "$item"
  for w in 360 430; do
    src="../${page}.html?state=${state}&theme=${THEME}"
    enc=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$src")
    suffix=""; [ "$THEME" = "dark" ] && suffix="-dark"
    out="${page}_${state}_${w}${suffix}.png"
    timeout 60 "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
      --allow-file-access-from-files --virtual-time-budget=1500 \
      --window-size=${w},${h} --screenshot="${WIN_DIR}\\shots\\${out}" \
      "${BASE}/tools/frame.html?w=${w}&h=${h}&src=${enc}" >/dev/null 2>&1 || echo "失敗: ${out}"
    echo "${out}"
  done
done
