// Чистая логика экранов без обращения к DOM: её проверяют тесты в tests/.

// Порядок ключей — порядок срочности: в нём идут счётчики и сортируется реестр.
export const STATUSES = {
  incompat: { label: 'Вероятна несовместимость', short: 'Несовместимость', hint: 'Изменённое или удалённое API найдено в коде проекта' },
  review: { label: 'Требует проверки', short: 'Проверить', hint: 'Влияние изменений не удалось подтвердить или исключить' },
  failed: { label: 'Анализ не удался', short: 'Не удался', hint: 'Новая версия найдена, но проанализировать её не получилось' },
  waiting: { label: 'Ждёт анализа', short: 'Ждёт', hint: 'Новая версия найдена, анализ ещё идёт' },
  low: { label: 'Низкий риск', short: 'Низкий риск', hint: 'Анализ не нашёл изменений, затрагивающих проект' },
  current: { label: 'Актуальна', short: 'Актуальна', hint: 'Новее текущей версии нет' },
};
export const STATUS_ORDER = Object.keys(STATUSES);

export function assertStatus(status) {
  if (!Object.hasOwn(STATUSES, status)) throw new Error(`Неизвестный статус зависимости: ${status}`);
  return status;
}

function compareDeps(a, b, key) {
  const byName = a.name.localeCompare(b.name);
  if (key === 'status') return STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || byName;
  // Без анализа числа мест нет — такие зависимости считаются «меньше нуля».
  if (key === 'places') return (a.places ?? -1) - (b.places ?? -1) || byName;
  return String(a[key] ?? '').localeCompare(String(b[key] ?? ''), 'en', { numeric: true }) || byName;
}

export function sortDeps(deps, key, dir) {
  return [...deps].sort((a, b) => dir * compareDeps(a, b, key));
}

export function filterDeps(deps, { filter, query }) {
  const needle = query.trim().toLowerCase();
  return deps
    .filter((dep) => !filter || dep.status === filter)
    .filter((dep) => !needle || dep.name.toLowerCase().includes(needle));
}

// forms: [одна, две, пять] — «место», «места», «мест».
export function plural(n, [one, few, many]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} ${few}`;
  return `${n} ${many}`;
}

export function placesSummary(places) {
  const files = new Set(places.map((place) => place.file)).size;
  return `${plural(places.length, ['место', 'места', 'мест'])} в ${plural(files, ['файле', 'файлах', 'файлах'])}`;
}

const RELATIVE = new Intl.RelativeTimeFormat('ru', { style: 'short', numeric: 'always' });
const UNITS = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

export function relativeTime(date, now = new Date()) {
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.trunc(seconds / size), unit);
  }
  return 'только что';
}

// Фрагменты в `обратных кавычках` в текстах отчёта — имена из кода.
export function splitInlineCode(text) {
  return text
    .split('`')
    .map((part, index) => ({ code: index % 2 === 1, text: part }))
    .filter((part) => part.text !== '');
}
