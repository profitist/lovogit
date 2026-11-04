import unittest
from datetime import UTC, datetime

from lovogit.app.services.status import CheckSummaryDTO, ProjectStatusDTO
from lovogit.bot.status_format import (
    STATUS_LABELS,
    format_overview,
    project_block,
    split_messages,
)
from lovogit.db import DependencyStatus, RunState

NOW = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)


def project(state=None, counts=None, name="backend", error=None):
    run = (
        CheckSummaryDTO(
            1, state, NOW, None if state == RunState.RUNNING else NOW, error
        )
        if state
        else None
    )
    return ProjectStatusDTO(
        1, name, 3, run, NOW if counts is not None else None, counts
    )


class StatusFormatTests(unittest.TestCase):
    def test_no_projects(self):
        self.assertIn("Проекты не настроены", format_overview([])[0])
        self.assertIn("/add_repo <github-url>", format_overview([])[0])

    def test_no_checks_and_no_fake_zeroes(self):
        text = project_block(project())
        self.assertIn("backend (ID: 1)", text)
        self.assertIn("Текущих зависимостей: 3", text)
        self.assertIn("Проверки ещё не выполнялись", text)
        self.assertIn("Оценки обновлений пока нет", text)
        self.assertNotIn("Актуальна: 0", text)

    def test_success_all_six_statuses_and_empty_success(self):
        for count in (0, 1):
            text = project_block(
                project(RunState.SUCCEEDED, dict.fromkeys(DependencyStatus, count))
            )
            for label in STATUS_LABELS.values():
                self.assertIn(f"{label}: {count}", text)
            self.assertIn("01.11.2026 12:30:00 UTC", text)
            self.assertIn("Результат: завершена", text)
            self.assertIn("Низкий риск не гарантирует совместимость", text)

    def test_failed_and_running_with_and_without_snapshot(self):
        for state in (RunState.FAILED, RunState.RUNNING):
            for counts in (None, dict.fromkeys(DependencyStatus, 0)):
                text = project_block(
                    project(state, counts, error="Источник недоступен")
                )
                self.assertIn("UTC", text)
                if state == RunState.FAILED:
                    self.assertIn("Результат: ошибка", text)
                    self.assertIn("Причина: Источник недоступен", text)
                else:
                    self.assertIn("Проверка выполняется с", text)
                if counts is None:
                    self.assertIn("Оценки обновлений пока нет", text)
                else:
                    self.assertIn("могут быть устаревшими", text)
                    self.assertIn("Оценки по снимку:", text)

    def test_unicode_and_long_reason_are_preserved_without_markup(self):
        reason = "<b>SQL?</b> *text* 😀" * 1000
        item = project(RunState.FAILED, name="<backend>_😀", error=reason)
        messages = format_overview([item] * 20)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m.encode("utf-16-le")) // 2 <= 4096 for m in messages))
        text = "".join(messages)
        expected = "LovoGit — статус проектов\n\n" + "\n\n".join(
            [project_block(item)] * 20
        )
        self.assertEqual(text, expected)
        self.assertEqual(text.count(reason), 20)

    def test_splitting_prefers_blocks_and_lines_without_losing_characters(self):
        for text in ("a" * 4096, "😀" * 5000, "a\n\nb\nccccc", "abc\ndefghijkl"):
            with self.subTest(text=text[:20]):
                chunks = split_messages(text, 10)
                self.assertEqual("".join(chunks), text)
                self.assertTrue(
                    all(0 < len(c.encode("utf-16-le")) // 2 <= 10 for c in chunks)
                )
        self.assertEqual(split_messages("abc\n\ndefghijkl", 10)[0], "abc\n\n")
        self.assertEqual(split_messages("abc\ndefghijkl", 10)[0], "abc\n")
