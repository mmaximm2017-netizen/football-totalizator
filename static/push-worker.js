// push-worker.js - push notification handler
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'TOTISH BRATISHEK';
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

// On notification click, open app URL
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
