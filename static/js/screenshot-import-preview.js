(function () {
    function init(root) {
        root.addEventListener('click', function (event) {
            var button = event.target.closest('[data-import-toggle]');
            if (!button) return;
            var row = button.closest('[data-import-row]');
            var compact = row.querySelector('[data-import-compact]');
            var editor = row.querySelector('[data-import-editor]');
            var expanded = editor.hidden;
            editor.hidden = !expanded;
            compact.hidden = expanded;
            row.classList.toggle('is-collapsed', !expanded);
            button.setAttribute('aria-expanded', String(expanded));
        });
    }
    document.querySelectorAll('[data-import-row]').forEach(function (row) {
        var root = row.closest('[data-rpl-import-preview], .rc-import-preview');
        if (root && !root.dataset.importToggleReady) {
            root.dataset.importToggleReady = '1';
            init(root);
        }
    });
}());
