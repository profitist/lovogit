// Экран «Зависимость»: что сделать перед обновлением и почему. Имя — в параметре ?name=.

import { ApiError, loadDependency } from './api.js';
import { STATUSES, assertStatus, placesSummary, plural } from './logic.js';
import {
  SOURCE_LINKS, emptyState, exactTimeEl, h, icon, inlineText,
  renderLoadError, renderLoading, renderMonitoringError, renderPage, renderTopbar, statusBadge,
} from './ui.js';

const KINDS = {
  removed: { label: 'удалено', status: 'incompat' },
  deprecated: { label: 'устарело', status: 'review' },
};

// Статусы, при которых анализ завершён и есть вывод, затронутый код и изменения.
const ANALYZED = new Set(['incompat', 'review', 'low']);

const main = document.getElementById('app');
const name = new URLSearchParams(location.search).get('name');

function parseDependency(data) {
  assertStatus(data.status);
  for (const item of data.history) assertStatus(item.status);
  for (const place of data.places ?? []) {
    if (place.kind && !Object.hasOwn(KINDS, place.kind)) throw new Error(`Неизвестный вид изменения API: ${place.kind}`);
  }
  // Блок границ анализа обязателен при любом статусе: «не нашли» не значит «совместимо».
  if (!data.bounds) throw new Error(`В отчёте по ${data.name} нет границ анализа`);
  return data;
}

function versions(dep) {
  if (!dep.latest) return h('p', { class: 'versions' }, dep.current);
  return h('p', { class: 'versions' },
    h('span', { class: 'sr-only' }, 'Обновление с версии '),
    `${dep.current} `,
    h('span', { class: 'versions__arrow', 'aria-hidden': 'true' }, '→'),
    h('span', { class: 'sr-only' }, ' до версии'),
    ' ',
    h('strong', {}, dep.latest));
}

function header(dep) {
  const links = SOURCE_LINKS
    .filter((link) => dep.links?.[link.key])
    .map((link) => h('a', { class: 'source-link', href: dep.links[link.key], target: '_blank', rel: 'noopener noreferrer' },
      icon(link.key, 17), link.title));
  return h('section', { class: 'dep-head', 'aria-labelledby': 'dep-title' },
    h('div', { class: 'dep-head__line' },
      h('h1', { id: 'dep-title', class: 'dep-title' }, dep.name),
      versions(dep),
      statusBadge(dep.status, 'status--lg')),
    links.length ? h('nav', { class: 'source-links', 'aria-label': 'Официальные источники' }, ...links) : null);
}

function callout(status, title, main, secondary) {
  return h('section', { class: `verdict status--${status}`, 'aria-labelledby': 'callout-h' },
    h('h2', { id: 'callout-h', class: 'eyebrow' }, title),
    h('p', { class: 'verdict__main' }, ...main),
    secondary ? h('p', { class: 'verdict__rec' }, ...secondary) : null);
}

function verdict(dep) {
  return callout(dep.status, 'Вывод', inlineText(dep.reason),
    [h('strong', {}, 'Рекомендация: '), ...inlineText(dep.recommendation)]);
}

function failure(dep) {
  return callout('failed', 'Почему анализ не удался', inlineText(dep.failureReason),
    ['Повторная попытка — при следующей проверке. До этого риск обновления не оценён: это не «низкий риск».']);
}

function waiting(dep) {
  return callout('waiting', 'Анализ идёт',
    [`Новая версия ${dep.latest} найдена. Анализ изменений и кода проекта ещё не завершён.`],
    ['Когда он закончится, здесь появятся вывод, затронутый код и список изменений, а в Telegram придёт уведомление. Пока риск обновления не оценён.']);
}

function upToDate() {
  return callout('current', 'Обновлений нет', ['Новее текущей версии нет.'], null);
}

function copyButton(location) {
  const status = h('span', { class: 'copy-btn__status', 'aria-live': 'polite' });
  // Без доступа к буферу обмена кнопка говорит об этом, а не делает вид, что скопировала.
  const copy = () => {
    if (!navigator.clipboard) {
      status.textContent = 'Буфер обмена недоступен';
      return;
    }
    navigator.clipboard.writeText(location).then(
      () => { status.textContent = 'Скопировано'; },
      () => { status.textContent = 'Не удалось скопировать'; },
    );
  };
  return h('button', { type: 'button', class: 'copy-btn', 'aria-label': `Скопировать путь ${location}`, onclick: copy },
    icon('copy', 17), status);
}

function placeItem(place) {
  const location = place.line ? `${place.file}:${place.line}` : place.file;
  const kind = place.kind ? KINDS[place.kind] : null;
  return h('li', { class: 'place' },
    h('span', { class: 'place__where' },
      h('span', { class: 'place__path' }, location),
      place.line ? null : h('span', { class: 'hint' }, 'строку определить не удалось')),
    h('span', { class: 'place__api' },
      h('code', {}, place.api),
      kind ? h('span', { class: `tag status--${kind.status}` }, kind.label) : null),
    copyButton(location));
}

function placesPanel(places) {
  return h('section', { class: 'panel', 'aria-labelledby': 'code-h' },
    h('div', { class: 'panel__head' },
      h('h2', { id: 'code-h', class: 'panel__title' }, 'Затронутый код'),
      places.length ? h('span', { class: 'hint' }, placesSummary(places)) : null),
    places.length
      ? h('ul', { class: 'places' }, ...places.map(placeItem))
      : h('p', { class: 'panel__empty' }, 'Мест, которые задевают изменения, в коде не найдено.'));
}

function changeItem(change) {
  const usage = change.usedInProject > 0
    ? h('span', { class: 'tag status--incompat' }, `используется в проекте · ${change.usedInProject}`)
    : h('span', { class: 'tag status--current' }, 'в коде не найдено');
  return h('li', { class: 'change' },
    h('div', { class: 'change__tags' },
      usage,
      change.assumption ? h('span', { class: 'tag tag--guess' }, 'предположение') : null),
    h('p', { class: 'change__text' }, ...inlineText(change.text)),
    change.assumption ? h('p', { class: 'change__guess' }, ...inlineText(change.assumption)) : null,
    h('a', { class: 'source', href: change.source.url, target: '_blank', rel: 'noopener noreferrer' },
      `Источник: ${change.source.title}`, icon('external', 13, 2)));
}

function changesPanel(changes) {
  return h('section', { class: 'panel', 'aria-labelledby': 'changes-h' },
    h('div', { class: 'panel__head panel__head--stack' },
      h('h2', { id: 'changes-h', class: 'panel__title' }, 'Что изменилось'),
      h('p', { class: 'hint' }, 'Изменения из changelog и release notes, которые анализ счёл значимыми для проекта')),
    changes.length
      ? h('ol', { class: 'changes' }, ...changes.map(changeItem))
      : h('p', { class: 'panel__empty' }, 'Значимых для проекта изменений не найдено.'));
}

function boundsList(items) {
  return h('ul', {}, ...items.map((item) => h('li', {}, ...inlineText(item))));
}

function boundsPanel(bounds) {
  return h('section', { class: 'panel bounds', id: 'bounds', 'aria-labelledby': 'bounds-h' },
    h('div', { class: 'bounds__head' },
      h('h2', { id: 'bounds-h', class: 'panel__title' }, 'Границы анализа'),
      h('p', { class: 'hint' }, '«Не нашли» не значит «совместимо»: всё, что не проверялось, остаётся на вас')),
    h('div', { class: 'bounds__grid' },
      h('div', { class: 'bounds__box' }, h('h3', {}, 'Проверено'), boundsList(bounds.checked)),
      h('div', { class: 'bounds__box bounds__box--unchecked' }, h('h3', {}, 'Не проверено'), boundsList(bounds.unchecked))));
}

function historyPanel(history) {
  if (history.length === 0) return null;
  const chevron = icon('chevron', 16, 2);
  chevron.classList.add('chev');
  return h('details', { class: 'panel history' },
    h('summary', {},
      chevron, 'История проверок ',
      h('span', { class: 'history__count' }, plural(history.length, ['проверка', 'проверки', 'проверок']))),
    h('ul', { class: 'history__list' }, ...history.map((item) => h('li', { class: 'history__item' },
      exactTimeEl(item.at),
      h('span', { class: 'mono' }, item.latest ? `${item.current} → ${item.latest}` : item.current),
      h('span', { class: `status-text status--${item.status}` }, STATUSES[item.status].label)))));
}

function statusBlocks(dep) {
  if (ANALYZED.has(dep.status)) return [verdict(dep), placesPanel(dep.places), changesPanel(dep.changes)];
  if (dep.status === 'failed') return [failure(dep)];
  if (dep.status === 'waiting') return [waiting(dep)];
  return [upToDate()];
}

function backLink() {
  return h('a', { class: 'back-link', href: 'registry.html' }, icon('back', 16, 2), 'Все зависимости');
}

function renderDependency(dep) {
  document.title = `${dep.name}${dep.latest ? ` ${dep.current} → ${dep.latest}` : ''} — LovoGit`;
  renderTopbar(dep);
  renderMonitoringError(dep.lastCheck);
  renderPage(main, h('div', { class: 'report' },
    backLink(),
    header(dep),
    ...statusBlocks(dep),
    boundsPanel(dep.bounds),
    historyPanel(dep.history)));
}

function renderNotFound(message) {
  renderPage(main, emptyState({
    iconName: 'search',
    title: 'Зависимость не найдена',
    text: message,
    action: h('a', { class: 'btn', href: 'registry.html' }, icon('back', 16, 2), 'Все зависимости'),
  }));
}

async function start() {
  if (!name) {
    renderNotFound('В адресе не указано, какую зависимость показать.');
    return;
  }
  renderLoading(main);
  let dep;
  try {
    dep = parseDependency(await loadDependency(name));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) renderNotFound(`В проекте нет зависимости «${name}».`);
    else renderLoadError(main, error, start);
    return;
  }
  renderDependency(dep);
}

start();
