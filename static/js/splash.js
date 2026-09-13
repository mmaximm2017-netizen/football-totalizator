(function () {
    const splash = document.getElementById('app-splash');
    if (!splash) return;

    let hidden = false;
    let errorLoggingInstalled = false;

    function installErrorLogging() {
        if (errorLoggingInstalled) return;
        errorLoggingInstalled = true;

        window.onerror = function (message, source, line, column, error) {
            console.error('[runtime-error]', { message, source, line, column, error });
        };

        window.onunhandledrejection = function (event) {
            console.error('[unhandled-rejection]', event.reason);
        };
    }

    function hideSplash() {
        if (hidden) return;
        hidden = true;
        splash.classList.add('hidden');
        installErrorLogging();
        splash.addEventListener('transitionend', function () {
            if (splash.classList.contains('hidden')) {
                splash.style.display = 'none';
            }
        }, { once: true });
    }

    if (document.readyState !== 'loading') {
        hideSplash();
        return;
    }

    document.addEventListener('DOMContentLoaded', hideSplash, { once: true });
    window.addEventListener('load', hideSplash, { once: true });
    window.setTimeout(hideSplash, 3000);
})();

(function () {
    const ANALYTICS_ENDPOINT = '/__analytics/event';
    const LOGIN_MARKER = 'totish_posthog_login_attempt';

    if (window.__totishPosthogInitialized) return;
    window.__totishPosthogInitialized = true;

    // Analytics goes to the same origin first. The Flask server forwards only a
    // strict allow-list of privacy-safe events/properties to PostHog. Prediction
    // scores, names, credentials, and other personal data are never submitted.
    function capture(eventName, properties) {
        try {
            const tokenMeta = document.querySelector('meta[name="csrf-token"]');
            const csrfToken = tokenMeta && tokenMeta.content;
            if (!csrfToken || !window.fetch) return;

            window.fetch(ANALYTICS_ENDPOINT, {
                method: 'POST',
                credentials: 'same-origin',
                keepalive: true,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    event: eventName,
                    properties: properties || {}
                })
            }).catch(function () {
                // Analytics must never affect the application experience.
            });
        } catch (error) {
            console.debug('[analytics-skipped]', eventName);
        }
    }

    function trackPageIntent() {
        const path = window.location.pathname;
        capture('$pageview', { pathname: path });

        if (path === '/login') {
            try {
                sessionStorage.removeItem(LOGIN_MARKER);
                const form = document.querySelector('form');
                if (form) {
                    form.addEventListener('submit', function () {
                        sessionStorage.setItem(LOGIN_MARKER, '1');
                    });
                }
            } catch (error) {
                // Analytics must never interfere with authentication.
            }
            return;
        }

        try {
            if (sessionStorage.getItem(LOGIN_MARKER) === '1') {
                sessionStorage.removeItem(LOGIN_MARKER);
                capture('login');
            }
        } catch (error) {
            // sessionStorage may be unavailable in hardened/private browsers.
        }

        if (/^\/profile(?:\/\d+)?(?:\/stats)?$/.test(path)) {
            capture('profile_viewed', {
                surface: path.includes('/stats') ? 'stats' : 'overview'
            });
        }

        if (
            path === '/my-predictions'
            || /^\/match\/\d+\/predictions$/.test(path)
            || /^\/profile\/\d+\/predictions$/.test(path)
        ) {
            let surface = 'match_predictions';
            if (path === '/my-predictions') surface = 'my_predictions';
            if (/^\/profile\/\d+\/predictions$/.test(path)) surface = 'public_profile_predictions';
            capture('results_viewed', { surface: surface });
        }
    }

    function installPredictionSuccessCapture() {
        if (!window.fetch || window.__totishPosthogFetchWrapped) return;
        window.__totishPosthogFetchWrapped = true;

        const originalFetch = window.fetch;
        window.fetch = function () {
            const args = arguments;
            const request = args[0];
            const options = args[1] || {};
            const method = String(
                options.method || (request && request.method) || 'GET'
            ).toUpperCase();

            return originalFetch.apply(this, args).then(function (response) {
                if (method !== 'POST' || !response || !response.ok) return response;

                let requestUrl;
                try {
                    requestUrl = new URL(
                        typeof request === 'string' ? request : request.url,
                        window.location.href
                    );
                } catch (error) {
                    return response;
                }

                if (requestUrl.origin !== window.location.origin || requestUrl.pathname !== '/') {
                    return response;
                }

                const contentType = response.headers.get('content-type') || '';
                if (!contentType.includes('application/json')) return response;

                response.clone().json().then(function (payload) {
                    if (!payload || payload.ok !== true || !payload.match_id) return;

                    capture('prediction_submitted', {
                        match_id: Number(payload.match_id)
                    });
                }).catch(function () {});

                return response;
            });
        };
    }

    trackPageIntent();
    installPredictionSuccessCapture();
})();
