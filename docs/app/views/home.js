// S1 ホーム(今日のレース一覧)。design/screens README 4章
import { html, raw, cls } from '../html.js';
import { fmtClock, fmtMonthDayWeek, fmtUpdated, minutesUntil, nowMs } from '../time.js';
import { pctInt, fmtYen, fmtRoiMarked, roiClass, fmtNumber, DAY_STATUS_LABELS, confidenceLevel, strategyLabel } from '../format.js';
import { boat, orderCombo, conf, gradeChip, raceSubText, tabbar, reloadButton, disclaimer, emptyBox } from '../parts.js';
import { raceStatus, favoriteBoat, outcomeKind, allRaces, stadiumStats, closeMs } from '../model.js';
import { raceRecommendation, dayHasOdds, evRankBadge, bestRank } from '../odds-ui.js';
import { getSettings, isCustomSettings, getDisplay } from '../settings.js';
import { isDefaultSelection } from '../strategy.js';

/** アプリバー(S1) */
export function homeAppbar({ date, dayStatus, updatedText, busy, showStatus }) {
  return html`<header class="appbar">
    <button class="appbar__date" data-action="open-dates" aria-haspopup="dialog" aria-label="日付を選ぶ(${fmtMonthDayWeek(date)})">${fmtMonthDayWeek(date)}<span class="caret" aria-hidden="true">▼</span></button>
    ${showStatus && dayStatus ? html`<span class="chip chip--pre">${DAY_STATUS_LABELS[dayStatus] || dayStatus}</span>` : ''}
    <span class="appbar__spacer"></span>
    ${updatedText ? html`<span class="appbar__updated">${updatedText}</span>` : ''}
    ${reloadButton(busy)}
  </header>`;
}

/** 読み込み中(初回・キャッシュなし)のスケルトン */
export function homeSkeleton({ date, busy }) {
  const row = (w1, w2) => html`<div class="race-row"><div><span class="sk" style="width:36px;height:30px;margin:auto"></span></div><div><span class="sk" style="width:${w1}%;height:16px"></span><span class="sk" style="width:${w2}%;height:11px;margin-top:6px"></span></div><div><span class="sk" style="width:64px;height:24px"></span></div></div>`;
  return html`
    <header class="appbar">
      ${date ? html`<span class="appbar__date" aria-hidden="true">${fmtMonthDayWeek(date)}</span>` : html`<span class="sk" style="width:96px;height:22px;margin-left:8px"></span>`}
      <span class="appbar__spacer"></span>
      <span class="appbar__updated">読み込み中…</span>
      ${reloadButton(true)}
    </header>
    <div class="daysum"><span class="sk" style="width:100%;height:20px"></span></div>
    <div class="seg" aria-hidden="true"><button aria-selected="true" tabindex="-1">締切順</button><button tabindex="-1">場ごと</button></div>
    <div class="sechead"><span class="sk" style="width:80px;height:12px"></span></div>
    <div class="list" aria-busy="true">${row(70, 50)}${row(60, 45)}${row(65, 40)}${row(55, 50)}${row(70, 45)}</div>
    <span class="sr-only" role="status">読み込み中</span>
    ${tabbar('home')}`;
}

/** その日の集計(1行)。null なら出さない */
function daySummary(day) {
  const s = day.day_summary;
  if (!s) return '';
  const total = allRaces(day).length;
  return html`<div class="daysum">
    <span>結果 <b>${fmtNumber(s.races_with_result)}</b>/${fmtNumber(total)}R</span>
    <span>的中 <b>${fmtNumber(s.hits)}</b></span>
    <span class="roi">回収率 <b class="${roiClass(s.roi)}">${fmtRoiMarked(s.roi)}</b></span>
  </div>`;
}

/** 本命の予想1着率が 80%以上・70%以上・60%以上だったレースで、本命が実際に1着になった割合 */
function favBands(day) {
  const done = [];
  for (const { race } of allRaces(day)) {
    if (raceStatus(race) !== 'result' || !race.prediction || !race.result || !race.result.order) continue;
    const fav = favoriteBoat(race.prediction);
    if (!fav || race.result.order[0] == null) continue;
    done.push({ p: fav.p_win, hit: race.result.order[0] === fav.entry });
  }
  if (!done.length) return '';
  const rows = [0.8, 0.7, 0.6].map((th) => {
    const g = done.filter((x) => x.p >= th);
    const k = g.filter((x) => x.hit).length;
    const avg = g.length ? g.reduce((a, x) => a + x.p, 0) / g.length : 0;
    return html`<div class="favband${g.length < 30 ? ' is-lown' : ''}">
      <span class="favband__th">予想${th * 10}割以上</span>
      <span class="favband__val">${g.length ? html`<b>${k}</b>/${g.length}R 当たり <b>${Math.round((k / g.length) * 100)}%</b>` : '該当なし'}</span>
      <span class="favband__avg">${g.length ? `予想の平均 ${Math.round(avg * 100)}%` : ''}</span>
    </div>`;
  });
  return html`<div class="favbands" aria-label="本命の予想確率ごとの当たり">
    <div class="favbands__head">本命の予想別の当たり <span class="faint">(結果が出た${done.length}R)</span></div>
    ${rows}
    ${done.length < 200 ? html`<p class="favbands__note">レース数が少ないので、ぶれが大きい参考値です。</p>` : ''}
  </div>`;
}

/** 締切前の1行 */
function openRow({ stadium, race }, day) {
  const min = minutesUntil(race.closed_at);
  const urgent = min <= 5;
  let time;
  if (min >= 60) {
    time = html`<span class="rr-left">締切</span><span class="rr-countdown rr-countdown--clock">${fmtClock(race.closed_at)}</span><span class="rr-clock">${min}分後</span>`;
  } else if (min <= 1 && (closeMs(race) - nowMs()) < 60000) {
    time = html`<span class="rr-left">締切</span><span class="rr-countdown" style="font-size:var(--fs-l)">まもなく</span><span class="rr-clock">${fmtClock(race.closed_at)}</span>`;
  } else {
    time = html`<span class="rr-left">あと</span><span class="rr-countdown">${min}<small>分</small></span><span class="rr-clock">${fmtClock(race.closed_at)}</span>`;
  }
  const p = race.prediction;
  let pred;
  if (!p) {
    pred = html`<span class="chip chip--none">予想なし</span>`;
  } else {
    const fav = favoriteBoat(p);
    // 点数・見送りは今の設定で計算した値(既定のままなら export の recommended)
    const rec = raceRecommendation(race, day);
    const recs = rec.bets;
    const rank = dayHasOdds(day) && recs.length ? evRankBadge(bestRank(recs), { sm: true }) : '';
    pred = html`<div class="rr-fav">${fav ? boat(fav.entry) : ''}<span class="rr-pwin">${fav ? `${pctInt(fav.p_win)}%` : '—'}</span></div>
      <div class="rr-meta">${conf(p.confidence)}${recs.length ? html`<span class="chip">${recs.length}点</span>` : html`<span class="chip chip--skip">見送り</span>`}${rank}</div>`;
  }
  return html`<a class="${cls('rcard rcard--row race-row', urgent && 'is-soon')}" href="#/race/${race.race_id}">
    <div class="rr-time">${time}</div>
    <div class="rr-main"><div class="rr-title">${stadium.stadium_name} <span class="rno">${race.race_number}R</span></div><div class="rr-sub">${raceSubText(race, stadium)}</div></div>
    <div class="rr-pred">${pred}</div>
  </a>`;
}

/** 締切後の行に出す、本命の艇と予想1着率。winner があれば本命が1着だったかを ✓/✕ で添える */
function favPred(race, winner) {
  const fav = race.prediction ? favoriteBoat(race.prediction) : null;
  if (!fav) return '';
  let mark = '';
  let actual = '';
  if (winner != null) {
    if (winner === fav.entry) {
      mark = html`<span class="rr-favres rr-favres--ok">✓当たり</span>`;
    } else {
      mark = html`<span class="rr-favres">✕外れ</span>`;
      // 実際に1着になった艇を、モデルが何%と見ていたか
      const w = (race.prediction.boats || []).find((b) => b.entry === winner);
      if (w) actual = html`<span class="rr-actual">1着${boat(winner, { size: 'sm' })}は予想${pctInt(w.p_win)}%</span>`;
    }
  }
  return html`<div class="rr-fav rr-fav--closed"><span class="rr-favlabel">本命</span>${boat(fav.entry, { size: 'sm' })}<span class="rr-pwin">${pctInt(fav.p_win)}%</span>${mark}</div>${actual}`;
}

/** 締切後(結果待ち・結果あり・中止)の1行 */
function closedRow({ stadium, race }, day) {
  const status = raceStatus(race);
  const canceled = status === 'canceled';
  let pred;
  if (canceled) {
    pred = html`<span class="chip chip--cancel">中止</span><span class="rr-note">返還</span>`;
  } else if (status === 'wait') {
    const recs = race.prediction ? raceRecommendation(race, day).bets : [];
    pred = html`${favPred(race)}<div class="rr-meta"><span class="chip chip--wait">結果待ち</span><span class="rr-note">${race.prediction ? (recs.length ? `${recs.length}点` : '見送り') : '予想なし'}</span></div>`;
  } else {
    const kind = outcomeKind(race);
    const o = race.result.recommended_outcome;
    const absent = race.result.absent_entries && race.result.absent_entries.length;
    let meta;
    if (kind === 'hit') meta = html`<span class="res res--hit">的中</span><span class="rr-return">${fmtYen(o.return)}</span>`;
    else if (kind === 'refund') meta = html`<span class="res res--refund">返還</span><span class="rr-note">${fmtYen(o.return)}</span>`;
    else if (kind === 'miss') meta = html`<span class="res res--miss">不的中</span>`;
    else if (kind === 'skip') meta = html`<span class="chip chip--skip">見送り</span>`;
    else meta = html`<span class="chip chip--none">予想なし</span>`;
    pred = html`${favPred(race, race.result.order && race.result.order[0])}<div class="rr-result">${orderCombo(race.result.order)}</div><div class="rr-meta">${absent ? html`<span class="rr-note">欠場あり</span>` : ''}${meta}</div>`;
  }
  return html`<a class="${cls('rcard rcard--row race-row', canceled && 'is-closed', outcomeKind(race) === 'hit' && 'is-hit')}" href="#/race/${race.race_id}">
    <div class="rr-time"><span class="rr-closed">${canceled ? '—' : '済'}</span><span class="rr-clock">${fmtClock(race.closed_at)}</span></div>
    <div class="rr-main"><div class="rr-title">${stadium.stadium_name} <span class="rno">${race.race_number}R</span></div><div class="rr-sub">${raceSubText(race, stadium)}</div></div>
    <div class="rr-pred">${pred}</div>
  </a>`;
}

/** 締切順の表示 */
function byDeadline(day, { closedOpen, isToday, filter }) {
  const races = allRaces(day).filter(filter);
  // 中止は締切前でも締切済み側。予想なしは締切前に残す
  const upcoming = races.filter((x) => raceStatus(x.race) === 'open');
  const closed = races.filter((x) => raceStatus(x.race) !== 'open');
  upcoming.sort((a, b) => closeMs(a.race) - closeMs(b.race) || a.stadium.stadium_number - b.stadium.stadium_number);
  closed.sort((a, b) => closeMs(b.race) - closeMs(a.race) || a.stadium.stadium_number - b.stadium.stadium_number);
  const open = closedOpen ?? upcoming.length === 0;
  const out = [];
  if (upcoming.length) {
    out.push(html`<div class="sechead">締切前 <span class="count">${upcoming.length}R</span>${isCustomSettings() && !isDefaultSelection(getSettings()) ? html`<span class="custom-tag">あなたの設定</span>` : ''}<span class="right">${isToday ? `現在 ${fmtClock(new Date(nowMs()).toISOString())}` : ''}</span></div>`);
    out.push(html`<div class="rlist list">${upcoming.map((x) => openRow(x, day))}</div>`);
  }
  if (closed.length) {
    const nResult = closed.filter((x) => raceStatus(x.race) === 'result').length;
    const nWait = closed.filter((x) => raceStatus(x.race) === 'wait').length;
    const nCancel = closed.filter((x) => raceStatus(x.race) === 'canceled').length;
    const counts = [`結果 ${nResult}`, nWait ? `結果待ち ${nWait}` : '', nCancel ? `中止 ${nCancel}` : ''].filter(Boolean).join(' · ');
    out.push(html`<div class="sechead">締切済み <span class="count">${closed.length}R</span>${open ? html`<span class="right">新しい順</span>` : ''}</div>`);
    if (open) {
      out.push(html`<div class="rlist list" id="closed-list">${closed.map((x) => closedRow(x, day))}</div>`);
      if (upcoming.length) out.push(html`<button class="more" data-action="toggle-closed" aria-expanded="true" aria-controls="closed-list">締切済みをたたむ<span aria-hidden="true">▲</span></button>`);
    } else {
      out.push(html`<button class="more" data-action="toggle-closed" aria-expanded="false">締切済みのレースを表示(${counts})<span aria-hidden="true">▼</span></button>`);
    }
  }
  if (!races.length) out.push(emptyBox({ icon: '—', title: allRaces(day).length ? '絞り込みに合うレースがありません' : 'この日のレースはありません', text: allRaces(day).length ? '上の絞り込みを外すと、すべてのレースが出ます。' : '' }));
  return out;
}

/** 場ごとのマス */
function raceCell(race, { isNext }) {
  const status = raceStatus(race);
  const fav = favoriteBoat(race.prediction);
  let text;
  let done = true;
  let hit = false;
  let soon = false;
  if (status === 'canceled') text = '中止';
  else if (status === 'wait') text = race.prediction ? '待ち' : '予想なし';
  else if (status === 'result') {
    const kind = outcomeKind(race);
    text = { hit: '✓的中', refund: '返還', miss: '×', skip: '見送', none: '予想なし' }[kind];
    hit = kind === 'hit';
  } else {
    done = false;
    const min = minutesUntil(race.closed_at);
    if (!race.prediction) text = '予想なし';
    else if (isNext && min < 60) text = min <= 1 ? 'まもなく' : `あと${min}分`;
    else text = fmtClock(race.closed_at);
    soon = isNext && min <= 5;
  }
  const showBadge = fav && status !== 'canceled';
  return html`<a class="${cls('rcell', done && 'is-done', hit && 'is-hit', isNext && 'is-next', soon && 'is-soon')}" href="#/race/${race.race_id}" aria-label="${race.race_number}R ${text}">
    <span class="rcell__r">${race.race_number}R</span>${showBadge ? boat(fav.entry, { size: 'sm' }) : ''}<span class="rcell__s">${text}</span></a>`;
}

/** 場ごとの表示 */
function byStadium(day, { isToday, favoritesOnly, favorites }) {
  const stadiums = favoritesOnly ? day.stadiums.filter((st) => favorites.includes(st.stadium_number)) : day.stadiums;
  if (!stadiums.length) return emptyBox({ icon: '—', title: 'お気に入りの場は今日開催がありません', text: '絞り込みを外すと、すべての場が出ます。' });
  return stadiums.map((stadium) => {
    const upcoming = stadium.races.filter((r) => raceStatus(r) === 'open').sort((a, b) => closeMs(a) - closeMs(b));
    const next = isToday ? upcoming[0] : null;
    let right;
    if (next) {
      right = html`<span class="stadium__right">次 ${next.race_number}R ${fmtClock(next.closed_at)}</span>`;
    } else {
      const st = stadiumStats(stadium);
      right = st.stake > 0
        ? html`<span class="stadium__right is-stat">的中${st.hits} · <span class="${roiClass(st.roi)}">${fmtRoiMarked(st.roi, 0)}</span></span>`
        : '';
    }
    const num = String(stadium.stadium_number).padStart(2, '0');
    return html`<section class="stadium" id="st-${num}" aria-label="${stadium.stadium_name}">
      <div class="stadium__head"><span class="stadium__name">${stadium.stadium_name}</span>${gradeChip(stadium.grade)}<span class="stadium__day">${stadium.day_number === 1 ? '初日' : stadium.day_number ? `${stadium.day_number}日目` : ''}</span>${right}</div>
      ${stadium.title ? html`<div class="stadium__title">${stadium.title}</div>` : ''}
      <div class="stadium__grid">${stadium.races.map((r) => raceCell(r, { isNext: next && r.race_id === next.race_id }))}</div>
    </section>`;
  });
}

/** S1 本体 */
/** S1 の絞り込み(W-7): 堅いレースだけ・推奨ありだけ・お気に入りの場だけ */
function filterRow(display, mode) {
  const f = display.filters;
  const hasFav = display.favorites.length > 0;
  const chip = (key, label, disabled) => html`<button class="pick-chip" data-action="toggle-filter" data-filter="${key}" aria-pressed="${String(!!f[key] && !disabled)}" ${raw(disabled ? 'disabled' : '')}>${label}</button>`;
  return html`<div class="filters" role="group" aria-label="絞り込み">
    ${mode === 'deadline' ? chip('kata', '堅いだけ', false) : ''}
    ${mode === 'deadline' ? chip('recommended', '推奨ありだけ', false) : ''}
    ${chip('favorites', hasFav ? '★お気に入り' : '★お気に入り(設定で選ぶ)', !hasFav)}
  </div>`;
}

function raceFilter(day, display) {
  const f = display.filters;
  const fav = display.favorites;
  return ({ stadium, race }) => {
    if (f.favorites && fav.length && !fav.includes(stadium.stadium_number)) return false;
    if (f.kata && !(race.prediction && confidenceLevel(race.prediction.confidence) === 3)) return false;
    if (f.recommended && !(race.prediction && raceRecommendation(race, day).bets.length)) return false;
    return true;
  };
}

/** 場で絞る: 場のタブ + その場の1〜12R(締切前・締切後とも同じ行の形) */
function focusView(day, { focusStadium, marksOf }) {
  if (!day.stadiums.length) return emptyBox({ icon: '—', title: 'この日のレースはありません' });
  const st = day.stadiums.find((x) => x.stadium_number === focusStadium) || day.stadiums[0];
  const tabs = html`<div class="stabs" role="tablist" aria-label="場">${day.stadiums.map((x) => html`<button role="tab" data-action="focus-stadium" data-stadium="${x.stadium_number}" aria-selected="${String(x.stadium_number === st.stadium_number)}">${x.stadium_name}</button>`)}</div>`;
  const rows = st.races.map((race) => {
    const row = raceStatus(race) === 'open' ? openRow({ stadium: st, race }, day) : closedRow({ stadium: st, race }, day);
    const marks = marksOf ? marksOf(race.race_id) : null;
    return marks && Object.keys(marks).length ? raw(String(row).replace('<div class="rr-sub">', '<div class="rr-sub"><span class="markflag" title="自分の印あり">印</span> ')) : row;
  });
  return html`${tabs}<div class="sechead">${st.stadium_name} <span class="count">${st.races.length}R</span><span class="right">${[st.grade, st.title].filter(Boolean).join(' · ')}</span></div><div class="rlist list">${rows}</div>`;
}

export function homeView({ date, dayInfo, day, isToday, mode, closedOpen, busy, banners, focusStadium, marksOf }) {
  const display = getDisplay();
  const custom = isCustomSettings() && !isDefaultSelection(getSettings());
  const updated = day ? `更新 ${fmtUpdated(day.generated_at, date)}` : '';
  const pastBanner = !isToday
    ? html`<div class="banner banner--muted"><span class="banner__icon" aria-hidden="true">ℹ</span><div class="banner__body">過去の日付を表示しています。</div><a class="banner__action btn btn--small" href="#/">今日へ</a></div>`
    : '';
  return html`
    ${homeAppbar({ date, dayStatus: (day && day.status) || (dayInfo && dayInfo.status), updatedText: updated, busy, showStatus: !isToday })}
    ${banners}
    ${pastBanner}
    ${daySummary(day)}
    ${favBands(day)}
    ${custom && day.day_summary ? html`<p class="disclaimer" style="margin-top:4px">集計は既定の買い方(${strategyLabel(day.strategy)})。一覧の点数は<a href="#/settings">あなたの設定</a>で計算しています。</p>` : ''}
    <div class="seg seg--3" role="tablist" aria-label="表示の切り替え">
      <button role="tab" data-action="set-mode" data-mode="deadline" aria-selected="${String(mode === 'deadline')}">締切順</button>
      <button role="tab" data-action="set-mode" data-mode="stadium" aria-selected="${String(mode === 'stadium')}">場ごと</button>
      <button role="tab" data-action="set-mode" data-mode="focus" aria-selected="${String(mode === 'focus')}">場で絞る</button>
    </div>
    ${mode === 'focus' ? '' : filterRow(display, mode)}
    ${mode === 'focus' ? focusView(day, { focusStadium, marksOf }) : mode === 'stadium' ? byStadium(day, { isToday, favoritesOnly: display.filters.favorites && display.favorites.length > 0, favorites: display.favorites }) : byDeadline(day, { closedOpen, isToday, filter: raceFilter(day, display) })}
    ${dayHasOdds(day) && mode === 'deadline' ? html`<p class="disclaimer">右端の A〜D = そのレースの推奨で最も高い期待値ランク(「–」はオッズ未取得)。締切前のオッズは締切までに変わります。</p>` : ''}
    ${disclaimer()}
    ${tabbar('home')}`;
}

/** 今日のデータがまだ無い(design/screens README 7.4) */
export function noTodayView({ today, latestDate, index, busy, banners }) {
  return html`
    ${homeAppbar({ date: today, updatedText: index.generated_at ? `更新 ${fmtUpdated(index.generated_at, today)}` : '', busy })}
    ${banners}
    ${emptyBox({
      icon: '◷',
      title: '今日の予想はまだありません',
      text: raw(`予想は前日の夜〜当日の朝に作成します。${latestDate ? `<br>最新のデータは ${fmtMonthDayWeek(latestDate)}のものです。` : ''}`),
      action: latestDate ? html`<a class="btn" href="#/date/${latestDate}">${fmtMonthDayWeek(latestDate)}の結果を見る</a>` : '',
    })}
    ${disclaimer()}
    ${tabbar('home')}`;
}

/** 日のデータが読めない・日付が無い */
export function homeErrorView({ date, busy, banners, box }) {
  return html`
    ${homeAppbar({ date, busy })}
    ${banners}
    ${box}
    ${tabbar('home')}`;
}
