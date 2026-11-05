// 数値・ラベルの表示形式と、JSON の値の読み替え

export const BET_TYPE_LABELS = {
  win: '単勝',
  place: '複勝',
  exacta: '2連単',
  quinella: '2連複',
  quinella_place: '拡連複',
  trifecta: '3連単',
  trio: '3連複',
};

export function betTypeLabel(betType) {
  return BET_TYPE_LABELS[betType] || betType || '';
}

/** 確率 → '3.1%'(digits 桁) */
export function fmtPct(p, digits = 1) {
  if (p === null || p === undefined || Number.isNaN(p)) return '—';
  return `${(p * 100).toFixed(digits)}%`;
}

/** 組合せの確率: 1%以上は小数1桁、1%未満は小数2桁(design/screens README 3.3) */
export function fmtComboPct(p) {
  if (p === null || p === undefined || Number.isNaN(p)) return '—';
  return fmtPct(p, p < 0.01 ? 2 : 1);
}

/** 確率 → 整数の % の数字だけ('19') */
export function pctInt(p) {
  if (p === null || p === undefined || Number.isNaN(p)) return '—';
  return String(Math.round(p * 100));
}

/** 金額 → '2,750' */
export function fmtNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('ja-JP');
}

/** 金額 → '2,750円' */
export function fmtYen(n) {
  return `${fmtNumber(n)}円`;
}

/** 収支 → '+2,250円' / '−500円'(マイナスは全角のマイナス記号) */
export function fmtSignedYen(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  if (n > 0) return `+${fmtNumber(n)}円`;
  if (n < 0) return `−${fmtNumber(-n)}円`;
  return '±0円';
}

/** 回収率 → '84.0%' */
export function fmtRoi(roi, digits = 1) {
  if (roi === null || roi === undefined || Number.isNaN(roi)) return '—';
  return `${(roi * 100).toFixed(digits)}%`;
}

/** 回収率 → 100%以上は '▲108.7%'、未満は '84.0%'(色と併用する記号) */
export function fmtRoiMarked(roi, digits = 1) {
  return `${roiMark(roi)}${fmtRoi(roi, digits)}`;
}

/** 回収率の増減クラスと、プラスのときの記号(色だけに頼らない) */
export function roiClass(roi) {
  if (roi === null || roi === undefined || Number.isNaN(roi)) return '';
  return roi >= 1 ? 'is-plus' : 'is-minus';
}
export function roiMark(roi) {
  return roi !== null && roi !== undefined && roi >= 1 ? '▲' : '';
}

/** オッズ → '32.3' */
export function fmtOdds(odds) {
  if (odds === null || odds === undefined || Number.isNaN(odds)) return '—';
  const n = Number(odds);
  // 1000倍以上は整数+桁区切り(design/screens README 3.3)
  if (n >= 1000) return Math.round(n).toLocaleString('ja-JP');
  return n.toFixed(1);
}

/** 期待値 → 小数2桁。1.00以上は '▲ 1.20' */
export function fmtEv(ev) {
  if (ev === null || ev === undefined || Number.isNaN(ev)) return '—';
  return ev >= 1 ? `▲ ${ev.toFixed(2)}` : ev.toFixed(2);
}

/** 組合せ '2-1-6' / '1=2=3' → { entries: [2,1,6], sep: '-' } */
export function parseCombination(combination) {
  if (combination === null || combination === undefined) return { entries: [], sep: '-' };
  const text = String(combination);
  const sep = text.includes('=') ? '=' : '-';
  const entries = text.split(sep).map((x) => Number(x));
  return { entries, sep };
}

/** 堅さ(confidence)の3段階。しきい値は PM 決定(堅 ≥ 0.50、中 0.35〜0.50、荒 < 0.35)。フロント側で持つ */
export const CONFIDENCE_THRESHOLDS = { high: 0.5, mid: 0.35 };
export function confidenceLevel(confidence) {
  if (confidence === null || confidence === undefined) return 0;
  if (confidence >= CONFIDENCE_THRESHOLDS.high) return 3;
  if (confidence >= CONFIDENCE_THRESHOLDS.mid) return 2;
  return 1;
}
export const CONFIDENCE_SHORT = { 1: '荒', 2: '中', 3: '堅' };
export const CONFIDENCE_LONG = { 1: '荒れ気味', 2: '普通', 3: '堅い' };

/** p_rank → 印(◎○▲△、5位以下は無印) */
export const MARKS = { 1: '◎', 2: '○', 3: '▲', 4: '△' };

/** 何日目 → '初日' / '3日目' */
export function fmtDayNumber(n) {
  if (!n) return '';
  return n === 1 ? '初日' : `${n}日目`;
}

/** index.json の status → 表示 */
export const DAY_STATUS_LABELS = { predicted: '予想のみ', in_progress: '途中', final: '確定' };

/**
 * 買い方(戦略)の表示名。PM決定により戦略名はハードコードせず、short_label → description の順に使う。
 * description が無いもの(performance.json の文字列・backtest の {name, params})は、
 * 名前と引数をそのまま並べる(券種キーだけ日本語にする)。例: 'top_k(3連単, k=5)'
 */
export function strategyLabel(strategy) {
  if (!strategy) return '';
  if (typeof strategy === 'object' && strategy.short_label) return strategy.short_label;
  if (typeof strategy === 'object' && strategy.description) return strategy.description;
  const s = normalizeStrategy(strategy);
  if (!s) return '';
  const args = [];
  if (s.bet_type) args.push(betTypeLabel(s.bet_type));
  Object.keys(s).forEach((key) => {
    if (['name', 'bet_type', 'description'].includes(key)) return;
    args.push(`${key}=${s[key]}`);
  });
  return args.length ? `${s.name}(${args.join(', ')})` : s.name;
}

/** 戦略の表し方をそろえる: {name, bet_type, k, ...} */
export function normalizeStrategy(strategy) {
  if (!strategy) return null;
  if (typeof strategy === 'string') {
    const m = strategy.match(/^([a-z0-9_]+)\((.*)\)$/i);
    if (!m) return { name: strategy };
    const out = { name: m[1] };
    m[2].split(',').map((x) => x.trim()).filter(Boolean).forEach((part) => {
      const [key, value] = part.split('=');
      if (value === undefined) out.bet_type = key;
      else out[key] = Number.isNaN(Number(value)) ? value : Number(value);
    });
    return out;
  }
  if (strategy.params) return { name: strategy.name, ...strategy.params };
  const { description, short_label: shortLabel, ...rest } = strategy;
  return rest;
}

/** 2つの戦略が同じか(名前と引数をすべて比べる) */
export function sameStrategy(a, b) {
  const x = normalizeStrategy(a);
  const y = normalizeStrategy(b);
  if (!x || !y) return false;
  const keys = new Set([...Object.keys(x), ...Object.keys(y)]);
  return [...keys].every((key) => (x[key] ?? null) === (y[key] ?? null));
}
