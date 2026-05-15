const CACHE_NAME = 'totish-cache-v3';
const urlsToCache = [
    '/static/manifest.json',
    '/static/icon-192-new.png',
    '/static/icon-512-new.png',
    '/static/push-worker.js'
];

// Установка и кеширование
self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
    );
});

// Активация — очистка старого кеша
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
});

// Перехват запросов
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request).then(resp => resp || fetch(event.request))
    );
});

// Push-уведомления
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'ТОТИШ БРАТИШЕК';
    const options = {
        body: data.body || '',
        icon: '/static/icon-192-new.png',
        badge: '/static/icon-192-new.png',
        tag: data.tag || 'default',
        vibrate: [200, 100, 200],
        data: data.url || '/'
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

// Клик по уведомлению
self.addEventListener('notificationclick', event => {
    event.notification.close();
    const url = event.notification.data || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(windowClients => {
            for (let client of windowClients) {
                if (client.url === url && 'focus' in client) return client.focus();
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});