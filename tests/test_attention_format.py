import unittest
from dataclasses import replace
from datetime import UTC, datetime

from lovogit.app.services.attention import AttentionDependencyDTO, ProjectAttentionDTO
from lovogit.app.services.status import CheckSummaryDTO
from lovogit.bot.attention_format import format_attention
from lovogit.db import DependencyStatus, RunState

TIME = datetime(2026, 11, 3, 12, 30, tzinfo=UTC)


def project():
    return ProjectAttentionDTO(
        project_id=1,
        project_name="backend",
        latest_run=CheckSummaryDTO(1, RunState.SUCCEEDED, TIME, TIME, None),
        snapshot_id=1,
        latest_snapshot_at=TIME,
        dependencies=(
            AttentionDependencyDTO("requests", "1", "2", DependencyStatus.INCOMPAT),
            AttentionDependencyDTO("pydantic", "3", None, DependencyStatus.REVIEW),
            AttentionDependencyDTO("fastapi", "4", "5", DependencyStatus.FAILED),
        ),
    )


class AttentionFormatTests(unittest.TestCase):
    def test_fields_labels_and_unknown_version(self):
        text = "".join(format_attention([project()]))
        for fragment in (
            "backend (ID: 1)",
            "03.11.2026 12:30:00 UTC",
            "requests: 1 → 2 — Вероятна несовместимость",
            "pydantic: 3 → неизвестна — Требует проверки",
            "fastapi: 4 → 5 — Анализ не удался",
            "не гарантирует совместимость",
            "ещё не оценены",
        ):
            self.assertIn(fragment, text)
        self.assertNotIn("Подробнее", text)
        self.assertNotIn("устаревшими", text)

    def test_missing_and_empty_are_distinct_and_mixed_projects_preserved(self):
        missing = replace(
            project(),
            project_id=2,
            project_name="worker",
            latest_run=None,
            snapshot_id=None,
            latest_snapshot_at=None,
            dependencies=(),
        )
        empty = replace(project(), project_id=3, project_name="empty", dependencies=())
        text = "".join(format_attention([project(), missing, empty]))
        self.assertIn(
            "worker (ID: 2)\nПроверки ещё не выполнялись\nОценки обновлений пока нет",
            text,
        )
        self.assertIn("empty (ID: 3)", text)
        self.assertIn("По этому снимку зависимостей, требующих внимания, нет", text)
        self.assertLess(text.index("backend"), text.index("worker"))
        self.assertLess(text.index("worker"), text.index("empty"))
        self.assertIn("Проекты не настроены", format_attention([])[0])
        self.assertIn("/add_repo <github-url>", format_attention([])[0])

    def test_running_and_failed_with_and_without_snapshot(self):
        for state in (RunState.RUNNING, RunState.FAILED):
            run = CheckSummaryDTO(
                2,
                state,
                TIME,
                TIME if state == RunState.FAILED else None,
                "Источник недоступен" if state == RunState.FAILED else None,
            )
            with self.subTest(state=state):
                current = replace(project(), latest_run=run)
                text = "".join(format_attention([current]))
                self.assertIn("могут быть устаревшими", text)
                self.assertIn("requests", text)
                self.assertIn("03.11.2026 12:30:00 UTC", text)
                if state == RunState.RUNNING:
                    self.assertIn("Проверка выполняется", text)
                else:
                    self.assertIn("Причина: Источник недоступен", text)
                text = "".join(
                    format_attention(
                        [
                            replace(
                                current,
                                snapshot_id=None,
                                latest_snapshot_at=None,
                                dependencies=(),
                            )
                        ]
                    )
                )
                self.assertIn("Оценки обновлений пока нет", text)
                self.assertNotIn("требующих внимания, нет", text)
                self.assertNotIn("устаревшими", text)

    def test_long_unicode_text_delivered_in_full_and_order(self):
        name = "<backend>_*😀" * 1000
        dependency = AttentionDependencyDTO(
            "<package>_*😀" * 1000, "1", "2", DependencyStatus.REVIEW
        )
        long = replace(project(), project_name=name, dependencies=(dependency,))
        parts = format_attention(
            [long, replace(project(), project_id=2, project_name="last-project")]
        )
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(0 < len(p.encode("utf-16-le")) // 2 <= 4096 for p in parts))
        text = "".join(parts)
        self.assertIn(name, text)
        self.assertIn(dependency.name + ": 1 → 2 — Требует проверки", text)
        self.assertLess(text.index(name), text.index(dependency.name))
        self.assertLess(text.index(dependency.name), text.index("last-project"))
        self.assertEqual(text.count(name), 1)
        self.assertEqual(text.count(dependency.name), 1)
        self.assertTrue(text.endswith("ожидающие анализа зависимости ещё не оценены"))
