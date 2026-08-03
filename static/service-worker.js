// キャッシュ名を更新（例: v1 -> v2）
const CACHE_NAME = 'tomato-app-v2';

const FILES_TO_CACHE = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/icon.png',
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