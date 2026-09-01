document.addEventListener('DOMContentLoaded', function() {
    if (window.location.pathname !== '/table') return;

    const tableContainer = document.querySelector('.table-container');

    function setTidOnLink(link, selectedTid) {
        if (!link || !selectedTid) return;

        const url = new URL(link.getAttribute('href') || '', window.location.origin);
        const syncedPaths = new Set(['/', '/table', '/profile']);

        if (!syncedPaths.has(url.pathname)) return;

        url.searchParams.set('tid', selectedTid);
        link.setAttribute('href', url.pathname + '?' + url.searchParams.toString() + url.hash);
    }

    function updateTournamentLinks(selectedTid) {
        if (!selectedTid) return;

        document.querySelectorAll('.bottom-nav a[href]').forEach(function(link) {
            setTidOnLink(link, selectedTid);
        });
    }

    async function loadTableByTid(tid) {
        if (!tableContainer || !tid) return;

        tableContainer.innerHTML = '<div style="text-align:center;padding:28px;color:#5d7894;font-weight:700;">Загрузка...</div>';

        try {
            const response = await fetch('/table?tid=' + encodeURIComponent(tid) + '&ajax=1', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });

            const data = await response.json();
            tableContainer.innerHTML = data.html || '';
            if (data.tid) {
                localStorage.setItem('selected_tid', String(data.tid));
            }
            if (data.tournament_name) {
                localStorage.setItem('selected_tournament_name', data.tournament_name);
            }

            const body = document.body;
            if (body) {
                body.classList.remove('tournament-cup', 'tournament-wc2026', 'tournament-rpl', 'tournament-rcup');
                if (data.tournament_key === 'wc2026') {
                    body.classList.add('tournament-wc2026');
                } else if (data.tournament_key === 'rpl') {
                    body.classList.add('tournament-rpl');
                } else if (data.tournament_name === 'Кубок России') {
                    body.classList.add('tournament-rcup');
                } else {
                    body.classList.add('tournament-cup');
                }
            }

            const trigger = document.querySelector('.tournament-trigger');
            if (trigger && data.tournament_name) {
                trigger.textContent = '🏆 ' + data.tournament_name + ' ';
                if (data.tournament_is_active === false) {
                    const badge = document.createElement('span');
                    badge.className = 'archive-badge';
                    badge.textContent = 'Архив';
                    trigger.appendChild(badge);
                    trigger.appendChild(document.createTextNode(' '));
                }
                trigger.appendChild(document.createTextNode('▼'));
            }

            const wcHeader = document.getElementById('wc-standings-header');
            const cupHeader = document.getElementById('cup-standings-header');
            const rplHeader = document.getElementById('rpl-standings-header');
            const rcupHeader = document.getElementById('rcup-standings-header');

            if (data.tournament_key === 'wc2026') {
                const currentHeader = cupHeader || rplHeader || rcupHeader;
                if (currentHeader) {
                    currentHeader.outerHTML = `
<div id="wc-standings-header" style="display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 16px;">
    <img class="wc-trophy-table-bg" src="/static/clubs/WorldCup.png" alt="" aria-hidden="true">
    <img src="/static/clubs/WorldCup.png" style="width: 48px; height: 48px; opacity: 0.9;" alt="Кубок мира">
    <div style="text-align: left;">
        <div style="color: #ffffff; font-size: 20px; font-weight: 700; text-shadow: 0 1px 2px rgba(0,0,0,0.3);">Турнирная таблица</div>
        <div style="color: #ffffff; font-size: 16px; font-weight: 600; text-shadow: 0 1px 2px rgba(0,0,0,0.3);">чемпионата мира 2026</div>
    </div>
</div>`;
                }
            } else if (data.tournament_key === 'rpl') {
                const currentHeader = wcHeader || cupHeader || rcupHeader;
                if (currentHeader) {
                    currentHeader.outerHTML = `
<div id="rpl-standings-header" style="display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 16px;">
    <img src="/static/clubs/russian-premier-league-footballlogos-org.png" style="width: 48px; height: 48px; object-fit: contain; opacity: 0.94;" alt="РПЛ">
    <div style="text-align: left;">
        <div style="font-size: 20px; font-weight: 700; color: #ffffff;">Турнирная таблица</div>
        <div style="font-size: 16px; font-weight: 600; color: #7fd3ff;">чемпионата России</div>
    </div>
</div>`;
                }
            } else if (data.tournament_name === 'Кубок России') {
                const currentHeader = wcHeader || rplHeader || cupHeader;
                if (currentHeader) {
                    currentHeader.outerHTML = `
<div id="rcup-standings-header">
    <img src="/static/clubs/Fonbet_Russian_Cup.png" alt="Кубок России">
    <div>
        <div>Турнирная таблица</div>
        <div>Кубок России</div>
    </div>
</div>`;
                }
            } else {
                const currentHeader = wcHeader || rplHeader || rcupHeader;
                if (currentHeader) {
                    currentHeader.outerHTML = `
<div id="cup-standings-header" style="display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 16px;">
    <img src="/static/clubs/MatchTV.png" style="width: 48px; height: 48px; object-fit: contain; opacity: 0.9;" alt="MatchTV">
    <div style="text-align: left;">
        <div style="font-size: 20px; font-weight: 700; color: #1a1a2e;">Турнирная таблица</div>
        <div style="font-size: 16px; font-weight: 600; color: #1a1a2e;">кубка Матч-Премьер</div>
    </div>
</div>`;
                }
            }

            const nextTid = data.tid || tid;
            history.pushState({ tid: nextTid }, '', '/table?tid=' + encodeURIComponent(nextTid));
            updateTournamentLinks(String(nextTid));

            document.querySelectorAll('.tournament-item').forEach(function(item) {
                const itemTid = item.getAttribute('data-tid')
                    || new URL(item.href, window.location.origin).searchParams.get('tid');
                item.classList.toggle('active', String(itemTid) === String(nextTid));
            });
            localStorage.setItem('selected_tournament_active', data.tournament_is_active === false ? '0' : '1');
        } catch (error) {
            console.error('Ошибка загрузки таблицы:', error);
            tableContainer.innerHTML = '<div style="text-align:center;padding:28px;color:#c62828;font-weight:700;">Ошибка загрузки. Обновите страницу.</div>';
        }
    }

    document.querySelectorAll('[data-ajax-table]').forEach(function(link) {
        link.addEventListener('click', function(e) {
            const href = link.getAttribute('href') || '';
            if (!href.includes('/table?tid=')) return;
            e.preventDefault();

            const parsed = new URL(href, window.location.origin);
            const tid = parsed.searchParams.get('tid');
            loadTableByTid(tid);

            const overlay = document.querySelector('[data-tournament-sheet-overlay]');
            const panel = document.querySelector('[data-tournament-sheet-panel]');
            if (overlay) overlay.classList.remove('open');
            if (panel) {
                panel.classList.remove('open');
                panel.setAttribute('aria-hidden', 'true');
            }
        });
    });
});

