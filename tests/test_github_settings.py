import unittest

from pydantic import ValidationError

from lovogit.app.providers.errors import InvalidRepoURL
from lovogit.app.providers.urls import parse_github_url
from lovogit.cfg.github import GitHubSettings


class SettingsTests(unittest.TestCase):
    def test_independent_settings_and_limits(self):
        settings = GitHubSettings(_env_file=None, GITHUB_TOKEN="secret")
        self.assertEqual(settings.GITHUB_DEADLINE, 120)
        self.assertNotIn("secret", repr(settings))
        for key, value in [
            ("GITHUB_TOKEN", " "),
            ("GITHUB_MAX_ENTRIES", 0),
            ("GITHUB_DEADLINE", -1),
        ]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                GitHubSettings(_env_file=None, **{"GITHUB_TOKEN": "x", key: value})

    def test_urls(self):
        for suffix in ["", "/", ".git", ".git/"]:
            self.assertEqual(
                parse_github_url("https://github.com/team/repo" + suffix).name, "repo"
            )
        for url in [
            "git@github.com:a/b",
            "http://github.com/a/b",
            "https://evil.com/a/b",
            "https://x@github.com/a/b",
            "https://github.com:443/a/b",
            "https://github.com/a/b/tree/main",
            "https://github.com/a/b?",
            "https://github.com/a/b#",
            "https://github.com/a/..",
            "https://github.com/a/b\\c",
        ]:
            with self.subTest(url=url), self.assertRaises(InvalidRepoURL):
                parse_github_url(url)
