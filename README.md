# boatrace-yoso
ボートレース予想ツール(個人用)

非公式データ(Boatrace Open API / Turnmark API)と、公式サイトの締切前オッズ(ローカルでのみ取得)に基づく参考情報です。正確性は保証しません。舟券の購入は自己責任で。仕様は `spec/` を参照してください。

## セットアップ(WSL / Ubuntu)

```bash
# 仮想環境の作成とライブラリの導入
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# LightGBM が使う OpenMP ランタイム
#   本来は: sudo apt install libgomp1
#   当面(sudo を後回しにしている間)は、.venv/lib/extra/ に置いた libgomp を
#   .venv/bin/activate が LD_LIBRARY_PATH に足す。必ず activate してから使うこと。
source .venv/bin/activate

# 動作確認
python -c "import lightgbm, pandas, sklearn; print(lightgbm.__version__)"
```

`.venv` を作り直したときは、sudo なしで次のように再設定できます(または `sudo apt install libgomp1`)。

```bash
mkdir -p /tmp/gomp && cd /tmp/gomp && apt-get download libgomp1 && dpkg -x libgomp1_*.deb ex
mkdir -p ~/workspace/boatrace-yoso/.venv/lib/extra
cp -a ex/usr/lib/x86_64-linux-gnu/libgomp.so.1* ~/workspace/boatrace-yoso/.venv/lib/extra/
echo 'export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/extra${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' >> ~/workspace/boatrace-yoso/.venv/bin/activate
```

`.venv/bin/python` を activate せずに直接使うと、LightGBM の import に失敗します(`libgomp.so.1` が見つからない)。`scripts/live_loop.py` は子プロセスに `.venv/lib/extra` を自動で渡すので、activate を忘れても動きます。

## テスト

```bash
source .venv/bin/activate
python -m pytest tests/common     # 担当ごとに tests/<範囲>
```

## 1日の運用(ローカルで手動)

commit・push・GitHub Pages での公開は、当面行いません。PC で実行し、スマホからは自宅の Wi-Fi で見ます(下の「スマホで見る」)。

```bash
source .venv/bin/activate

# まず予定を確認する(通信・書き込みなし)
python -m scripts.live_loop --dry-run

# 朝の準備だけ(当日の出走表の取得 → 特徴量 → 予想 → JSON の書き出し)
python -m scripts.live_loop --prepare-only

# 1日中動かす(準備 → 締切5分前のオッズ取得 → 30分ごとの結果反映 → 最終レースの40分後に終了)
python -m scripts.live_loop

# よく使うオプション
python -m scripts.live_loop --no-odds                 # 締切前オッズを取らない
python -m scripts.live_loop --pages odds3t            # 3連単のページだけ取る(リクエスト数を約1/3に)
python -m scripts.live_loop --stadiums 2,12           # 場を絞る
python -m scripts.live_loop --skip-prepare            # 途中から再開する
```

- 止めるときは Ctrl+C。オッズ取得の子プロセスも止まります。
- ナイター・ミッドナイトのある日は、最終レースの締切が22時台になることがあります。その40分後まで動きます。PC がスリープすると止まります。
- 外部へのアクセスは、収集のモジュール(`src/collect/`)だけが行います。間隔は1.5秒以上で、公式サイトから 403・429 が返ったら、その日のオッズ取得は止まります(`spec/07` 2.5)。
- 締切前オッズの数値は `docs/data-local/`(git 管理外)にだけ書き出し、公開用の `docs/data/` には入れません(`spec/07` 1章)。

過去分の取得や学習は、各モジュールの CLI を直接使います(`spec/01`〜`spec/03`)。

## スマホで見る(自宅の Wi-Fi)

PC で簡易 Web サーバーを立て、同じ Wi-Fi のスマホのブラウザで開きます。

### 1. WSL でサーバーを立てる

```bash
cd ~/workspace/boatrace-yoso
python3 -m http.server 8000 --bind 0.0.0.0 --directory docs
```

PC のブラウザで `http://localhost:8000/` が開けることを確かめます。

### 2. Windows 側で、スマホから WSL に届くようにする(初回のみ)

WSL2 は標準では Windows の内側の別ネットワークにあるため、スマホから直接は届きません。次のどちらかを行います。

**方法A: ミラーモード(Windows 11 22H2 以降。おすすめ)**

1. Windows の `%UserProfile%\.wslconfig` に次を書く:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
2. PowerShell で `wsl --shutdown` を実行し、WSL を開き直す。
3. 管理者の PowerShell で、受信を許可する:
   ```powershell
   New-NetFirewallRule -DisplayName "boatrace-yoso 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
   ```

**方法B: ポート転送(ミラーモードが使えない場合)**

WSL の IP アドレスは再起動のたびに変わるので、そのたびに転送先を設定し直します。

```bash
# WSL 側で IP アドレスを調べる
hostname -I
```

```powershell
# 管理者の PowerShell(<WSLのIP> を上の値に置き換える)
netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=<WSLのIP>
New-NetFirewallRule -DisplayName "boatrace-yoso 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

- 使い終わったら転送を消せます: `netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0`
- Wi-Fi のネットワークの種類を「プライベート」にしておきます(ファイアウォールの規則を `-Profile Private` に限っているため)。

### 3. スマホで開く

1. Windows の PowerShell で `ipconfig` を実行し、Wi-Fi の「IPv4 アドレス」(例: `192.168.1.23`)を調べる。
2. スマホのブラウザで `http://192.168.1.23:8000/?data=local` を開く。
   - `?data=local` を付けると、締切前オッズの期待値ランクなどが入ったローカル用のデータ(`docs/data-local/`)を読みます。無い場合は公開用のデータ(`docs/data/`)を読みます。
   - `?data=sample` を付けると、開発用のサンプルデータを表示します。

注意:
- 自宅のネットワークの中だけで使ってください。サーバーは `docs/` の中身をそのまま見せます。外出先の公衆 Wi-Fi でサーバーを立てないこと。
- `http://` の LAN 内アドレスでは、ブラウザの制限で Service Worker が動きません。オフライン表示とホーム画面への追加は、ローカル表示では使えません(PC で `localhost` を開いた場合は動きます)。
- 表示されるのは、最後に `scripts.live_loop`(または export)を実行した時点のデータです。画面の「データ更新」の時刻を確認してください。

## 自動実行(GitHub Actions)

`.github/workflows/daily.yml` が、PC を起動していなくても 収集(Open API)→ 予想 → export → 公開(Cloudflare Workers の `br-yoso`)を行います。`docs/data` のコミットは朝と前日分の確定の1日2回だけです(リポジトリの肥大化を抑えるため。日中の更新は Cloudflare への公開だけ)。**Actions からは公式サイト(boatrace.jp)にアクセスしません**(締切前オッズは取らない。オッズはこれまでどおり手元とスマホの中継 Worker で)。

| ジョブ | JST | cron(UTC) | 内容 |
|---|---|---|---|
| 朝 | 7:41 | `41 22 * * *` | `live_loop --prepare-only --no-odds`(出走表 → 特徴量 → pre 予想 → export) |
| 日中 | 8:04〜23:34 の30分ごと | `4,34 23 * * *` と `4,34 0-14 * * *` | openapi → build_interim → 当日の pre・preview の特徴量 → 直前版の予想(`--per-race-lock`。直前情報が揃った締切前のレースだけ1回確定)→ export。pre 予想が無ければ準備から |
| 前日分の確定 | 1:12 | `12 16 * * *` | 前日の openapi を取り直して結果を最終反映(fetch は翌日1時以降の取得を確定とみなす) |

- 各ジョブは「R2 から状態を取得 → 処理 → 公開 →(朝と確定のみ)`docs/data` をコミット・push(github-actions[bot])→ R2 に状態を保存」。途中で失敗したら状態は保存しません。
- 同時には1つしか動きません(concurrency)。1回30分で打ち切ります。GitHub の都合で起動が数分〜数十分遅れることがあります。

### 1. 必要な secrets

GitHub のリポジトリの Settings → Secrets and variables → Actions に登録します。

| 名前 | 内容 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare の API トークン。権限は「Workers Scripts: Edit」と「Workers R2 Storage: Edit」(アカウント単位) |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare のアカウント ID(ダッシュボードの右側に表示) |

Settings → Actions → General の「Workflow permissions」が「Read and write」でなくても、ワークフロー側で `contents: write` を指定しています(組織の設定で禁止されている場合は変更が必要)。

### 2. R2 バケットを作る(初回のみ、手元で)

```bash
nvm use 22 && npx wrangler login
npx wrangler r2 bucket create br-yoso-data     # 非公開(公開アクセスや独自ドメインは設定しない)
```

### 3. 初回の状態のアップロード(手元から)

Actions は、手元の `data/raw`・`data/interim`・`data/predictions`・`models` を元に動きます(`data/raw/official_odds/` は上げません)。`data/features` は上げず、ジョブ内で当日分だけ `build_features --date` で作り直します。

```bash
source .venv/bin/activate
python -m scripts.ci_state push --dry-run    # tar.gz を作ってサイズを確認するだけ(R2 に触らない)
python -m scripts.ci_state push              # R2 の state/<種類>.tar.gz に上げる(合計 約110MB)
python -m scripts.ci_state push --only models   # モデルを学習し直したときは、これだけ上げる
```

手元でモデルを学習し直したり過去分を取り込んだりしたら、Actions が動いていない時間帯(例: 2:00〜7:00)に上げてください。逆に Actions の最新の状態を手元に持ってくるときは `python -m scripts.ci_state pull`(手元の該当ディレクトリは置き換わります)。

### 4. 手動実行

GitHub の Actions タブ → daily → Run workflow で、`mode`(morning / daytime / finalize)・対象日・公開するかを選んで実行します。または `gh workflow run daily -f mode=daytime`。

### 5. 止め方

- 一時的に止める: Actions タブ → daily → 「…」→ Disable workflow(または `gh workflow disable daily`)。再開は Enable。
- 実行中のものを止める: その実行を開いて Cancel(状態は保存されません)。
- 60日間リポジトリに動きが無いと、GitHub が定期実行を自動で止めます(朝と確定の bot のコミットで通常は続きます)。

### 6. 失敗に気づく・やり直す

- 各ジョブは成功したときだけ `docs/data/status.json`(最後に成功した時刻・ジョブの種類・次の予定時刻・使っているモデル)を書き、Cloudflare への公開に含めます(git にはコミットしません)。アプリはこれを見て、開催時間中に更新が90分以上止まっていると画面上部に知らせます。
- **失敗メール**: GitHub は、定期実行のワークフローが失敗すると、ワークフローを最後に編集した人に既定でメールを送ります。GitHub の右上のアイコン → Settings → Notifications → 「Actions」で、Email にチェックがあり、「Only notify for failed workflows」(失敗したワークフローのみ)になっていることを確かめてください。
- **失敗したジョブを見る**: リポジトリの Actions タブ → 左の daily → 赤い×の実行を開く → 失敗したステップを開いてログを見る。`gh run list --workflow daily` と `gh run view <番号> --log-failed` でも見られます。
- **再実行**: 失敗した実行の画面の右上「Re-run jobs」→「Re-run failed jobs」(または `gh run rerun <番号> --failed`)。失敗したジョブは R2 に状態を保存していないので、そのままやり直せます。時間がたっている場合は、手動実行(上の4.)で今の mode を選んで実行する方が確実です。

### 7. 費用(無料枠の範囲)

- GitHub Actions: 公開リポジトリの標準の runner は無料。1日 約33回 × 数分。
- Cloudflare R2: 無料枠は保存 10GB/月、書き込み(Class A)100万回/月、読み出し(Class B)1000万回/月、転送は無料。使うのは保存 約0.1〜0.3GB、書き込み 約5000回/月、読み出し 約5000回/月。
- Cloudflare Workers の静的アセット: 無料プランで足ります(デプロイは1日 約33回)。
- 注意: `docs/data` のコミットは1日2回(約1.5MB/日、年に約0.5GB)。古い days の整理は spec/06 の U5・E-6。

## ディレクトリ

| パス | 内容 |
|---|---|
| `spec/` | 仕様書 |
| `src/collect/` | データ取得(外部アクセスはここだけ) |
| `src/features/` | 整形・特徴量 |
| `src/model/` | 学習・予測・期待値 |
| `src/export/` | アプリ用 JSON の出力 |
| `src/common/` | 共通のコード値など |
| `scripts/` | 統合スクリプト(`live_loop.py`) |
| `docs/` | PWA 本体(`docs/data/` は公開用 JSON、`docs/data-local/` はローカル専用で git 管理外) |
| `data/` | 生データ・中間データ(git 管理外) |
| `design/` | 画面デザイン・ビジュアルの資料 |
