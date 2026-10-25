// Экран «Реестр»: все зависимости проекта и сводка по критичности.

import { loadRegistry } from './api.js';
import { STATUSES, STATUS_ORDER, assertStatus, filterDeps, plural, sortDeps } from './logic.js';
import {
  SOURCE_LINKS, dependencyUrl, emptyState, h, icon, renderLoadError, renderLoading,
  renderMonitoringError, renderPage, renderTopbar, statusBadge,
} from './ui.js';

const COLUMNS = [
  { key: 'name', label: 'Зависимость' },
  { key: 'current', label: 'Текущая' },
  { key: 'latest', label: 'Новая' },
  { key: 'status', label: 'Статус' },
  { key: 'places', label: 'Мест', numeric: true },
];
const REGISTRY_LINKS = SOURCE_LINKS.filter((link) => link.key !== 'releaseNotes');

const main = document.getElementById('app');
const state = { filter: null, query: '', sortKey: 'status', sortDir: 1 };
const els = {};
let deps = [];

function parseRegistry(data) {
  for (const dep of data.dependencies) assertStatus(dep.status);
  return data;
}

function linkButtons(dep) {
  return REGISTRY_LINKS
    .filter((link) => dep.links?.[link.key])
    .map((link) => h('a', {
      class: 'icon-link',
      href: dep.links[link.key],
      target: '_blank',
      rel: 'noopener noreferrer',
      title: link.title,
      'aria-label': `${link.title}: ${dep.name}`,
    }, icon(link.key)));
}

function tableRow(dep) {
  return h('tr', {},
    h('th', { scope: 'row' }, h('a', { class: 'dep-name', href: dependencyUrl(dep.name) }, dep.name)),
    h('td', { class: 'mono' }, dep.current),
    h('td', { class: 'mono' }, dep.latest ?? ''),
    h('td', {}, statusBadge(dep.status)),
    h('td', { class: 'num mono' }, dep.places === null ? '' : String(dep.places)),
    h('td', {}, h('div', { class: 'links' }, ...linkButtons(dep))),
  );
}

function card(dep) {
  const versions = dep.latest ? `${dep.current} → ${dep.latest}` : dep.current;
  const places = dep.places === null ? '' : plural(dep.places, ['место', 'места', 'мест']);
  return h('li', {}, h('a', { class: 'dep-card', href: dependencyUrl(dep.name) },
    h('span', { class: 'dep-card__row' },
      h('span', { class: 'dep-card__name' }, dep.name),
      h('span', { class: 'dep-card__versions' }, versions)),
    h('span', { class: 'dep-card__row dep-card__row--center' },
      statusBadge(dep.status),
      h('span', { class: 'dep-card__places' }, places))));
}

// Кнопки-счётчики и заголовки создаются один раз, чтобы фокус клавиатуры не терялся при перерисовке.
function buildSummary() {
  els.counters = STATUS_ORDER.map((key) => {
    const count = deps.filter((dep) => dep.status === key).length;
    return h('button', {
      type: 'button',
      class: `counter status--${key}${count === 0 ? ' counter--empty' : ''}`,
      'data-status': key,
      'aria-pressed': 'false',
      onclick: () => setFilter(state.filter === key ? null : key),
    },
      h('span', { class: 'counter__label' },
        h('span', { class: 'counter__full' }, STATUSES[key].label),
        h('span', { class: 'counter__short', 'aria-hidden': 'true' }, STATUSES[key].short)),
      h('span', { class: 'counter__count' }, String(count)));
  });
  return h('section', { 'aria-labelledby': 'summary-h' },
    h('div', { class: 'summary__head' },
      h('h2', { id: 'summary-h', class: 'eyebrow' }, 'Критичность обновлений'),
      h('p', { class: 'hint' }, 'Нажмите на счётчик, чтобы отфильтровать таблицу')),
    h('div', { class: 'counters', role: 'group', 'aria-label': 'Фильтр по статусу' }, ...els.counters));
}

function buildPanel() {
  els.count = h('span', { class: 'panel__count' });
  els.chipLabel = h('span', {});
  els.chip = h('button', { type: 'button', class: 'btn btn--soft', hidden: '', onclick: () => setFilter(null) },
    els.chipLabel, icon('close', 16, 2));
  els.search = h('input', {
    type: 'search',
    placeholder: 'Поиск по имени',
    autocomplete: 'off',
    oninput: (event) => { state.query = event.target.value; update(); },
  });
  els.headers = COLUMNS.map((column) => h('th', { scope: 'col', class: column.numeric ? 'num' : null, 'data-sort': column.key },
    h('button', { type: 'button', class: 'sort-btn', onclick: () => sortBy(column.key) }, column.label)));
  els.rows = h('tbody');
  els.cards = h('ul', { class: 'dep-cards', 'aria-label': 'Зависимости проекта' });
  els.noResults = h('div', { class: 'no-results', hidden: '' },
    h('p', {}, 'Ничего не найдено. Проверьте написание имени или снимите фильтр по статусу.'),
    h('button', { type: 'button', class: 'btn', onclick: resetAll }, 'Сбросить поиск и фильтр'));

  return h('section', { class: 'panel', 'aria-labelledby': 'deps-h' },
    h('div', { class: 'panel__head' },
      h('h2', { id: 'deps-h', class: 'panel__title' }, 'Зависимости ', els.count),
      h('div', { class: 'toolbar' },
        els.chip,
        h('label', { class: 'search' },
          icon('search', 18, 1.8),
          h('span', { class: 'sr-only' }, 'Поиск по имени зависимости'),
          els.search))),
    h('div', { class: 'table-wrap' },
      h('table', { class: 'deps-table' },
        h('caption', { class: 'sr-only' }, 'Зависимости проекта'),
        h('thead', {}, h('tr', {}, ...els.headers, h('th', { scope: 'col', class: 'num' }, 'Ссылки'))),
        els.rows)),
    els.cards,
    els.noResults);
}

function setFilter(filter) {
  state.filter = filter;
  update();
}

function sortBy(key) {
  state.sortDir = state.sortKey === key ? -state.sortDir : 1;
  state.sortKey = key;
  update();
}

function resetAll() {
  state.query = '';
  els.search.value = '';
  setFilter(null);
}

function update() {
  const visible = sortDeps(filterDeps(deps, state), state.sortKey, state.sortDir);
  for (const button of els.counters) {
    button.setAttribute('aria-pressed', String(button.dataset.status === state.filter));
  }
  els.chip.hidden = !state.filter;
  if (state.filter) {
    els.chipLabel.textContent = `Статус: ${STATUSES[state.filter].label}`;
    els.chip.setAttribute('aria-label', `Снять фильтр: ${STATUSES[state.filter].label}`);
  }
  for (const th of els.headers) {
    const active = th.dataset.sort === state.sortKey;
    th.setAttribute('aria-sort', active ? (state.sortDir === 1 ? 'ascending' : 'descending') : 'none');
  }
  els.rows.replaceChildren(...visible.map(tableRow));
  els.cards.replaceChildren(...visible.map(card));
  els.count.textContent = `${visible.length} из ${deps.length}`;
  els.noResults.hidden = visible.length > 0;
}

function renderRegistry(registry) {
  renderTopbar(registry);
  renderMonitoringError(registry.lastCheck);
  deps = registry.dependencies;

  if (!registry.lastCheck) {
    renderPage(main, emptyState({
      iconName: 'clock',
      title: 'Проект ещё не проверялся',
      text: 'Первая проверка запустится автоматически. Когда она закончится, здесь появятся зависимости проекта и оценка каждого обновления.',
      note: 'Ничего делать не нужно — обновите страницу позже.',
    }));
    return;
  }
  if (deps.length === 0) {
    renderPage(main, emptyState({
      iconName: 'search',
      title: 'Зависимости не найдены',
      text: 'Последняя проверка не нашла в проекте ни одной зависимости.',
    }));
    return;
  }

  renderPage(main, h('div', { class: 'registry' },
    buildSummary(),
    buildPanel(),
    h('p', { class: 'hint registry__note' },
      '«Низкий риск» значит, что анализ не нашёл изменений, затрагивающих код проекта. Это не гарантия совместимости: что проверялось, а что нет, показано на странице зависимости.')));
  update();
}

async function start() {
  renderLoading(main);
  let registry;
  try {
    registry = parseRegistry(await loadRegistry());
  } catch (error) {
    renderLoadError(main, error, start);
    return;
  }
  renderRegistry(registry);
}

start();
