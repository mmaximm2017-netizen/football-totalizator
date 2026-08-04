const CACHE_PREFIX = 'totish-static-';
const CACHE_NAME = `${CACHE_PREFIX}v6`;
const LEGACY_CACHE_NAMES = new Set([
    'totish-cache-v5',
]);

self.addEventListener('install', event => {
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys
                    .filter(key =>
                        LEGACY_CACHE_NAMES.has(key) ||
                        (key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
                    )
                    .map(key => caches.delete(key))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;

    const url = new URL(request.url);

    if (
        request.method !== 'GET' ||
        request.mode === 'navigate' ||
        url.origin !== location.origin ||
        url.pathname === '/login' ||
        url.pathname === '/logout' ||
        url.pathname === '/admin' ||
        url.pathname.startsWith('/admin/') ||
        url.pathname === '/api' ||
        url.pathname.startsWith('/api/') ||
        !url.pathname.startsWith('/static/')
    ) {
        return;
    }

    event.respondWith(
        fetch(request)
            .then(response => {
                if (response.ok) {
                    event.waitUntil(
                        caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone()))
                    );
                }
                return response;
            })
            .catch(async () => {
                const cache = await caches.open(CACHE_NAME);
                const cachedResponse = await cache.match(request);
                return cachedResponse || Response.error();
            })
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
