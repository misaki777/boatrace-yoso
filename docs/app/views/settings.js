// S6 設定(design/screens README 12.6・13.5、spec/07 4.2・9.7)
// 保存ボタンは置かず、変えたらすぐ保存してトーストで知らせる。
import { html, raw, cls } from '../html.js';
import { fmtMonthDayWeek, fmtClock } from '../time.js';
import { betTypeLabel, fmtNumber } from '../format.js';
import { tabbar, ICONS } from '../parts.js';
import { CONSTANTS, defaultSettings, sameSettings } from '../strategy.js';
import { getSettings, getDisplay, getWorkerConfig, validWorkerUrl } from '../settings.js';
import { describeSettings } from '../odds-ui.js';
import { evRankBadge } from '../odds-ui.js';

const STADIUM_NAMES = ['桐生', '戸田', '江戸川', '平和島', '多摩川', '浜名湖', '蒲郡', '常滑', '津', '三国', 'びわこ', '住之江', '尼崎', '鳴門', '丸亀', '児島', '宮島', '徳山', '下関', '若松', '芦屋', '福岡', '唐津', '大村'];

const MIN_PROB_LABELS = { null: 'なし', 0.01: '1%', 0.02: '2%', 0.03: '3%', 0.05: '5%' };

function choiceButton({ action, value, checked, label, disabled = false, extra = '' }) {
  return html`<button role="radio" aria-checked="${String(checked)}" data-action="${action}" data-value="${value}" ${raw(disabled ? 'disabled' : '')}>${extra}${label}</button>`;
}

function changedTag(changed) {
  return changed ? html`<span class="changed-tag changed-tag--end">既定から変更</span>` : '';
}

/** 推奨買い目の決め方のカード */
function strategyCard({ settings, preview, date }) {
  const def = defaultSettings();
  const isDefault = sameSettings(settings, def);
  const btChanged = JSON.stringify(settings.bet_types) !== JSON.stringify(def.bet_types);
  const evOff = settings.ev_filter === 'none';
  const summary = html`<div class="set-summary">
    <div class="row"><b>${describeSettings(settings)}</b>${isDefault ? html`<span class="chip chip--pre">既定</span>` : html`<span class="custom-tag">既定から変更中</span>`}</div>
    ${preview ? html`<div class="count">${date ? `今日(${fmtMonthDayWeek(date)})は` : ''} ${fmtNumber(preview.races)}R で推奨 · 計 ${fmtNumber(preview.bets)}点${isDefault ? '' : `(既定は ${fmtNumber(preview.defaultRaces)}R · ${fmtNumber(preview.defaultBets)}点)`}</div>` : html`<div class="count">今日のデータを読み込むと、推奨の件数を出します。</div>`}
    ${isDefault ? '' : html`<div class="row"><button class="btn btn--small btn--reset" data-action="settings-reset">既定に戻す</button></div>`}
  </div>`;

  const typeRows = CONSTANTS.bet_type_order.map((bt) => {
    const on = bt in settings.bet_types;
    const [lo, hi] = CONSTANTS.k_ranges[bt];
    const k = on ? settings.bet_types[bt] : lo;
    const last = on && Object.keys(settings.bet_types).length === 1;
    return html`<div class="${cls('typerow', !on && 'is-off')}">
      <span class="checkbox" role="checkbox" tabindex="0" aria-checked="${String(on)}" aria-label="${betTypeLabel(bt)}" ${raw(last ? 'aria-disabled="true" title="最低1つは選んでください"' : '')} data-action="set-bettype" data-bt="${bt}"></span>
      <span class="typerow__name">${betTypeLabel(bt)}<small>${lo}〜${hi}点${last ? ' · 最低1つは選ぶ' : ''}</small></span>
      <span class="${cls('stepper', !on && 'is-off')}" role="group" aria-label="${betTypeLabel(bt)}の点数">
        <button data-action="set-k" data-bt="${bt}" data-delta="-1" aria-label="減らす" ${raw(!on || k <= lo ? 'disabled' : '')}>−</button>
        <output aria-live="polite">${k}<small>点</small></output>
        <button data-action="set-k" data-bt="${bt}" data-delta="1" aria-label="増やす" ${raw(!on || k >= hi ? 'disabled' : '')}>＋</button>
      </span>
    </div>`;
  });

  const [sLo, sHi, sStep] = CONSTANTS.stake_range;
  return html`<section class="card card--emph" aria-label="推奨買い目の決め方">
    <div class="card__head"><h2 class="card__title">推奨買い目の決め方</h2></div>
    ${summary}
    <div class="${cls('set-group', btChanged && 'is-changed')}">
      <div class="set-label"><span class="step">1</span>券種と点数 <small>複数選べます</small>${changedTag(btChanged)}</div>
      ${typeRows}
    </div>
    <div class="${cls('set-group', settings.min_prob !== def.min_prob && 'is-changed')}">
      <div class="set-label"><span class="step">2</span>最低確率 <small>これ未満の組合せは買わない</small>${changedTag(settings.min_prob !== def.min_prob)}</div>
      <div class="choice choice--fill" role="radiogroup" aria-label="最低確率">${CONSTANTS.min_prob_choices.map((v) => choiceButton({ action: 'set-minprob', value: String(v), checked: settings.min_prob === v, label: MIN_PROB_LABELS[v] }))}</div>
    </div>
    <div class="${cls('set-group', (settings.ev_filter !== def.ev_filter || settings.no_odds !== def.no_odds) && 'is-changed')}">
      <div class="set-label"><span class="step">3</span>期待値で絞る <small>締切前オッズがあるレースだけ</small>${changedTag(settings.ev_filter !== def.ev_filter || settings.no_odds !== def.no_odds)}</div>
      <div class="choice" role="radiogroup" aria-label="期待値で絞る">
        ${choiceButton({ action: 'set-evfilter', value: 'none', checked: evOff, label: 'なし' })}
        ${choiceButton({ action: 'set-evfilter', value: 'B_or_above', checked: settings.ev_filter === 'B_or_above', label: '以上(1.0以上)', extra: evRankBadge('B', { sm: true }) })}
        ${choiceButton({ action: 'set-evfilter', value: 'A_only', checked: settings.ev_filter === 'A_only', label: 'だけ(1.2以上)', extra: evRankBadge('A', { sm: true }) })}
      </div>
      <div class="set-label" style="margin-top:12px;font-weight:400">オッズが無いとき</div>
      <div role="radiogroup" aria-label="オッズが無いとき">
        <button class="radio-row" role="radio" aria-checked="${String(settings.no_odds === 'prob_only')}" data-action="set-noodds" data-value="prob_only" ${raw(evOff ? 'disabled' : '')}><span class="radio" aria-hidden="true"></span><span>確率だけで選ぶ<small>期待値の条件をはずして選ぶ</small></span></button>
        <button class="radio-row" role="radio" aria-checked="${String(settings.no_odds === 'skip')}" data-action="set-noodds" data-value="skip" ${raw(evOff ? 'disabled' : '')}><span class="radio" aria-hidden="true"></span><span>見送る</span></button>
      </div>
    </div>
    <div class="${cls('set-group', settings.skip_confidence !== def.skip_confidence && 'is-changed')}">
      <div class="set-label"><span class="step">4</span>堅さで見送り${changedTag(settings.skip_confidence !== def.skip_confidence)}</div>
      <div class="choice" role="radiogroup" aria-label="堅さで見送り">
        ${choiceButton({ action: 'set-skip', value: 'none', checked: settings.skip_confidence === 'none', label: 'なし' })}
        ${choiceButton({ action: 'set-skip', value: 'ara', checked: settings.skip_confidence === 'ara', label: '「荒」なら見送り' })}
        ${choiceButton({ action: 'set-skip', value: 'chu_or_below', checked: settings.skip_confidence === 'chu_or_below', label: '「中」以下なら見送り' })}
      </div>
      <p class="set-help">堅さは1着確率の最大値: 堅 ${Math.round(CONSTANTS.confidence_kata * 100)}%以上 / 中 ${Math.round(CONSTANTS.confidence_ara * 100)}〜${Math.round(CONSTANTS.confidence_kata * 100)}% / 荒 ${Math.round(CONSTANTS.confidence_ara * 100)}%未満(固定)</p>
    </div>
    <div class="${cls('set-group', settings.stake !== def.stake && 'is-changed')}">
      <div class="set-label">1点あたりの金額 <small>投資・払戻の試算にだけ使います</small>${changedTag(settings.stake !== def.stake)}</div>
      <span class="stepper" role="group" aria-label="1点あたりの金額">
        <button data-action="set-stake" data-delta="-${sStep}" aria-label="減らす" ${raw(settings.stake <= sLo ? 'disabled' : '')}>−</button>
        <output aria-live="polite">${settings.stake}<small>円</small></output>
        <button data-action="set-stake" data-delta="${sStep}" aria-label="増やす" ${raw(settings.stake >= sHi ? 'disabled' : '')}>＋</button>
      </span>
    </div>
  </section>`;
}

/** Worker の設定のカード */
function workerCard({ ui, badKey }) {
  const cfg = getWorkerConfig();
  const urlInvalid = ui.urlDraft !== undefined ? !validWorkerUrl(ui.urlDraft) : !validWorkerUrl(cfg.url);
  const status = badKey ? html`<span class="statustag statustag--bad">要確認</span>` : cfg.ready ? html`<span class="statustag statustag--ok">設定済み</span>` : html`<span class="statustag statustag--none">未設定</span>`;
  const keyField = cfg.hasKey && !ui.editingKey
    ? html`<div class="secret-saved"><span class="secret-saved__dots" aria-hidden="true">●●●●●●●●</span><span class="secret-saved__state">保存済み(表示しません)</span><button class="btn btn--small" data-action="worker-key-edit">変更</button></div>`
    : html`<form class="secret" data-noform onsubmit="return false"><input class="input input--mono" id="worker-key" type="${ui.showKey ? 'text' : 'password'}" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="合言葉" data-worker-key aria-describedby="worker-key-help"><button class="secret__toggle" type="button" data-action="worker-key-toggle" aria-pressed="${String(!!ui.showKey)}">${ui.showKey ? '隠す' : '表示'}</button></form>`;
  return html`<section class="card" id="worker-card" aria-label="オッズの取得(Worker)">
    <div class="card__head"><h2 class="card__title">オッズの取得(Worker)</h2>${status}</div>
    <p class="card__note" style="padding-top:4px">レース詳細の「オッズを取る」で、そのレースの今のオッズを取ります。オッズはこの端末にだけ表示し、保存・送信しません。</p>
    ${badKey ? html`<div class="banner banner--error" role="alert" style="margin:0 12px 8px"><span class="banner__icon" aria-hidden="true">🔑</span><div class="banner__body"><b class="banner__title">合言葉を確認してください</b><span class="banner__text">${fmtClock(new Date(badKey).toISOString())} の取得で「合言葉が違います」と返されました。</span></div></div>` : ''}
    <div class="set-group">
      <div class="${cls('field', urlInvalid && 'is-invalid')}">
        <label class="field__label" for="worker-url">Worker の URL</label>
        <input class="input input--mono" id="worker-url" type="url" inputmode="url" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="https://xxxx.workers.dev" value="${ui.urlDraft !== undefined ? ui.urlDraft : cfg.url}" data-worker-url ${raw(urlInvalid ? 'aria-invalid="true"' : '')}>
        <span class="field__msg" role="alert">⚠ https:// から始まる URL を入れてください(末尾の /odds は不要)</span>
      </div>
    </div>
    <div class="set-group">
      <div class="${cls('field', badKey && 'is-invalid')}">
        <label class="field__label" for="worker-key">合言葉</label>
        ${keyField}
        <span class="field__help" id="worker-key-help">入力欄から離れたときに保存します。保存した合言葉は画面に表示しません。</span>
        ${badKey ? html`<span class="field__msg">✕ 合言葉が違うと返されました</span>` : ''}
      </div>
    </div>
    <div class="set-group">
      <div class="switch-row">
        <span class="txt">レース詳細を開いたら自動で取る<small>締切まで15分以内で、60秒以内に取っていないときだけ</small></span>
        <button class="switch" role="switch" aria-checked="${String(cfg.auto)}" aria-label="自動で取る" data-action="worker-auto" ${raw(cfg.ready ? '' : 'disabled')}><span class="switch__track" aria-hidden="true"></span><span class="switch__text" aria-hidden="true"></span></button>
      </div>
      ${cfg.ready ? '' : html`<p class="set-help">URL と合言葉がそろうと選べます。</p>`}
    </div>
  </section>`;
}

/** どう選ばれるか(適用の順序) */
function flowCard({ settings, open }) {
  const types = CONSTANTS.bet_type_order.filter((bt) => settings.bet_types[bt]);
  const evOff = settings.ev_filter === 'none';
  const items = [
    ['券種ごとに、確率の高い順に並べる', types.map(betTypeLabel).join('・'), false],
    ['最低確率に届かない組合せを除く', settings.min_prob === null ? 'なし' : MIN_PROB_LABELS[settings.min_prob], settings.min_prob === null],
    [`期待値ランクが${settings.ev_filter === 'A_only' ? 'A' : 'B'}未満の組合せを除く`, evOff ? 'なし' : settings.ev_filter === 'A_only' ? 'Aだけ' : 'B以上', evOff],
    ['上から点数ぶんを選ぶ', types.map((bt) => `${settings.bet_types[bt]}点`).join('・'), false],
    [`堅さが${settings.skip_confidence === 'chu_or_below' ? '「中」以下' : '「荒」'}のレースは全部見送る`, settings.skip_confidence === 'none' ? 'なし' : settings.skip_confidence === 'ara' ? '荒は見送り' : '中以下は見送り', settings.skip_confidence === 'none'],
  ];
  return html`<section class="card"><details class="fold fold--flow" data-fold="flow" data-race="settings" ${raw(open ? 'open' : '')}>
    <summary>どう選ばれるか <span class="count">適用の順序</span></summary>
    <ol class="flow">${items.map(([t, v, off]) => html`<li class="${cls(off && 'is-off')}">${t}<span class="v">${v}</span></li>`)}</ol>
    <p class="set-help" style="padding:0 12px 12px">オッズが無いレースでは③を${settings.no_odds === 'skip' ? '満たさない扱いにして見送ります' : '飛ばします'}。</p>
  </details></section>`;
}

/** 表示の設定 */
function displayCard() {
  const d = getDisplay();
  const theme = (v, label) => choiceButton({ action: 'set-theme', value: v, checked: d.theme === v, label });
  return html`<section class="card" aria-label="表示">
    <div class="card__head"><h2 class="card__title">表示</h2></div>
    <div class="set-group" style="border-top:0">
      <div class="set-label">表示モード</div>
      <div class="choice choice--fill" role="radiogroup" aria-label="表示モード">${theme('auto', '端末に合わせる')}${theme('light', 'ライト')}${theme('dark', 'ダーク')}</div>
    </div>
    <div class="set-group">
      <div class="set-label">お気に入りの場 <small>一覧の絞り込みに使います</small></div>
      <div class="multi" role="group" aria-label="お気に入りの場">${STADIUM_NAMES.map((name, i) => html`<button aria-pressed="${String(d.favorites.includes(i + 1))}" data-action="toggle-fav" data-value="${i + 1}">${name}</button>`)}</div>
    </div>
    <div class="set-group">
      <div class="set-label">確率上位に出す券種</div>
      <div class="multi multi--plain" role="group" aria-label="確率上位に出す券種">${CONSTANTS.bet_type_order.map((bt) => html`<button aria-pressed="${String(d.rank_types.includes(bt))}" data-action="toggle-ranktype" data-value="${bt}">${betTypeLabel(bt)}</button>`)}</div>
    </div>
  </section>`;
}

/** S6 本体 */
export function settingsView({ preview, date, ui, resetNotice, badKey }) {
  const settings = getSettings();
  return html`
    <header class="appbar">
      <a class="iconbtn" href="#/about" data-action="settings-back" aria-label="戻る">${ICONS.back}</a>
      <h1 class="appbar__title">設定</h1>
    </header>
    ${resetNotice ? html`<div class="banner banner--warn" role="alert"><span class="banner__icon" aria-hidden="true">!</span><div class="banner__body"><b class="banner__title">設定を既定に戻しました</b><span class="banner__text">保存されていた設定を読めなかったため、既定の買い方に戻しています。</span></div></div>` : ''}
    ${strategyCard({ settings, preview, date })}
    ${workerCard({ ui, badKey })}
    ${flowCard({ settings, open: ui.flowOpen })}
    ${displayCard()}
    <p class="disclaimer">設定はこの端末にだけ保存されます(サーバーには送りません)。成績画面の集計は、設定に関係なく既定の買い方で出します。</p>
    ${tabbar('about')}`;
}
