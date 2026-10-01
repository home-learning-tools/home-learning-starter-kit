"""kit.starter_web.migration（スナップショット取得・復元）のテスト。

ADR 0006 の受入条件3〜11を実装側で固定する。実在の家庭データに依存せず、
public抽出物だけでも成功することを前提に書く。
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kit.starter_web import migration
from kit.starter_web.migration import (
    MigrationError,
    create_snapshot,
    load_manifest,
    plan_restore,
    resolve_snapshot_dir,
    restore_snapshot,
    verify_snapshot,
)

WORKSHEET = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="material-type" content="worksheet"><title>たしざん</title></head>
<body><ol class="problems"><li>2 + 3 =</li></ol>
<section class="answers"><h2>こたえ</h2><ol><li>5</li></ol></section></body></html>
"""

FILES = {
    "learners.md": "| 学習者ID | 呼び名 |\n|---|---|\n| learner-1 | ハル |\n",
    "schedule.md": "| 日付 | 学習者ID |\n|---|---|\n| 2026-08-03 | learner-1 |\n",
    "activity-log.md": "# 家庭学習ログ\n",
    "tests.md": "# テスト\n",
    "materials/index.md": "| 教材ID | 対象 |\n|---|---|\n| m-1 | 共通 |\n",
    "materials/shared/20260803-math.html": WORKSHEET,
    "materials/learners/learner-1/20260803-learner-1-math.html": WORKSHEET,
    # allowlist外。スナップショット・復元のどちらの対象にもならない。
    "notes.md": "家庭の方針メモ\n",
    "import/keep.txt": "作業用\n",
}


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _state(workspace: Path):
    """allowlist対象の現在の内容（パス→本文）。"""

    return {
        relative: (workspace / relative).read_text(encoding="utf-8")
        for relative in migration.collect_targets(workspace)
    }


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.workspace = self.root / "workspace" / "tutorial"
        for relative, text in FILES.items():
            _write(self.workspace, relative, text)

    def snapshot(self, stage="C", migration_date="2026-08-04"):
        return create_snapshot(
            self.workspace, migration_date, stage, writer=io.StringIO()
        )

    def restore(self, snapshot_id, answer="y\n"):
        writer = io.StringIO()
        restored = restore_snapshot(
            self.workspace,
            snapshot_id,
            reader=io.StringIO(answer),
            writer=writer,
        )
        return restored, writer.getvalue()

    def snapshot_dir(self, snapshot_id):
        return self.workspace / "import" / snapshot_id


class SnapshotTest(MigrationTestCase):
    def test_shared_font_assets_are_copied_and_restored(self):
        group = self.workspace / "materials/shared/assets/fonts/example"
        group.joinpath("fonts").mkdir(parents=True)
        files = {
            "manifest.json": b'{"schema_version":1}\n',
            "a.css": b"@font-face {}\n",
            "fonts/a.woff2": b"wOF2\0\xff",
            "licenses/OFL.txt": b"OFL fixture\n",
        }
        for name, payload in files.items():
            target = group / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        snapshot_id = self.snapshot()
        entries = verify_snapshot(self.snapshot_dir(snapshot_id))
        for name, payload in files.items():
            relative = "materials/shared/assets/fonts/example/" + name
            self.assertIn(relative, {entry.path for entry in entries})
            self.assertEqual((self.snapshot_dir(snapshot_id) / "payload" / relative).read_bytes(), payload)
        (group / "fonts/a.woff2").write_bytes(b"changed")
        self.restore(snapshot_id)
        self.assertEqual((group / "fonts/a.woff2").read_bytes(), files["fonts/a.woff2"])

    def test_snapshot_records_allowlisted_files_only(self):
        snapshot_id = self.snapshot()

        self.assertEqual(snapshot_id, "snapshot-2026-08-04-C-1")
        entries = verify_snapshot(self.snapshot_dir(snapshot_id))
        self.assertEqual(
            [entry.path for entry in entries],
            [
                "activity-log.md",
                "learners.md",
                "materials/index.md",
                "materials/learners/learner-1/20260803-learner-1-math.html",
                "materials/shared/20260803-math.html",
                "schedule.md",
                "tests.md",
            ],
        )
        document = json.loads(
            (self.snapshot_dir(snapshot_id) / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(document["schema_version"], migration.SCHEMA_VERSION)
        self.assertEqual(document["snapshot"], snapshot_id)
        for record in document["files"]:
            self.assertNotIn("\\", record["path"])
            self.assertFalse(record["path"].startswith("/"))
            self.assertEqual(len(record["sha256"]), 64)
            self.assertGreater(record["size"], 0)

    def test_snapshot_never_overwrites_an_existing_id(self):
        """受入条件8: 同じIDが既にあれば、既存のmanifestとpayloadは変わらない。"""

        first = self.snapshot()
        first_dir = self.snapshot_dir(first)
        before = {
            path.relative_to(first_dir).as_posix(): path.read_bytes()
            for path in sorted(first_dir.rglob("*"))
            if path.is_file()
        }

        _write(self.workspace, "schedule.md", "書き換えた予定\n")
        second = self.snapshot()

        self.assertEqual(second, "snapshot-2026-08-04-C-2")
        after = {
            path.relative_to(first_dir).as_posix(): path.read_bytes()
            for path in sorted(first_dir.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_temporary_directory_is_not_a_restore_target(self):
        """作成途中の一時ディレクトリは正式なスナップショットにならない。"""

        temporary = self.workspace / "import" / ".tmp-snapshot-2026-08-04-C-1-abc"
        (temporary / "payload").mkdir(parents=True)

        with self.assertRaises(MigrationError):
            resolve_snapshot_dir(self.workspace, temporary.name)

    def test_snapshot_rejects_symlinked_material_file(self):
        """受入条件5: 教材ファイルのsymlinkは作成時点で失敗する。"""

        outside = _write(self.root, "outside.html", WORKSHEET)
        link = self.workspace / "materials" / "shared" / "linked.html"
        os.symlink(str(outside), str(link))

        with self.assertRaises(MigrationError):
            self.snapshot()

    def test_snapshot_rejects_symlinked_material_directory(self):
        outside = self.root / "outside"
        outside.mkdir()
        _write(outside, "extra.html", WORKSHEET)
        os.symlink(str(outside), str(self.workspace / "materials" / "linked"))

        with self.assertRaises(MigrationError):
            self.snapshot()

    def test_snapshot_rejects_symlinked_import_directory(self):
        """格納経路自体がsymlinkの場合も失敗する。"""

        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        import_dir = self.workspace / "import"
        for path in sorted(import_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        import_dir.rmdir()
        os.symlink(str(elsewhere), str(import_dir))

        with self.assertRaises(MigrationError):
            self.snapshot()

    def test_snapshot_rejects_invalid_stage_or_date(self):
        for migration_date, stage in (
            ("2026-08-04", "E"),
            ("2026/08/04", "C"),
            ("2026-99-99", "C"),
            ("2026-02-30", "C"),
        ):
            with self.subTest(date=migration_date, stage=stage):
                with self.assertRaises(MigrationError):
                    create_snapshot(
                        self.workspace, migration_date, stage, writer=io.StringIO()
                    )

    def test_snapshot_rejects_a_symlinked_workspace(self):
        """--workspace の指定自体がsymlinkなら受け付けない。"""

        link = self.root / "workspace-link"
        os.symlink(str(self.workspace), str(link))

        with self.assertRaises(MigrationError):
            create_snapshot(link, "2026-08-04", "C", writer=io.StringIO())
        self.assertFalse(list((self.workspace / "import").glob("snapshot-*")))

    def test_snapshot_fails_when_a_source_changes_during_the_copy(self):
        """コピー中に元ファイルが変われば、正式なスナップショットにしない。"""

        real_copy = migration._copy_file
        changed = {"done": False}

        def copy_then_change(source, destination):
            real_copy(source, destination)
            if not changed["done"]:
                changed["done"] = True
                # まだコピーしていないファイルを、manifest作成後に書き換える。
                _write(self.workspace, "tests.md", "コピー中に変わった内容\n")

        with mock.patch.object(migration, "_copy_file", copy_then_change):
            with self.assertRaises(MigrationError):
                self.snapshot()

        import_dir = self.workspace / "import"
        self.assertFalse(list(import_dir.glob("snapshot-*")))
        self.assertFalse(list(import_dir.glob(".tmp-*")))

    def test_snapshot_fails_when_a_copied_file_changes_afterwards(self):
        """コピー済みファイルが後から変わった場合も、正式化しない。"""

        real_copy = migration._copy_file
        touched = {"done": False}

        def copy_then_change_earlier(source, destination):
            real_copy(source, destination)
            if not touched["done"] and destination.name != "activity-log.md":
                touched["done"] = True
                # 先にコピー済みのファイルを書き換える。
                _write(self.workspace, "activity-log.md", "コピー後に変わった\n")

        with mock.patch.object(migration, "_copy_file", copy_then_change_earlier):
            with self.assertRaises(MigrationError) as caught:
                self.snapshot()

        self.assertIn("コピー中にworkspaceが変わりました", str(caught.exception))
        import_dir = self.workspace / "import"
        self.assertFalse(list(import_dir.glob("snapshot-*")))
        self.assertFalse(list(import_dir.glob(".tmp-*")))

    def test_snapshot_fails_when_a_file_is_added_during_the_copy(self):
        """コピー中に増えたファイルはmanifestから漏れるため、正式化しない。"""

        real_copy = migration._copy_file
        added = {"done": False}

        def copy_then_add(source, destination):
            real_copy(source, destination)
            if not added["done"]:
                added["done"] = True
                _write(self.workspace, "materials/shared/20260805-added.html", WORKSHEET)

        with mock.patch.object(migration, "_copy_file", copy_then_add):
            with self.assertRaises(MigrationError):
                self.snapshot()

        import_dir = self.workspace / "import"
        self.assertFalse(list(import_dir.glob("snapshot-*")))
        self.assertFalse(list(import_dir.glob(".tmp-*")))
        # 増えたファイルは残したまま（勝手に消さない）。
        self.assertTrue(
            (self.workspace / "materials" / "shared" / "20260805-added.html").is_file()
        )

    def test_snapshot_does_not_take_over_a_broken_symlink_identifier(self):
        """壊れたsymlinkが置かれたIDは空きとみなさない。"""

        import_dir = self.workspace / "import"
        import_dir.mkdir(exist_ok=True)
        occupied = import_dir / "snapshot-2026-08-04-C-1"
        os.symlink(str(self.root / "missing"), str(occupied))

        snapshot_id = self.snapshot()

        self.assertEqual(snapshot_id, "snapshot-2026-08-04-C-2")
        self.assertTrue(occupied.is_symlink())
        self.assertFalse(occupied.exists())


class SnapshotIdentifierTest(MigrationTestCase):
    def test_restore_rejects_traversal_and_malformed_identifiers(self):
        """受入条件9: パスとして解釈されうる値・形式違いを拒否する。"""

        self.snapshot()
        for value in (
            "../../etc",
            "/tmp/snapshot-2026-08-04-C-1",
            "snapshot-2026-08-04-C-1/../..",
            "snapshot-2026-08-04-C-1/payload",
            "..",
            ".",
            "snapshot-2026-08-04-E-1",
            "snapshot-2026-08-04-C-0",
            "snapshot-2026-8-4-C-1",
            "rollback-snapshot-2026-08-04-C-1-1",
            "snapshot-2026-08-04-C-1 ",
        ):
            with self.subTest(value=value):
                with self.assertRaises(MigrationError):
                    resolve_snapshot_dir(self.workspace, value)

    def test_restore_requires_an_existing_snapshot(self):
        self.snapshot()
        with self.assertRaises(MigrationError):
            resolve_snapshot_dir(self.workspace, "snapshot-2026-08-04-D-1")


class SnapshotIntegrityTest(MigrationTestCase):
    def setUp(self):
        super().setUp()
        self.snapshot_id = self.snapshot()
        self.directory = self.snapshot_dir(self.snapshot_id)
        self.before = _state(self.workspace)
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")

    def assert_workspace_untouched(self):
        self.assertEqual(
            _state(self.workspace)["schedule.md"], "移行で投入した予定\n"
        )
        self.assertIn("learners.md", _state(self.workspace))

    def test_missing_manifest_stops_before_touching_the_workspace(self):
        """受入条件4: manifest欠損では正本を1つも削除せずに停止する。"""

        (self.directory / "manifest.json").unlink()
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()

    def test_unknown_schema_version_is_refused(self):
        document = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        document["schema_version"] = migration.SCHEMA_VERSION + 1
        (self.directory / "manifest.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MigrationError):
            load_manifest(self.directory)

    def test_hash_mismatch_stops_before_touching_the_workspace(self):
        (self.directory / "payload" / "learners.md").write_text(
            "改変された内容\n", encoding="utf-8"
        )
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()

    def test_payload_with_an_unlisted_file_is_refused(self):
        _write(self.directory / "payload", "materials/extra.html", WORKSHEET)
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()

    def test_payload_missing_a_listed_file_is_refused(self):
        (self.directory / "payload" / "tests.md").unlink()
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()

    def test_duplicate_manifest_paths_are_refused(self):
        document = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        document["files"].append(dict(document["files"][0]))
        (self.directory / "manifest.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MigrationError):
            load_manifest(self.directory)

    def test_manifest_entry_outside_the_allowlist_is_refused(self):
        """受入条件6: ハッシュが正しくてもallowlist外なら失敗する。"""

        payload_note = _write(self.directory / "payload", "notes.md", "メモ\n")
        document = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        document["files"].append(
            {
                "path": "notes.md",
                "size": payload_note.stat().st_size,
                "sha256": migration._hash_file(payload_note),
            }
        )
        (self.directory / "manifest.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()

    def test_manifest_entry_with_traversal_is_refused(self):
        document = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        document["files"][0]["path"] = "../outside.md"
        (self.directory / "manifest.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MigrationError):
            load_manifest(self.directory)

    def test_symlink_inside_the_payload_is_refused(self):
        """受入条件5: 復元元payloadのsymlinkも拒否する。"""

        outside = _write(self.root, "outside-payload.md", "外部\n")
        target = self.directory / "payload" / "tests.md"
        target.unlink()
        os.symlink(str(outside), str(target))
        with self.assertRaises(MigrationError):
            self.restore(self.snapshot_id)
        self.assert_workspace_untouched()


class RestoreTest(MigrationTestCase):
    def test_restore_returns_the_workspace_to_the_snapshot_state(self):
        """受入条件3: 新規教材を含めて投入前と完全一致へ戻る。"""

        before = _state(self.workspace)
        snapshot_id = self.snapshot()

        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        _write(self.workspace, "materials/shared/20260805-new.html", WORKSHEET)
        _write(
            self.workspace,
            "materials/learners/learner-2/20260805-learner-2-math.html",
            WORKSHEET,
        )
        (self.workspace / "tests.md").unlink()

        restored, _output = self.restore(snapshot_id)

        self.assertTrue(restored)
        self.assertEqual(_state(self.workspace), before)
        self.assertFalse(
            (self.workspace / "materials" / "shared" / "20260805-new.html").exists()
        )
        self.assertFalse((self.workspace / "materials" / "learners" / "learner-2").exists())
        # allowlist外は触らない。
        self.assertTrue((self.workspace / "notes.md").is_file())
        self.assertTrue((self.workspace / "import" / "keep.txt").is_file())

    def test_restore_reports_paths_by_change_kind(self):
        """受入条件11: パス単位の追加・変更・削除を提示する。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        _write(self.workspace, "materials/shared/20260805-new.html", WORKSHEET)
        (self.workspace / "tests.md").unlink()

        _restored, output = self.restore(snapshot_id)

        self.assertIn("追加（復元で作られる）: 1件", output)
        self.assertIn("  - tests.md", output)
        self.assertIn("変更（復元で上書きされる）: 1件", output)
        self.assertIn("  - schedule.md", output)
        self.assertIn("削除（復元で消える）: 1件", output)
        self.assertIn("  - materials/shared/20260805-new.html", output)

    def test_restore_stops_on_eof_without_changing_anything(self):
        """受入条件7: EOF・未回答では何も変更しない。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        after_input = _state(self.workspace)

        restored, output = self.restore(snapshot_id, answer="")

        self.assertFalse(restored)
        self.assertIn("中止", output)
        self.assertEqual(_state(self.workspace), after_input)
        self.assertFalse(
            list((self.workspace / "import").glob("rollback-*")),
            msg="中止時は退避も作らない",
        )

    def test_restore_stops_when_the_workspace_changes_during_confirmation(self):
        """確認の待ち時間に入った変更を、画面に出さないまま上書きしない。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        workspace = self.workspace

        class ChangingReader(io.StringIO):
            def readline(self, *args, **kwargs):
                _write(workspace, "tests.md", "確認中に書き換えた内容\n")
                return super().readline(*args, **kwargs)

        writer = io.StringIO()
        with self.assertRaises(MigrationError) as caught:
            restore_snapshot(
                self.workspace,
                snapshot_id,
                reader=ChangingReader("y\n"),
                writer=writer,
            )

        self.assertIn("確認中にworkspaceが変わりました", str(caught.exception))
        self.assertNotIn("tests.md", writer.getvalue().split("復元しますか")[0])
        self.assertEqual(
            (self.workspace / "tests.md").read_text(encoding="utf-8"),
            "確認中に書き換えた内容\n",
        )
        self.assertEqual(
            (self.workspace / "schedule.md").read_text(encoding="utf-8"),
            "移行で投入した予定\n",
        )
        self.assertFalse(list((self.workspace / "import").glob("rollback-*")))

    def test_restore_stops_when_a_file_appears_while_stashing(self):
        """退避に含まれないファイルを、配置で消してしまわない。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        appeared = self.workspace / "materials" / "shared" / "20260805-late.html"
        real_stash = migration._stash_current

        def stash_then_add(workspace, import_dir, sid, entries):
            stash = real_stash(workspace, import_dir, sid, entries)
            _write(self.workspace, "materials/shared/20260805-late.html", WORKSHEET)
            return stash

        with mock.patch.object(migration, "_stash_current", stash_then_add):
            with self.assertRaises(MigrationError) as caught:
                self.restore(snapshot_id)

        self.assertIn("退避中にworkspaceが変わりました", str(caught.exception))
        # 退避に入らなかったファイルは消えていない。
        self.assertTrue(appeared.is_file())
        self.assertEqual(
            (self.workspace / "schedule.md").read_text(encoding="utf-8"),
            "移行で投入した予定\n",
        )
        stash = self.workspace / "import" / "rollback-{}-1".format(snapshot_id)
        self.assertNotIn(
            "materials/shared/20260805-late.html",
            {entry.path for entry in verify_snapshot(stash)},
        )

    def test_restore_rejects_a_symlinked_workspace(self):
        snapshot_id = self.snapshot()
        link = self.root / "workspace-link"
        os.symlink(str(self.workspace), str(link))

        with self.assertRaises(MigrationError):
            restore_snapshot(
                link, snapshot_id, reader=io.StringIO("y\n"), writer=io.StringIO()
            )

    def test_restore_stops_on_a_negative_answer(self):
        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        after_input = _state(self.workspace)

        restored, _output = self.restore(snapshot_id, answer="n\n")

        self.assertFalse(restored)
        self.assertEqual(_state(self.workspace), after_input)

    def test_restore_stashes_the_current_state_before_replacing(self):
        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")

        self.restore(snapshot_id)

        stash = self.workspace / "import" / "rollback-{}-1".format(snapshot_id)
        entries = verify_snapshot(stash)
        stashed = {entry.path for entry in entries}
        self.assertIn("schedule.md", stashed)
        self.assertEqual(
            (stash / "payload" / "schedule.md").read_text(encoding="utf-8"),
            "移行で投入した予定\n",
        )

    def test_restore_is_idempotent_and_reuses_an_identical_stash(self):
        """受入条件10の後半: 再実行で収束し、同一状態の退避は取り直さない。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")

        self.restore(snapshot_id)
        first = _state(self.workspace)
        self.restore(snapshot_id)
        self.restore(snapshot_id)

        self.assertEqual(_state(self.workspace), first)
        stashes = sorted(
            path.name for path in (self.workspace / "import").glob("rollback-*")
        )
        self.assertEqual(
            stashes,
            [
                "rollback-{}-1".format(snapshot_id),
                "rollback-{}-2".format(snapshot_id),
            ],
            msg="3回目は2回目と同じ状態なので退避を再利用する",
        )

    def test_failed_placement_keeps_the_snapshot_and_the_stash(self):
        """受入条件10: 配置途中で失敗しても材料が残り、未完了として報告する。"""

        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        _write(self.workspace, "materials/shared/20260805-new.html", WORKSHEET)

        real_copy = migration._copy_file
        calls = {"count": 0}

        def flaky_copy(source, destination):
            calls["count"] += 1
            # 退避のコピーは通し、配置の2ファイル目で失敗させる。
            if "payload" not in str(source):
                return real_copy(source, destination)
            if calls["count"] % 2 == 0:
                raise OSError("ディスクエラー（テスト）")
            return real_copy(source, destination)

        with mock.patch.object(migration, "_copy_file", flaky_copy):
            with self.assertRaises(MigrationError) as caught:
                self.restore(snapshot_id)

        message = str(caught.exception)
        self.assertIn("未完了", message)
        stash = self.workspace / "import" / "rollback-{}-1".format(snapshot_id)
        self.assertIn(str(stash), message)
        self.assertIn(str(self.snapshot_dir(snapshot_id)), message)
        # 材料は両方とも完全なまま残る。
        verify_snapshot(self.snapshot_dir(snapshot_id))
        verify_snapshot(stash)

        # 同じ復元を再実行すると収束する。
        restored, _output = self.restore(snapshot_id)
        self.assertTrue(restored)
        self.assertTrue(plan_restore(
            self.workspace, verify_snapshot(self.snapshot_dir(snapshot_id))
        ).is_empty)

    def test_restore_reports_completion_only_after_verification(self):
        snapshot_id = self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")

        with mock.patch.object(migration, "_place", lambda *args: None):
            with self.assertRaises(MigrationError) as caught:
                self.restore(snapshot_id)

        self.assertIn("一致しません", str(caught.exception))


class CommandLineTest(MigrationTestCase):
    def test_snapshot_and_restore_through_main(self):
        before = _state(self.workspace)
        code = migration.main(
            [
                "snapshot",
                "--workspace",
                str(self.workspace),
                "--date",
                "2026-08-04",
                "--stage",
                "C",
            ]
        )
        self.assertEqual(code, 0)

        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        with mock.patch("sys.stdin", io.StringIO("y\n")):
            code = migration.main(
                [
                    "restore",
                    "--workspace",
                    str(self.workspace),
                    "--snapshot",
                    "snapshot-2026-08-04-C-1",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(_state(self.workspace), before)

    def test_restore_without_confirmation_exits_nonzero(self):
        self.snapshot()
        _write(self.workspace, "schedule.md", "移行で投入した予定\n")
        with mock.patch("sys.stdin", io.StringIO("")):
            code = migration.main(
                [
                    "restore",
                    "--workspace",
                    str(self.workspace),
                    "--snapshot",
                    "snapshot-2026-08-04-C-1",
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            (self.workspace / "schedule.md").read_text(encoding="utf-8"),
            "移行で投入した予定\n",
        )

    def test_invalid_snapshot_identifier_exits_nonzero(self):
        self.snapshot()
        code = migration.main(
            [
                "restore",
                "--workspace",
                str(self.workspace),
                "--snapshot",
                "../../elsewhere",
            ]
        )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
