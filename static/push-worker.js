async function unsubscribeFromPush() {
    try {
        const subscription = await self.registration.pushManager?.getSubscription();
        if (subscription) await subscription.unsubscribe();
    } catch (error) {
        console.warn('Could not remove previous push subscription', error);
    }
}

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => {
    event.waitUntil(unsubscribeFromPush().then(() => self.registration.unregister()));
});
