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
