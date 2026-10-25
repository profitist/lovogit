// Откуда брать данные, страница говорит сама через <meta name="lovogit:…">:
// прод-страницы смотрят в API бэкенда, макеты — в JSON-файлы с демо-данными.
// Формат ответа ещё согласуется с бэкендом; образец — mockups/data/.

export class ApiError extends Error {
  constructor(message, status, options) {
    super(message, options);
    this.name = 'ApiError';
    this.status = status;
  }
}

function endpoint(name) {
  const meta = document.querySelector(`meta[name="lovogit:${name}"]`);
  if (!meta) throw new Error(`На странице нет <meta name="lovogit:${name}">`);
  return meta.content;
}

async function fetchJson(url) {
  let response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (cause) {
    throw new ApiError(`GET ${url} — сервер не ответил`, null, { cause });
  }
  if (!response.ok) throw new ApiError(`GET ${url} — ${response.status}`, response.status);
  return response.json();
}

export function loadRegistry() {
  return fetchJson(endpoint('registry-url'));
}

export function loadDependency(name) {
  return fetchJson(endpoint('dependency-url').replace('{name}', encodeURIComponent(name)));
}
