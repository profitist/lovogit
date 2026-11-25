import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lovogit.app.project_update.artifacts import create_run_directory
from lovogit.app.project_update.dto import ProjectUpdateRequest, ProjectUpdateResultDTO
from lovogit.app.project_update.report import highlight, save_report
from lovogit.cfg.github import GitHubSettings
from lovogit.cli import analyze_update


class ProjectCLITests(unittest.TestCase):
    def test_help_and_argument_validation(self):
        for args, code in [(["--help"], 0), ([], 2)]:
            with (
                self.subTest(args=args),
                redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                analyze_update.main(args)
            self.assertEqual(raised.exception.code, code)

    def test_unique_paths_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            self.assertNotEqual(
                create_run_directory(checkout, "Example.Pkg"),
                create_run_directory(checkout, "Example_Pkg"),
            )
            for status, code in [
                ("completed", 0),
                ("no_update", 0),
                ("skipped", 0),
                ("partial", 2),
                ("failed", 1),
            ]:
                result = ProjectUpdateResultDTO(
                    "run", ProjectUpdateRequest("url", "example"), status=status
                )
                with (
                    patch.object(analyze_update.Path, "cwd", return_value=checkout),
                    patch.object(
                        analyze_update, "execute", AsyncMock(return_value=result)
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(
                        analyze_update.main(["--repo", "url", "--package", "example"]),
                        code,
                    )

    def test_storage_failure_and_keyboard_interrupt(self):
        for failure, expected in [(OSError("disk"), 1), (KeyboardInterrupt(), 130)]:
            with (
                patch.object(
                    analyze_update, "create_run_directory", side_effect=failure
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    analyze_update.main(["--repo", "url", "--package", "example"]),
                    expected,
                )

    def test_html_escaping_offsets_and_standalone_report(self):
        text = '<script>α "quoted"</script>'
        result = highlight(text, [(8, 9), (8, 11), (-1, 4)], "source-0")
        self.assertNotIn("<script>", result)
        self.assertIn('id="source-0-8"', result)
        self.assertIn("<mark><u>α", result)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            dto = ProjectUpdateResultDTO("run", ProjectUpdateRequest("url", "<script>"))
            save_report(dto, folder)
            page = (folder / "report.html").read_text()
            self.assertIn("&lt;script&gt;", page)
            self.assertNotIn("<script>", page)
            self.assertNotIn('src="https://', page)
            self.assertEqual(
                json.loads((folder / "report.json").read_text())["format_version"], 1
            )


class ExecuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_failure_preserves_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            result = ProjectUpdateResultDTO(
                "run", ProjectUpdateRequest("url", "pkg"), status="completed"
            )
            with (
                patch.object(
                    analyze_update,
                    "GitHubSettings",
                    return_value=GitHubSettings(
                        _env_file=None, GITHUB_TOKEN="test-key"
                    ),
                ),
                patch.object(
                    analyze_update,
                    "analyze_project_update",
                    AsyncMock(return_value=result),
                ),
                patch.object(
                    analyze_update, "TraceExportSettings", return_value=object()
                ),
                patch.object(
                    analyze_update,
                    "export_trace",
                    AsyncMock(return_value={"status": "failed"}),
                ),
                patch.object(
                    analyze_update,
                    "UpdateModelSettings",
                    side_effect=AssertionError("LLM must remain lazy"),
                ),
            ):
                actual = await analyze_update.execute(
                    result.request, folder, langfuse=True
                )
            self.assertEqual(actual.status, "completed")
            self.assertEqual(actual.export["status"], "failed")
            self.assertTrue((folder / "report.html").exists())

    async def test_cancel_does_not_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with (
                patch.object(
                    analyze_update,
                    "GitHubSettings",
                    return_value=GitHubSettings(_env_file=None, GITHUB_TOKEN="key"),
                ),
                patch.object(
                    analyze_update,
                    "analyze_project_update",
                    AsyncMock(side_effect=asyncio.CancelledError),
                ),
                patch.object(analyze_update, "export_trace", AsyncMock()) as export,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await analyze_update.execute(
                        ProjectUpdateRequest("url", "pkg"), folder, langfuse=True
                    )
                export.assert_not_called()

    async def test_setup_failure_saves_report_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(
                analyze_update, "GitHubSettings", side_effect=ValueError("secret-value")
            ):
                result = await analyze_update.execute(
                    ProjectUpdateRequest("url", "pkg"), folder
                )
            self.assertEqual(result.status, "failed")
            self.assertNotIn("secret-value", (folder / "report.json").read_text())
