#!/usr/bin/env bash
# docs/ を Cloudflare(Workers の静的アセット)に公開する。
# ローカル専用のデータ(docs/data-local/ = オッズ・期待値の数値)と開発用テストページは除外する。
#
# 使い方:
#   scripts/deploy_pages.sh            # 既定のプロジェクト名 br-yoso
#   PAGES_PROJECT=名前 scripts/deploy_pages.sh
# 事前に: nvm use 22 && npx wrangler login
set -euo pipefail

PROJECT="${PAGES_PROJECT:-br-yoso}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# wrangler は Node 22 以上が必要
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "Node 22 以上が必要です(今: $(node --version 2>/dev/null || echo なし))。先に 'nvm use 22' を実行してください。" >&2
  exit 2
fi

# 公開用の一時フォルダに、除外対象を外してコピーする
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
cp -a "$ROOT/docs/." "$OUT/"
rm -rf "$OUT/data-local" "$OUT/app/tests"

# 念のため、オッズ・期待値の数値キーが公開データに混ざっていないか確認する
if grep -rqE '"(odds|ev)"[[:space:]]*:[[:space:]]*[0-9]' "$OUT/data" --include='*.json' \
     --exclude-dir=sample --exclude-dir=sample-local; then
  echo "公開データにオッズ・期待値の数値が含まれています。公開を中止しました。" >&2
  exit 1
fi

# wrangler はリポジトリの外(公開用フォルダの親)で動かす。
# リポジトリ内で動かすと wrangler.jsonc を自動生成し、docs/ 全体(ローカル用データを含む)を対象にしてしまうため。
WORK="$(mktemp -d)"
trap 'rm -rf "$OUT" "$WORK"' EXIT
cd "$WORK"

# 静的ファイルだけの Worker として公開する(Cloudflare Pages は Workers の静的アセットに統合された)
cat > wrangler.jsonc <<JSON
{
  "name": "$PROJECT",
  "compatibility_date": "2026-11-01",
  "workers_dev": true,
  "preview_urls": false,
  "assets": { "directory": "$OUT" }
}
JSON

npx --yes wrangler deploy 2>&1 | tee "$WORK/deploy.log"
URL="$(grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' "$WORK/deploy.log" | head -1)"
echo "公開しました: ${URL:-(URL はダッシュボードで確認してください)}"
