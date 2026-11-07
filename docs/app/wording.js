// 画面の文言(spec/09_wording.md の表をキーを変えずに写したもの。文言の変更は PM が spec/09 で行い、ここでは書き換えない)
// {n} {pct} などのプレースホルダは t() で埋める。
export const WORDING = Object.freeze({
  "help.probability": "モデルが計算した、その着順になる見込みです。過去のデータから作った推定で、外れることもよくあります。",
  "help.calibration": "「予測の確率」と「実際に当たった割合」がどれだけ一致しているかです。グラフの点が斜めの線に近いほど、確率をそのまま信じやすいモデルです。線より下にあるときは、モデルが強気すぎます。",
  "help.required_odds": "期待値がちょうど1.0(損も得もしない)になるオッズです。1 ÷ 確率 で計算します。実際のオッズがこれより低ければ、長く買い続けると損になる見込みです。",
  "help.expected_value": "確率 × オッズ です。1.0より大きいと長い目で見て得、小さいと損の見込みです。確率の推定がずれていると、期待値もずれます。締切前のオッズは締切までに変わります。",
  "help.ev_rank": "期待値を A(1.2以上)・B(1.0〜1.2)・C(0.8〜1.0)・D(0.8未満)の4段階にしたものです。",
  "help.confidence_interval": "数字のぶれの幅です。件数が少ないほど幅が広くなります。回収率の区間が100%をまたいでいるときは、「プラスともマイナスとも言えない」という意味です。",
  "help.sample_size": "集計に使ったレースや買い目の数です。少ない件数の成績は偶然の影響が大きいため、200レース未満の数字は薄く表示しています。",
  "help.roi": "払戻の合計 ÷ 購入額の合計 です。100%を超えるとプラスです。ボートレースは控除率があるため、平均的には75%前後になります。",
  "help.hit_rate": "買ったレースのうち、1点以上当たったレースの割合です。的中率が高くても、回収率が高いとは限りません。",
  "help.backtest_vs_live": "「検証」は過去のデータで、後から計算した成績です。買い方の条件を選んだ期間を含むため、実際より良く見えがちです(後知恵)。「実運用」は、その日の朝に確定した予想で記録した成績です。",
  "help.prediction_version": "「前日版」は出走表だけで作った予想、「直前版」は展示タイムなどの直前情報も使った予想です。どちらも一度確定したら書き換えません。",
  "help.reasons": "その艇の確率を上げた・下げた要因のうち、影響が大きかったもの3つです。モデルの計算の内訳で、因果関係を示すものではありません。",
  "help.combined_odds": "複数の買い目を同じ金額で買ったとき、全体として何倍になるかの目安です。",
  "help.torigami": "当たっても、払戻が購入額の合計より少なく、損になることです。",
  "help.drawdown": "収支が一番良かった時点から、一番落ち込んだ時点までの差です。どれだけ負けが続く可能性があるかの目安になります。",
  "help.losing_streak": "続けて外れたレースの数の最大値です。",
  "help.confidence_label": "本命の1着確率で分けた目安です。堅 50%以上、中 35〜50%、荒 35%未満。",
  "help.baseline": "「1号艇の単勝を買い続ける」など、モデルを使わない単純な買い方です。モデルの成績がこれより良いかを比べるために表示しています。",
  "help.allocation": "予算を買い目にどう分けるかの選び方です。「均等」「上位厚め」「確率に比例」は、過去のデータではどれも回収率が約78%で、ほとんど差がありませんでした(2026年9〜10月)。配分を変えても、長い目で見た損得は大きくは変わりません。損の額は賭け金に比例して大きくなるので、予算は無理のない額にしてください。",
  "help.allocation_ev": "期待値が1より大きい買い目ほど多く、1未満の買い目には0円を割り当てます。1点に予算の40%より多くは割り当てません。オッズは締切まで変わり、確率の推定にも誤差があるため、期待値は目安です。期待値が高く見えても、当たるとは限りません。",
  "help.allocation_payout": "どの買い目が当たっても、払戻がほぼ同じになるように分けます。オッズが低い買い目ほど多くなります。オッズは締切まで変わるため、実際の払戻はずれます。",
  "help.skip": "設定した条件(堅さ・確率・期待値)に合う買い目が無いレースです。",
  "skip.low_confidence": "本命の確率が低く、荒れる見込みのため見送り",
  "skip.low_prob": "条件の確率を満たす買い目が無いため見送り",
  "skip.low_ev": "期待値の条件を満たす買い目が無いため見送り",
  "skip.none": "推奨はありません",
  "alloc.need_odds": "オッズが無いため選べません。オッズを取得するか、手で入力すると選べます。",
  "alloc.no_positive_ev": "期待値が1以上の買い目が無いため、金額はすべて0円です。",
  "alloc.budget_too_small": "予算が点数に対して少ないため、一部の買い目は0円になりました。",
  "alloc.no_budget": "1レースの予算を入力すると、買い目ごとの金額を表示します。",
  "alloc.cap_applied": "1点あたり予算の40%までに抑えています。",
  "alloc.not_history": "金額の計算に、これまでの収支や前のレースの結果は使っていません。",
  "warn.budget_day_80": "今日の予算の{pct}%を使いました(残り{remain}円)。",
  "warn.budget_day_100": "今日の予算に達しました。今日はここまでにしませんか。",
  "warn.budget_month_80": "今月の予算の{pct}%を使いました(残り{remain}円)。",
  "warn.budget_month_100": "今月の予算に達しました。",
  "warn.losing_streak": "{n}レース続けて外れています。負けを取り返そうと金額を増やすのは避けましょう。",
  "warn.late_night": "深夜です。無理のない範囲で。",
  "warn.session_long": "{min}分使っています。少し休憩しませんか。",
  "warn.few_samples": "件数が少ないため({n}レース)、参考程度に見てください。",
  "warn.ci_crosses_100": "この成績からは、プラスともマイナスとも言えません。",
  "warn.hindsight": "過去のデータで後から計算した成績です。実際より良く見えることがあります。",
  "warn.overconfident": "モデルは確率を高めに出す傾向があります(例: 予測{pred}%に対し実際{actual}%)。",
  "warn.odds_change": "締切前のオッズは締切までに変わります。投票の前に、投票アプリの最新オッズを確認してください。",
  "warn.torigami_unavoidable": "この組合せでは、どれが当たっても損にならない配分はできません。",
  "warn.backup": "記録はこの端末の中だけにあります。ブラウザのデータを消すと失われるため、ときどきバックアップを書き出してください。",
  "notice.reference_only": "非公式のデータに基づく、個人用の参考情報です。正確性や的中を保証するものではありません。",
  "notice.self_responsibility": "舟券の購入は、ご自身の判断と責任で行ってください。",
  "notice.under_20": "20歳未満の方は、舟券を購入できません(モーターボート競走法)。",
  "notice.simulation": "回収率は、1点100円で買ったと仮定したシミュレーションです。将来の成績を保証するものではありません。",
  "notice.excluded_races": "中止・見送りのレースは集計に含みません。",
  "notice.default_strategy": "成績は既定の買い方({label})で集計しています。今の設定とは異なります。",
  "notice.local_records": "購入記録・印・メモは、この端末の中だけに保存しています。外部には送信しません。",
  "notice.copy_footer": "※個人用の参考情報です。的中を保証するものではありません。",
  "notice.official_link": "投票は公式の投票サービスで行ってください(BOAT RACE オフィシャルウェブサイトのトップページを開きます)。",
  "notice.data_source": "データ: Boatrace Open API、Turnmark API(いずれも非公式・MITライセンス)。締切前オッズは BOAT RACE オフィシャルウェブサイトから、本人の閲覧のためにだけ取得しています。",
  "helpline.heading": "ギャンブルのことで困ったら",
  "helpline.intro": "自分ではやめられない、借金がある、家族が心配している――そんなときは、一人で抱えずに相談してください。相談は無料で、秘密は守られます。",
  "helpline.mhlw": "ギャンブル等依存症についての国の情報と、相談先の案内です。",
  "helpline.caa": "借金の相談先や、家族向けの情報を案内しています。",
  "helpline.ncasa": "全国の相談窓口・治療機関・自助グループを探せます。",
  "helpline.mhwc": "お住まいの都道府県・政令指定都市の相談窓口です(全国精神保健福祉センター長会の一覧)。",
  "helpline.hotline188": "借金や契約のトラブルを、身近な消費生活センターにつなぎます。",
  "label.version_pre": "前日版",
  "label.version_preview": "直前版",
  "label.locked_at": "{time} 確定",
  "label.reason_up": "上げる要因",
  "label.reason_down": "下げる要因",
  "label.live": "実運用",
  "label.backtest": "検証(過去データ)",
  "label.model_top": "モデルの上位",
  "label.matches_settings": "条件に合う買い目",
  "label.my_marks": "自分の印",
  "label.record_bet": "購入を記録",
  "label.copy_bets": "買い目をコピー",
  "label.backup_export": "バックアップを書き出す",
  "label.backup_import": "バックアップを読み込む",
  "stale.banner": "データの更新が止まっているようです(最終更新 {time})。表示中の予想・結果は古い可能性があります。",
  "stale.detail": "自動の更新が{min}分以上行われていません。しばらくしても直らない場合は、GitHub の Actions の実行結果を確認してください。",
  "tenkai.heading": "展開予想",
  "tenkai.nige": "1コース({entry}号艇)が逃げる確率 {pct}%",
  "tenkai.others": "差し {sashi}%・まくり {makuri}%・まくり差し {msashi}%・その他 {other}%",
  "tenkai.note": "決まり手の確率は、着順の確率とは別のモデルで計算しています。数字が一致しないことがあります。",
  "help.tenkai": "レースがどの決まり手で決まるかの見込みです。過去の決まり手の傾向と、各選手のレース運び(コース別の逃げ・差し・まくりの率など)から計算しています。",
  "card.heading": "選手カード",
  "card.note": "コース別・当地などの率は、本ツールが過去の結果から集計したもので、公式の成績とは計算の仕方が違います。件数が少ない率は薄く表示しています。",
  "card.unavailable": "この日の選手カードはありません(当日と前日の分だけ表示できます)。",
  "card.load_failed": "選手カードを読み込めませんでした。通信の状態を確かめて、もう一度開いてください。",
  "models.heading": "モデルの更新履歴",
  "models.note": "毎月、新しいデータで学び直した候補を、前月のデータで今のモデルと比べ、決めた基準をすべて満たしたときだけ切り替えています。確定した予想は、切り替えの後も書き換えません。",
  "models.not_adopted": "候補は基準を満たさなかったため、切り替えていません。",
  "ana.tab": "分析",
  "ana.few": "参考(件数 {n})",
  "ana.insufficient": "件数不足({n}件)",
  "ana.not_precomputed": "この組合せは事前に集計していません。条件を減らしてください。",
  "ana.period": "集計期間: {from}〜{to}({n}レース)",
  "ana.hindsight": "過去のデータで、条件を見てから比べた成績です。たまたま良かった条件が上に出やすく、これから同じ成績になるとは限りません。",
  "ana.live_only": "実運用(その日の朝に確定した予想)だけで集計しています。",
  "ana.sim_budget": "最大損失は、1レースの予算を1,000円と仮定して計算しています。",
  "ana.own_local": "この集計は、この端末の購入記録だけから計算しています。外部には送信しません。",
  "ana.manshu": "万舟率: 3連単の払戻が10,000円以上だったレースの割合",
  "ana.result_weather": "風・天候は、レース本番の記録で分けています。",
  "personas.heading": "仮想の購入者",
  "personas.intro": "決まった買い方を続けた場合の成績を、10人の仮想の購入者で比べています。どの人も1レース1,000円で固定し、負けたあとに金額を増やすことはしません。あなたの購入記録は使っていません。",
  "personas.win_fav.name": "単勝さん",
  "personas.win_fav.desc": "全レースで、本命(確率が一番高い艇)の単勝を1点買う",
  "personas.tri_top5.name": "3連単さん",
  "personas.tri_top5.desc": "全レースで、3連単の確率上位5点を均等に買う",
  "personas.steady.name": "堅実さん",
  "personas.steady.desc": "本命の確率が50%以上(「堅」)のレースだけ、2連単の確率上位3点を均等に買う",
  "personas.upset.name": "荒れ狙いさん",
  "personas.upset.desc": "本命の確率が35%未満(「荒」)のレースだけ、3連単の確率上位10点を均等に買う",
  "personas.boat1.name": "1号艇さん(基準)",
  "personas.boat1.desc": "モデルを使わず、全レースで1号艇の単勝を1点買う。比べるための基準です",
  "personas.no_recommend": "どの買い方が良いかを勧めるものではありません。成績は期間や件数によって大きく変わります。",
  "personas.live": "実運用(その日の朝に確定した予想、2026年11月2日から)",
  "personas.backtest": "過去の参考(学習に使っていない期間を、後から計算したもの)。実際より良く見えることがあります。",
  "personas.fixed_stake": "金額は1レース1,000円で固定です。前のレースの結果や収支で金額を変えることはしません。",
  "personas.in_nige.name": "イン逃げさん",
  "personas.in_nige.desc": "モデルを使わず、全レースで1号艇が1着の3連単 1-2-3・1-2-4・1-3-2・1-3-4 の4点を買う",
  "personas.app.name": "アプリさん",
  "personas.app.desc": "全レースで、アプリが示す買い目(その日の既定の買い方)のとおりに買う",
  "personas.box3.name": "ボックスさん",
  "personas.box3.desc": "全レースで、1着の確率が高い3艇の3連単ボックス6点を買う",
  "personas.quinella3.name": "2連複さん",
  "personas.quinella3.desc": "全レースで、2連複の確率の上位3点を買う",
  "personas.outer_ana.name": "外枠穴さん",
  "personas.outer_ana.desc": "外側(4〜6コース)で展示ST・展示タイムがともに1位の艇がいるレースだけ、その艇と1着確率の上位2艇との2連複2点を買う",
  "personas.select10.name": "厳選さん",
  "personas.select10.desc": "1日のうち本命の確率が高い10レースだけ、3連単の確率の上位5点を買う",
  "reflect.heading": "反省点",
  "reflect.intro": "負けの内訳から、過去の検証と実運用の両方で回収率が上がった改善だけを示しています。改善は「買わない」「点数を減らす」「券種を変える」の3つの方向だけで、金額を増やす方向は扱いません。",
  "reflect.skip": "{persona}は、{segment}のレースで回収率が{rate_bad}%でした。{segment}を買わないと、過去の検証で{before_bt}%→{after_bt}%、実運用で{before_live}%→{after_live}%でした(実運用 {n}レース)。",
  "reflect.fewer": "{persona}は、確率{rank_from}位以下の買い目の回収率が{rate_bad}%でした。上位{k}点に減らすと、過去の検証で{before_bt}%→{after_bt}%、実運用で{before_live}%→{after_live}%でした(実運用 {n}レース)。",
  "reflect.switch": "{persona}は、{from_type}を{to_type}に変えると、過去の検証で{before_bt}%→{after_bt}%、実運用で{before_live}%→{after_live}%でした(実運用 {n}レース)。",
  "reflect.ci_note": "ただし、この差は件数に対して小さく、偶然の可能性があります。",
  "reflect.none": "過去の検証と実運用の両方で回収率が上がる改善は、今のところ見つかっていません。",
  "reflect.few": "実運用の件数が少ないため、参考程度に見てください。",
  "tenkai.summary.in_nige": "1コース({entry}号艇)の逃げが有力({pct}%)",
  "tenkai.summary.sashi2": "2コース({entry}号艇)の差しに注意({pct}%)",
  "tenkai.summary.kado_makuri": "カド({entry}号艇)が先に出れば、まくりの展開も({pct}%)",
  "tenkai.summary.outer_start": "外の{entry}号艇のスタートに注目(トップスタート {pct}%)",
  "tenkai.summary.makurisashi": "{entry}号艇のまくり差しに注意({pct}%)",
  "tenkai.summary.mixed": "混戦の見込み(どの展開も{pct}%未満)",
  "tenkai.start.heading": "スタート隊形",
  "tenkai.start.ahead_of_inner": "{course}コースが内側より先に出る確率 {pct}%",
  "tenkai.start.kado": "カド(4コース)が3コースより先に出る確率 {pct}%",
  "tenkai.start.top": "トップスタートの確率",
  "tenkai.by_boat.heading": "艇ごとの決まり手"
});

/** 表の文言以外の列(help.* は見出し、warn.* は表示条件、helpline.* は名称と URL) */
export const WORDING_META = Object.freeze({
  "help.probability": [
    "確率"
  ],
  "help.calibration": [
    "較正(かくせい)"
  ],
  "help.required_odds": [
    "必要オッズ"
  ],
  "help.expected_value": [
    "期待値"
  ],
  "help.ev_rank": [
    "期待値ランク"
  ],
  "help.confidence_interval": [
    "信頼区間(95%)"
  ],
  "help.sample_size": [
    "件数"
  ],
  "help.roi": [
    "回収率"
  ],
  "help.hit_rate": [
    "的中率"
  ],
  "help.backtest_vs_live": [
    "検証と実運用"
  ],
  "help.prediction_version": [
    "予想の版"
  ],
  "help.reasons": [
    "確率の根拠"
  ],
  "help.combined_odds": [
    "合成オッズ"
  ],
  "help.torigami": [
    "トリガミ"
  ],
  "help.drawdown": [
    "最大ドローダウン"
  ],
  "help.losing_streak": [
    "最大連敗"
  ],
  "help.confidence_label": [
    "堅さ(堅・中・荒)"
  ],
  "help.baseline": [
    "基準の買い方"
  ],
  "help.allocation": [
    "配分のしかた"
  ],
  "help.allocation_ev": [
    "期待値に比例"
  ],
  "help.allocation_payout": [
    "払戻をそろえる"
  ],
  "help.skip": [
    "見送り"
  ],
  "alloc.need_odds": [
    "オッズが無く「払戻をそろえる」「期待値に比例」を選べないとき"
  ],
  "alloc.no_positive_ev": [
    "期待値に比例で、期待値1以上の買い目が無いとき"
  ],
  "alloc.budget_too_small": [
    "予算が点数×100円に満たないとき"
  ],
  "alloc.no_budget": [
    "予算が未入力のとき"
  ],
  "alloc.cap_applied": [
    "期待値に比例で、上限40%が効いたとき"
  ],
  "alloc.not_history": [
    "配分の欄の近く(常に)"
  ],
  "warn.budget_day_80": [
    "当日の購入額が1日の予算の80%以上"
  ],
  "warn.budget_day_100": [
    "当日の購入額が1日の予算以上"
  ],
  "warn.budget_month_80": [
    "当月が月の予算の80%以上"
  ],
  "warn.budget_month_100": [
    "当月が月の予算以上"
  ],
  "warn.losing_streak": [
    "自分の購入記録で連敗が設定回数(既定5)以上"
  ],
  "warn.late_night": [
    "0:00〜4:59 に操作したとき(控えめに1回)"
  ],
  "warn.session_long": [
    "連続利用60分ごと(控えめに)"
  ],
  "warn.few_samples": [
    "集計のレース数が200未満"
  ],
  "warn.ci_crosses_100": [
    "回収率の95%区間が100%をまたぐ"
  ],
  "warn.hindsight": [
    "検証(バックテスト)の成績の近く"
  ],
  "warn.overconfident": [
    "較正で、予測の平均が実際より10ポイント以上高い帯がある"
  ],
  "warn.odds_change": [
    "締切前オッズ・期待値の近く"
  ],
  "warn.torigami_unavoidable": [
    "買い目合計で、トリガミを避ける配分が無いとき"
  ],
  "warn.backup": [
    "購入記録・印・メモがあり、30日以上バックアップしていないとき"
  ],
  "notice.reference_only": [
    "全画面の下部・S5"
  ],
  "notice.self_responsibility": [
    "S5・買い目合計の近く"
  ],
  "notice.under_20": [
    "S5"
  ],
  "notice.simulation": [
    "S3・S9"
  ],
  "notice.excluded_races": [
    "S3"
  ],
  "notice.default_strategy": [
    "S3(設定が既定と違うとき)"
  ],
  "notice.local_records": [
    "S8・S9"
  ],
  "notice.copy_footer": [
    "買い目のコピーの末尾"
  ],
  "notice.official_link": [
    "公式サイトへのリンクの近く"
  ],
  "notice.data_source": [
    "S5"
  ],
  "helpline.heading": [
    "—",
    "—"
  ],
  "helpline.intro": [
    "—",
    "—"
  ],
  "helpline.mhlw": [
    "厚生労働省「依存症対策」",
    "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/0000070789.html"
  ],
  "helpline.caa": [
    "消費者庁「ギャンブル等依存症でお困りの皆様へ」",
    "https://www.caa.go.jp/policies/policy/consumer_policy/caution/caution_012/"
  ],
  "helpline.ncasa": [
    "依存症対策全国センター",
    "https://www.ncasa-japan.jp/"
  ],
  "helpline.mhwc": [
    "全国の精神保健福祉センター(一覧)",
    "https://www.zmhwc.jp/centerlist.html"
  ],
  "helpline.hotline188": [
    "消費者ホットライン 188(いやや)",
    "—"
  ]
});

/** 文言を取り出し、{名前} を埋める */
export function t(key, vars = {}) {
  const text = WORDING[key];
  if (text === undefined) return key;
  return text.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
}

/** 用語説明の見出し(help.* の「見出し」列) */
export function helpTitle(key) {
  return (WORDING_META[key] || [])[0] || '';
}
