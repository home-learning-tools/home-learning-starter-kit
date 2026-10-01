"""配布manifestの完全性と、危険な入力を拒否する契約。"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from kit.distribution import ALLOWLIST, MANIFEST, generate, verify


class DistributionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "kit").mkdir()
        (self.root / "package.json").write_text('{"version":"0.1.0"}', encoding="utf-8")
        (self.root / "kit/core.py").write_bytes(b"core\n")
        self.paths = [ALLOWLIST, "package.json", "kit/core.py"]
        self.write_allowlist(self.paths)

    def write_allowlist(self, paths):
        (self.root / ALLOWLIST).write_text(
            json.dumps({"schema_version": 1, "files": paths}), encoding="utf-8")

    def test_reproducible_manifest_and_self_exclusion(self):
        generate(self.root)
        first = (self.root / MANIFEST).read_bytes()
        generate(self.root)
        self.assertEqual(first, (self.root / MANIFEST).read_bytes())
        manifest = verify(self.root)
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertNotIn(MANIFEST, [entry["path"] for entry in manifest["files"]])
        entry = next(item for item in manifest["files"] if item["path"] == "kit/core.py")
        self.assertEqual(entry["size"], 5)
        self.assertEqual(entry["sha256"], hashlib.sha256(b"core\n").hexdigest())

    def test_missing_changed_and_extra_files_are_rejected(self):
        generate(self.root)
        core = self.root / "kit/core.py"
        core.write_bytes(b"edit\n")
        with self.assertRaises(ValueError):
            verify(self.root)
        core.unlink()
        with self.assertRaises(ValueError):
            verify(self.root)
        core.write_bytes(b"core\n")
        extra = self.root / "kit/unlisted.py"
        extra.write_bytes(b"extra")
        with self.assertRaises(ValueError):
            verify(self.root)
        extra.unlink()
        verify(self.root)

    def test_unsafe_and_duplicate_paths_are_rejected(self):
        for path in ("/absolute", "../outside", "kit/../outside", "kit//x", "kit/./x",
                     "kit\\x", "C:/x", "kit/x\n", "workspace/record.md", "dist/index.html",
                     ".git/config", ".GIT/config", "WORKSPACE/record.md", "kit/__pycache__/x",
                     MANIFEST, MANIFEST.upper(), "KIT/CORE.PY", "kit/core.py"):
            with self.subTest(path=path):
                self.write_allowlist(self.paths + [path])
                with self.assertRaises(ValueError):
                    generate(self.root)

    def test_symlinks_in_files_directories_and_manifest_are_rejected(self):
        generate(self.root)
        for name in ("kit/core.py", MANIFEST):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                path.symlink_to(self.root / "package.json")
                with self.assertRaises(ValueError):
                    verify(self.root)
                path.unlink()
                path.write_bytes(original)
        directory = self.root / "linked"
        directory.symlink_to(self.root / "kit", target_is_directory=True)
        with self.assertRaises(ValueError):
            generate(self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOのある環境のみ")
    def test_special_file_is_rejected_without_reading_it(self):
        os.mkfifo(self.root / "kit/pipe")
        with self.assertRaises(ValueError):
            generate(self.root)

    def test_invalid_json_schema_and_manifest_changes_are_rejected(self):
        for value in ('{"schema_version":1,"schema_version":1,"files":[]}',
                      '{"schema_version":true,"files":[]}', '[]', '{broken'):
            with self.subTest(value=value):
                (self.root / ALLOWLIST).write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    generate(self.root)
        self.write_allowlist(self.paths)
        generate(self.root)
        original = (self.root / MANIFEST).read_text(encoding="utf-8")
        for field, value in (("schema_version", True), ("version", "0.2.0"), ("files", [])):
            manifest = json.loads(original)
            manifest[field] = value
            (self.root / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify(self.root)

    def test_git_and_python_cache_are_excluded_but_workspace_is_not(self):
        generate(self.root)
        for name in (".git/config", "kit/__pycache__/core.cpython-311.pyc"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"development")
        verify(self.root)
        (self.root / "workspace").mkdir()
        (self.root / "workspace/record.md").write_bytes(b"record")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_failed_generation_preserves_the_previous_manifest(self):
        generate(self.root)
        original = (self.root / MANIFEST).read_bytes()
        (self.root / "unexpected").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            generate(self.root)
        self.assertEqual(original, (self.root / MANIFEST).read_bytes())


if __name__ == "__main__":
    unittest.main()
