// S5 その他 / このアプリについて、S4 日付の選択(ボトムシート)
import { html } from '../html.js';
import { fmtDateTime, fmtMonthDay, weekdayOf } from '../time.js';
import { fmtRoiMarked, roiClass, fmtNumber, DAY_STATUS_LABELS, CONFIDENCE_THRESHOLDS } from '../format.js';
import { tabbar } from '../parts.js';
import { t, WORDING_META } from '../wording.js';
import { SCHEMA_VERSION, isSample, isLocalData, dataBase } from '../data.js';

export const APP_VERSION = '0.6.0';

/** S5 本体。index は読めていなければ null */
export function aboutView({ index, dayModel, banners }) {
  const sources = (index && index.sources) || [];
  const high = Math.round(CONFIDENCE_THRESHOLDS.high * 100);
  const mid = Math.round(CONFIDENCE_THRESHOLDS.mid * 100);
  return html`
    <header class="appbar"><h1 class="appbar__title is-pad">その他</h1></header>
    ${banners}
    <section class="card">
      <a class="linkrow linkrow--first" href="#/settings">設定 <small>推奨買い目の決め方・オッズの取得・表示</small></a>
    </section>
    <section class="card">
      <div class="card__head"><h2 class="card__title">このアプリについて</h2></div>
      <div class="prose">
        <p><b>${t('notice.reference_only')}</b></p>
        <ul>
          <li>${t('notice.self_responsibility')}</li>
          <li>${t('notice.under_20')}</li>
          <li>回収率はシミュレーション(推奨買い目を1点100円で買ったと仮定)で、将来の成績を保証しません。</li>
        </ul>
      </div>
    </section>

    <section class="card" aria-label="${t('helpline.heading')}">
      <div class="card__head"><h2 class="card__title">${t('helpline.heading')}</h2></div>
      <p class="card__note" style="padding-top:4px">${t('helpline.intro')}</p>
      ${['helpline.mhlw', 'helpline.caa', 'helpline.ncasa', 'helpline.mhwc'].map((k) => html`<a class="linkrow" href="${WORDING_META[k][1]}" target="_blank" rel="noopener noreferrer"><span>${WORDING_META[k][0]}<small style="display:block;margin-left:0">${t(k)}</small></span></a>`)}
      <div class="linkrow"><span>${WORDING_META['helpline.hotline188'][0]}<small style="display:block;margin-left:0">${t('helpline.hotline188')}</small></span></div>
      <p class="card__note" style="padding-top:8px"><b>${t('notice.under_20')}</b></p>
    </section>
    <section class="card">
      <div class="card__head"><h2 class="card__title">予想のしくみ</h2></div>
      <div class="prose">
        <p>LightGBM で各艇の1着確率を出し、Harville の方法で2連単・3連単などの組合せの確率に変換しています。</p>
        <p>「必要オッズ ≧ 19.3倍」は 1 ÷ 確率 の値です。投票アプリのオッズがこれ以上なら、期待値がプラス(1.0以上)になる目安です。</p>
        <p>締切前オッズがあるレースでは、期待値(確率 × オッズ)をランク A(1.2以上)・B(1.0〜1.2)・C(0.8〜1.0)・D(0.8未満)で示します。公開版はランクだけで、オッズの数値は公開しません。締切前のオッズは締切までに変わるので、投票は投票アプリの最新オッズで確かめてください。</p>
        <p>印 ◎○▲△ は1着確率の1〜4位。堅さは1着確率の最大値で、${high}%以上を「堅」、${mid}〜${high}%を「中」、${mid}%未満を「荒」としています。</p>
      </div>
    </section>

    <section class="card">
      <div class="card__head"><h2 class="card__title">データの出典</h2></div>
      ${sources.length
        ? sources.map((s) => html`<a class="linkrow" href="${s.url}" target="_blank" rel="noopener noreferrer">${s.name} <small>${s.license || ''}</small></a>`)
        : html`<p class="card__note" style="padding-top:8px">${index ? '出典の情報がありません。' : '読み込み中です…'}</p>`}
      <p class="card__note" style="padding-top:8px">いずれも非公式・個人運営のデータです。</p>
    </section>

    <section class="card">
      <div class="card__head"><h2 class="card__title">データの状態</h2></div>
      <div class="prose prose--small">
        <p>データ生成: ${index ? fmtDateTime(index.generated_at) : '—'}${index && index.latest_date ? ` · 最新の日付: ${index.latest_date}` : ''}<br>
        ${dayModel ? html`モデル: ${dayModel.model_id}${dayModel.train_until ? `(学習 〜${dayModel.train_until})` : ''}<br>` : ''}
        データ形式: schema_version ${SCHEMA_VERSION} · アプリ版: ${APP_VERSION}${isSample ? ' · サンプルデータを表示中' : ''}<br>
        データの出どころ: ${isLocalData() ? `ローカル(PC の ${dataBase().replace(/\/$/, '')}。私的使用のため公開しない)` : '公開版(オッズの数値なし)'}<br>
        オフライン表示: ${isLocalData() && !window.isSecureContext ? '使えません(LAN の http では Service Worker が動かないため)' : '使えます'}</p>
      </div>
    </section>
    ${tabbar('about')}`;
}

/** S4 日付の選択(ボトムシート) */
export function datesSheet({ index, currentDate, today }) {
  const rows = (index.dates || []).map((d) => {
    const s = d.summary;
    const selected = d.date === currentDate;
    const status = DAY_STATUS_LABELS[d.status] || d.status || '';
    let sub = status;
    if (d.status === 'in_progress' && s) sub = `${status} ${fmtNumber(s.races_with_result)}/${fmtNumber(d.race_count)}R`;
    else if (d.status === 'final') sub = `${status} ${fmtNumber(d.race_count)}R`;
    const href = d.date === today ? '#/' : `#/date/${d.date}`;
    return html`<a class="date-row" href="${href}" data-action="pick-date" ${selected ? html`aria-current="true"` : ''}>
      <span class="check" aria-hidden="true">${selected ? '✓' : ''}</span>
      <span class="d">${fmtMonthDay(d.date)}<small>${weekdayOf(d.date)}${d.date === today ? ' · 今日' : ''}</small></span>
      <span class="r">${s && s.roi !== null && s.roi !== undefined ? html`<b class="${roiClass(s.roi)}">${fmtRoiMarked(s.roi)}</b>` : html`<b class="faint">—</b>`}<span>${sub}</span></span>
    </a>`;
  });
  return html`<div class="scrim" data-action="close-sheet"></div>
    <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title" tabindex="-1" data-sheet>
      <div class="sheet__grip" aria-hidden="true"></div>
      <div class="sheet__head"><span class="sheet__title" id="sheet-title">日付を選ぶ</span><button class="iconbtn" data-action="close-sheet" aria-label="閉じる">✕</button></div>
      ${rows.length ? rows : html`<p class="card__note" style="padding:12px 16px">日付がありません。</p>`}
    </div>`;
}
