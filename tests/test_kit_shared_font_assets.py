"""共有静的フォントの参照・完全性・境界の回帰検査。"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from kit.starter_web.shared_font_assets import (
    css_dependencies, verify_manifest, worksheet_references,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SharedFontAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.materials = Path(self.temp.name) / "materials"
        self.group = self.materials / "shared/assets/fonts/example"
        self.group.mkdir(parents=True)
        font = (b"wOF2" + b"OTTO" + (48).to_bytes(4, "big")
                + (1).to_bytes(2, "big") + b"\0\0"
                + (64).to_bytes(4, "big") + (1).to_bytes(4, "big")
                + b"\0" * 24)
        font_name = "fonts/{}.woff2".format(digest(font))
        css = ("/* test */\n@font-face {{ font-family: 'Example'; font-style: normal; "
               "font-weight: 400 700; font-display: swap; src: url({}) "
               "format('woff2'); unicode-range: U+0041-005A; }}\n".format(font_name)).encode()
        css_name = "{}.css".format(digest(css))
        (self.group / "fonts").mkdir()
        (self.group / "licenses").mkdir()
        (self.group / font_name).write_bytes(font)
        (self.group / css_name).write_bytes(css)
        license_data = b"OFL example\n"
        (self.group / "licenses/example-OFL.txt").write_bytes(license_data)
        self.css_name = css_name
        self.font_name = font_name
        self.document = {
            "schema_version": 1,
            "source_lock_sha256": "a" * 64,
            "files": sorted([
                {"path": css_name, "sha256": digest(css), "bytes": len(css), "kind": "css"},
                {"path": font_name, "sha256": digest(font), "bytes": len(font), "kind": "woff2"},
                {"path": "licenses/example-OFL.txt", "sha256": digest(license_data),
                 "bytes": len(license_data), "kind": "license"},
            ], key=lambda row: row["path"]),
            "css": [{"path": css_name, "families": ["Example"], "woff2": [font_name]}],
        }
        self.save_manifest()
        self.html = self.materials / "learners/l1/example.html"
        self.html.parent.mkdir(parents=True)
        self.href = "../../shared/assets/fonts/example/" + css_name

    def save_manifest(self):
        (self.group / "manifest.json").write_text(json.dumps(self.document), encoding="utf-8")

    def markup(self, href=None, declaration=True):
        meta = '<meta name="material-assets" content="workspace-local-v1">' if declaration else ""
        return ("<html><head>{}<link rel=\"stylesheet\" data-shared-font=\"example\" "
                "href=\"{}\"></head><body></body></html>".format(meta, href or self.href))

    def test_valid_reference_and_manifest(self):
        self.assertEqual(verify_manifest(self.group, self.materials), self.document)
        self.assertEqual(worksheet_references(self.html, self.markup()), [self.group / self.css_name])

    def test_reference_requires_declaration_and_confined_path(self):
        with self.assertRaisesRegex(ValueError, "宣言"):
            worksheet_references(self.html, self.markup(declaration=False))
        for href in ("file:///tmp/a.css", "../../../../outside.css", self.href + "?x=1"):
            with self.subTest(href=href), self.assertRaises(ValueError):
                worksheet_references(self.html, self.markup(href=href))

    def test_missing_modified_and_unregistered_assets(self):
        original = (self.group / self.css_name).read_bytes()
        (self.group / self.css_name).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "manifest"):
            verify_manifest(self.group, self.materials)
        (self.group / self.css_name).write_bytes(original)
        (self.group / self.font_name).unlink()
        with self.assertRaises(OSError):
            verify_manifest(self.group, self.materials)
        font = (b"wOF2" + b"OTTO" + (48).to_bytes(4, "big")
                + (1).to_bytes(2, "big") + b"\0\0"
                + (64).to_bytes(4, "big") + (1).to_bytes(4, "big")
                + b"\0" * 24)
        (self.group / self.font_name).write_bytes(font)
        (self.group / "extra").write_text("unregistered")
        with self.assertRaisesRegex(ValueError, "未登録"):
            verify_manifest(self.group, self.materials)
        verify_manifest(self.group, self.materials, allowed_unregistered=frozenset({"extra"}))

    def test_rejects_css_import_and_other_rules(self):
        for css in (b"@import url(x);", b"@font-face { src: url(https://evil.test/a); }",
                    b"body {color:red}"):
            with self.subTest(css=css), self.assertRaises(ValueError):
                css_dependencies(css)

    def test_rejects_symlink(self):
        (self.group / self.css_name).unlink()
        (self.group / self.css_name).symlink_to(self.group / self.font_name)
        with self.assertRaisesRegex(ValueError, "symlink"):
            verify_manifest(self.group, self.materials)


if __name__ == "__main__":
    unittest.main()
