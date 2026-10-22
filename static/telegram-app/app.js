/* Telegram identity stays in memory and travels only in the Authorization header. */
(() => {
  'use strict';
  const tg = window.Telegram?.WebApp;
  const $ = id => document.getElementById(id);
  const titles = {ranking: 'Таблица', matches: 'Матчи', predictions: 'Прогнозы', stats: 'Статистика', player: 'Участники'};
  let kind = 'rpl', tab = 'ranking', current = '', expect = null, history = [];
  let sequence = 0, controller = null, currentText = null, questionState = null, asking = false;
  const cache = new Map();
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
  function root() { return `ui:t:${tab}:${kind}`; }
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
    document.querySelectorAll('[data-kind]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.kind === kind)));
    document.querySelectorAll('[data-tab]').forEach(b => {
      if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    $('page-title').textContent = titles[tab];
    $('eyebrow').textContent = tab === 'ranking' ? 'ОБЩИЙ ЗАЧЁТ' : kind === 'rpl' ? 'РОССИЙСКАЯ ПРЕМЬЕР-ЛИГА' : 'КУБОК РОССИИ';
    if (tg?.BackButton) {
      if (history.length) tg.BackButton.show(); else tg.BackButton.hide();
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
  function rankingTable(lines) {
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
      player.append(node('span', 'player-label', match[2])); name.append(player);
      row.dataset.place = match[1];
      row.append(node('td', '', match[1].padStart(2, '0')), name, node('td', '', match[3]));
      body.append(row);
    });
    table.append(body);
    if (body.childElementCount) $('content').append(table);
  }
  function matchHeader(lines, index) {
    const teams = lines[index]?.split(' — ');
    const stamp = lines[index + 1]?.match(/^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}) МСК$/);
    if (teams?.length !== 2 || teams.some(team => !team.trim()) || !stamp) return null;
    const [, day, month, year, hour, minute] = stamp;
    const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), 12));
    if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() + 1 !== Number(month) || date.getUTCDate() !== Number(day) || Number(hour) > 23 || Number(minute) > 59) return null;
    return {home: teams[0], away: teams[1], date, day: `${year}-${month}-${day}`, time: `${hour}:${minute}`, details: []};
  }
  function predictionRows(lines) {
    const rows = [];
    for (const line of lines) {
      const score = line.match(/^(.+): (\d+):(\d+)$/);
      const missing = line.match(/^(.+): нет прогноза$/);
      if (score) rows.push({name: score[1], value: `${score[2]}:${score[3]}`});
      else if (missing) rows.push({name: missing[1], value: 'Нет прогноза', missing: true});
      else return null;
    }
    return rows;
  }
  function scheduleCards(body, metric) {
    const lines = body.split('\n').filter(line => line.trim());
    const matches = [];
    for (let i = 0; i < lines.length;) {
      const match = matchHeader(lines, i);
      if (!match) return false;
      i += 2;
      while (i < lines.length && !matchHeader(lines, i)) match.details.push(lines[i++]);
      // Unknown or partial schedule formats keep the original readable text.
      if (metric === 'matches' && match.details.length) return false;
      matches.push(match);
    }
    if (!matches.length) return false;
    let group, previousDay;
    for (const match of matches) {
      if (match.day !== previousDay) {
        group = node('section', 'match-day');
        const heading = node('h2', 'day-heading');
        const date = node('time', '', new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'}).format(match.date));
        date.dateTime = match.day;
        heading.append(date, node('span', 'day-weekday', new Intl.DateTimeFormat('ru-RU', {weekday: 'long', timeZone: 'UTC'}).format(match.date)));
        group.append(heading); $('content').append(group); previousDay = match.day;
      }
      const card = node('article', 'match-card');
      card.setAttribute('aria-label', `${match.home} — ${match.away}, ${match.time} МСК`);
      const fixture = node('div', 'match-fixture');
      const teams = node('div', 'match-teams');
      for (const name of [match.home, match.away]) {
        const team = node('div', 'match-team');
        team.append(node('span', 'team-name', name)); teams.append(team);
      }
      const clock = node('div', 'match-clock');
      const time = node('time', '', match.time); time.dateTime = match.time;
      clock.append(time, node('span', '', 'МСК')); fixture.append(teams, clock); card.append(fixture);
      if (match.details.length) {
        const rows = predictionRows(match.details);
        if (rows) {
          const table = node('table', 'prediction-table');
          table.setAttribute('aria-label', `Прогнозы: ${match.home} — ${match.away}`);
          const head = node('thead'), header = node('tr');
          for (const title of ['Участник', 'Прогноз']) { const cell = node('th', '', title); cell.scope = 'col'; header.append(cell); }
          head.append(header); const tbody = node('tbody');
          for (const entry of rows) {
            const row = node('tr'); row.append(node('td', 'prediction-name', entry.name), node('td', entry.missing ? 'prediction-missing' : 'prediction-score', entry.value)); tbody.append(row);
          }
          table.append(head, tbody); card.append(table);
        } else {
          // Deadline messages and unrecognized continuation notes are preserved.
          card.append(node('p', 'match-note', match.details.join('\n')));
        }
      }
      group.append(card);
    }
    return true;
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
    if (data === 'adm:main') { history = []; return load(root()); }
    if (data.startsWith('ui:s:')) {
      history = []; return load(root());
    }
    if (data.startsWith('ui:')) return load(data, {push: true});
  }
  function draw(payload) {
    const metric = current.match(/^ui:[pvrud]:(?:rpl|cup):([a-z_]+):/)?.[1];
    if (tab === 'stats') $('page-title').textContent = metricTitles[metric] || titles.stats;
    const text = payload.text || '';
    const divider = text.indexOf('\n\n');
    const heading = divider >= 0 ? text.slice(0, divider) : '';
    const body = divider >= 0 ? text.slice(divider + 2) : text;
    $('content').replaceChildren(); $('controls').replaceChildren();
    $('controls').classList.toggle('metric-grid', current.startsWith('ui:t:stats:'));
    expect = payload.expect || null;
    $('date-form').hidden = !expect;
    const isRanking = tab === 'ranking' && /\d+\. .+: .* очк\./.test(body);
    if (isRanking) {
      const page = heading.match(/Страница \d+\/\d+/);
      if (page) $('content').append(node('p', 'screen-note', page[0]));
      rankingTable(body.split('\n').filter(Boolean));
    } else {
      // Keep trusted result text intact; never interpret names or answers as HTML.
      const enhanced = ['matches', 'predictions', 'points', 'round_wins', 'average', 'hits', 'streak'].includes(metric);
      const context = enhanced && heading.includes('\n') ? heading.split('\n').slice(1).join('\n') : heading;
      if (context && !current.startsWith('ui:t:stats:')) $('content').append(node('p', enhanced ? 'context-strip' : 'screen-note', context));
      if (!expect && !current.startsWith('ui:t:stats:')) {
        const rendered = ['matches', 'predictions'].includes(metric) ? scheduleCards(body, metric) : measurementTable(body, metric);
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
  async function load(callback, {push = false, force = false, text = null} = {}) {
    if (push && current && current !== callback) history.push(current);
    current = callback; currentText = text; synchronize();
    const id = ++sequence;
    controller?.abort(); controller = new AbortController();
    const timer = setTimeout(() => controller?.abort(), 25000);
    const cached = !text && !force ? cache.get(callback) : null;
    setStatus(); $('content').setAttribute('aria-busy', 'true'); $('updated').textContent = '';
    try {
      if (cached && Date.now() - cached.time < 30000) {
        draw(cached.payload); $('updated').textContent = cached.label; return;
      }
      skeleton();
      const payload = await api({action: 'screen', callback, text}, controller.signal);
      if (id !== sequence) return;
      const label = 'Обновлено в ' + new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit'}).format(new Date()) + ' МСК';
      if (!text && !payload.expect) {
        cache.set(callback, {payload, time: Date.now(), label});
        if (cache.size > 40) cache.delete(cache.keys().next().value);
      }
      draw(payload); $('updated').textContent = label;
    } catch (error) {
      if (id !== sequence) return;
      $('content').replaceChildren(); $('controls').replaceChildren();
      setStatus(error.name === 'AbortError' ? 'Загрузка заняла слишком много времени. Нажмите обновление.' : error.message, true);
    } finally {
      clearTimeout(timer);
      if (id === sequence) $('content').setAttribute('aria-busy', 'false');
    }
  }
  function back() {
    const target = history.pop();
    if (target) load(target);
  }
  document.querySelectorAll('[data-kind]').forEach(button => button.addEventListener('click', () => {
    if (kind === button.dataset.kind) return;
    kind = button.dataset.kind; history = []; feedback(); load(root());
  }));
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    if (tab === button.dataset.tab && current === root()) return;
    tab = button.dataset.tab; history = []; feedback(); load(root());
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
