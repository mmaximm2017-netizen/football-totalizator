(function () {
    'use strict';

    const card = document.querySelector('[data-web-push]');
    if (!card) return;

    const message = card.querySelector('[data-push-message]');
    const enableButton = card.querySelector('[data-push-enable]');
    const disableButton = card.querySelector('[data-push-disable]');
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

    function setMessage(text) {
        message.textContent = text;
    }

    function setState(state) {
        enableButton.hidden = state !== 'enable';
        disableButton.hidden = state !== 'disable';
        if (state === 'active') setMessage('Уведомления включены');
        if (state === 'enable') setMessage('Включите уведомления, чтобы получать важные сообщения ТОТИШа');
        if (state === 'denied') setMessage('Уведомления заблокированы в настройках браузера');
        if (state === 'unsupported') setMessage('Уведомления не поддерживаются этим браузером');
        if (state === 'ios-guidance') setMessage('Чтобы включить уведомления на iPhone, добавьте ТОТИШ на экран «Домой» и откройте его как приложение.');
        if (state === 'error') setMessage('Не удалось настроить уведомления. Попробуйте ещё раз.');
    }

    function isIos() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent)
            || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    }

    function isStandalone() {
        return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    }

    function base64UrlToUint8Array(value) {
        const padding = '='.repeat((4 - (value.length % 4)) % 4);
        const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
        const raw = window.atob(base64);
        return Uint8Array.from(Array.from(raw, character => character.charCodeAt(0)));
    }

    async function responseJson(response) {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) throw new Error(data.error || 'request_failed');
        return data;
    }

    async function getRegistration() {
        if (!('serviceWorker' in navigator)) throw new Error('service_worker_unsupported');
        return navigator.serviceWorker.ready;
    }

    async function currentSubscription() {
        const registration = await getRegistration();
        return registration.pushManager.getSubscription();
    }

    async function syncSubscription(subscription) {
        const json = subscription.toJSON();
        const response = await fetch(card.dataset.subscribeUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys })
        });
        return responseJson(response);
    }

    async function enable() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            setState('unsupported');
            return;
        }
        if (isIos() && !isStandalone()) {
            setState('ios-guidance');
            return;
        }

        enableButton.disabled = true;
        setMessage('Подключаем уведомления…');
        try {
            const permission = await Notification.requestPermission();
            if (permission === 'denied') {
                setState('denied');
                return;
            }
            if (permission !== 'granted') {
                setState('enable');
                return;
            }

            const registration = await getRegistration();
            const vapidResponse = await fetch(card.dataset.vapidUrl, { credentials: 'same-origin' });
            const vapid = await responseJson(vapidResponse);
            let subscription = await registration.pushManager.getSubscription();
            if (!subscription) {
                subscription = await registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: base64UrlToUint8Array(vapid.public_key)
                });
            }
            await syncSubscription(subscription);
            setState('active');
        } catch (error) {
            setState('error');
        } finally {
            enableButton.disabled = false;
        }
    }

    async function disable() {
        disableButton.disabled = true;
        setMessage('Отключаем уведомления…');
        try {
            const subscription = await currentSubscription();
            if (subscription) {
                const response = await fetch(card.dataset.unsubscribeUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    body: JSON.stringify({ endpoint: subscription.endpoint })
                });
                await responseJson(response);
                await subscription.unsubscribe();
            }
            setState('enable');
        } catch (error) {
            setState('error');
        } finally {
            disableButton.disabled = false;
        }
    }

    async function initialize() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            setState('unsupported');
            return;
        }
        if (isIos() && !isStandalone()) {
            setState('ios-guidance');
            return;
        }
        if (Notification.permission === 'denied') {
            setState('denied');
            return;
        }
        try {
            setState(await currentSubscription() ? 'active' : 'enable');
        } catch (error) {
            setState('error');
        }
    }

    enableButton.addEventListener('click', enable);
    disableButton.addEventListener('click', disable);
    initialize();
})();
