(function () {
    'use strict';

    const card = document.querySelector('[data-web-push]');
    if (!card) return;

    const message = card.querySelector('[data-push-message]');
    const toggle = card.querySelector('[data-push-toggle]');
    const testButton = card.querySelector('[data-push-test]');
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    let state = 'inactive';
    let busy = false;

    function setMessage(text) {
        message.textContent = text;
    }

    function setState(nextState, customMessage) {
        state = nextState;
        toggle.checked = nextState === 'active';
        toggle.disabled = busy || ['unsupported', 'denied', 'ios-guidance'].includes(nextState);
        testButton.hidden = nextState !== 'active';
        if (customMessage) {
            setMessage(customMessage);
            return;
        }
        if (nextState === 'active') setMessage('Уведомления включены');
        if (nextState === 'inactive') setMessage('Уведомления выключены');
        if (state === 'denied') setMessage('Уведомления заблокированы в настройках браузера');
        if (nextState === 'unsupported') setMessage('Уведомления не поддерживаются этим браузером');
        if (nextState === 'ios-guidance') setMessage('Чтобы включить уведомления на iPhone, добавьте ТОТИШ на экран «Домой» и откройте его как приложение.');
    }

    function setBusy(value) {
        busy = value;
        card.classList.toggle('is-busy', value);
        toggle.disabled = value || ['unsupported', 'denied', 'ios-guidance'].includes(state);
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

        setBusy(true);
        setMessage('Подключаем уведомления…');
        try {
            if (Notification.permission === 'denied') {
                setState('denied');
                return;
            }
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
            setState('inactive', 'Не удалось включить уведомления. Попробуйте ещё раз.');
        } finally {
            setBusy(false);
        }
    }

    async function disable() {
        setBusy(true);
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
            setState('inactive');
        } catch (error) {
            setState('active', 'Не удалось отключить уведомления. Попробуйте ещё раз.');
        } finally {
            setBusy(false);
        }
    }

    async function sendTest() {
        testButton.disabled = true;
        setMessage('Отправляем тестовое уведомление…');
        try {
            const response = await fetch(card.dataset.testUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken
                },
                body: '{}'
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) {
                const error = new Error(data.error || 'request_failed');
                error.retryAfter = data.retry_after;
                throw error;
            }
            setMessage('Тестовое уведомление отправлено');
        } catch (error) {
            if (error.message === 'test_push_cooldown') {
                setMessage('Подождите немного перед повторной отправкой');
            } else if (error.message === 'no_active_subscription') {
                setState('inactive');
                setMessage('Сначала включите уведомления');
            } else {
                setMessage('Не удалось отправить тестовое уведомление. Попробуйте ещё раз.');
            }
        } finally {
            testButton.disabled = false;
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
            setState(await currentSubscription() ? 'active' : 'inactive');
        } catch (error) {
            setState('inactive', 'Не удалось проверить состояние уведомлений.');
        }
    }

    toggle.addEventListener('change', () => {
        if (busy) return;
        if (toggle.checked) {
            enable();
        } else {
            disable();
        }
    });
    testButton.addEventListener('click', sendTest);
    initialize();
})();
