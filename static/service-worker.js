// ==================================================
// service-worker.js ― PWA（ホーム画面に追加できる Web アプリ）用のサービスワーカー
// --------------------------------------------------
// 【やること】
//   install  : 下の FILES_TO_CACHE をブラウザのキャッシュに保存する
//   activate : 名前（CACHE_NAME）が違う古いキャッシュを削除する
//
// 【注意（現状の仕様）】
//   ・fetch イベントの処理が無いので、保存したキャッシュを画面表示には使っていない
//     （オフライン表示はしない。常にサーバーから最新を読む）。
//   ・どの HTML からも navigator.serviceWorker.register() されていないため、
//     現在このファイルはブラウザに登録されていない。
// ==================================================

// キャッシュ名を更新（JS/CSS を変えたら必ず番号を上げる）
const CACHE_NAME = 'tomato-app-v12'; // v12: 2026-11-16 仕様変更（品目固定・平年値/土壌校正/予約散水/自動読み上げ廃止・オンライン表示3分割）

const FILES_TO_CACHE = [
  '/',
  '/static/style.css',
  '/static/crop.js',
  '/static/reference.js',
  '/static/app.js',
  '/static/games.js',
  '/static/history.js',
  '/static/watering.js',
  '/static/detail.js',
  '/static/icon.png',
  '/static/icon_history.png',
  '/static/icon_camera.png',
  '/static/icon_watering.png',
  '/static/icon_ai.png',
  '/static/icon_clock.png',
  '/static/icon_tomato.png',
  '/static/icon_daikon.png',
  '/static/manifest.json'
];

// installイベントで旧キャッシュを無視して最新を取得
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(FILES_TO_CACHE);
    })
  );
});

// 新しいサービスワーカーが有効になったとき、古い名前のキャッシュを消してすぐ全ページを管理下に置く
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key); // 古いキャッシュを全削除
          }
        })
      );
    })
  );
  return self.clients.claim();
});
