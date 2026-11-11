// Service Worker(キャッシュ方針は spec/05_pwa.md 9章)
// - アプリ本体(HTML/CSS/JS/アイコン): キャッシュ優先。APP_VERSION を上げると入れ替わる
// - data/**/*.json・data-local/**/*.json: ネットワーク優先。失敗したらキャッシュを返し、応答ヘッダ X-SW-Cache: 1 を付ける
//   (画面はこのヘッダを見て「オフライン表示中(21:05時点)」を出す)
// - パスはすべてこのファイルの場所からの相対(GitHub Pages のサブパス /boatrace-yoso/ で動かすため)

const APP_VERSION = '0.12.2';
const APP_CACHE = `br-app-${APP_VERSION}`;
const DATA_CACHE = 'br-data-v1'; // app/data.js の DATA_CACHE と合わせる

const APP_FILES = [
  './',
  './index.html',
  './manifest.webmanifest',
  './app/main.js',
  './app/data.js',
  './app/format.js',
  './app/html.js',
  './app/model.js',
  './app/parts.js',
  './app/store.js',
  './app/time.js',
  './app/views/home.js',
  './app/views/race.js',
  './app/views/performance.js',
  './app/views/about.js',
  './app/views/settings.js',
  './app/strategy.js',
  './app/settings.js',
  './app/oddsfetch.js',
  './app/odds-ui.js',
  './app/localdb.js',
  './app/ledger.js',
  './app/wording.js',
  './app/charts.js',
  './app/betsum.js',
  './app/views/phase3b.js',
  './app/analytics.js',
  './app/views/analytics.js',
  './app/views/ledger.js',
  './app/styles/tokens.css',
  './app/styles/components.css',
  './app/styles/app.css',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-192.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon-180.png',
];

const SCOPE_URL = new URL('./', self.location.href);
const DATA_PREFIXES = [new URL('./data/', self.location.href).href, new URL('./data-local/', self.location.href).href];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then((cache) => cache.addAll(APP_FILES.map((p) => new Request(p, { cache: 'reload' }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('br-app-') && k !== APP_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== SCOPE_URL.origin) return; // 外部には何もしない

  if (DATA_PREFIXES.some((p) => url.href.startsWith(p)) && url.pathname.endsWith('.json')) {
    event.respondWith(networkFirst(req));
    return;
  }
  // Worker への要求(別オリジン)には何もしない(上で除外済み)。テストページはキャッシュしない
  if (url.pathname.includes('/app/tests/')) return;
  if (req.mode === 'navigate') {
    // ページの表示はネットワーク優先。Cloudflare Access のログイン画面への転送などは
    // ブラウザにそのまま任せる。通信できないときだけ、キャッシュの index.html を返す
    event.respondWith(fetch(req).catch(async () => {
      const cache = await caches.open(APP_CACHE);
      const hit = await cache.match(new URL('./index.html', SCOPE_URL).href, { ignoreSearch: true });
      return hit || Response.error();
    }));
    return;
  }
  event.respondWith(cacheFirst(req, req));
});

/** データ: ネットワーク優先、失敗したらキャッシュ(印のヘッダ付き) */
async function networkFirst(req) {
  const cache = await caches.open(DATA_CACHE);
  const key = req.url.split('?')[0];
  try {
    // Cloudflare Access のログインが切れると、ログイン画面への転送(302)になる。
    // 転送を追わずに受け取り、401 と印(X-SW-Auth)を返す(ページ側で「再ログイン」を出す)
    const res = await fetch(req, { cache: 'no-cache', redirect: 'manual' });
    if (isAuthResponse(res)) return authExpiredResponse();
    if (res.ok) await cache.put(key, res.clone());
    return res;
  } catch (e) {
    const cached = await cache.match(key);
    if (!cached) throw e;
    const headers = new Headers(cached.headers);
    headers.set('X-SW-Cache', '1');
    const body = await cached.blob();
    return new Response(body, { status: 200, statusText: 'OK', headers });
  }
}

/** ログイン切れの応答か(転送・401・403) */
function isAuthResponse(res) {
  return res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400) || res.status === 401 || res.status === 403;
}

function authExpiredResponse() {
  return new Response('{"error":"auth"}', { status: 401, headers: { 'Content-Type': 'application/json', 'X-SW-Auth': '1' } });
}

/** アプリ本体: キャッシュ優先、無ければネットワーク(取れたらキャッシュに入れる) */
async function cacheFirst(cacheKey, req) {
  const cache = await caches.open(APP_CACHE);
  const hit = await cache.match(cacheKey, { ignoreSearch: true });
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok && new URL(req.url).origin === SCOPE_URL.origin) cache.put(cacheKey, res.clone());
  return res;
}
