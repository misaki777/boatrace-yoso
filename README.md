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
