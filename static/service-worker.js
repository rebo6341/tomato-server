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