const CACHE_NAME = 'totish-cache-v5';

const STATIC_ASSETS = [
    '/static/manifest.json',
    '/static/icon-192-new.png',
    '/static/icon-512-new.png',
    '/static/apple-touch-icon.png',
    '/static/push-worker.js'
];

self.addEventListener('install', event => {
    self.skipWaiting();

    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys
                    .filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;

    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    if (url.origin !== location.origin) {
        return;
    }

    if (request.mode === 'navigate') {
        event.respondWith(fetch(request));
        return;
    }

    event.respondWith(
        fetch(request)
            .then(response => {
                const responseClone = response.clone();

                if (
                    response.ok &&
                    (
                        url.pathname.startsWith('/static/icons/') ||
                        url.pathname.startsWith('/static/flags/') ||
                        url.pathname.endsWith('.css') ||
                        url.pathname.endsWith('.js') ||
                        url.pathname.endsWith('.png') ||
                        url.pathname.endsWith('.svg')
                    )
                ) {
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(request, responseClone);
                    });
                }

                return response;
            })
            .catch(() => caches.match(request))
    );
});

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

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();

    const url = event.notification.data || '/';

    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(windowClients => {
            for (let client of windowClients) {
                if (client.url === url && 'focus' in client) {
                    return client.focus();
                }
            }

            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
