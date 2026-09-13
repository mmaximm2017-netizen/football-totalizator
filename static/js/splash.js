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
    const POSTHOG_PROJECT_KEY = 'phc_wsowVHb2m7SUGPrTPCca8Rp9f5PH8tQckdRgNGVoAn5A';
    const POSTHOG_API_HOST = 'https://us.i.posthog.com';
    const LOGIN_MARKER = 'totish_posthog_login_attempt';

    if (!POSTHOG_PROJECT_KEY || window.__totishPosthogInitialized) return;
    window.__totishPosthogInitialized = true;

    // PostHog project keys are public browser-side identifiers. Do not add any
    // personal data, prediction scores, credentials, or secret tokens here.
    !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split('.');2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement('script')).type='text/javascript',p.crossOrigin='anonymous',p.async=!0,p.src=s.api_host.replace('.i.posthog.com','-assets.i.posthog.com')+'/static/array.js',(r=t.getElementsByTagName('script')[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a='posthog',u.people=u.people||[],u.toString=function(t){var e='posthog';return'posthog'!==a&&(e+='.'+a),t||(e+=' (stub)'),e},u.people.toString=function(){return u.toString(1)+'.people (stub)'},o='init capture register register_once unregister get_distinct_id reset opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing'.split(' '),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

    posthog.init(POSTHOG_PROJECT_KEY, {
        api_host: POSTHOG_API_HOST,
        person_profiles: 'identified_only',
        capture_pageview: true,
        capture_pageleave: true,
        autocapture: false,
        disable_session_recording: true
    });

    function capture(eventName, properties) {
        try {
            posthog.capture(eventName, properties || {});
        } catch (error) {
            console.debug('[analytics-skipped]', eventName);
        }
    }

    function trackPageIntent() {
        const path = window.location.pathname;

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
