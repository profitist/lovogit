from lovogit.app.services.attention import ProjectAttentionDTO
from lovogit.bot.status_format import STATUS_LABELS, format_time, split_messages
from lovogit.db import RunState


def _project_block(project: ProjectAttentionDTO) -> str:
    lines = [f"{project.project_name} (ID: {project.project_id})"]
    run = project.latest_run
    if run is None:
        lines.append("Проверки ещё не выполнялись")
    elif run.state == RunState.RUNNING:
        lines.append(f"Проверка выполняется с {format_time(run.started_at)}")
    elif run.state == RunState.FAILED:
        lines.extend(
            [
                f"Последняя проверка: {format_time(run.finished_at)}",
                "Результат: ошибка",
                f"Причина: {run.error}",
            ]
        )
    if project.snapshot_id is None:
        lines.append("Оценки обновлений пока нет")
    else:
        lines.append(f"Оценки по снимку: {format_time(project.latest_snapshot_at)}")
        if run is not None and run.state != RunState.SUCCEEDED:
            lines.append("Данные предыдущей успешной проверки могут быть устаревшими")
        if project.dependencies:
            lines.extend(
                f"{dependency.name}: {dependency.current_version} → "
                f"{dependency.latest_version if dependency.latest_version is not None else 'неизвестна'}"
                f" — {STATUS_LABELS[dependency.status]}"
                for dependency in project.dependencies
            )
        else:
            lines.append("По этому снимку зависимостей, требующих внимания, нет")
    return "\n".join(lines)


def format_attention(projects: list[ProjectAttentionDTO]) -> list[str]:
    if not projects:
        return [
            "Проекты не настроены. Добавьте репозиторий: /add_repo <github-url>"
        ]
    text = "LovoGit — требует внимания\n\n" + "\n\n".join(map(_project_block, projects))
    if any(project.snapshot_id is not None for project in projects):
        text += (
            "\n\nОтсутствие требующих внимания зависимостей не гарантирует совместимость; "
            "ожидающие анализа зависимости ещё не оценены"
        )
    return split_messages(text)
