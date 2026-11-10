"""Форматирование DTO и разбиение сводки без обращения к Telegram."""

from datetime import UTC, datetime

from lovogit.app.services.status import ProjectStatusDTO
from lovogit.db import DependencyStatus, RunState

STATUS_LABELS = {
    DependencyStatus.INCOMPAT: "Вероятна несовместимость",
    DependencyStatus.REVIEW: "Требует проверки",
    DependencyStatus.FAILED: "Анализ не удался",
    DependencyStatus.WAITING: "Ждёт анализа",
    DependencyStatus.LOW: "Низкий риск",
    DependencyStatus.CURRENT: "Актуальна",
}


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M:%S UTC")


def project_block(project: ProjectStatusDTO) -> str:
    lines = [
        f"{project.project_name} (ID: {project.project_id})",
        f"Текущих зависимостей: {project.dependencies_count}",
    ]
    run = project.latest_run
    if run is None:
        lines.append("Проверки ещё не выполнялись")
    elif run.state == RunState.RUNNING:
        lines.append(f"Проверка выполняется с {format_time(run.started_at)}")
    else:
        lines.append(f"Последняя проверка: {format_time(run.finished_at)}")
        if run.state == RunState.FAILED:
            lines.extend(["Результат: ошибка", f"Причина: {run.error}"])
        else:
            lines.append("Результат: завершена")
    if project.status_counts is None:
        lines.append("Оценки обновлений пока нет")
    else:
        lines.append(f"Оценки по снимку: {format_time(project.latest_snapshot_at)}")
        if run is not None and run.state != RunState.SUCCEEDED:
            lines.append("Данные предыдущей успешной проверки могут быть устаревшими")
        lines.extend(
            f"{label}: {project.status_counts[status]}"
            for status, label in STATUS_LABELS.items()
        )
        lines.append("Низкий риск не гарантирует совместимость")
    return "\n".join(lines)


def split_messages(text: str, limit: int = 4096) -> list[str]:
    """Сохранить текст целиком, предпочитая границы блоков и строк."""
    if limit < 2:
        raise ValueError("Лимит должен вмещать любой Unicode-символ")
    messages = []
    while text:
        units = 0
        end = 0
        for index, char in enumerate(text):
            width = 2 if ord(char) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end = index + 1
        if end < len(text):
            boundary = text.rfind("\n\n", 0, end)
            if boundary >= 0:
                end = boundary + 2
            else:
                boundary = text.rfind("\n", 0, end)
                if boundary >= 0:
                    end = boundary + 1
        messages.append(text[:end])
        text = text[end:]
    return messages


def format_overview(projects: list[ProjectStatusDTO]) -> list[str]:
    if not projects:
        return ["Проекты не настроены. Добавьте репозиторий: /add_repo <github-url>"]
    return split_messages(
        "LovoGit — статус проектов\n\n" + "\n\n".join(map(project_block, projects))
    )
