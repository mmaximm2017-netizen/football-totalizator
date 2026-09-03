const CACHE_PREFIX = 'totish-static-';
const CACHE_NAME = `${CACHE_PREFIX}__TOTISH_RELEASE__`;
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

function safeInternalUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return '/';

    try {
        const parsed = new URL(value, self.location.origin);
        if (parsed.origin !== self.location.origin) return '/';
        if (!parsed.pathname.startsWith('/')) return '/';
        return parsed.pathname + parsed.search + parsed.hash;
    } catch (error) {
        return '/';
    }
}

self.addEventListener('push', event => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
        if (!data || typeof data !== 'object' || Array.isArray(data)) data = {};
    } catch (error) {
        data = {};
    }

    const title = typeof data.title === 'string' && data.title.trim()
        ? data.title
        : 'ТОТИШ';

    const options = {
        body: typeof data.body === 'string' ? data.body : '',
        icon: '/static/icon-192-new.png',
        badge: '/static/notification-badge.png?v=20260815-circular',
        tag: typeof data.tag === 'string' && data.tag.trim() ? data.tag : 'totish-default',
        vibrate: [200, 100, 200],
        data: safeInternalUrl(data.url)
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();

    const url = safeInternalUrl(event.notification.data);

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            const sameOriginClient = windowClients.find(client => {
                try {
                    return new URL(client.url).origin === self.location.origin;
                } catch (error) {
                    return false;
                }
            });

            if (sameOriginClient && 'focus' in sameOriginClient) {
                if (sameOriginClient.url !== new URL(url, self.location.origin).href && 'navigate' in sameOriginClient) {
                    return sameOriginClient.navigate(url).then(client => client.focus());
                }
                return sameOriginClient.focus();
            }

            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
