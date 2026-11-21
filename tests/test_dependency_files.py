import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from lovogit.app.dependencies.dto import InventoryLimits, ProvenanceDTO
from lovogit.app.dependencies.files import InventoryFileError, SnapshotReader


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "sub").mkdir()
        (self.root / "data.txt").write_bytes(b"abc")
        self.origin = ProvenanceDTO("requirements.txt", start_line=1, end_line=1)

    def test_relative_paths_hashes_and_cache(self):
        reader = SnapshotReader(
            self.root, InventoryLimits(max_files=1, max_total_bytes=3)
        )
        path = reader.locate("../data.txt", self.origin, parent="sub/req.txt")
        self.assertEqual(reader.read(path, self.origin), b"abc")
        self.assertEqual(reader.read(path, self.origin), b"abc")
        self.assertEqual(reader.files[0].sha256, hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(len(reader.files), 1)

    def test_forbidden_paths_symlinks_and_special_files(self):
        (self.root / "alias").symlink_to(self.root / "sub", target_is_directory=True)
        os.mkfifo(self.root / "fifo")
        reader = SnapshotReader(self.root, InventoryLimits())
        for path, code in (
            ("../outside", "outside_snapshot"),
            ("/etc/passwd", "forbidden_path"),
            ("https://example.com/a", "forbidden_path"),
            ("alias/file", "symlink"),
            ("fifo", "special_file"),
            ("missing", "missing_file"),
        ):
            with (
                self.subTest(path=path),
                self.assertRaises(InventoryFileError) as error,
            ):
                reader.read(path, self.origin)
            self.assertEqual(error.exception.code, code)
        (self.root / "sub/data").symlink_to(self.root / "data.txt")
        with self.assertRaises(InventoryFileError):
            reader.read("sub/data", self.origin)

    def test_budgets_stop_further_reads(self):
        (self.root / "second.txt").write_bytes(b"xy")
        for limits, code, first in (
            (InventoryLimits(max_file_bytes=2), "file_size_limit", False),
            (InventoryLimits(max_total_bytes=4), "total_size_limit", True),
            (InventoryLimits(max_files=1), "file_count_limit", True),
        ):
            reader = SnapshotReader(self.root, limits)
            if first:
                reader.read("data.txt", self.origin)
            with (
                self.subTest(code=code),
                self.assertRaises(InventoryFileError) as error,
            ):
                reader.read("second.txt" if first else "data.txt", self.origin)
            self.assertEqual(error.exception.code, code)
            with self.assertRaises(InventoryFileError) as error:
                reader.read("data.txt", self.origin)
            self.assertEqual(error.exception.code, "budget_exhausted")

    def test_record_budget(self):
        reader = SnapshotReader(self.root, InventoryLimits(max_records=2))
        reader.consume_records(2, self.origin)
        with self.assertRaises(InventoryFileError) as error:
            reader.consume_records(1, self.origin)
        self.assertEqual(error.exception.code, "record_limit")

    def test_depth_budget(self):
        reader = SnapshotReader(self.root, InventoryLimits(max_depth=1))
        reader.consume_records(0, self.origin, depth=1)
        with self.assertRaises(InventoryFileError) as error:
            reader.consume_records(0, self.origin, depth=2)
        self.assertEqual(error.exception.code, "include_depth_limit")
