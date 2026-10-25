import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  assertStatus,
  filterDeps,
  placesSummary,
  plural,
  relativeTime,
  sortDeps,
  splitInlineCode,
} from '../app/logic.js';

const DEPS = [
  { name: 'rich', current: '13.7.0', latest: '13.9.2', status: 'low', places: 0 },
  { name: 'pydantic', current: '1.10.13', latest: '2.9.2', status: 'incompat', places: 7 },
  { name: 'click', current: '8.1.7', latest: null, status: 'current', places: null },
  { name: 'httpx', current: '0.25.2', latest: '0.27.2', status: 'review', places: 2 },
  { name: 'celery', current: '5.3.6', latest: '5.10.0', status: 'review', places: 1 },
];

const names = (deps) => deps.map((dep) => dep.name);

test('по умолчанию реестр сортируется по срочности статуса, внутри статуса — по имени', () => {
  assert.deepEqual(names(sortDeps(DEPS, 'status', 1)), ['pydantic', 'celery', 'httpx', 'rich', 'click']);
});

test('обратная сортировка разворачивает порядок', () => {
  assert.deepEqual(names(sortDeps(DEPS, 'status', -1)), ['click', 'rich', 'httpx', 'celery', 'pydantic']);
});

test('версии сравниваются как числа, а не как строки', () => {
  const sorted = sortDeps([
    { name: 'a', latest: '5.10.0', status: 'review', places: 1 },
    { name: 'b', latest: '5.9.0', status: 'review', places: 1 },
  ], 'latest', 1);
  assert.deepEqual(names(sorted), ['b', 'a']);
});

test('зависимости без числа мест идут раньше нуля при сортировке по местам', () => {
  assert.deepEqual(names(sortDeps(DEPS, 'places', 1)), ['click', 'rich', 'celery', 'httpx', 'pydantic']);
});

test('сортировка не меняет исходный массив', () => {
  const before = names(DEPS);
  sortDeps(DEPS, 'name', 1);
  assert.deepEqual(names(DEPS), before);
});

test('фильтр по статусу и поиск по подстроке имени без учёта регистра', () => {
  assert.deepEqual(names(filterDeps(DEPS, { filter: 'review', query: '' })), ['httpx', 'celery']);
  assert.deepEqual(names(filterDeps(DEPS, { filter: null, query: '  PY ' })), ['pydantic']);
  assert.deepEqual(names(filterDeps(DEPS, { filter: 'low', query: 'py' })), []);
});

test('склонение по числу', () => {
  const forms = ['место', 'места', 'мест'];
  assert.equal(plural(1, forms), '1 место');
  assert.equal(plural(3, forms), '3 места');
  assert.equal(plural(5, forms), '5 мест');
  assert.equal(plural(11, forms), '11 мест');
  assert.equal(plural(12, forms), '12 мест');
  assert.equal(plural(21, forms), '21 место');
  assert.equal(plural(0, forms), '0 мест');
});

test('сводка мест считает уникальные файлы', () => {
  const places = [{ file: 'a.py' }, { file: 'a.py' }, { file: 'b.py' }];
  assert.equal(placesSummary(places), '3 места в 2 файлах');
  assert.equal(placesSummary([{ file: 'a.py' }]), '1 место в 1 файле');
});

test('относительное время в прошлом и будущем', () => {
  const now = new Date('2026-10-25T12:52:00Z');
  assert.equal(relativeTime(new Date('2026-10-25T12:40:00Z'), now), '12 мин. назад');
  assert.equal(relativeTime(new Date('2026-10-25T09:52:00Z'), now), '3 ч назад');
  assert.equal(relativeTime(new Date('2026-10-25T13:40:00Z'), now), 'через 48 мин.');
  assert.equal(relativeTime(new Date('2026-10-25T12:51:40Z'), now), 'только что');
});

test('текст в обратных кавычках становится кодом', () => {
  assert.deepEqual(splitInlineCode('импорт `BaseSettings` из `pydantic`'), [
    { code: false, text: 'импорт ' },
    { code: true, text: 'BaseSettings' },
    { code: false, text: ' из ' },
    { code: true, text: 'pydantic' },
  ]);
  assert.deepEqual(splitInlineCode('без кода'), [{ code: false, text: 'без кода' }]);
});

test('неизвестный статус — ошибка, а не молчаливый пропуск', () => {
  assert.equal(assertStatus('review'), 'review');
  assert.throws(() => assertStatus('safe'), /Неизвестный статус зависимости: safe/);
});
