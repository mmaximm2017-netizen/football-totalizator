/* Telegram identity stays in memory and travels only in the Authorization header. */
(() => {
  'use strict';
  const tg = window.Telegram?.WebApp;
  const $ = id => document.getElementById(id);
  const titles = {ranking: 'Таблица', matches: 'Матчи', stats: 'Статистика'};
  let kind = 'rpl', tab = 'ranking', current = '', expect = null, history = [];
  let sequence = 0, controller = null, currentText = null, questionState = null, asking = false;
  const cache = new Map();
  let profileReturn = null, periodController = null, periodMode = null, dateEditing = false;
  const periodSelections = new Map();
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
  function setStatus(message = '', error = false, retry = false) {
    $('status').replaceChildren();
    if (message) $('status').append(node('p', '', message));
    $('status').classList.toggle('error', error);
    if (retry && hasAuth) {
      const button = node('button', 'retry-button', 'Повторить'); button.type = 'button';
      button.addEventListener('click', () => load(current || root(), {force: true, text: currentText}));
      $('status').append(button);
    }
  }
  function synchronize() {
    document.body.dataset.view = tab;
    $('open-round-summary').hidden = true;
    document.querySelectorAll('[data-kind]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.kind === kind)));
    document.querySelectorAll('[data-tab]').forEach(b => {
      if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    const metric = current.match(/^ui:[pvrud]:(?:rpl|cup):([a-z_]+):/)?.[1];
    $('page-title').textContent = metric === 'card' ? 'Профиль' : metric === 'recent' ? 'Последние результаты' : metricTitles[metric] || titles[tab];
    if (tab === 'matches' && current.startsWith('round:')) $('page-title').append(node('small', 'match-timezone', 'МСК'));
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
  function periodConfig(callback = current) {
    const match = callback.match(/^ui:v:(rpl|cup):([a-z_]+):([^:]+):([0-9]+):[0-9]+$/);
    return match && metrics[match[2]] ? {kind: match[1], metric: match[2], period: match[3], user: match[4], key: `${match[1]}:${match[2]}:${match[4]}`} : null;
  }
  function normalizePeriod(callback) {
    const match = callback.match(/^ui:p:(rpl|cup):([a-z_]+):[^:]+:([0-9]+):[0-9]+$/);
    if (!match || !metrics[match[2]]) {
      const config = periodConfig(callback);
      return config ? periodTarget(config, config.period) : callback;
    }
    return periodSelections.get(`${match[1]}:${match[2]}:${match[3]}`) || `ui:v:${match[1]}:${match[2]}:all:${match[3]}:0`;
  }
  function periodTarget(config, period) { return `ui:v:${config.kind}:${config.metric}:${period}:${config.user}:0`; }
  function configurePeriod() {
    periodController?.abort();
    const config = periodConfig();
    document.body.dataset.period = String(Boolean(config)); dateEditing = false;
    if (config) $('page-title').textContent = metricTitles[config.metric];
    $('period-controls').hidden = !config; $('period-tabs').replaceChildren(); $('period-options').replaceChildren();
    $('date-form').hidden = true;
    if (!config) { periodMode = null; return; }
    periodSelections.set(config.key, current);
    while (periodSelections.size > 40) periodSelections.delete(periodSelections.keys().next().value);
    const period = config.period;
    periodMode = period.startsWith('r') ? 'round' : period.startsWith('d') || ['today', 'yesterday'].includes(period) ? 'dates' : 'all';
    for (const [mode, label] of [['all', 'Весь турнир'], ['round', 'Тур'], ['dates', 'Даты']]) {
      const button = node('button', '', label); button.type = 'button'; button.dataset.periodMode = mode;
      button.addEventListener('click', () => {
        feedback(); periodController?.abort(); periodMode = mode; dateEditing = mode === 'dates'; setStatus();
        if (mode === 'all') return load(periodTarget(config, 'all'));
        showPeriodOptions(config);
      }); $('period-tabs').append(button);
    }
    const label = period === 'all' ? 'Весь турнир' : period.startsWith('r') ? `Тур ${period.slice(1)}` : period.startsWith('d') ? period.slice(1).split('-').map(value => `${value.slice(6, 8)}.${value.slice(4, 6)}.${value.slice(0, 4)}`).join(' – ') : period === 'today' ? 'Сегодня' : 'Вчера';
    $('period-applied').textContent = `Показано: ${label} · МСК`;
    if (/^d[0-9]{8}-[0-9]{8}$/.test(period)) {
      const values = period.slice(1).split('-').map(value => `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`);
      $('date-from').value = values[0]; $('date-to').value = values[1];
    }
    showPeriodOptions(config);
  }
  function showPeriodOptions(config) {
    $('period-tabs').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.periodMode === periodMode)));
    $('period-options').replaceChildren(); $('date-form').hidden = periodMode !== 'dates' || !dateEditing;
    if (periodMode === 'dates' && !$('date-from').value) {
      const today = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Europe/Moscow'}).format(new Date());
      $('date-from').value = today; $('date-to').value = today;
    }
    if (periodMode === 'dates' && !dateEditing) {
      const edit = node('button', 'period-edit', 'Изменить даты'); edit.type = 'button';
      edit.addEventListener('click', () => { dateEditing = true; showPeriodOptions(config); }); $('period-options').append(edit);
    }
    if (periodMode === 'round') loadPeriodRounds(config);
  }
  async function loadPeriodRounds(config) {
    const origin = current, request = new AbortController(); periodController = request;
    const callback = `ui:r:${config.kind}:${config.metric}:all:${config.user}:0`;
    const timer = setTimeout(() => request.abort(), 25000);
    $('period-options').append(node('p', 'period-hint', 'Загрузка туров…'));
    try {
      const cached = cache.get(callback);
      const payload = cached && Date.now() - cached.time < 30000 ? cached.payload : await api({action: 'screen', callback}, request.signal);
      if (request.signal.aborted || current !== origin || periodMode !== 'round') return;
      cache.set(callback, {payload, time: Date.now()});
      while (cache.size > 40) cache.delete(cache.keys().next().value);
      const choices = (payload.rows || []).flat().filter(button => {
        const target = periodConfig(button.callback_data || '');
        return target && target.kind === config.kind && target.metric === config.metric && target.user === config.user && /^r[1-9][0-9]*$/.test(target.period);
      });
      $('period-options').replaceChildren();
      if (!choices.length) { $('period-options').append(node('p', 'period-hint', 'Туров пока нет. Выберите весь турнир или даты.')); return; }
      const select = node('select', 'period-select'); select.setAttribute('aria-label', 'Тур статистики');
      const placeholder = node('option', '', 'Выберите тур'); placeholder.value = ''; select.append(placeholder);
      for (const choice of choices) {
        const option = node('option', '', choice.text); option.value = choice.callback_data; select.append(option);
      }
      select.value = choices.some(choice => choice.callback_data === current) ? current : '';
      select.addEventListener('change', () => { if (select.value) { feedback(); load(select.value); } });
      $('period-options').append(select);
    } catch (error) {
      if (current !== origin || periodMode !== 'round' || periodController !== request) return;
      $('period-options').replaceChildren(node('p', 'period-hint', 'Не удалось загрузить туры. Результаты ниже сохранены.'));
      const retry = node('button', 'retry-button', 'Повторить загрузку туров'); retry.type = 'button';
      retry.addEventListener('click', () => { $('period-options').replaceChildren(); loadPeriodRounds(config); }); $('period-options').append(retry);
    } finally { clearTimeout(timer); }
  }
  function emptyState(title, description, symbol = 'chart') {
    const section = node('div', 'empty-state'); const badge = node('span', 'empty-icon'); badge.append(icon(symbol));
    section.append(badge, node('h2', '', title), node('p', '', description)); $('content').append(section);
  }
  function skeleton() {
    const roster = /^ui:(?:t:player:|u:)/.test(current);
    const count = tab === 'matches' ? 8 : roster || tab === 'ranking' || periodConfig() ? 6 : 3;
    const shell = node('div', `loading-view ${tab === 'matches' ? 'loading-matches' : 'loading-list'}`); shell.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < count; i++) {
      const row = node('div', 'skeleton'); row.append(node('span', 'skeleton-name'), node('span', 'skeleton-value')); shell.append(row);
    }
    $('content').replaceChildren(node('span', 'sr-only', 'Загрузка данных…'), shell);
    $('controls').replaceChildren();
    if (!periodConfig()) $('date-form').hidden = true;
  }
  function participantList(players) {
    if (!players.length) { emptyState('Участников пока нет', 'Для этого турнира пока нет доступных участников.', 'user'); return; }
    const list = node('ol', 'participant-list'); list.setAttribute('aria-label', 'Участники турнира');
    for (const player of players) {
      const row = node('li'); row.dataset.place = player.place ?? '';
      const position = node('span', 'place-marker', player.place === null ? '—' : String(player.place).padStart(2, '0'));
      const name = participantName(player.name, player.user_id, 'participant-list-name');
      const score = node('span', 'participant-list-score'); score.append(node('strong', '', String(player.points)), node('small', '', 'очк.'));
      row.append(position, name, score);
      if (name.tagName === 'BUTTON') row.addEventListener('click', event => { if (!event.target.closest('button')) name.click(); });
      list.append(row);
    }
    $('content').append(list);
    if (players.every(player => player.place === null)) $('content').append(node('p', 'profile-note', 'Места появятся после первых завершённых прогнозов.'));
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
      history = []; feedback(); load(`ui:v:${kind}:card:all:${uid}:0`);
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
    const label = profileReturn.callback.startsWith('summary:') ? '‹ К итогам тура' : profileReturn.tab === 'matches' ? '‹ К матчам тура' : profileReturn.tab === 'stats' ? '‹ К статистике' : '‹ К таблице';
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
      emptyState('Матчей пока нет', 'Расписание появится после добавления матчей в турнир.', 'ball'); return;
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
    if (!payload.matches.length) emptyState('В этом туре матчей пока нет.', 'Попробуйте выбрать другой тур.', 'ball');
    $('content').append(list);
  }
  function playerProfile(profile) {
    const hero = node('section', 'profile-hero'); hero.setAttribute('aria-label', 'Участник турнира');
    hero.append(node('p', 'profile-kicker', profile.active ? 'УЧАСТНИК ТУРНИРА' : 'АРХИВНЫЙ УЧАСТНИК'), node('h2', 'profile-name', profile.name));
    const totals = node('div', 'profile-totals');
    const place = node('div'); place.append(node('strong', '', profile.place === null ? '—' : `#${profile.place}`), node('span', '', profile.place === null ? 'Место не определено' : `из ${profile.participants} участников`));
    const points = node('div'); points.append(node('strong', '', String(profile.points)), node('span', '', 'очков в турнире'));
    totals.append(place, points); hero.append(totals); $('content').append(hero);
    const metrics = node('div', 'profile-metrics');
    for (const [value, label] of [[profile.round_wins, 'Победы в турах'], [profile.exact, 'Точные счета'], [profile.average, 'Средние очки']]) {
      const item = node('div', 'profile-metric'); item.append(node('strong', '', value === null ? '—' : String(value)), node('span', '', label)); metrics.append(item);
    }
    $('content').append(metrics);
    if (!profile.active) {
      $('content').append(node('p', 'screen-note', 'Архивный участник. Подробные результаты закрыты.')); return;
    }
    if (profile.round_wins === null) $('content').append(node('p', 'profile-note', 'Победы появятся после полностью завершённого тура с начисленными очками.'));
    $('content').append(node('h2', 'subsection-title', 'Последние результаты'));
    const list = node('ol', 'recent-results'); list.setAttribute('aria-label', 'Последние завершённые матчи');
    for (const match of profile.recent) {
      const row = node('li');
      const fixture = node('div'); fixture.append(node('span', 'recent-fixture', `${match.home} — ${match.away}`), node('time', 'recent-date', new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'short', timeZone: 'Europe/Moscow'}).format(new Date(match.kickoff))));
      const score = node('strong', 'recent-points', match.points === null ? '—' : `+${match.points}`);
      score.dataset.exact = String(match.points >= 10); score.setAttribute('aria-label', match.points === null ? 'Очки ещё не начислены' : `${match.points} очков`);
      row.append(fixture, score); list.append(row);
    }
    $('content').append(list);
    if (!profile.recent.length) emptyState('Завершённых прогнозов пока нет.', 'Последние результаты появятся после завершения матчей.', 'ball');
    if (profile.recent.some(match => match.points === null)) $('content').append(node('p', 'profile-note', 'Прочерк: очки ещё не начислены.'));
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
    if (!payload.rows.length) { emptyState(payload.status === 'pending_points' ? 'Ждём начисления очков' : 'Результатов пока нет', payload.status === 'pending_points' ? 'Итоги появятся после завершения расчёта.' : 'Итоги появятся после завершения матчей тура.', 'table'); return; }
    const winners = payload.status === 'final' ? payload.rows.filter(entry => entry.winner) : [];
    if (winners.length) {
      const spotlight = node('section', 'round-winners'); spotlight.setAttribute('aria-label', 'Победители тура');
      const badge = node('span', 'winner-icon'); badge.append(icon('table')); spotlight.append(badge, node('p', 'winner-kicker', winners.length === 1 ? 'ПОБЕДИТЕЛЬ ТУРА' : 'ПОБЕДИТЕЛИ ТУРА'));
      const names = node('div', 'winner-names');
      for (const entry of winners) names.append(participantName(entry.name, entry.user_id));
      spotlight.append(names, node('p', 'winner-score', `${winners[0].points} очк. за тур`)); $('content').append(spotlight);
    }
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
    const maximum = Math.max(0, ...entries.map(entry => Number(entry.value.replace(',', '.'))).filter(Number.isFinite));
    const table = node('table', 'measurement-table');
    const head = node('thead'), header = node('tr');
    for (const title of ['Участник', labels[metric]]) { const cell = node('th', '', title); cell.scope = 'col'; header.append(cell); }
    head.append(header); const tbody = node('tbody');
    for (const entry of entries) {
      const row = node('tr'), person = node('td');
      const name = node('div', 'measurement-name');
      if (entry.position) name.append(node('span', 'measurement-position', entry.position.padStart(2, '0')));
      name.append(node('span', '', entry.name)); person.append(name);
      if (['round_wins', 'hits'].includes(metric) && maximum > 0 && Number.isFinite(Number(entry.value))) {
        const track = node('span', 'comparison-track'); track.setAttribute('aria-hidden', 'true');
        const bar = node('span', 'comparison-bar'); bar.style.width = `${Math.max(0, Math.min(100, Number(entry.value) / maximum * 100))}%`;
        track.append(bar); person.append(track);
      }
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
    if (/^ui:(?:u:(?:rpl|cup):|t:player:|s:player$)/.test(data)) {
      tab = 'ranking'; history = []; profileReturn = null; return load(root());
    }
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
    else if (metric === 'recent') $('page-title').textContent = 'Последние результаты';
    else if (tab === 'stats') $('page-title').textContent = metricTitles[metric] || titles.stats;
    const text = payload.text || '';
    const divider = text.indexOf('\n\n');
    const heading = divider >= 0 ? text.slice(0, divider) : '';
    const body = divider >= 0 ? text.slice(divider + 2) : text;
    $('content').replaceChildren(); $('controls').replaceChildren();
    profileBackButton();
    $('controls').classList.toggle('metric-grid', current.startsWith('ui:t:stats:'));
    expect = payload.expect || null;
    if (!periodConfig()) { $('date-form').hidden = !expect; if (expect) $('period-controls').hidden = false; }
    const isRanking = tab === 'ranking' && /\d+\. .+: .* очк\./.test(body);
    if (Array.isArray(payload.players)) {
      participantList(payload.players);
    } else if (metric === 'card' && payload.profile) {
      playerProfile(payload.profile);
    } else if (isRanking) {
      const page = heading.match(/Страница \d+\/\d+/);
      if (page) $('content').append(node('p', 'screen-note', page[0]));
      rankingTable(body.split('\n').filter(Boolean), payload.participants);
    } else {
      // Keep trusted result text intact; never interpret names or answers as HTML.
      const enhanced = ['matches', 'predictions', 'points', 'round_wins', 'average', 'hits', 'streak'].includes(metric);
      const context = enhanced && heading.includes('\n') ? heading.split('\n').slice(1).join('\n') : heading;
      if (context && !current.startsWith('ui:t:stats:') && !periodConfig()) $('content').append(node('p', enhanced ? 'context-strip' : 'screen-note', context));
      if (!expect && !current.startsWith('ui:t:stats:')) {
        const rendered = measurementTable(body, metric);
        if (!rendered) {
          if (/^(Матчей пока нет\.|Завершённых прогнозов пока нет\.|Подходящих завершённых матчей нет\.|Завершённых туров с начисленными прогнозами нет\.)/m.test(body)) emptyState('Пока нет результатов', body);
          else body.split('\n\n').filter(Boolean).forEach(block => $('content').append(node('div', 'data-card', block)));
        }
      }
      else if (expect) $('content').append(node('p', 'screen-note', 'Выберите один день или диапазон дат. Время МСК.'));
    }
    for (const row of payload.rows || []) {
      const line = node('div', 'control-row');
      for (const action of row) {
        const target = action.callback_data;
        // Refresh is always available in the heading, the app menu is the tab bar.
        if (!target || target === 'adm:main' || action.text.includes('Обновить')) continue;
        if (/^ui:u:/.test(target) && profileReturn) continue;
        if (periodConfig() && /^ui:p:/.test(target) && metrics[target.split(':')[3]]) continue;
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
    callback = normalizePeriod(callback);
    if (push && current && current !== callback) history.push(current);
    current = callback; currentText = text; synchronize(); configurePeriod();
    const id = ++sequence;
    controller?.abort(); const requestController = new AbortController(); controller = requestController;
    const timer = setTimeout(() => requestController.abort(), 25000);
    const cached = !text && !force ? cache.get(callback) : null;
    setStatus(); $('content').setAttribute('aria-busy', 'true'); $('updated').textContent = '';
    try {
      if (cached && Date.now() - cached.time < 30000) {
        draw(cached.payload); restoreOrigin(restore); $('updated').textContent = cached.label; return;
      }
      skeleton();
      const round = callback.match(/^(round|summary):(rpl|cup):(auto|[1-9][0-9]{0,3})$/);
      const body = round ? {action: round[1] === 'summary' ? 'round_summary' : 'matches', kind: round[2], round_number: round[3] === 'auto' ? null : Number(round[3])} : {action: 'screen', callback, text};
      const payload = await api(body, requestController.signal);
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
      setStatus(error.name === 'AbortError' ? 'Загрузка заняла слишком много времени. Попробуйте ещё раз.' : error.message, true, true);
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
    const config = periodConfig();
    if (config) return load(periodTarget(config, `d${start.replaceAll('-', '')}-${end.replaceAll('-', '')}`), {force: true});
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
