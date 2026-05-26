(function () {
    const splash = document.getElementById('app-splash');
    if (!splash) return;

    function hideSplash() {
        splash.classList.add('hidden');
        splash.addEventListener('transitionend', function () {
            if (splash.classList.contains('hidden')) {
                splash.style.display = 'none';
            }
        }, { once: true });
    }

    if (document.readyState === 'complete') {
        hideSplash();
        return;
    }

    window.addEventListener('load', hideSplash);
})();
