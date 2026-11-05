// 画面モックの表示切替(モック専用。実装では使わない)
// ?state=<id>  : その状態だけを表示する(index.html の iframe から使う)
// ?theme=dark  : ダークテーマで表示する
// 引数なし     : 全状態を縦に並べて表示する(各状態の上にラベルが出る)
(function () {
  const params = new URLSearchParams(location.search);
  const root = document.documentElement;
  // モックは既定をライトに固定する(実装では端末設定に従う。tokens.css 参照)
  root.setAttribute('data-theme', params.get('theme') === 'dark' ? 'dark' : 'light');
  const stateId = params.get('state');
  const states = Array.from(document.querySelectorAll('.state'));
  if (stateId) {
    root.classList.add('mock-one');
    const target = states.find((s) => s.dataset.state === stateId) || states[0];
    target.classList.add('is-shown');
  } else {
    root.classList.add('mock-all');
    states.forEach((s) => {
      const label = document.createElement('div');
      label.className = 'mock-label';
      label.textContent = `state=${s.dataset.state} : ${s.dataset.title || ''}`;
      s.prepend(label);
    });
  }
  // モック内のリンクで画面が飛ばないようにする(ナビゲーション先は README を参照)
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (a) e.preventDefault();
  });
})();
