# design/visual(デザイナーB: ビジュアル・トークン・部品・アイコン)

版: v0.8(2026-11-05)/ 状態: PMレビュー待ち(DB-1〜DB-6)

### 変更履歴

- v0.8: デザイナーAの DA-6(`design/screens/README.md` 17章)に対応。**追加のみ**。A の名前で使える `.stalebar`(黄)`.mini`(`.p1/.p2/.p3/.pf`)`.tbl tr.is-plan`、アプリバーの更新時刻 `.updated-at.is-stale`、選手名のリンク `.racer-link`、カードが無い日の `.emptybox`
- v0.7: DB-6(`spec/10` Phase 3b)。**追加のみ**(色トークンの追加なし。検査を5組追加)。
  - 更新停止の帯 `.stale`(`--sticky`)
  - 展開予想 `.tenkai`(`__hero`)、帯グラフ `.tkbar` + `.tkbar-labels`、行 `.tk-rows > .tk-row`(`.is-main`)
  - シート `.sheet`(`--inline`)`.scrim`、選手カードの部品 `.statgrid`、コース別の表 `.cstable`(`tr.is-plan` / `tr.is-lown`)、着順の並び `.fins > .fin--1〜6/f/l/x`(`.fins--detail`)
  - モデルの更新履歴 `.mhists > .mhist--adopted/kept`
- v0.6: デザイナーAの DA-5(`design/screens/README.md` 16章)に対応。**追加のみ**。
  - 同じ重さの結果チップ `.res--hit/miss/refund/pending`、予算メーター `.meter`(`is-warn/is-over`)、場のタブ `.stabs`、下部タブ `.tabbar.tabbar--4`
  - A の名前で使える別名: `.warnbar`(`--over`)`.gentle` `.few` `.fewnote` `.tip`
  - **自分の印・個人データの色 `--c-mine`(青緑)** と `.minetag`、`.markpick--mine`、`.markvs b.is-mine`
- v0.5: DB-5(`spec/08` Phase 3a)。**追加のみ**。
  - `charts.js`(新規・依存なし): `BRCharts.calibration()`(較正グラフ + Wilson 区間)、`BRCharts.pnl()`(累積収支 + ドローダウン)、`BRCharts.wilson()`
  - 区間つきの数値 `.ci-num`(`__bar` `__verdict`)/ `.ci-inline`、件数が少ない `.is-lown` + `.lown-tag`
  - 印の入力 `.markpick`、自分とモデルの比較 `.markvs`、根拠 `.reasons > .reason--up/down[data-strength]`
  - 買い目合計 `.betsum`(`__grid` `__alloc` `__foot`)と `.trigami--ok/ng`
  - 警告 `.budgetbar` `.banner--quiet` `.banner__close`、説明 `.help` + `.helppop`
  - DA-4 の要望の残り: `.steps`(`is-done/is-now/is-fail`)、`.switch.is-on`(つまみに ✓)、`.input.is-error/.is-saved`
  - 色トークン: `chart-ci` `chart-ideal` `chart-dd` `reason-up` `reason-down`
- v0.4: DB-4(`spec/07` 9章、中継 Worker でのオッズ取得)。**追加のみ**(新しい色トークンは無し。検査する組合せを9つ追加)。
  - 取得ボタン `.fetchbtn[data-state=idle|loading|cooldown|disabled]`、小さい更新ボタン `.refreshbtn`
  - 取得中の進み表示 `.fetchprog`(`--t` で目安の進み)
  - 取得時刻 `.fetchstamp`、キャッシュ表示 `.cachetag`、券種ページの状態 `.pagestat--ok/error`
  - 帯の拡張 `.banner--stack`(`__body` `__title` `__text` `__actions`)と灰の帯 `.banner--muted`
  - オッズの出どころ `.oddssrc--manual/worker/cache/rank/none`
  - デザイナーAの S2(DA-4)の形: コンパクトな箱 `.fetchbox`(`--warn/--error`)、2段のボタン `.fetchbtn--compact`(`<small>` で補足)、副ボタン `.fetchbtn--sub`
  - 入力欄 `.field`(`.is-invalid` `.is-ok`)`.input`(`--mono`)、合言葉 `.secret` + `.secret__toggle`、保存済みの表示 `.secret-saved`
- v0.3: DB-3(`spec/07_official_odds_and_settings.md`)。**追加のみ**(既存のクラス・変数は変えていない。`.seg` は選択状態に `aria-checked` / `aria-pressed` も受け付けるよう広げただけ)。
  - 期待値ランク `.evrank--a/b/c/d/none`(`--sm` 20px)、`.evrank-label`、`.evlegend`
  - オッズの時点 `.oddstime--ok/partial/late/stopped/not-targeted/none` と注記 `.oddsnote`
  - ローカル表示の目印 `.localmark`、ローカル版の数値 `.oddsval` `.evval` `.evtable`
  - 設定画面: `.setgroup` `.setting`(`.is-changed`)`.changed-tag` `.pick-chip` `.stepper` `.select` `.range` `.switch` `.btn--reset` `.setsummary`、デザイナーAの S6 モックに合わせた `.checkbox` `.typerow` `.choice` `.radio-row` `.flow` `.toast`
  - 色トークン: `ev-plus-on` `local` `local-weak` `changed` `switch-off` `switch-knob`
- v0.2: デザイナーAの依頼(`design/screens/README.md` 10章)に対応。**既存のクラス名・変数名は変えていない(追加のみ)**。
  - `.rlist` + `.rcard--row`(S1 のリスト内の行。角丸・影・枠なし、区切り線)を追加
  - `.banner--warn`(黄の帯。オフライン表示中に使う)を追加
  - `.best-time`(★+黄地+「最速」)と `.best-value`(太字だけ)を追加。`.best` は互換のため残すが非推奨
  - 推奨買い目用の枠番バッジ 28px を `--badge-bet` と `.boat--bet` として追加(`--badge-md` は24pxのまま)
  - `.result--sm`(一覧の行用、高さ22px)と `.bet__combo`(組合せ+「✓的中」を折り返せるセル)を追加
- v0.1: 初版

## 1. 成果物

| パス | 内容 |
|---|---|
| `tokens.json` | デザイントークンの原本(色はライト/ダーク、文字・余白・角丸・影など) |
| `tokens.css` | `tokens.json` から生成した CSS カスタムプロパティ。**直接編集しない** |
| `components.css` | 部品の見た目の基準(枠番バッジ、確率バー、チップ、的中バッジ、必要オッズ、レースカードなど) |
| `charts.js` | 較正グラフ・収支グラフ・Wilson 区間の描画(依存なし。`window.BRCharts`) |
| `components.html` | 部品カタログ。ブラウザで直接開ける(外部CDNなし)。`?theme=light` / `?theme=dark` で表示モードを固定できる |
| `icons/icon.svg` / `icon-192.png` / `icon-512.png` | アプリアイコン(purpose: any。角丸の正方形、角は透過) |
| `icons/icon-maskable.svg` / `icon-maskable-192.png` / `icon-maskable-512.png` | maskable 用(全面塗り、図案は中心から半径40%以内) |
| `icons/apple-touch-icon-180.png` | iOS のホーム画面用(全面塗り。iOS が自動で角を丸める) |
| `icons/splash.svg` | スプラッシュの見本(参考) |
| `icons/preview.html` | アイコンを円・角丸・しずく型のマスクで切り抜いた見え方の確認ページ |
| `shots/` | 自己確認用のスクリーンショット(`components-{light,dark}-{360,430}.png`、`icons-preview.png`) |
| `tools/build_tokens.py` | `tokens.json` → `tokens.css` の生成(標準ライブラリのみ) |
| `tools/contrast.py` | コントラスト比の検証(標準ライブラリのみ。`--md` で下の表を出力) |

```
python3 design/visual/tools/build_tokens.py   # トークンを変えたら必ず実行
python3 design/visual/tools/contrast.py       # NG が 0 件であること
```

## 2. 使い方(フロント担当向け)

1. `tokens.css`、`components.css`、`icons/*.png`(と `icon.svg`)を `docs/` 側にコピーする(`design/` は直接参照しない。`05_pwa.md` 10章)。
2. `tokens.css` → `components.css` → 画面ごとのCSS の順に読み込む。値は必ず `var(--...)` で使う。
3. 表示モード: 既定は端末設定(`prefers-color-scheme`)に従う。S6 の設定で固定するときは `<html data-theme="light">` / `<html data-theme="dark">` を付ける。属性を外すと「端末に合わせる」に戻る。
4. 数字はすべて `.num`(`font-variant-numeric: tabular-nums`)を付けて桁をそろえる。確率・オッズ・時刻・金額の列は右寄せ。
5. 部品の HTML の書き方は `components.html` のソース(下部の script のテンプレート)を見本にする。

### トークンの命名

| 種類 | CSS変数 | 例 |
|---|---|---|
| 色 | `--c-<名前>` | `--c-surface`、`--c-boat-3-bg`、`--c-hit` |
| 影 | `--shadow-<sm/md/lg>` | ダークでは影の代わりに薄い縁取り |
| 文字サイズ | `--size-<2xs〜3xl>` | 11 / 12 / 13 / 15 / 17 / 20 / 24 / 30px。本文は 15px |
| 太さ | `--weight-<regular/medium/bold/heavy>` | 400 / 500 / 700 / 800 |
| 余白 | `--space-<0〜10>` | 0 / 2 / 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48px |
| 角丸 | `--radius-<xs/sm/md/lg/pill>` | 4 / 6 / 10 / 14 / 999px |
| その他 | `--tap-min`(44px)、`--badge-<sm/md/bet/lg>`(20/24/28/36px)、`--bar-height`(8px) | |

フォントはシステムフォントのみ(`--font-family-base`)。Webフォントは読み込まない(表示速度2秒以内の要件のため)。

## 3. 配色の考え方

- **アクセント(藍 #2B47C2 / ダーク #8EA2FF)**: 主ボタン、選択中のタブ、本命の確率バー、推移グラフの線。枠番の4号艇(青 #1E5AD2)と色相が近いが、枠番は常に数字入りのバッジなので取り違えない。
- **枠番(1白 2黒 3赤 4青 5黄 6緑)**: 公式の慣習どおり。数字と地のコントラストは全艇 AA(4.5:1)以上。
  - 1号艇(白)はライトでは灰の枠線で輪郭を出す。2号艇(黒)はダークでは灰の枠線で輪郭を出す。5号艇(黄)はライトで暗い黄の枠線。
  - 3・6号艇は白文字で AA を満たすため、一般的な赤・緑より少し暗くしている。
- **状態色**(必ず形・文字と併用する。色だけで区別しない):

| 状態 | 色 | 形・文字 |
|---|---|---|
| 的中 | 紅 `--c-hit` の塗り | ✓ アイコン + 「的中」+ 払戻額。買い目の行は左に4pxの帯 + 淡い紅の地 |
| 不的中 | 灰の淡い地 | ✕ アイコン + 「不的中」 |
| 中止 | 灰 | 斜線の地 + ⊘ アイコン + 「中止」 |
| 見送り(推奨0件) | 灰 | **破線**の枠 + 「見送り」 |
| 予想なし | 薄い灰 | 点線の枠 + 「予想なし」 |
| 締切間近 | 橙 `--c-soon` | 点滅する点(動きを減らす設定では止める)+「あと3分」。カードは左に橙の帯 |
| 期待値プラス(Phase 2) | 緑 `--c-ev-plus` | ↗ アイコン + 値 + 「買い」 |
| 期待値ランク A〜D | A・B 緑、C・D 灰 | 文字 A〜D + 形(A 塗り+三角 / B 太枠 / C 淡い地 / D 破線)。なしは点線「–」 |
| ローカル表示 | 紫 `--c-local` | 家のアイコン + 「ローカル表示」。**紫はこの用途専用** |
| 自分の印・個人データ | 青緑 `--c-mine`(ライト #0A6A7A / ダーク #5FCDE0) | 人のアイコン + 「自分の印」など(`.minetag`)、印の入力の選択枠(`.markpick--mine`)。端末の中だけの自分のデータ(印・メモ・購入記録)に使う。紫(ローカル表示)・藍(アクセント)・緑(期待値プラス)と色相を離した |
| 結果(Phase 3a) | 色なし | `.res`: 的中・不的中・返還を同じ枠・同じ大きさで、✓ ✕ ↩ と文字だけで区別(08 4章-5)。v0.1 の塗りの `.result--hit` は Phase 3a で `.res` に置き換える |
| 既定から変更(設定) | 藍 `--c-changed` | 左の帯 + 「● 既定から変更」 |
| F持ち・エラー | 赤 `--c-danger` | 「F1」の塗りバッジ / 警告アイコン |
| 展示タイム最速 | 黄地 `--c-warn-weak` | 「★ 6.70 最速」(`.best-time`)。勝率などの最高値は太字だけ(`.best-value`) |
| オフライン表示中 | 黄地 `--c-warn-weak` | 雲に斜線のアイコン + 時点の時刻 + 左に黄土の帯(`.banner--warn`) |
| 注意(進入が枠番と違う、参考値) | 黄土 `--c-warn` | 淡い黄の地に文字 |

- 「的中」を紅にしたのは、新聞の赤印(◎)と結びつけるため。◎ も同じ紅(`--c-hit-text`)。締切間近の橙とは明度・色相とも離している。
- ダークは反転ではなく、別に選んだ値(彩度を落とし明度を上げた色)。背景は真っ黒にせず #0D1015。

## 4. 部品の意図

| 部品 | クラス | 要点 |
|---|---|---|
| 枠番バッジ | `.boat .boat-<1〜6>`(`.boat--sm/bet/lg`) | sm 20 / md 24(既定)/ bet 28(推奨買い目・確率上位の組合せ)/ lg 36px(各艇の行)。欠場は `.is-absent`(薄く + 斜線)。28px は md を変えずに別サイズとした(md 24 は S1 の本命表示などで使用中のため) |
| 組合せ | `.combo` + `.combo__sep` | 小さい枠番バッジを `-`(順序あり)/ `=`(順不同)でつなぐ。`aria-label="2-1-3"` を付ける |
| 印 | `.mark`(`.mark--1`) | p_rank 1〜4 に ◎○▲△、5位以下は無印 |
| 確率バー | `.pbar`(`--p`、任意で `--p2` `--p3`) | 1着確率を濃い色、2着内・3着内を同じ色の薄い2層で重ねる。本命は `.is-top`。数値は必ず右に出す |
| 6艇分布バー | `.dist` | S1 のカードで「どれだけ1艇に集中しているか」を見せる案。幅は p_win。必須ではない |
| 堅さ | `.conf--1/2/3` | 3段の棒 + 「荒/中/堅」(デザイナーAのモックに合わせた)。しきい値の案: 堅 ≥ 0.50、中 0.35〜0.50、荒 < 0.35 |
| ステータスチップ | `.chip--open/soon/closed/canceled/skip/nopred/bets/pre/preview/warn` | 高さ24px。チップ自体はタップ対象にしない(タップはカード全体) |
| 的中バッジ | `.result--hit/miss/refund`(`.result--sm/lg`) | 結果ブロックでは lg、S1 のリスト行では sm(22px) |
| 必要オッズ | `.req` | 下記 |
| 推奨買い目の行 | `.bet`(`.is-hit`)、`.bet__combo` | 組合せ(`.boat--bet` 28px)/ 確率 / 必要オッズ の3列。行の高さ52px。「✓的中」は幅が足りなければ組合せの下に折り返す |
| 見送り | `.skipbox` | 推奨0件のときにブロックごと置き換える |
| オッズ入力→期待値(Phase 2) | `.oddsin` + `.evtag--plus/minus/empty` | `inputmode="decimal"`。入力欄の高さ44px |
| レースカード | `.rcard`(`.is-soon/.is-closed/.is-hit`) | 独立したカード。状態ごとの色・帯の当て方の基準。レイアウトはデザイナーAが正 |
| レースカード(リスト行) | `.rlist` > `.rcard.rcard--row` | **S1 の既定**。1枚のリストに区切り線で並べる(角丸・影・枠なし)。状態クラスは `.rcard` と同じ。左の4pxの帯(締切間近=橙、的中=紅)は残す |
| 帯 | `.banner--warn/info/error` | warn(黄・左に帯)= オフライン表示中など見落としてほしくない注意 / info(青)= 一般のお知らせ / error = 読み込み失敗(再読み込みボタン付き) |
| 操作 | `.btn`(`--primary`)、`.seg` | 最小 44×44px。フォーカスは3pxのアクセント色の輪郭 |
| 推移グラフ | `svg.chart` | 下記 |

### v0.3 の部品(DB-3: 締切前オッズ・期待値・設定)

| 部品 | クラス | 要点 |
|---|---|---|
| 期待値ランク | `.evrank .evrank--a/b/c/d/none`(`.evrank--sm` 20px) | 文字 A〜D と形の両方で区別: **A = 緑の塗り + 右上の小さな三角 / B = 太い緑の枠 / C = 淡い灰の地(枠なし) / D = 破線の枠・薄い文字 / なし = 点線の枠と「–」**。`aria-label="期待値ランクA"` を付ける。境界(1.20 / 1.00 / 0.80)は凡例 `.evlegend` で示す |
| ランクの置き場所 | `.bet` の3列目に `.evrank` + `.req` を並べる | 必要オッズの**左**。必要オッズ(1/prob)はオッズが無くても使えるので残す(07 3.4) |
| オッズの時点 | `.oddstime--ok / partial / late / stopped / not-targeted / none` | ok = 時計 +「10:40時点(締切5分前)」、partial = 黄地 + 警告、late = 黄土の破線、stopped = 赤地 + ⊘、not_targeted と未取得(null)= 点線。期待値を出すブロックの見出しに必ず1つ置く |
| 注記 | `.oddsnote` | 「締切前のオッズで計算。締切まで変わり、確定オッズとは違う。投票は投票アプリの最新のオッズで」(07 3.4)。期待値の直下に11px |
| ローカル表示の目印 | `.localmark` | 家のアイコン + 「ローカル表示」(狭いときは「ローカル」)。上部バーに常に出す。他の状態色と重ならない**紫**にした |
| ローカル版の数値 | `.oddsval`(「24.5倍」)、`.evval`(「↗1.27」+ ランク)、`.evtable` | 期待値1.00以上は緑の文字 + ↗。オッズが無い組合せは「–」とランク「–」。S7 の手入力値の行は `tr.is-manual` で「手入力」と付く |
| 設定のまとまり | `.setgroup` > `.setting`(`.setting--inline`) | 見出し `.setting__label`、説明 `.setting__desc`(既定値を書く) |
| 「既定と違う」 | `.setting.is-changed` + `.changed-tag`(右寄せは `--end`) | 左に3pxの帯 + 「● 既定から変更」の文字。リセットは `.btn--reset`(変更が無いときは disabled) |
| 複数選択 | `.pick-chip[aria-pressed]` または `.checkbox[aria-checked]` + `.typerow` | 券種。✓ の箱で選択を示す。`.typerow` は「券種 + 点数のステッパー」を1行にまとめる形(デザイナーAの S6 の形)。オフの行は `.is-off` |
| ステッパー | `.stepper`(`.is-off`) | 44×44px のボタン。上限・下限で disabled |
| 段階の選択 | 5つ以下の短い選択肢 = `.seg`(`role=radio` + `aria-checked`)、文言が長い = `.choice`(折り返す。選ぶと ✓)、説明つき = `.radio-row`、多い = `.select`、連続値 = `.range` | 最低確率は `.seg`、堅さの見送りは `.choice` を推奨 |
| トグル | `.switch[role=switch][aria-checked]` | つまみの位置 + 「オン/オフ」の文字 |
| 適用の順序 | `.flow`(`li.is-off`) | 07 4.2 の順序を番号つきで示し、右に今の値 |
| 今の設定の要約 | `.setsummary` | S2・S3 の上に「今の設定: …」と「既定から変更」を出すとき |
| 知らせ | `.toast` | 「保存しました」。`role="status"`、2秒で消す |

### v0.4 の部品(DB-4: 中継 Worker でのオッズ取得)

| 部品 | クラス | 要点 |
|---|---|---|
| 取得ボタン | `.fetchbtn`(`data-state`)、全幅は `--block` | **idle** = 塗り「オッズを取る / 3券種・約30秒」、**loading** = 淡い地 + 回る輪「取得中… / 12秒経過(目安30秒)」、**cooldown** = 枠だけ「取り直しは あと42秒 / 同じレースは60秒に1回まで」、**disabled** = 破線 + 理由(「締切後のため取得できません」「設定で合言葉を入れてください」)。idle 以外は `disabled` 属性も付ける |
| 取得中 | `.fetchprog`(`style="--t:0.4"`)、`role="status"` | スマホには途中経過が届かないので、**経過時間で進める「目安」のバー**(`--t = min(経過秒 ÷ 30, 0.9)`、応答が来たら1)。経過「12秒 / 約30秒」と「取り消す」(`AbortController`)。45秒で打ち切り |
| 取得後 | `.fetchstamp` | 大きく「10:40:12」+「時点のオッズ」(`fetched_at`)。下の段に公式の表記(`odds_time_label`)と券種ごとの ✓/✕(`.pagestat`)。右に `.refreshbtn`(60秒たつまで `disabled` で残り「0:42」、その後「更新」) |
| キャッシュ | `.cachetag` | `cached: true` のとき「25秒前に取得したもの」(`cache_age_sec`)。破線の枠 |
| 状態の帯 | `.banner--stack` + `--info/warn/error/muted` | 見出し + 説明 + 操作ボタン。文言案は下の表 |
| 出どころ | `.oddssrc--manual`(鉛筆・手入力、淡い藍 + 藍の枠)/ `--worker`(↓・取得、藍の塗り)/ `--cache`(取得・◯秒前、藍の破線)/ `--rank`(一覧・ランクのみ、灰の破線)/ `--none`(オッズなし、点線) | 優先順は 手入力 → 取得 → ランク → なし(07 9.6)。全部が同じ出どころならブロックの見出しに1つ、混在するときだけ行ごとに付ける |
| URL 入力 | `.field` > `.input.input--mono` | `type="url" inputmode="url" autocapitalize="off" spellcheck="false"`。文字は16px(iOS の拡大を防ぐ)。誤りは `.field.is-invalid`(赤い2pxの枠 + ⚠ + 文)と `aria-invalid` |
| 合言葉 | `.secret` > `input[type=password]` + `.secret__toggle[aria-pressed]` | 「👁 表示」⇔「隠す」で `type` を切り替える。**保存後は値を画面に戻さず `.secret-saved`(●●●●●● 保存済み [変更])にする**(07 9.7) |
| 自動取得 | `.switch` | 「レース詳細を開いたら自動で取る」既定オフ |
| コンパクトな箱 | `.fetchbox` > `__icon` `__text`(`__title`)+ `.fetchbtn.fetchbtn--compact` | S2 の見出し下に置く1行の箱(デザイナーAの形)。今の出どころと時刻を左に、ボタンを右に。状態の帯を出すほどでない一部失敗は `--warn` |

### v0.5 の部品(DB-5: 較正・区間・印・根拠・買い目合計・警告・説明・収支)

| 部品 | クラス・関数 | 要点 |
|---|---|---|
| 較正グラフ | `BRCharts.calibration(bins, {minN})` → `svg.chart.calib` | 横 = 予測の1着確率、縦 = 実際の1着率。**両軸 0〜100%・20%刻みで固定**し、点は (予測の平均, k/n) をそのまま置く(目盛りと値が一致)。対角の破線 = 予測どおり。縦線 = Wilson 95%区間。件数 < minN(既定30)の帯は**白抜きの点 + 点線の区間**。各点に `<title>`(予測・実際・区間・件数)。グラフの下に同じ値の表を必ず置く(色と位置だけに頼らない) |
| 区間つきの数値 | `.ci-num`(`--lo --hi --v --ref --max`)、表では `.ci-inline` | 大きい値 + 区間の小さなバー(点 = 推定、帯 = 区間、縦線 = 100%)+「95%区間 81〜105%」+ 件数。区間が100%をまたぐときは `.is-straddle` と「プラスともマイナスとも言えない」(`__verdict`) |
| 件数が少ない | `.is-lown` + `.lown-tag`(「参考」) | **透明度ではなく文字色を text-3 に下げる**(4.5:1 を保つ)+ 破線のタグ。集計は200R未満、較正の帯は30件未満(08 4章-1) |
| 印の入力 | `.markpick[role=radiogroup] > button[role=radio]` | ◎○▲△ の4つ(34×40px)。選んだものは白地 + 藍の枠、◎は紅の文字。比較は `.markvs`「自 ◎ ＝ モ ◎」(ずれは ≠ が黄土) |
| 根拠 | `.reasons > li.reason.reason--up/down[data-strength=1-3]` | 「▲ 上げる / ▼ 下げる」+ 項目名 + 値 + 3本の棒(強さ)。パーセントは出さない(08 5.2) |
| 買い目合計 | `.betsum` | 上に 点数 / 総額 / 合成オッズ(オッズなしは「オッズなし」)。下にトリガミ回避の配分表(`tr.is-short` = 総額に届かない行に ✕)。フッターに判定 `.trigami--ok/ng` と「買い目をコピー」 |
| 予算・連敗 | `.banner--warn`(80%)/ `.banner--error`(100%以上)+ `.budgetbar`(`--p`、80%に目印、超過は `.is-over`) | 超えても帯を消さない(08 4章-6)。連敗は `.banner--warn` |
| 利用時間・深夜 | `.banner--quiet` + `.banner__close` | 枠だけの小さな帯。閉じられる。操作を妨げない(08 4章-7) |
| 「?」の説明 | `button.help[aria-expanded][aria-controls]` + `.helppop[role=dialog]` | 見た目20pxの丸・タップ領域44px。開くと藍に反転。吹き出しの三角の位置は `--arrow-x`。Esc と外側タップで閉じる(フロント) |
| 収支の推移 | `BRCharts.pnl(days)` → `{svg, total, maxDrawdown}` | 累積収支の線、それまでの最高(細い点線)、最高からの下落を帯で塗る。最大ドローダウンに縦線と「最大 −3,400」。0円の破線 |

### v0.7 の部品(DB-6: 停止の帯・展開予想・選手カード・モデルの履歴)

| 部品 | クラス | 要点 |
|---|---|---|
| 更新停止の帯 | `.stale`(画面上部に固定は `--sticky`)、時刻は `.stale__time` | 最終成功から90分超・開催時間中だけ。黄の地 + 下に黄土の線 + ⚠。「データの更新が止まっています / 最終更新 14:05(2時間前)…」。閉じられる(`.stale__close`) |
| 展開予想 | `.tenkai` > `__hero`(枠番バッジ lg + 「1コース(1号艇)が逃げる確率」+ 大きな %) | 主役は逃げ。版のチップ(`.chip--pre/--preview`)と、別モデルである注記(`.oddsnote`)を添える |
| 決まり手の帯 | `.tkbar > i.tk-nige/sashi/makuri/makurizashi/other`(`--p`)+ `.tkbar-labels` | **逃げ = 塗り(アクセント)、差し・まくり・まくり差し = 向きの違う斜線(45°・−45°・縦)、その他 = 破線**。2px の隙間。帯の下には15%以上の区間だけ名前と%(細い区間は重なるため) |
| 決まり手の行 | `.tk-rows > .tk-row`(逃げは `.is-main` = ★ + 太字 + 濃いバー) | 帯と同じ値を全部書く(色や模様を見なくても読める) |
| シート | `.scrim` + `.sheet[role=dialog][aria-modal]` > `__handle` `__head` `__body` `__sec` `__note` | 下から出る。最大高さ88vh、中身だけスクロール。閉じるは × と下スワイプと scrim のタップ(フロント) |
| 数値の並び | `dl.statgrid`(`div.is-lown` で薄く) | 3列の格子。件数は `dd small` で添える |
| コース別の表 | `table.cstable` | 1〜6コースの行。**今回の予定コース = `tr.is-plan`**(淡い藍の地 + 左の帯 + 丸の塗り + 「今回」)。件数10未満 = `tr.is-lown`(薄い文字 + 「参考」)。件数の列 `.n` |
| 着順の並び | `ol.fins > li.fin.fin--N` | **1着 = 濃い塗り、2・3着 = 太い枠、4〜6着 = 細い枠・薄い文字、F・L・欠 = 赤の破線 + 文字**。数字が情報なので色だけに頼らない。今節は `.fins--detail` で日付・Rを小さく添える |
| モデルの履歴 | `ol.mhists > li.mhist.mhist--adopted/kept` | 日付 / タグ(「⇄ 切り替えた」= 藍の太枠、「＝ 切り替えなし」= 灰の枠。大きさは同じ)/ モデルID(等幅)/ 理由と logloss の比較 |

#### 状態ごとの文言案(`components.html` 15節に同じものがある)

| 場面(API) | 帯 | 見出し | 説明 | ボタン |
|---|---|---|---|---|
| 合言葉・URL 未設定 | info | オッズ取得の設定がまだです | 設定で Worker の URL と合言葉を入れると、このレースのオッズを取れます。 | 設定へ |
| 401 `unauthorized` | error | 合言葉が違います | 設定の合言葉を確かめてください。 | 設定へ |
| 409 `closed` | muted | 締切後のため取得しません | 確定オッズは翌日に反映されます。 | — |
| 429 `daily_limit` | warn | 今日の取得上限に達しました | 公式サイトへの負担を抑えるため、1日の取得数に上限があります。明日(0時)に戻ります。それまでは必要オッズで判断してください。 | — |
| 502 `upstream_error` | error | 公式サイトから取得できませんでした | 公式サイトが混み合っているか、一時的に応答していません。少し待ってから再試行してください。 | 再試行 |
| 503 `stopped` | error | 今日はオッズの取得を停止しています | 公式サイトから取得を断られたため、今日は自動で止めています。明日(0時)に再開します。 | — |
| 通信失敗・45秒で打ち切り | warn | 通信できませんでした | 電波の良い場所で再試行してください。 | 再試行 |
| オフライン(`navigator.onLine` が false) | muted | オフラインのため取得できません | つながったら「オッズを取る」を押してください。 | — |
| 200 で一部の `pages` が `error` / `parse_error` | warn | 一部の券種だけ取得しました | (例)単勝・複勝は取れませんでした。取れた券種だけで期待値を計算しています。 | 再試行 |
| 400 `bad_request` | error | 取得できません(要求の誤り) | アプリを再読み込みしてください。直らないときは日付が今日かどうかを確かめてください。 | 再読み込み |

- 403 `forbidden_origin` は開発時だけの想定なので、400 と同じ帯で「この URL からは使えない設定です(許可するオリジンを確かめてください)」とする案。
- 帯は取得ボタンのすぐ下に1つだけ出す(重ねない)。再試行のボタンは 502・通信失敗・一部失敗のときだけ。上限・停止の日に再試行を出さないのは、余計な要求を Worker に送らないため(07 9.3)。
- 帯の上の見出しは「何が起きたか」、説明は「どうすればよいか」。エラーコードや英語は画面に出さない。

### 必要オッズの見せ方

- 表記は **「≧ 19.3倍」**(不等号 + 数字を大きく + 「倍」を小さく)。枠で囲み、「この値と見比べる」ものだとわかる形にする。
- 「オッズがこれ以上なら買い」という意味は、ブロックの見出しの右に**1回だけ**書く(各行に書くと長くなるため)。初めて見たときのために、S5 にも説明を書くとよい。
- 行の中の並びは「組合せ → 確率(小さく灰色) → 必要オッズ(太く)」。締切直前に見るのは必要オッズなので、右端に一番強く置く。
- Phase 2 でオッズを入力したら、同じ行に「期待値 1.27 買い」(緑・↗)/「0.74 見送り」(灰・✕)を出す。1.0 ちょうどは「買い」側。

### 累積回収率の推移(S3)

- 線1本(2px、アクセント色)+ 100% の破線の基準線。系列が1つなので凡例は出さず、見出しで名前を示す。
- 最新値だけを線の右端に直接ラベルとして書く(すべての点には書かない)。縦軸は 0% から、最大値を 50% 刻みで切り上げた値まで。横軸のラベルは最初と最後の日付だけ。
- 実装時は、タップした位置の日付・その日の回収率・累積回収率をツールチップで出すことを推奨(当たり判定は線より太く取る)。値の確認用に月別の表(`by_month`)を併置するので、グラフだけに情報を頼らない。

## 5. アイコン・スプラッシュ

- 図案: 水面の波の上に、右肩上がりの3本の確率バー(一番高いバーは琥珀色)。特定のブランドやロゴを模したものではない独自の図案。背景は藍のグラデーション(#2B47C2 → #172A78)。
- maskable は全面塗りで、図案は中心から最大 183px(512px 基準。安全領域は 205px = 40%)に収めている。`icons/preview.html` で円・角丸・しずく型のマスクを確認済み。
- PNG は SVG 原本をブラウザでレンダリングして作成(下記7章)。SVG を直したら PNG も作り直す。

manifest の記述案(フロント担当が `docs/manifest.webmanifest` に書く):

```json
{
  "name": "ボートレース予想",
  "short_name": "BR予想",
  "display": "standalone",
  "background_color": "#172A78",
  "theme_color": "#2B47C2",
  "icons": [
    {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    {"src": "icons/icon-maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
    {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
  ]
}
```

- `name` / `short_name` は仮。決めるのは PM。
- スプラッシュ: Android は `background_color` + アイコン + `name` で自動生成されるので、画像は不要。`background_color` をアイコンの背景色と同じ藍にして、起動時に色が途切れないようにした。
- HTML の `<head>` には次を推奨(状態バーの色を表示モードに合わせる):

```html
<link rel="icon" href="icons/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icons/apple-touch-icon-180.png">
<meta name="theme-color" content="#F3F4F7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0D1015" media="(prefers-color-scheme: dark)">
```

## 6. コントラスト比(`tools/contrast.py --md` の出力)

基準: 文字は WCAG 2.x AA の 4.5:1(小さい文字を含むため、大きい文字の 3:1 は使わない)。枠線・バー・アイコンなど文字でないものは WCAG 2.1 の 1.4.11 に従い 3:1。**全86組、ライト・ダークとも基準を満たす(NG 0件)。**

| 組み合わせ | 基準 | ライト | ダーク |
|---|---|---|---|
| 本文 / 背景 (`text` / `bg`) | 4.5:1 | 16.16 | 16.09 |
| 本文 / カード (`text` / `surface`) | 4.5:1 | 17.78 | 14.58 |
| 補助文字 / カード (`text-2` / `surface`) | 4.5:1 | 8.63 | 9.33 |
| 薄い文字 / カード (`text-3` / `surface`) | 4.5:1 | 5.71 | 6.39 |
| 薄い文字 / 背景 (`text-3` / `bg`) | 4.5:1 | 5.19 | 7.05 |
| 薄い文字 / 面2 (`text-3` / `surface-2`) | 4.5:1 | 5.00 | 5.69 |
| リンク / カード (`accent-text` / `surface`) | 4.5:1 | 8.48 | 8.76 |
| 主ボタン文字 / 主ボタン (`text-on-accent` / `accent`) | 4.5:1 | 7.55 | 7.95 |
| 選択中タブ文字 / 選択中背景 (`accent-text` / `accent-weak`) | 4.5:1 | 7.14 | 6.92 |
| 入力枠など(非テキスト) / カード (`border-strong` / `surface`) | 3.0:1 | 3.71 | 4.52 |
| 確率バー(本命) / トラック (`bar-fill-top` / `bar-track`) | 3.0:1 | 6.09 | 5.53 |
| 確率バー(その他) / トラック (`bar-fill` / `bar-track`) | 3.0:1 | 3.11 | 3.24 |
| 的中バッジ文字 / 的中バッジ (`hit-on` / `hit`) | 4.5:1 | 6.02 | 7.24 |
| 的中行の文字 / 的中行の背景 (`hit-text` / `hit-weak`) | 4.5:1 | 6.72 | 7.54 |
| 的中の枠・アイコン(非テキスト) / カード (`hit` / `surface`) | 3.0:1 | 6.02 | 6.32 |
| ◎印・的中の文字 / カード (`hit-text` / `surface`) | 4.5:1 | 7.82 | 8.25 |
| 締切間近の残り時間 / カード (`soon-text` / `surface`) | 4.5:1 | 7.16 | 11.07 |
| 不的中チップ文字 / 背景 (`miss` / `miss-weak`) | 4.5:1 | 4.91 | 5.47 |
| 中止チップ文字 / 背景 (`canceled` / `canceled-weak`) | 4.5:1 | 4.78 | 5.54 |
| 見送りチップ文字 / カード (`skip` / `surface`) | 4.5:1 | 8.63 | 9.33 |
| 見送りチップ破線(非テキスト) / カード (`skip-border` / `surface`) | 3.0:1 | 3.71 | 4.52 |
| 締切間近チップ文字 / 背景 (`soon-text` / `soon-weak`) | 4.5:1 | 6.43 | 8.87 |
| 締切間近の点・枠(非テキスト) / カード (`soon` / `surface`) | 3.0:1 | 3.35 | 8.90 |
| 期待値プラス文字 / 背景 (`ev-plus-text` / `ev-plus-weak`) | 4.5:1 | 6.26 | 8.88 |
| 期待値プラス文字 / カード (`ev-plus-text` / `surface`) | 4.5:1 | 7.18 | 10.35 |
| 期待値ランクA 文字 / ランクA 地 (`ev-plus-on` / `ev-plus`) | 4.5:1 | 5.34 | 8.16 |
| 期待値ランクB 文字 / ランクB 地 (`ev-plus-text` / `ev-plus-weak`) | 4.5:1 | 6.26 | 8.88 |
| 期待値ランクB 枠(非テキスト) / カード (`ev-plus` / `surface`) | 3.0:1 | 5.34 | 8.47 |
| 期待値ランクC 文字 / ランクC 地 (`text-2` / `miss-weak`) | 4.5:1 | 7.43 | 7.99 |
| 期待値ランクD・オッズなし 文字 / カード (`text-3` / `surface`) | 4.5:1 | 5.71 | 6.39 |
| ローカル表示 文字 / 地 (`local` / `local-weak`) | 4.5:1 | 6.23 | 7.80 |
| ローカル表示 枠(非テキスト) / カード (`local` / `surface`) | 3.0:1 | 7.31 | 8.95 |
| 「既定から変更」文字 / カード (`changed` / `surface`) | 4.5:1 | 7.55 | 8.76 |
| 「既定から変更」文字 / 設定の要約の地 (`changed` / `accent-weak`) | 4.5:1 | 6.35 | 6.92 |
| 取得停止チップ文字 / 地 (`danger` / `danger-weak`) | 4.5:1 | 5.75 | 6.29 |
| オッズ時点(ok)文字 / 地 (`text` / `surface-2`) | 4.5:1 | 15.58 | 12.99 |
| トグル(オフ)の地(非テキスト) / カード (`switch-off` / `surface`) | 3.0:1 | 3.17 | 3.62 |
| トグルのつまみ(オフ) / トグルの地(非テキスト) (`switch-knob` / `switch-off`) | 3.0:1 | 3.17 | 4.77 |
| トグルのつまみ(オン) / トグルの地(非テキスト) (`text-on-accent` / `accent`) | 3.0:1 | 7.55 | 7.95 |
| トグル(オン)・スライダー(非テキスト) / カード (`accent` / `surface`) | 3.0:1 | 7.55 | 7.20 |
| F持ち・エラー文字 / カード (`danger` / `surface`) | 4.5:1 | 6.57 | 6.79 |
| F持ちバッジ文字 / F持ちバッジ (`surface` / `danger`) | 4.5:1 | 6.57 | 6.79 |
| エラー帯の文字 / エラー帯 (`text` / `danger-weak`) | 4.5:1 | 15.54 | 13.51 |
| 注意文字(F持ち等) / 注意背景 (`warn` / `warn-weak`) | 4.5:1 | 5.78 | 8.72 |
| 注意文字 / カード (`warn` / `surface`) | 4.5:1 | 6.33 | 10.29 |
| 薄い文字 / 締切済み行 (`text-3` / `row-muted`) | 4.5:1 | 5.04 | 6.11 |
| 本文 / 情報帯(オフライン表示等) (`text` / `info-weak`) | 4.5:1 | 15.28 | 13.33 |
| 本文 / 黄の帯(オフライン表示) (`text` / `warn-weak`) | 4.5:1 | 16.22 | 12.36 |
| 推移グラフ線 / カード (`chart-line` / `surface`) | 3.0:1 | 7.55 | 7.20 |
| 100%基準線 / カード (`chart-baseline` / `surface`) | 3.0:1 | 3.71 | 4.52 |
| 1号艇 数字 / 地 (`boat-1-fg` / `boat-1-bg`) | 4.5:1 | 17.78 | 16.30 |
| 2号艇 数字 / 地 (`boat-2-fg` / `boat-2-bg`) | 4.5:1 | 16.66 | 21.00 |
| 3号艇 数字 / 地 (`boat-3-fg` / `boat-3-bg`) | 4.5:1 | 5.28 | 4.84 |
| 4号艇 数字 / 地 (`boat-4-fg` / `boat-4-bg`) | 4.5:1 | 6.09 | 5.37 |
| 5号艇 数字 / 地 (`boat-5-fg` / `boat-5-bg`) | 4.5:1 | 11.80 | 11.80 |
| 6号艇 数字 / 地 (`boat-6-fg` / `boat-6-bg`) | 4.5:1 | 5.03 | 4.71 |
| 1号艇 外形 / カード(非テキスト) (`boat-1-border` / `surface`) | 3.0:1 | 3.71 | 15.83 |
| 2号艇 外形 / カード(非テキスト) (`boat-2-border` / `surface`) | 3.0:1 | 16.66 | 5.58 |
| 3号艇 外形 / カード(非テキスト) (`boat-3-border` / `surface`) | 3.0:1 | 5.28 | 3.57 |
| 4号艇 外形 / カード(非テキスト) (`boat-4-border` / `surface`) | 3.0:1 | 6.09 | 4.35 |
| 5号艇 外形 / カード(非テキスト) (`boat-5-border` / `surface`) | 3.0:1 | 3.10 | 12.23 |
| 6号艇 外形 / カード(非テキスト) (`boat-6-border` / `surface`) | 3.0:1 | 5.03 | 4.86 |
| 取得中の経過時間・補足 / 取得中の地 (`text-2` / `accent-weak`) | 4.5:1 | 7.26 | 7.38 |
| 取得中の進みバー / バーの地(非テキスト) (`accent` / `surface`) | 3.0:1 | 7.55 | 7.20 |
| 帯(灰)の補足文字・表示切替 / 面2 (`text-2` / `surface-2`) | 4.5:1 | 7.56 | 8.31 |
| 帯(黄)の補足文字 / 黄の地 (`text-2` / `warn-weak`) | 4.5:1 | 7.87 | 7.91 |
| 帯(赤)の補足文字 / 赤の地 (`text-2` / `danger-weak`) | 4.5:1 | 7.54 | 8.65 |
| 帯(青)の補足文字 / 青の地 (`text-2` / `info-weak`) | 4.5:1 | 7.42 | 8.54 |
| 出どころ「手入力」 / 地 (`accent-text` / `accent-weak`) | 4.5:1 | 7.14 | 6.92 |
| 出どころ「取得」 / 地 (`ev-plus-text` / `ev-plus-weak`) | 4.5:1 | 6.26 | 8.88 |
| 入力欄の誤り枠(非テキスト) / カード (`danger` / `surface`) | 3.0:1 | 6.57 | 6.79 |
| 較正の区間の線 / カード(非テキスト) (`chart-ci` / `surface`) | 3.0:1 | 4.96 | 5.87 |
| 較正の理想線(対角) / カード(非テキスト) (`chart-ideal` / `surface`) | 3.0:1 | 3.71 | 4.52 |
| 累積収支の線 / ドローダウンの帯(非テキスト) (`chart-line` / `chart-dd`) | 3.0:1 | 5.59 | 5.82 |
| 根拠「上げる」文字 / カード (`reason-up` / `surface`) | 4.5:1 | 7.18 | 10.35 |
| 根拠「下げる」文字 / カード (`reason-down` / `surface`) | 4.5:1 | 6.57 | 6.79 |
| 件数が少ない数字(薄い表示) / カード (`text-3` / `surface`) | 4.5:1 | 5.71 | 6.39 |
| 自分の印・個人データ 文字 / 地 (`mine` / `mine-weak`) | 4.5:1 | 5.41 | 7.62 |
| 自分の印 文字・枠 / カード (`mine` / `surface`) | 4.5:1 | 6.26 | 9.29 |
| 結果チップ(的中・不的中 同じ重さ) / カード (`text` / `surface`) | 4.5:1 | 17.78 | 14.58 |
| 個人データ帯の補足 / 地 (`text-2` / `mine-weak`) | 4.5:1 | 7.46 | 7.66 |
| コース別の表(今回の行)文字 / 地 (`text` / `accent-weak`) | 4.5:1 | 14.96 | 11.52 |
| コース別の表(今回の行)件数 / 地 (`text-3` / `accent-weak`) | 4.5:1 | 4.80 | 5.05 |
| 展開予想の帯・斜線(非テキスト) / カード (`bar-fill` / `surface`) | 3.0:1 | 3.86 | 4.22 |
| 着順チップ(1着)文字 / 塗り (`surface` / `text`) | 4.5:1 | 17.78 | 14.58 |
| 更新停止の帯 文字 / 地 (`text` / `warn-weak`) | 4.5:1 | 16.22 | 12.36 |

補足:
- 確率バーの薄い層(2着内・3着内)は `color-mix()` で作る補助の表現で、数値を必ず併記するため、コントラスト基準の対象外とした。
- `text-3`(薄い文字)も 4.5:1 を満たすようにしてある。これより薄い文字色は作らない。

## 7. 確認の方法

- Playwright はこの環境(WSL の node v16 / .venv)に入っていなかったため、**Windows 側の Microsoft Edge のヘッドレスモード**(`msedge.exe --headless=new --screenshot`)で `components.html` とアイコンをレンダリングした。ライブラリは追加していない。
- ヘッドレスの Edge は窓の幅が約500px未満にならないため、360px / 430px の確認は iframe に入れて撮った(`shots/*-360.png` の右側の灰色は余白)。360px と 430px の両方で横のはみ出しが無いことを確認済み。

## 8. デザイナーA(design/screens/)との対応

デザイナーAの `mock.css` の仮トークンは、次のとおり置き換えられる(大半は同じ名前)。

| mock.css | tokens.css |
|---|---|
| `.banner--offline` | `.banner--warn` |
| `.race-row`(`.list` の中) | `.rcard.rcard--row`(`.rlist` の中)。`.is-urgent` → `.is-soon`、`.is-muted` → `.is-closed` |
| 無印の `.waku`(28px) | `.boat.boat--bet` |
| 「★最速」 | `.best-time`(勝率の最高値などは `.best-value`) |
| `.evr--A/B/C/D/none`(`--s`) | `.evrank--a/b/c/d/none`(`--sm`)。**C と D の見た目が違う**: B案は C = 淡い灰の地、D = 破線の枠(D を「無効」に見せ、C と D の差を形でも出すため)。モックを B に合わせてほしい |
| `.check`(`.is-on`) | `.checkbox[aria-checked="true"]`(✓ は CSS で出る) |
| `.bt-row`(`.nm`、`.is-off`) | `.typerow`(`.typerow__name`、`.is-off`) |
| `.choice` `.choice--fill` `.radio-row` `.radio` `.flow` `.toast` `.stepper.is-off` | 同名 |
| `.set-label .changed`(「変更」) | `.changed-tag.changed-tag--end`(「● 既定から変更」) |
| `.set-summary` | `.setsummary` |
| **DA-6(17章)** | |
| `.stalebar`(赤系の地 + 左帯) | `.stale`(または同名の `.stalebar`)。**差: 色は黄(`--c-warn-weak` の地 + 黄土の線 + ⚠)**。統括判断: 停止は故障ではなく注意なので黄、赤はエラー専用 |
| アプリバーの「更新 08:10」を赤字 | `.updated-at.is-stale`。**差: 赤ではなく黄土の文字 + ⚠**(同じ理由) |
| `.tk-hero`(`.v` `.lbl`) | `.tenkai__hero`(`__hero-val` `__hero-label`)。数字は A 36px / B 30px(`--size-3xl`)。枠番バッジを左に添える |
| `.tk-row`(`.p`、`.is-main`) | `.tk-rows > .tk-row`(`__name` `__pct` `.pbar`、`.is-main` = ★ + 太字)。B は上に模様つきの帯 `.tkbar` も置く(任意) |
| `.cardsheet` | `.sheet`(`__head` `__body` `__sec` `__note`)+ `.scrim` |
| `.mini`(`.p1`) | `.fins > .fin--1〜6/f/l/x`。同名の `.mini` も用意(`.p1` 塗り、`.p2/.p3` 太枠、`.pf` 赤の破線) |
| `tr.is-plan`(`.tbl`) | `.cstable tr.is-plan`(淡い藍 + 左の帯 + 「今回」)。`.tbl tr.is-plan` も同じ見た目で用意 |
| `.few`(行・値) | 既存の `.few` / `tr.is-lown` / `.statgrid .is-lown`(文字色を下げる方式) |
| `.mh`(`.t` `.k`)+ `.res` のタグ | `.mhists > .mhist--adopted/kept`。タグは `.mhist__tag`(⇄ / ＝、同じ大きさ)。A の `.res` のままでもよい(どちらも同じ重さ) |
| 選手名の点線の下線 | `.racer-link` |
| `card.unavailable` / `card.load_failed` | `.emptybox` |
| **DA-5(16章)** | |
| 較正グラフ(`.chart` の拡張) | `BRCharts.calibration()` → `svg.chart.calib`。**差**: A は「点の大きさ = 件数」、B は点の大きさを一定にし、件数30未満を白抜き + 点線の区間にした(大きさの差は小さい画面で読み取りにくいため。件数は下の表と title で出す) |
| `.ci` | `.ci-num`(大きい値 + 区間バー)/ 表では `.ci-inline` |
| `.few` `.fewnote` | 同名で対応(`.is-lown` `.lown-tag` と同じ扱い)。**差**: A は `opacity: .55`、B は文字色を `--c-text-3` に下げる(4.5:1 を保つため。透明度だとダークで3:1を割る) |
| `.markpick`(紫の選択) | `.markpick.markpick--mine`。**差: 選択の色は紫ではなく青緑 `--c-mine`**(統括判断。紫はローカル表示専用) |
| `.why`(`.up` `.dn` `.lv`) | `.reasons > .reason--up/down[data-strength]` |
| `.sum` | `.betsum` |
| `.warnbar` `.warnbar--over` | 同名で対応(`.banner--warn/--error` + `.budgetbar` と同じ系統) |
| `.gentle`(`.x`) | 同名で対応(`.banner--quiet` と同じ役割)。閉じるは `.gentle__close`(`.x` も可) |
| `.help` `.tip` | `.help`(同名。A は18px、B は20px + タップ領域44px)、`.tip`(同名、反転色の短い吹き出し)。長い説明は `.helppop` |
| `.res` | 同名。`--hit/--miss/--refund/--pending` で記号が付く |
| `.meter`(`> i`、`.is-warn`) | 同名。幅は `--p`(A の `> i` の width 指定でも可)、`.is-over` を追加、80%の目印 |
| `.stabs` | 同名。選択は `aria-selected="true"`(反転) |
| `.tabbar--4` | `.tabbar.tabbar--4`。選択中は色 + 太字 + 上の帯 |
| `.mine`(「あなたの設定」) | `.changed-tag` / `.setsummary` のまま(藍)。**`.mine` は設定の目印で、自分の印の色 `--c-mine` とは別物**。名前が紛らわしいので、A のモックでは `.mine` を `.mysetting` などに改名してほしい |
| `.progress` `.steps`(`.is-done` `.is-now` `.is-fail`) | `.fetchprog__bar`(目安のバー)+ `.steps`(同名、✓ … ✕ の記号つき)。段階はスマホから見えないので「目安」と書く |
| `.switch.is-on` `.is-disabled`、`.input.is-error` `.is-saved` | 同名で対応(`aria-checked` 版も可)。オンのつまみに ✓ |
| `.oddsin` の紫の枠 | 既存の `.oddsin`(藍の枠)のまま。**紫はローカル表示専用**とし、手入力は `.oddssrc--manual`(鉛筆)で区別する案(v0.4 と同じ理由) |
| `.fetch` `.fetch__row` `.fetch__text` `.fetch__title` `.fetch__icon`(`--warn/--error`) | `.fetchbox` `.fetchbox__text` `.fetchbox__title` `.fetchbox__icon`(`--warn/--error`)。`__row` は不要(箱そのものが横並び) |
| `.fetchbtn`(`<small>`)`.fetchbtn--sub` | 同名。2段にするときは `.fetchbtn--compact` を足す。状態は `data-state`(`idle/loading/cooldown/disabled`) |
| `.src` `.src--worker` `.src--manual` `.src--cache` | `.oddssrc` `--worker` `--manual` `--cache`。**手入力は紫(`--c-local`)にしない**: 紫は「ローカル表示」の目印専用にしたいため、手入力は淡い藍 + 鉛筆にした |
| `--c-bg` `--c-surface` `--c-surface-2` `--c-text` `--c-text-2` `--c-text-3` `--c-border` `--c-accent` `--c-accent-weak` `--c-hit` `--c-hit-weak` `--c-bar-track` `--c-warn` `--c-warn-weak` `--c-info-weak` | 同名 |
| `--c-urgent` / `--c-urgent-weak` | `--c-soon`(点・枠)、`--c-soon-text`(文字)/ `--c-soon-weak` |
| `--c-plus` / `--c-plus-weak` | `--c-ev-plus-text`(文字)、`--c-ev-plus`(枠)/ `--c-ev-plus-weak` |
| `--c-minus` | `--c-danger` |
| `--c-error-weak` | `--c-danger-weak` |
| `--c-muted-row` | `--c-row-muted` |
| `--c-bar` / `--c-bar-strong` | `--c-bar-fill` / `--c-bar-fill-top` |
| `--waku-N-bg` / `-fg` / `-bd` | `--c-boat-N-bg` / `-fg` / `-border` |
| `--fs-xs/s/m/l/xl/xxl`(11/12/14/16/20/28) | `--size-2xs/xs/sm/md/xl/2xl`(11/12/13/15/20/24)。本文は15pxを推奨 |
| `--sp-1〜6`(4/8/12/16/24/32) | `--space-2/3/4/5/7/8` |
| `--r-s/m/l/full`(4/8/12/999) | `--radius-xs/sm〜md/lg/pill`(4/6・10/14/999) |
| `--h-tap` | `--tap-min` |
| `--font` | `--font-family-base` |

- `--gutter`、`--h-appbar`、`--h-tabbar` はレイアウトの値なので、デザイナーAの側で持つ(トークンには入れていない)。
- 回収率の「100%以上/未満」の色は、文字(`+`/`−`の符号、「%」の数値)と併用する前提で `--c-ev-plus-text` / `--c-danger` を使う案。

## 9. 未決・確認したいこと

- 堅さのしきい値(0.50 / 0.35)。実データの confidence の分布を見て、PM・モデル担当と決めたい。
- アプリ名(`name` / `short_name`)。
- iOS のスプラッシュ画像(`apple-touch-startup-image`)は端末サイズごとに画像が要るため、MVPでは作らない案(背景色だけになる)。
