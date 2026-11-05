# プロジェクト概要
- ボートレースの予想ツール(個人用)。最終的にPWA(GitHub Pages)で表示する。
- 構成: Python(データ収集・学習・予想・JSON書き出し)+ GitHub Actionsで定期実行 + docs/ 配下のPWA。
- 予想はLightGBMなど、CPUで動く無料の手法のみ。有料サービスは使わない。

# 方針
- リポジトリは公開(Public)。公式サイトから集めた生データ(オッズ等)は data/raw/ に置き、.gitignore で除外する。コミットしない。
- 外部サイトへのアクセスは1秒以上間隔を空け、必要最小限にする。
- 環境: WSL(Ubuntu)、Python 3.14、仮想環境は .venv。
- 追加ライブラリは必要になった時点で相談してから入れる。LightGBM等が3.14で入らない場合は、別バージョンの仮想環境を提案する。
- コメントと説明は日本語。変数名・関数名は英語。
- git commit と push は、指示があるまで行わない。

# ディレクトリ
- src/collect/ : データ取得
- src/features/ : 特徴量
- src/model/ : 学習・予測・期待値計算
- src/export/ : アプリ用JSON出力
- data/raw/ : 生データ(git管理外)
- docs/ : PWA本体と公開用JSON
