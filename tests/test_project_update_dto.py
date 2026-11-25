import json
import unittest
from dataclasses import asdict
from types import SimpleNamespace

from lovogit.app.project_update.dto import (
    ChangeSearchDTO,
    ProjectUpdateRequest,
    ProjectUpdateResultDTO,
    analysis_status,
)


class ProjectUpdateDTOTests(unittest.TestCase):
    def test_serialization_and_stage_defaults(self):
        result = ProjectUpdateResultDTO("run-1", ProjectUpdateRequest("url", "httpx"))
        value = json.loads(json.dumps(asdict(result)))
        self.assertEqual(value["format_version"], 1)
        self.assertEqual(value["stages"]["compare"]["outcome"], "not_run")

    def test_status_table(self):
        cases = [
            (None, None, None, "failed"),
            ("no_update", None, None, "no_update"),
            ("skipped", None, None, "skipped"),
            ("selected", "failed", None, "failed"),
            ("selected", "no_materials", None, "partial"),
            ("selected", "completed", None, "completed"),
            ("selected", "completed", "timeout", "partial"),
        ]
        for selection, extraction, reason, expected in cases:
            with self.subTest(expected=expected, reason=reason):
                result = ProjectUpdateResultDTO(
                    "run", ProjectUpdateRequest("url", "pkg")
                )
                if selection:
                    result.selection = SimpleNamespace(status=selection)
                if extraction:
                    result.extraction = SimpleNamespace(status=extraction)
                result.reason = reason
                self.assertEqual(analysis_status(result), expected)
        result.searches.append(ChangeSearchDTO("change-1"))
        self.assertEqual(analysis_status(result), "partial")
