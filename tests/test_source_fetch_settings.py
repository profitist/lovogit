import unittest

from pydantic import ValidationError

from lovogit.cfg.source_fetch import SourceFetchSettings


class SourceFetchSettingsTests(unittest.TestCase):
    def test_defaults_and_zero_redirects(self):
        settings = SourceFetchSettings(_env_file=None, SOURCE_FETCH_MAX_REDIRECTS=0)
        self.assertEqual(settings.SOURCE_FETCH_MAX_REDIRECTS, 0)
        self.assertEqual(settings.SOURCE_FETCH_MAX_SOURCES, 10)
        self.assertEqual(settings.SOURCE_FETCH_CONNECT_TIMEOUT, 5)
        self.assertEqual(settings.SOURCE_FETCH_READ_TIMEOUT, 10)
        self.assertEqual(settings.SOURCE_FETCH_SOURCE_DEADLINE, 20)
        self.assertEqual(settings.SOURCE_FETCH_DEADLINE, 60)
        self.assertEqual(settings.SOURCE_FETCH_MAX_RESPONSE_BYTES, 2 * 1024 * 1024)

    def test_invalid_boundaries(self):
        for field, value in (
            ("SOURCE_FETCH_MAX_REDIRECTS", -1),
            ("SOURCE_FETCH_MAX_SOURCES", 0),
            ("SOURCE_FETCH_CONNECT_TIMEOUT", 0),
            ("SOURCE_FETCH_READ_TIMEOUT", 0),
            ("SOURCE_FETCH_SOURCE_DEADLINE", 0),
            ("SOURCE_FETCH_DEADLINE", 0),
            ("SOURCE_FETCH_MAX_RESPONSE_BYTES", 0),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                SourceFetchSettings(_env_file=None, **{field: value})
