const CACHE_PREFIX = 'totish-static-';
const CACHE_NAME = `${CACHE_PREFIX}__TOTISH_RELEASE__`;
const LEGACY_CACHE_NAMES = new Set([
    'totish-cache-v5',
]);

self.addEventListener('install', event => {
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(unsubscribeFromPush());
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

async function unsubscribeFromPush() {
    try {
        const subscription = await self.registration.pushManager?.getSubscription();
        if (subscription) await subscription.unsubscribe();
    } catch (error) {
        console.warn('Could not remove previous push subscription', error);
    }
}
