/* Telegram identity stays in memory and travels only in the Authorization header. */
(() => {
  'use strict';
  const tg = window.Telegram?.WebApp;
  const $ = id => document.getElementById(id);
  const titles = {ranking: 'Таблица', matches: 'Матчи', stats: 'Статистика', player: 'Участники'};
  let kind = 'rpl', tab = 'ranking', current = '', expect = null, history = [];
  let sequence = 0, controller = null, currentText = null, questionState = null, asking = false;
  const cache = new Map();
  let profileReturn = null;
  const hasAuth = Boolean(tg?.initData);
  let themePreference = 'light';
  try { if (localStorage.getItem('totish-mini-theme') === 'dark') themePreference = 'dark'; } catch { /* Appearance storage is optional. */ }
  const metrics = {
    points: ['chart', 'За день, тур или период'],
    round_wins: ['table', 'Кто чаще побеждает'],
    average: ['chart', 'Эффективность прогнозов'],
    hits: ['target', 'Попадания в точный счёт'],
    streak: ['streak', 'Матчи без точного счёта']
  };
  const metricTitles = {points: 'Очки', round_wins: 'Победы в турах', average: 'Средние очки', hits: 'Точные счета', streak: 'Серии без точного счёта'};

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function root() { return tab === 'matches' ? `round:${kind}:auto` : `ui:t:${tab}:${kind}`; }
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`); svg.setAttribute('aria-hidden', 'true');
    svg.append(use); return svg;
  }
  function theme() {
    const dark = themePreference === 'dark';
    document.documentElement.dataset.theme = themePreference;
    $('toggle-theme').setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
    $('toggle-theme').setAttribute('aria-pressed', String(dark));
    $('toggle-theme').replaceChildren(icon(dark ? 'sun' : 'moon'));
    try {
      tg?.setHeaderColor?.('#1465e8');
      tg?.setBackgroundColor?.(dark ? '#09172c' : '#f0f6ff');
      if (tg?.isVersionAtLeast?.('7.10')) tg.setBottomBarColor?.(dark ? '#09172c' : '#f0f6ff');
    } catch { /* Older Telegram clients still render the app normally. */ }
  }
  function feedback() { tg?.HapticFeedback?.selectionChanged(); }
  function setStatus(message = '', error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  function synchronize() {
    document.body.dataset.view = tab;
    $('open-round-summary').hidden = true;
    document.querySelectorAll('[data-kind]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.kind === kind)));
    document.querySelectorAll('[data-tab]').forEach(b => {
      if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    $('page-title').textContent = titles[tab];
    if (tab === 'matches') $('page-title').append(node('small', 'match-timezone', 'МСК'));
    $('eyebrow').textContent = tab === 'ranking' ? 'ОБЩИЙ ЗАЧЁТ' : kind === 'rpl' ? 'РОССИЙСКАЯ ПРЕМЬЕР-ЛИГА' : 'КУБОК РОССИИ';
    if (tg?.BackButton) {
      if (history.length || profileReturn) tg.BackButton.show(); else tg.BackButton.hide();
    }
  }
  async function api(body, signal) {
    if (!hasAuth) throw new Error('Откройте мини-приложение кнопкой в личном чате с ботом.');
    const response = await fetch('/telegram-app/api', {
      method: 'POST', credentials: 'omit', cache: 'no-store', signal,
      headers: {'Content-Type': 'application/json', 'Authorization': `tma ${tg.initData}`},
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось загрузить данные. Повторите запрос.');
    return data;
  }
  function skeleton() {
    $('content').replaceChildren(...Array.from({length: 5}, () => node('div', 'skeleton')));
    $('controls').replaceChildren();
    $('date-form').hidden = true;
  }
  function participantName(name, uid, className = '') {
    const valid = /^[1-9][0-9]{0,9}$/.test(String(uid));
    if (!valid) return node('span', className, name);
    const button = node('button', `participant-link ${className}`, name);
    button.type = 'button'; button.dataset.userId = String(uid);
    button.setAttribute('aria-label', `Открыть профиль: ${name}`);
    button.addEventListener('click', () => {
      const originCallback = current.startsWith('round:') ? `round:${kind}:${document.querySelector('.round-select').value}` : current;
      profileReturn = {callback: originCallback, text: currentText, kind, tab, history: [...history], scroll: window.scrollY,
        match: $('content').querySelector('details.round-match[open]')?.dataset.matchId,
        uid: String(uid), from: $('date-from').value, to: $('date-to').value};
      tab = 'player'; history = []; feedback(); load(`ui:v:${kind}:card:all:${uid}:0`);
      window.scrollTo(0, 0);
    });
    return button;
  }
  function restoreOrigin(origin) {
    if (!origin) return;
    const match = [...$('content').querySelectorAll('details.round-match')].find(card => card.dataset.matchId === origin.match);
    if (match) match.open = true;
    const button = [...(match || $('content')).querySelectorAll('.participant-link')].find(link => link.dataset.userId === origin.uid);
    button?.focus({preventScroll: true});
    if (origin.from) $('date-from').value = origin.from;
    if (origin.to) $('date-to').value = origin.to;
    requestAnimationFrame(() => {
      if (current === origin.callback && tab === origin.tab && kind === origin.kind) window.scrollTo(0, origin.scroll);
    });
  }
  function returnFromProfile() {
    const origin = profileReturn;
    if (!origin) return;
    profileReturn = null; kind = origin.kind; tab = origin.tab; history = origin.history;
    load(origin.callback, {text: origin.text, restore: origin});
  }
  function profileBackButton() {
    if (!profileReturn) return;
    const label = profileReturn.callback.startsWith('summary:') ? '‹ К итогам тура' : profileReturn.tab === 'matches' ? '‹ К матчам тура' : '‹ К таблице';
    const button = node('button', 'summary-back profile-back', label); button.type = 'button';
    button.addEventListener('click', () => { feedback(); returnFromProfile(); });
    $('content').append(button);
  }
  function rankingTable(lines, participants = []) {
    const table = node('table', 'ranking-table');
    const head = node('thead'), header = node('tr');
    ['№', 'УЧАСТНИК', 'ОЧКИ'].forEach(label => {
      const th = node('th', '', label); th.scope = 'col'; header.append(th);
    });
    head.append(header); table.append(head);
    const body = node('tbody');
    lines.forEach(line => {
      const match = line.match(/^(\d+)\. (.+): (-?\d+(?:[.,]\d+)?) очк\.$/);
      if (!match) { $('content').append(node('p', 'screen-note', line)); return; }
      const row = node('tr'), name = node('td', 'player-name');
      const player = node('div', 'player-content');
      const entry = participants.find(person => person.place === Number(match[1]) && person.name === match[2]);
      player.append(participantName(match[2], entry?.user_id, 'player-label')); name.append(player);
      row.dataset.place = match[1];
      const place = node('td'); place.append(node('span', 'place-marker', match[1].padStart(2, '0')));
      row.append(place, name, node('td', '', match[3]));
      body.append(row);
    });
    table.append(body);
    if (body.childElementCount) $('content').append(table);
  }
  function roundMatches(payload) {
    $('content').replaceChildren(); $('controls').replaceChildren();
    $('controls').classList.remove('metric-grid'); $('date-form').hidden = true; expect = null;
    if (!payload.rounds.length) {
      $('content').append(node('p', 'screen-note', 'Туров пока нет.')); return;
    }
    const picker = node('div', 'round-picker');
    const index = payload.rounds.findIndex(round => round.number === payload.round_number);
    const changeRound = number => { feedback(); load(`round:${kind}:${number}`); };
    const previous = node('button', 'round-step'); previous.type = 'button';
    previous.setAttribute('aria-label', 'Предыдущий тур'); previous.append(icon('arrow'));
    previous.disabled = index <= 0;
    previous.addEventListener('click', () => changeRound(payload.rounds[index - 1].number));
    const select = node('select', 'round-select'); select.setAttribute('aria-label', 'Выбрать тур');
    const shortDate = value => new Intl.DateTimeFormat('ru-RU', {day: '2-digit', month: '2-digit', timeZone: 'Europe/Moscow'}).format(new Date(value));
    for (const round of payload.rounds) {
      const option = node('option', '', `Тур ${round.number} · ${shortDate(round.start)}–${shortDate(round.end)}.${round.end.slice(0, 4)}`);
      option.value = round.number; option.selected = round.number === payload.round_number; select.append(option);
    }
    select.addEventListener('change', () => changeRound(select.value));
    const next = node('button', 'round-step next'); next.type = 'button';
    next.setAttribute('aria-label', 'Следующий тур'); next.append(icon('arrow'));
    next.disabled = index < 0 || index >= payload.rounds.length - 1;
    next.addEventListener('click', () => changeRound(payload.rounds[index + 1].number));
    picker.append(previous, select, next); $('content').append(picker);
    $('open-round-summary').hidden = !payload.matches.length;
    $('open-round-summary').onclick = () => { feedback(); load(`summary:${kind}:${payload.round_number}`, {push: true}); };
    const list = node('div', 'round-list');
    let previousDay;
    for (const match of payload.matches) {
      const kickoff = new Date(match.kickoff);
      const day = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Europe/Moscow'}).format(kickoff);
      if (previousDay !== day) {
        const heading = node('h2', 'round-day', new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'long', weekday: 'short', timeZone: 'Europe/Moscow'}).format(kickoff));
        list.append(heading); previousDay = day;
      }
      const card = node(match.finished ? 'details' : 'article', 'round-match');
      card.dataset.matchId = String(match.id);
      const fixture = node(match.finished ? 'summary' : 'div', 'round-fixture');
      const time = node('time', 'round-time', new Intl.DateTimeFormat('ru-RU', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow'}).format(kickoff));
      time.dateTime = match.kickoff;
      const teams = node('span', 'round-teams');
      teams.append(node('span', '', match.home), node('span', '', match.away));
      fixture.append(time, teams);
      if (match.finished) {
        const arrow = node('span', 'round-expand'); arrow.append(icon('arrow')); fixture.append(arrow);
        card.append(fixture);
        const result = node('div', 'match-result');
        result.append(node('span', 'scoreboard-team', match.home), node('strong', '', match.score.join(':')), node('span', 'scoreboard-team', match.away), node('span', 'scoreboard-label', 'Итоговый счёт'));
        const table = node('table', 'match-predictions');
        table.setAttribute('aria-label', `Прогнозы и очки: ${match.home} — ${match.away}`);
        const head = node('thead'), header = node('tr'), body = node('tbody');
        for (const title of ['Участник', 'Прогноз', 'Очки']) { const th = node('th', '', title); th.scope = 'col'; header.append(th); }
        head.append(header);
        for (const prediction of match.predictions) {
          const row = node('tr');
          if (prediction.points !== null && prediction.score && prediction.score[0] === match.score[0] && prediction.score[1] === match.score[1]) row.dataset.exact = 'true';
          const name = node('td'); name.append(participantName(prediction.name, prediction.user_id));
          row.append(name, node('td', prediction.score ? '' : 'missing-score', prediction.score ? prediction.score.join(':') : 'Нет прогноза'), node('td', '', prediction.points === null ? '—' : String(prediction.points)));
          body.append(row);
        }
        table.append(head, body); card.append(result, table);
        if (!match.predictions.length) card.append(node('p', 'screen-note result-empty', 'Прогнозов нет.'));
        card.addEventListener('toggle', () => {
          if (card.open) list.querySelectorAll('details[open]').forEach(other => { if (other !== card) other.open = false; });
        });
      } else card.append(fixture);
      list.append(card);
    }
    if (!payload.matches.length) list.append(node('p', 'screen-note', 'В этом туре матчей пока нет.'));
    $('content').append(list);
  }
  function roundSummary(payload) {
    $('content').replaceChildren(); $('controls').replaceChildren();
    $('controls').classList.remove('metric-grid'); $('date-form').hidden = true; expect = null;
    $('page-title').textContent = `Итоги тура ${payload.round_number}`;
    const back = node('button', 'summary-back', `‹ Матчи тура ${payload.round_number}`); back.type = 'button';
    back.addEventListener('click', () => { feedback(); history = []; load(`round:${kind}:${payload.round_number}`); });
    $('content').append(back);
    const messages = {
      final: 'Тур завершён',
      in_progress: `Предварительные итоги · завершено ${payload.finished_matches} из ${payload.total_matches} матчей`,
      no_results: 'Завершённых матчей в этом туре пока нет.',
      pending_points: 'Очки за завершённые матчи ещё не начислены полностью. Обновите итоги позже.'
    };
    $('content').append(node('p', 'summary-status', messages[payload.status] || 'Итоги пока недоступны.'));
    if (!payload.rows.length) return;
    const table = node('table', 'round-summary-table'); table.setAttribute('aria-label', `Итоги тура ${payload.round_number}`);
    const head = node('thead'), header = node('tr'), body = node('tbody');
    for (const label of ['Участник', 'За тур', 'Место: до → после']) { const th = node('th', '', label); th.scope = 'col'; header.append(th); }
    head.append(header);
    for (const entry of payload.rows) {
      const row = node('tr'); row.dataset.winner = String(entry.winner);
      const person = node('td'), name = participantName(entry.name, entry.user_id, 'summary-name');
      person.append(name);
      if (entry.winner) person.append(node('small', 'winner-label', 'Победитель тура'));
      const places = node('td', 'summary-places');
      places.append(node('span', '', `${entry.before_place ?? '—'} → ${entry.after_place ?? '—'}`));
      if (entry.movement !== null) {
        const direction = entry.movement > 0 ? 'up' : entry.movement < 0 ? 'down' : 'same';
        const label = entry.movement > 0 ? `↑ ${entry.movement}` : entry.movement < 0 ? `↓ ${Math.abs(entry.movement)}` : 'Без изменений';
        places.append(node('small', `rank-change ${direction}`, label));
      }
      row.append(person, node('td', 'summary-points', String(entry.points)), places); body.append(row);
    }
    table.append(head, body); $('content').append(table);
    if (!payload.has_scores) $('content').append(node('p', 'screen-note', 'В этом туре нет прогнозов с начисленными очками.'));
    const method = node('details', 'method-note');
    const explanation = 'Общий зачёт: места до первого и после последнего матча выбранного тура. Более поздние матчи не учитываются. Для незавершённого тура учитываются только завершённые матчи. Места восстановлены по текущим результатам и действующему составу участников; это не сохранённый снимок прошлой таблицы. При равенстве очков за тур победа у каждого лидера. В общем зачёте действуют обычные дополнительные показатели турнира.';
    method.append(node('summary', '', 'Как считается'), node('p', '', explanation)); $('content').append(method);
    if (!payload.movement_available) $('content').append(node('p', 'screen-note summary-caveat', 'Изменение места недоступно: до тура зачёт ещё не сформирован или есть неначисленные очки.'));
  }
  function measurementTable(body, metric) {
    if (metric === 'points') {
      const lines = body.split('\n').filter(Boolean);
      if (!lines.some(line => /^\d+\. .+: -?\d+(?:[.,]\d+)? очк\.$/.test(line))) return false;
      rankingTable(lines); return true;
    }
    const labels = {round_wins: 'Победы', average: 'Ср. очки', hits: 'Точные счета', streak: 'Без точного'};
    if (!labels[metric]) return false;
    const entries = [], notes = [];
    for (const line of body.split('\n').filter(Boolean)) {
      let match;
      if (metric === 'round_wins' && (match = line.match(/^(\d+)\. (.+): (\d+) побед$/))) entries.push({name: match[2], value: match[3], position: match[1]});
      else if (metric === 'average' && (match = line.match(/^(.+): (-?\d+(?:[.,]\d+)?) очк\. \((\d+) прогнозов\)$/))) entries.push({name: match[1], value: match[2], note: `${match[3]} прогнозов`});
      else if (metric === 'hits' && (match = line.match(/^(.+): (\d+) раз$/))) entries.push({name: match[1], value: match[2]});
      else if (metric === 'streak' && (match = line.match(/^(.+): (\d+) матчей(?: \((.+)\))?$/))) entries.push({name: match[1], value: match[2], note: match[3] || ''});
      else if (['average', 'hits'].includes(metric) && (match = line.match(/^(.+): нет прогнозов с начисленными очками$/))) entries.push({name: match[1], value: '—', note: 'Нет прогнозов с начисленными очками'});
      else notes.push(line);
    }
    if (!entries.length) return false;
    const table = node('table', 'measurement-table');
    const head = node('thead'), header = node('tr');
    for (const title of ['Участник', labels[metric]]) { const cell = node('th', '', title); cell.scope = 'col'; header.append(cell); }
    head.append(header); const tbody = node('tbody');
    for (const entry of entries) {
      const row = node('tr'), person = node('td');
      const name = node('div', 'measurement-name');
      if (entry.position) name.append(node('span', 'measurement-position', entry.position.padStart(2, '0')));
      name.append(node('span', '', entry.name)); person.append(name);
      if (entry.note) person.append(node('span', 'measurement-note', entry.note));
      const value = node('td', 'measurement-value', entry.value);
      if (metric === 'streak') value.append(node('small', '', 'матчей'));
      row.append(person, value); tbody.append(row);
    }
    table.append(head, tbody); $('content').append(table);
    if (notes.length) {
      const details = node('details', 'method-note');
      details.append(node('summary', '', 'Как считается'), node('p', '', notes.join('\n'))); $('content').append(details);
    }
    return true;
  }
  function follow(data) {
    if (/^ui:[pvrud]:(rpl|cup):predictions:/.test(data)) {
      tab = 'matches'; history = []; profileReturn = null; return load(root());
    }
    if (data === 'adm:main') { history = []; profileReturn = null; return load(root()); }
    if (data.startsWith('ui:s:')) {
      history = []; return load(root());
    }
    if (data.startsWith('ui:')) return load(data, {push: true});
  }
  function draw(payload) {
    if (payload.type === 'round_matches') { roundMatches(payload); return; }
    if (payload.type === 'round_summary') { roundSummary(payload); return; }
    const metric = current.match(/^ui:[pvrud]:(?:rpl|cup):([a-z_]+):/)?.[1];
    if (metric === 'card') $('page-title').textContent = 'Профиль';
    if (tab === 'stats') $('page-title').textContent = metricTitles[metric] || titles.stats;
    const text = payload.text || '';
    const divider = text.indexOf('\n\n');
    const heading = divider >= 0 ? text.slice(0, divider) : '';
    const body = divider >= 0 ? text.slice(divider + 2) : text;
    $('content').replaceChildren(); $('controls').replaceChildren();
    profileBackButton();
    $('controls').classList.toggle('metric-grid', current.startsWith('ui:t:stats:'));
    expect = payload.expect || null;
    $('date-form').hidden = !expect;
    const isRanking = tab === 'ranking' && /\d+\. .+: .* очк\./.test(body);
    if (isRanking) {
      const page = heading.match(/Страница \d+\/\d+/);
      if (page) $('content').append(node('p', 'screen-note', page[0]));
      rankingTable(body.split('\n').filter(Boolean), payload.participants);
    } else {
      // Keep trusted result text intact; never interpret names or answers as HTML.
      const enhanced = ['matches', 'predictions', 'points', 'round_wins', 'average', 'hits', 'streak'].includes(metric);
      const context = enhanced && heading.includes('\n') ? heading.split('\n').slice(1).join('\n') : heading;
      if (context && !current.startsWith('ui:t:stats:')) $('content').append(node('p', enhanced ? 'context-strip' : 'screen-note', context));
      if (!expect && !current.startsWith('ui:t:stats:')) {
        const rendered = measurementTable(body, metric);
        if (!rendered) body.split('\n\n').filter(Boolean).forEach(block => $('content').append(node('div', 'data-card', block)));
      }
      else if (expect) $('content').append(node('p', 'screen-note', 'Выберите один день или диапазон дат. Время МСК.'));
    }
    for (const row of payload.rows || []) {
      const line = node('div', 'control-row');
      for (const action of row) {
        const target = action.callback_data;
        // Refresh is always available in the heading, the app menu is the tab bar.
        if (!target || target === 'adm:main' || action.text.includes('Обновить')) continue;
        if (target.startsWith('ui:s:') || (target === root() && history.length === 0)) continue;
        const button = node('button', '', action.text);
        const metric = target.split(':')[3];
        if (current.startsWith('ui:t:stats:') && metrics[metric]) {
          button.className = 'metric-card'; button.dataset.metric = metric;
          const badge = node('span', 'metric-icon'); badge.append(icon(metrics[metric][0]));
          const arrow = node('span', 'metric-arrow'); arrow.append(icon('arrow'));
          button.replaceChildren(badge, arrow, node('span', 'metric-title', action.text), node('span', 'metric-description', metrics[metric][1]));
        }
        button.type = 'button'; button.addEventListener('click', () => { feedback(); follow(target); });
        line.append(button);
      }
      if (line.childElementCount) $('controls').append(line);
    }
    if (expect) {
      const today = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Europe/Moscow'}).format(new Date());
      $('date-from').value = today; $('date-to').value = today;
    }
  }
  async function load(callback, {push = false, force = false, text = null, restore = null} = {}) {
    if (push && current && current !== callback) history.push(current);
    current = callback; currentText = text; synchronize();
    const id = ++sequence;
    controller?.abort(); controller = new AbortController();
    const timer = setTimeout(() => controller?.abort(), 25000);
    const cached = !text && !force ? cache.get(callback) : null;
    setStatus(); $('content').setAttribute('aria-busy', 'true'); $('updated').textContent = '';
    try {
      if (cached && Date.now() - cached.time < 30000) {
        draw(cached.payload); restoreOrigin(restore); $('updated').textContent = cached.label; return;
      }
      skeleton();
      const round = callback.match(/^(round|summary):(rpl|cup):(auto|[1-9][0-9]{0,3})$/);
      const body = round ? {action: round[1] === 'summary' ? 'round_summary' : 'matches', kind: round[2], round_number: round[3] === 'auto' ? null : Number(round[3])} : {action: 'screen', callback, text};
      const payload = await api(body, controller.signal);
      if (id !== sequence) return;
      const label = (round ? 'Время матчей МСК · ' : '') + 'Обновлено в ' + new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit'}).format(new Date()) + ' МСК';
      if (!text && !payload.expect) {
        const entry = {payload, time: Date.now(), label};
        cache.set(callback, entry);
        if (payload.type === 'round_matches') cache.set(`round:${payload.kind}:${payload.round_number}`, entry);
        while (cache.size > 40) cache.delete(cache.keys().next().value);
      }
      draw(payload); restoreOrigin(restore); $('updated').textContent = label;
    } catch (error) {
      if (id !== sequence) return;
      $('content').replaceChildren(); $('controls').replaceChildren();
      profileBackButton();
      setStatus(error.name === 'AbortError' ? 'Загрузка заняла слишком много времени. Нажмите обновление.' : error.message, true);
    } finally {
      clearTimeout(timer);
      if (id === sequence) $('content').setAttribute('aria-busy', 'false');
    }
  }
  function back() {
    const target = history.pop();
    if (target) load(target);
    else if (profileReturn) returnFromProfile();
  }
  document.querySelectorAll('[data-kind]').forEach(button => button.addEventListener('click', () => {
    if (kind === button.dataset.kind) return;
    kind = button.dataset.kind; history = []; profileReturn = null; feedback(); load(root());
  }));
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    if (tab === button.dataset.tab && current === root()) return;
    tab = button.dataset.tab; history = []; profileReturn = null; feedback(); load(root());
  }));
  $('refresh').addEventListener('click', () => load(current || root(), {force: true, text: currentText}));
  $('date-form').addEventListener('submit', event => {
    event.preventDefault();
    const start = $('date-from').value, end = $('date-to').value;
    if (start > end) return setStatus('Дата окончания должна быть не раньше начала.', true);
    if ((Date.parse(end) - Date.parse(start)) / 86400000 > 366) return setStatus('Период не должен превышать 366 дней.', true);
    const format = date => date.split('-').reverse().join('.');
    load(expect, {text: `с ${format(start)} по ${format(end)}`, force: true});
  });
  const dialog = $('chat-dialog');
  $('toggle-theme').addEventListener('click', () => {
    themePreference = themePreference === 'light' ? 'dark' : 'light';
    try { localStorage.setItem('totish-mini-theme', themePreference); } catch { /* Optional preference only. */ }
    feedback(); theme();
  });
  $('open-chat').addEventListener('click', () => dialog.showModal());
  $('close-chat').addEventListener('click', () => dialog.close());
  function bubble(text, outgoing = false) {
    const result = node('div', 'bubble' + (outgoing ? ' outgoing' : ''), text);
    $('chat-feed').append(result); result.scrollIntoView({block: 'nearest'}); return result;
  }
  async function ask(text, visibleText = text) {
    if (asking) return;
    asking = true; $('question-form').querySelector('button').disabled = true;
    $('suggestions').hidden = true; bubble(visibleText, true);
    const pending = bubble('Проверяю данные…');
    const abort = new AbortController(); const timer = setTimeout(() => abort.abort(), 25000);
    try {
      const result = await api({action: 'question', text, state: questionState}, abort.signal);
      questionState = result.state || null; pending.textContent = result.text;
      if (result.choices?.length) {
        const choices = node('div', 'choice-row');
        const values = {rpl: 'РПЛ', cup: 'Кубок России', wc: 'ЧМ', today: 'сегодня', yesterday: 'вчера'};
        for (const choice of result.choices) {
          const button = node('button', 'choice', choice.label); button.type = 'button';
          button.addEventListener('click', () => {
            if (asking) return;
            choices.remove(); ask(values[choice.value] || choice.value, choice.label);
          }); choices.append(button);
        }
        $('chat-feed').append(choices);
      }
      while ($('chat-feed').children.length > 45) $('chat-feed').children[2]?.remove();
    } catch (error) {
      pending.textContent = error.name === 'AbortError' ? 'Запрос занял слишком много времени. Повторите вопрос.' : error.message;
      if (/устарел/.test(pending.textContent)) questionState = null;
    } finally {
      clearTimeout(timer); asking = false; $('question-form').querySelector('button').disabled = false;
      pending.scrollIntoView({block: 'nearest'});
    }
  }
  $('question-form').addEventListener('submit', event => {
    event.preventDefault(); const text = $('question').value.trim();
    if (!text || asking) return; $('question').value = ''; ask(text);
  });
  $('suggestions').querySelectorAll('button').forEach(button => button.addEventListener('click', () => ask(button.textContent)));
  theme(); tg?.onEvent('themeChanged', theme); tg?.BackButton?.onClick(back);
  tg?.ready(); tg?.expand();
  if (hasAuth) load(root());
  else { synchronize(); setStatus('Откройте приложение кнопкой в личном чате с ботом ТОТИШ.', true); }
})();
