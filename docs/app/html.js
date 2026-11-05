// HTML 文字列の組み立て。差し込む値は既定でエスケープし、raw() で包んだものだけをそのまま入れる。

class RawHtml {
  constructor(text) {
    this.text = text;
  }
  toString() {
    return this.text;
  }
}

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** エスケープせずに差し込む(自分で組み立てた HTML だけに使う) */
export function raw(text) {
  return new RawHtml(text);
}

function flatten(value) {
  if (value === null || value === undefined || value === false) return '';
  if (Array.isArray(value)) return value.map(flatten).join('');
  if (value instanceof RawHtml) return value.text;
  return escapeHtml(value);
}

/** タグ付きテンプレート: html`<b>${name}</b>` */
export function html(strings, ...values) {
  let out = '';
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) out += flatten(values[i]);
  });
  return new RawHtml(out);
}

/** 値を文字列の HTML にする(innerHTML に入れる直前に使う) */
export function toHtmlString(value) {
  return flatten(value);
}

/** class 属性用: 偽の値を除いて空白でつなぐ */
export function cls(...names) {
  return names.filter(Boolean).join(' ');
}
