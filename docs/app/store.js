// 端末ごとの表示の好み(localStorage)と、画面を描き直しても保ちたい一時的な状態。
// localStorage は使えないことがある(プライベートモードなど)ので、失敗しても動くようにする。

const PREFIX = 'br.';

export function getPref(key, fallback) {
  try {
    const value = localStorage.getItem(PREFIX + key);
    return value === null ? fallback : JSON.parse(value);
  } catch (e) {
    return fallback;
  }
}

export function setPref(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (e) {
    // 保存できなくても表示は続ける
  }
}

/** 画面の一時的な状態(リロードで消えてよいもの) */
export const session = {
  closedOpen: {}, // date -> 締切済みを開いているか
  rankMore: {}, // race_id -> 確率上位の11〜20位を出しているか
  folds: {}, // race_id:key -> details の開閉(選手データ以外)
  scroll: new Map(), // ハッシュ -> スクロール位置
  scrollToStadium: null, // S1 場ごとで、描画後にスクロールする場番号
  manualMode: {},
  betUnselected: {}, // race_id -> 買い目合計から外した '券種:組合せ'
  focusStadium: null, // S1 場で絞る: 場番号
  perfTab: 'live', // S3: live / backtest // race_id -> 推奨の行に手入力の欄を出しているか
  settingsUi: { editingKey: false, showKey: false, urlDraft: undefined, flowOpen: false }, // S6 の一時的な状態
};
