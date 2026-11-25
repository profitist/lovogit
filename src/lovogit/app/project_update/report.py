import html
import json
from itertools import pairwise

from lovogit.app.project_update.artifacts import json_value, write_json


def highlight(text, ranges, prefix):
    ranges = [(start, end) for start, end in ranges if 0 <= start < end <= len(text)]
    boundaries = sorted({0, len(text), *(point for pair in ranges for point in pair)})
    parts = []
    for start, end in pairwise(boundaries):
        if any(left == start for left, _ in ranges):
            parts.append(f'<span id="{prefix}-{start}"></span>')
        value = html.escape(text[start:end])
        parts.append(
            f"<mark><u>{value}</u></mark>"
            if any(left <= start < right for left, right in ranges)
            else value
        )
    return "".join(parts)


def save_report(result, folder):
    write_json(folder / "report.json", result)
    data = json_value(result)
    trace_path = folder / "trace.json"
    trace = (
        json.loads(trace_path.read_text(encoding="utf-8"))
        if trace_path.exists()
        else {}
    )
    extraction = data.get("extraction") or {}
    changes = extraction.get("changes", [])
    articles, ranges = [], {}
    for index, change in enumerate(changes):
        refs = []
        evidence = change["evidence"] + (
            [change["version_evidence"]] if change.get("version_evidence") else []
        )
        for item in evidence:
            source_id = item["source_id"]
            ranges.setdefault(source_id, []).append((item["start"], item["end"]))
            refs.append(
                f'<a href="#{source_id}-{item["start"]}">{html.escape(source_id)}: {item["start"]}–{item["end"]}</a>'
            )
        articles.append(
            f'<article id="change-{index}"><h3>change-{index} · {html.escape(change["symbol"] or change["category"])}</h3>'
            f"<p>{html.escape(change['description'])}</p><p>{' · '.join(refs)}</p></article>"
        )
    materials = []
    for index, source in enumerate((data.get("fetching") or {}).get("results", [])):
        source_id = f"source-{index}"
        materials.append(
            f"<details><summary>{source_id} · {html.escape(source['status'])} · {html.escape(source.get('final_url') or source.get('requested_url') or '')}</summary>"
            f"<pre>{highlight(source.get('text') or '', ranges.get(source_id, []), source_id)}</pre></details>"
        )
    pretty = lambda value: html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    searches = []
    for search in data["searches"]:
        searches.append(
            f'<article><h3><a href="#{search["change_id"]}">{search["change_id"]}</a> · {html.escape(search["outcome"])}</h3>'
            f"<pre>{pretty(search)}</pre></article>"
        )
    raw = []
    for span in trace.get("spans", []):
        if span["kind"] in {"generation", "tool"}:
            raw.append(
                f"<details><summary>{html.escape(span['kind'])} · {html.escape(span['name'])}</summary><pre>{pretty(span)}</pre></details>"
            )
    sections = [
        (
            "stages",
            "Этапы и диагностика",
            f"<pre>{pretty({'stages': data['stages'], 'reason': data['reason'], 'diagnostics': data['diagnostics'], 'export': data['export']})}</pre>",
        ),
        (
            "selection",
            "Зависимость и выбор версии",
            f"<pre>{pretty({'repo': data['repo'], 'selection': data['selection'], 'inventory': data['inventory']})}</pre>",
        ),
        (
            "changes",
            "Принятые изменения",
            "".join(articles)
            or "<p>Принятых изменений нет. Это не доказывает отсутствие влияния.</p>",
        ),
        (
            "code",
            "Потенциально затронутый код",
            "".join(searches) or "<p>Поиск не выполнялся.</p>",
        ),
        (
            "materials",
            "Материалы и использованные цитаты",
            "".join(materials) or "<p>Материалов нет.</p>",
        ),
        (
            "raw",
            "Сырые ответы модели и инструменты",
            "".join(raw) or "<p>Вызовов модели не было.</p>",
        ),
        (
            "rejected",
            "Неподтверждённые и отклонённые утверждения",
            f"<pre>{pretty({'unscoped': extraction.get('unscoped_changes', []), 'rejected': extraction.get('rejected', [])})}</pre>",
        ),
        (
            "limits",
            "Ограничения",
            f"<p>Найденные места — кандидаты для проверки. Совпадение цитаты не доказывает правильность интерпретации. Тесты не выполнялись. Совместимость не определена.</p><pre>{pretty(data['limitations'])}</pre>",
        ),
    ]
    title = html.escape(f"{data['request']['package']} · {data['status']}")
    nav = " ".join(f'<a href="#{key}">{label}</a>' for key, label, _ in sections)
    body = "".join(
        f'<section id="{key}"><h2>{label}</h2>{content}</section>'
        for key, label, content in sections
    )
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#f4f3ee;color:#202d2b;font:16px/1.65 system-ui}}main{{max-width:1080px;margin:auto;padding:36px 20px}}section{{background:white;margin:24px 0;padding:24px;border:1px solid #d8dfd9;border-radius:12px}}h1{{font-size:36px}}h2{{font-size:24px}}a{{color:#17614e;text-underline-offset:3px}}nav{{display:flex;gap:12px;flex-wrap:wrap}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 ui-monospace,monospace;background:#f7f8f5;padding:16px;border-radius:8px}}mark{{background:#fff0a8}}summary{{cursor:pointer;padding:12px 0}}article{{border-top:1px solid #d8dfd9;padding:12px 0}}:target{{scroll-margin-top:24px}}a:focus-visible,summary:focus-visible{{outline:3px solid #17614e}}@media(max-width:600px){{main{{padding:20px 12px}}section{{padding:16px}}h1{{font-size:28px}}}}</style>
<main><p>LOVOGIT · АНАЛИЗ ОБНОВЛЕНИЯ</p><h1>{title}</h1><p>{html.escape(data["run_id"])}</p><nav>{nav}</nav>{body}<footer>Локальные файлы: report.json · trace.json · langfuse-otel.json</footer></main></html>"""
    temporary = folder / "report.html.tmp"
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(folder / "report.html")
