// Общие элементы разметки обоих экранов: шапка, статусы, время, состояния загрузки и ошибки.

import { STATUSES, relativeTime, splitInlineCode } from './logic.js';

const ICONS = {
  repo: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10"/><path d="M18 9c0 5-7 4-11.5 8.5"/>',
  pypi: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5"/><path d="M12 12v9"/>',
  changelog: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/><path d="M9.5 13h6M9.5 17h6"/>',
  releaseNotes: '<path d="M4 12V4h8l8 8-8 8z"/><circle cx="8.5" cy="8.5" r="1.2"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  alert: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  retry: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v6H4V6h6"/>',
};

// Ссылки на официальные источники. Неизвестная ссылка не показывается совсем.
export const SOURCE_LINKS = [
  { key: 'repo', title: 'Репозиторий' },
  { key: 'pypi', title: 'PyPI' },
  { key: 'changelog', title: 'Changelog' },
  { key: 'releaseNotes', title: 'Release notes' },
];

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

export function icon(name, size = 18, strokeWidth = 1.7) {
  if (!Object.hasOwn(ICONS, name)) throw new Error(`Неизвестная иконка: ${name}`);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const attrs = {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': strokeWidth, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
  };
  for (const [name, value] of Object.entries(attrs)) svg.setAttribute(name, value);
  svg.innerHTML = ICONS[name];
  return svg;
}

export function statusBadge(status, extraClass = '') {
  const { label, hint } = STATUSES[status];
  return h('span', { class: `status status--${status} ${extraClass}`.trim(), title: hint }, label);
}

export function inlineText(text) {
  return splitInlineCode(text).map((part) => (part.code ? h('code', {}, part.text) : part.text));
}

const EXACT = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const SHORT = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

function parseDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) throw new Error(`Некорректная дата: ${iso}`);
  return date;
}

// Время показывается относительно, точное — во всплывающей подсказке.
export function relativeTimeEl(iso) {
  const date = parseDate(iso);
  return h('time', { datetime: iso, title: EXACT.format(date) }, relativeTime(date));
}

export function exactTimeEl(iso) {
  return h('time', { datetime: iso }, SHORT.format(parseDate(iso)));
}

export function dependencyUrl(name) {
  return `dependency.html?name=${encodeURIComponent(name)}`;
}

export function renderTopbar({ project, lastCheck }) {
  const topbar = document.getElementById('topbar');
  const lastCheckText = lastCheck
    ? h('p', { class: 'last-check' }, 'Последняя проверка: ', relativeTimeEl(lastCheck.at))
    : h('p', { class: 'last-check' }, 'Последняя проверка: ', h('strong', {}, 'ещё не было'));
  topbar.replaceChildren(
    h('div', { class: 'topbar__project' }, h('span', { class: 'logo' }, 'LovoGit'), h('span', { class: 'project-name' }, project)),
    lastCheckText,
  );
}

// Без этой плашки пользователь примет старые данные за свежие.
export function renderMonitoringError(lastCheck) {
  const alerts = document.getElementById('alerts');
  if (!lastCheck?.error) {
    alerts.replaceChildren();
    return;
  }
  alerts.replaceChildren(h('div', { class: 'alert-bar', role: 'alert' },
    h('div', { class: 'container alert-bar__inner' },
      icon('alert', 20, 1.8),
      h('p', {},
        h('strong', {}, 'Последняя проверка завершилась ошибкой: '),
        ...inlineText(lastCheck.error),
        ' (', relativeTimeEl(lastCheck.at), '). Данные ниже могут быть устаревшими.'))));
}

export function renderLoading(main) {
  main.setAttribute('aria-busy', 'true');
  main.replaceChildren(h('p', { class: 'loading' }, 'Загрузка…'));
}

export function renderPage(main, ...children) {
  main.removeAttribute('aria-busy');
  main.replaceChildren(...children);
}

export function emptyState({ iconName, title, text, note = null, details = null, error = false, action = null }) {
  return h('section', { class: `empty-state${error ? ' empty-state--error' : ''}`, role: error ? 'alert' : null },
    icon(iconName, 40, 1.5),
    h('h2', {}, title),
    h('p', {}, text),
    note ? h('p', { class: 'hint' }, note) : null,
    details ? h('p', { class: 'hint mono' }, details) : null,
    action);
}

export function renderLoadError(main, error, retry) {
  console.error(error);
  renderPage(main, emptyState({
    iconName: 'alert',
    title: 'Не удалось загрузить данные',
    text: 'Результаты проверок не потеряны — попробуйте загрузить их ещё раз.',
    details: error.message,
    error: true,
    action: h('button', { type: 'button', class: 'btn btn--primary', onclick: retry }, icon('retry', 17, 2), 'Повторить'),
  }));
}
