"""kit.starter_web.checkup（構造・workspace・ワークシート検証）のテスト。

実在の家庭データに依存せず、public抽出物だけでも成功することを前提に書く。
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from kit.starter_web.checkup import (
    check_repo,
    check_workspace,
    check_worksheet,
    is_private_source,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_AVAILABLE = shutil.which("git") is not None

VALID_WORKSHEET = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="material-type" content="worksheet">
  <title>たしざんカード</title>
  <style>
    @page { size: A4; margin: 14mm; }
    body { margin: 0; }
    section.answers { break-before: page; page-break-before: always; }
  </style>
</head>
<body><main>
  <ol class="problems"><li>2 + 3 =</li></ol>
  <section class="answers"><h2>こたえ</h2><ol><li>5</li></ol></section>
</main></body>
</html>
"""

VALID_RED_SHEET = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="material-type" content="worksheet"><meta name="answer-layout" content="red-sheet">
<title>赤シート確認</title><style>@page { size: A4; }</style></head><body>
<p class="red-sheet-instructions">赤シートで隠して確認します</p>
<p class="problems">2 + 3 = <span class="answers red-sheet">5</span></p></body></html>
"""

PLAIN_MATERIAL = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="material-type" content="reference">
  <title>よみものメモ</title>
  <style>body { margin: 0; }</style>
</head>
<body><main><p>ワークシートではない教材。</p></main></body>
</html>
"""

VALID_SCHEDULE = """# 架空スケジュール

| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |
|---|---|---|---|---|---|---|---|---|
| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | — | — |
| 2026-08-04 | learner-1 | アオ | 夜 | 国語 | 音読 | 予定 | — | — |

## 反復ルーティン

| 開始日 | 終了日 | 曜日 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 教材 |
|---|---|---|---|---|---|---|---|---|
"""

VALID_ACTIVITY = """# 架空ログ

| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |
|---|---|---|---|---|---|---|---|---|
| 2026-08-03 | learner-1 | アオ | 算数 | たしざん | — | 10分 | 全問正解。 | 次はひきざん。 |

## 週次振り返り
"""

VALID_LEARNERS = """# 学習者台帳

| 学習者ID | 呼び名 | 学年目安 | 状態 | メモ |
|---|---|---|---|---|
| learner-1 | アオ | 小学2年生くらい | 利用中 | — |
"""

VALID_MATERIALS_INDEX = """# 教材台帳

| 教材ID | 対象 | 学習者ID | 教科 | 種別 | タイトル | ファイル | 出典 | 状態 |
|---|---|---|---|---|---|---|---|---|
"""

VALID_TESTS = """# テスト台帳

| テストID | テスト名 | 方式 | 種別 | 教科 | 周期 | 状態 | 備考 |
|---|---|---|---|---|---|---|---|
| test-check | 確認テスト | 単体 | 学校 | 算数 | — | 利用中 | — |

| 実施ID | テストID | 回・版 | 受験区分 | 日付 | 学習者ID | 学習者 | 状態 | 問題 | 所要時間 | 結果 | 次アクション | 繰越先 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| attempt-check | test-check | — | — | 2026-08-18 | learner-1 | アオ | 予定 | — | — | — | — | — |

| 実施ID | 教科・領域 | 得点 | 満点 | 平均点 | 偏差値 | 順位 | 受験者数 | 判定 | 備考 |
|---|---|---|---|---|---|---|---|---|---|
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_workspace(root: Path) -> Path:
    workspace = root / "my-family"
    write(workspace / "schedule.md", VALID_SCHEDULE)
    write(workspace / "activity-log.md", VALID_ACTIVITY)
    write(workspace / "learners.md", VALID_LEARNERS)
    write(workspace / "tests.md", VALID_TESTS)
    write(workspace / "materials" / "index.md", VALID_MATERIALS_INDEX)
    return workspace


def make_public_tree(root: Path) -> Path:
    """checkupが公開ツリーとして扱う最小のfixtureを作る。"""

    write(root / "README.md", "# starter kit\n")
    write(root / "CLAUDE.md", "docs/AI-OPERATIONS.md を参照。\n")
    write(root / "AGENTS.md", "docs/AI-OPERATIONS.md を参照。\n")
    write(root / "LICENSE", "MIT\n")
    write(root / "LICENSE-CONTENT.md", "CC BY 4.0\n")
    write(root / "package.json", "{}\n")
    write(root / ".gitignore", "dist/\n__pycache__/\nworkspace/\n")
    write(root / "docs" / "AI-OPERATIONS.md", "# AI運用ルール\n")
    write(root / "docs" / "EXTENDING.md", "# 拡張ガイド\n")
    write(root / "docs" / "WORKSPACE-CONTRACT.md", "# workspace契約\n")
    write(root / "templates" / "learners.md", VALID_LEARNERS)
    write(root / "templates" / "schedule.md", VALID_SCHEDULE)
    write(root / "templates" / "activity-log.md", VALID_ACTIVITY)
    write(root / "templates" / "tests.md", VALID_TESTS)
    write(root / "templates" / "materials" / "index.md", VALID_MATERIALS_INDEX)
    write(root / "templates" / "worksheet.html", VALID_WORKSHEET)
    write(root / "templates" / "worksheet-red-sheet.html", VALID_RED_SHEET)
    write(root / "examples" / "demo" / "schedule.md", VALID_SCHEDULE)
    write(root / "examples" / "demo" / "activity-log.md", VALID_ACTIVITY)
    write(root / "examples" / "demo" / "learners.md", VALID_LEARNERS)
    write(root / "examples" / "demo" / "tests.md", VALID_TESTS)
    write(
        root / "examples" / "demo" / "materials" / "index.md",
        VALID_MATERIALS_INDEX,
    )
    (root / "kit").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    return root


class WorksheetCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def check_html(self, text, strict=None):
        path = self.root / "worksheet.html"
        path.write_text(text, encoding="utf-8")
        return check_worksheet(path, strict=strict)

    def test_valid_worksheet_passes_strict(self):
        self.assertEqual(self.check_html(VALID_WORKSHEET, strict=True), [])

    def test_reference_material_gets_base_checks_only(self):
        self.assertEqual(self.check_html(PLAIN_MATERIAL), [])

    def test_undeclared_material_detected(self):
        # 種別宣言を消しても厳格検査を回避できない（宣言なし＝検証エラー）
        issues = self.check_html(
            PLAIN_MATERIAL.replace(
                '<meta name="material-type" content="reference">\n  ', ""
            )
        )
        self.assertTrue(any("種別宣言" in issue for issue in issues))

    def test_stripped_worksheet_marker_detected(self):
        # 雛形由来WSからマーカー・A4・問題・解答を全部消しても合格しない
        stripped = (
            "<!doctype html>\n"
            '<html lang="ja"><head><meta charset="utf-8">\n'
            "<title>骨抜きWS</title></head><body><p>問題</p></body></html>\n"
        )
        issues = self.check_html(stripped)
        self.assertTrue(any("種別宣言" in issue for issue in issues))

    def test_unknown_material_type_detected(self):
        issues = self.check_html(
            PLAIN_MATERIAL.replace('content="reference"', 'content="memo"')
        )
        self.assertTrue(any("種別宣言" in issue for issue in issues))

    def test_missing_registered_html_does_not_emit_a_raw_os_error(self):
        workspace = make_public_tree(self.root) / "examples" / "demo"
        missing = workspace / "materials" / "shared" / "reference.html"
        missing.parent.mkdir(parents=True, exist_ok=True)
        missing.write_text(PLAIN_MATERIAL, encoding="utf-8")
        index = workspace / "materials" / "index.md"
        index.write_text(
            index.read_text(encoding="utf-8").rstrip()
            + "\n| missing-reference | 共通 | — | 国語 | reference | "
            "参考資料 | [資料](shared/reference.html) | 自作 | 利用中 |\n",
            encoding="utf-8",
        )
        missing.unlink()

        issues = check_workspace(workspace)
        joined = "\n".join(issues)
        self.assertIn("reference.html", joined)
        self.assertNotIn("[Errno", joined)

    def test_missing_title_and_lang_detected(self):
        issues = self.check_html("<!doctype html>\n<html><body></body></html>\n")
        text = "\n".join(issues)
        self.assertIn("lang", text)
        self.assertIn("title", text)
        self.assertIn("charset", text)

    def test_external_resource_detected(self):
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "</head>",
                '<script src="https://example.invalid/x.js"></script></head>',
            )
        )
        self.assertTrue(any("自己完結" in issue for issue in issues))

    def test_unquoted_external_resource_detected(self):
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "</head>",
                "<script src=https://example.invalid/x.js></script></head>",
            )
        )
        self.assertTrue(any("自己完結" in issue for issue in issues))

    def test_external_css_import_detected(self):
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "body { margin: 0; }",
                "@import 'other.css'; body { margin: 0; }",
            )
        )
        self.assertTrue(any("自己完結" in issue for issue in issues))

    def test_absolute_reference_detected(self):
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "<main>", '<main><img src="/absolute/picture.png" alt="図">'
            )
        )
        self.assertTrue(any("絶対パス" in issue for issue in issues))

    def test_worksheet_declaration_triggers_strict_checks(self):
        # worksheet宣言はあるがA4設定・問題・解答が無いHTMLは合格させない
        broken = (
            "<!doctype html>\n"
            '<html lang="ja"><head><meta charset="utf-8">\n'
            '<meta name="material-type" content="worksheet">\n'
            "<title>不完全なWS</title></head><body></body></html>\n"
        )
        issues = self.check_html(broken)
        text = "\n".join(issues)
        self.assertIn("A4印刷設定", text)
        self.assertIn("problems", text)
        self.assertIn("answers", text)

    def test_strict_requires_worksheet_declaration(self):
        issues = self.check_html(PLAIN_MATERIAL, strict=True)
        self.assertTrue(any("material-type" in issue for issue in issues))

    def test_answers_without_page_break_detected(self):
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "section.answers { break-before: page; page-break-before: always; }",
                "section.answers { margin-top: 8px; }",
            )
        )
        self.assertTrue(any("改ページ" in issue for issue in issues))

    def test_page_break_outside_answers_block_detected(self):
        # 無関係なセレクタの改ページでは合格させない（.answersブロック内限定）
        issues = self.check_html(
            VALID_WORKSHEET.replace(
                "section.answers { break-before: page; page-break-before: always; }",
                ".other { break-before: page; }\n    section.answers { margin: 0; }",
            )
        )
        self.assertTrue(any("改ページ" in issue for issue in issues))

    def test_page_break_text_outside_style_does_not_count(self):
        broken = VALID_WORKSHEET.replace(
            "section.answers { break-before: page; page-break-before: always; }",
            "section.answers { margin: 0; }",
        )
        for suffix in (
            "<!-- section.answers { break-before: page; } -->",
            "<script>const sample = 'section.answers { break-before: page; }';</script>",
            "<p>section.answers { break-before: page; }</p>",
            "<style>/* section.answers { break-before: page; } */</style>",
        ):
            with self.subTest(suffix=suffix):
                issues = self.check_html(broken.replace("</body>", suffix + "</body>"))
                self.assertTrue(any("改ページ" in issue for issue in issues))


class WorkspaceCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = make_workspace(Path(self._tmp.name))

    def rewrite(self, name, old, new):
        path = self.workspace / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
        )

    def test_valid_workspace_passes(self):
        self.assertEqual(check_workspace(self.workspace), [])

    def test_migration_work_files_do_not_break_the_check(self):
        """移行の作業領域と移行報告を置いても、workspace検証は通る。"""

        write(self.workspace / "import" / "source.csv", "日付,教科\n2026-08-03,算数\n")
        write(
            self.workspace / "import" / "snapshot-2026-08-04-C-1" / "payload" / "tests.md",
            "# テスト\n",
        )
        write(
            self.workspace / "import" / "snapshot-2026-08-04-C-1" / "manifest.json",
            '{"schema_version": 1, "files": []}\n',
        )
        write(
            self.workspace / "migration-notes.md",
            "# 移行報告\n\n元12件のうち9件を投入し、3件は対象期間外のため見送り。\n",
        )

        self.assertEqual(check_workspace(self.workspace), [])

    def test_missing_activity_log_detected(self):
        (self.workspace / "activity-log.md").unlink()
        issues = check_workspace(self.workspace)
        self.assertTrue(any("activity-log.md" in issue for issue in issues))

    def test_unknown_status_detected(self):
        self.rewrite("schedule.md", "| 完了 |", "| 済 |")
        issues = check_workspace(self.workspace)
        self.assertTrue(any("入力契約" in issue for issue in issues))

    def test_learner_id_conflict_detected(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-04 | learner-1 | アオ |",
            "| 2026-08-04 | learner-1 | コハク |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("学習者" in issue for issue in issues))

    def test_short_row_detected(self):
        # 列数不足の行はadapterに黙って読み飛ばされるため、checkupで検出する
        self.rewrite(
            "schedule.md",
            "| 2026-08-04 | learner-1 | アオ | 夜 | 国語 | 音読 | 予定 | — | — |",
            "| 2026-08-04 | learner-1 | アオ | 夜 | 国語 | 音読 | 予定 | — |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("列数" in issue for issue in issues))

    def test_unknown_table_header_detected(self):
        schedule = self.workspace / "schedule.md"
        schedule.write_text(
            schedule.read_text(encoding="utf-8")
            + "\n| 日付 | 学習者lD | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
            + "|---|---|---|---|---|---|---|---|---|\n"
            + "| 2026-08-05 | learner-1 | アオ | 朝 | 算数 | 消える行 | 予定 | — | — |\n",
            encoding="utf-8",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("所定の列構成" in issue for issue in issues))

    def test_invalid_test_event_date_detected(self):
        self.rewrite(
            "tests.md",
            "| attempt-check | test-check | — | — | 2026-08-18 |",
            "| attempt-check | test-check | — | — | 8/18 |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("入力契約" in issue for issue in issues))

    def test_empty_test_event_name_detected(self):
        self.rewrite(
            "tests.md",
            "| test-check | 確認テスト | 単体 |",
            "| test-check | | 単体 |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("テスト名は空にできません" in issue for issue in issues))

    def test_missing_material_link_detected(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | — | — |",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | "
            "[たしざん](materials/missing.html) | — |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("リンク先が存在しません" in issue for issue in issues))

    def test_undecodable_material_link_is_reported_without_crashing(self):
        """`%00` を含むリンクでも、例外で止まらず問題一覧を返す。

        復号結果がパスとして使えない場合に素の `ValueError` を上げると、
        `check_workspace()` が結果を返せずクラッシュする。
        """

        self.rewrite(
            "schedule.md",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | — | — |",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | "
            "[こわれ](materials/a%00b.html) | — |",
        )

        issues = check_workspace(self.workspace)

        self.assertTrue(
            any("リンクに使えない文字" in issue for issue in issues)
        )

    def test_percent_encoded_material_link_resolves_to_the_real_file(self):
        """encodedなリンクも復号して実在判定する（リンク切れと誤報しない）。"""

        write(self.workspace / "materials" / "国語.html", "<p>x</p>")
        self.rewrite(
            "schedule.md",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | — | — |",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | "
            "[国語](materials/%E5%9B%BD%E8%AA%9E.html) | — |",
        )

        issues = check_workspace(self.workspace)

        self.assertFalse(
            any("リンク先が存在しません" in issue for issue in issues), issues
        )

    def test_external_material_link_detected(self):
        self.rewrite(
            "activity-log.md",
            "| — | 10分 |",
            "| [動画](https://example.invalid/movie) | 10分 |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("外部URLの教材リンク" in issue for issue in issues))

    def test_link_outside_workspace_detected(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | — | — |",
            "| 2026-08-03 | learner-1 | アオ | 朝 | 算数 | たしざん | 完了 | "
            "[外](../outside.html) | — |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("workspace外" in issue for issue in issues))

    def test_broken_material_worksheet_detected(self):
        write(
            self.workspace / "materials" / "broken.html",
            "<p>doctypeがないワークシート</p>\n",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("doctype" in issue for issue in issues))


class RepoCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_public_tree(Path(self._tmp.name) / "public")

    def test_clean_public_tree_passes(self):
        self.assertEqual(check_repo(self.root), [])

    def test_fixture_is_treated_as_public_tree(self):
        self.assertFalse(is_private_source(self.root))

    def test_stray_top_level_entry_detected(self):
        write(self.root / "notes" / "memo.md", "勝手な新領域\n")
        issues = check_repo(self.root)
        self.assertTrue(any("許可外のトップレベル" in issue for issue in issues))

    def test_duplicate_canonical_file_detected(self):
        write(self.root / "docs" / "schedule.md", VALID_SCHEDULE)
        issues = check_repo(self.root)
        self.assertTrue(any("正本の重複禁止" in issue for issue in issues))

    def test_missing_required_file_detected(self):
        (self.root / "docs" / "AI-OPERATIONS.md").unlink()
        issues = check_repo(self.root)
        self.assertTrue(
            any("docs/AI-OPERATIONS.md" in issue for issue in issues)
        )

    def test_missing_gitignore_pattern_detected(self):
        write(self.root / ".gitignore", "dist/\n")
        issues = check_repo(self.root)
        self.assertTrue(any("workspace/" in issue for issue in issues))

    def test_gitignore_negation_detected_statically(self):
        write(
            self.root / ".gitignore",
            "dist/\nworkspace/\n!workspace/keep.md\n",
        )
        issues = check_repo(self.root)
        self.assertTrue(any("再許可ルール" in issue for issue in issues))

    @unittest.skipUnless(GIT_AVAILABLE, "gitが利用できない環境")
    def test_git_effective_reinclusion_detected(self):
        # .gitignoreに文字列としてはworkspace/があっても、後続の!で再許可されて
        # いれば実効的に除外されていない。git check-ignoreで検出する。
        subprocess.run(
            ["git", "-C", str(self.root), "init", "-q"],
            check=True,
            capture_output=True,
        )
        write(
            self.root / ".gitignore",
            "dist/\nworkspace/\n!workspace/\n",
        )
        issues = check_repo(self.root)
        self.assertTrue(
            any("Git除外が実効になっていません" in issue for issue in issues),
            msg="\n".join(issues),
        )

    @unittest.skipUnless(GIT_AVAILABLE, "gitが利用できない環境")
    def test_exclusion_only_in_git_info_exclude_detected(self):
        # .git/info/excludeによる除外はclone先へ引き継がれないため合格させない
        subprocess.run(
            ["git", "-C", str(self.root), "init", "-q"],
            check=True,
            capture_output=True,
        )
        write(self.root / ".gitignore", "dist/\n")
        write(self.root / ".git" / "info" / "exclude", "workspace/\n")
        issues = check_repo(self.root)
        text = "\n".join(issues)
        self.assertIn("workspace/ の除外がありません", text)
        self.assertIn(".gitignore ではありません", text)

    @unittest.skipUnless(GIT_AVAILABLE, "gitが利用できない環境")
    def test_git_effective_exclusion_passes(self):
        subprocess.run(
            ["git", "-C", str(self.root), "init", "-q"],
            check=True,
            capture_output=True,
        )
        self.assertEqual(check_repo(self.root), [])

    def test_incomplete_workspace_detected(self):
        # schedule.mdが無い未完成workspaceも検査対象にする
        write(
            self.root / "workspace" / "half-made" / "activity-log.md",
            VALID_ACTIVITY,
        )
        issues = check_repo(self.root)
        self.assertTrue(any("schedule.md がありません" in issue for issue in issues))

    def test_workspace_under_public_tree_is_checked(self):
        workspace = self.root / "workspace" / "my-family"
        write(workspace / "schedule.md", VALID_SCHEDULE.replace("| 完了 |", "| 済 |"))
        write(workspace / "activity-log.md", VALID_ACTIVITY)
        write(workspace / "learners.md", VALID_LEARNERS)
        write(workspace / "tests.md", VALID_TESTS)
        write(workspace / "materials" / "index.md", VALID_MATERIALS_INDEX)
        issues = check_repo(self.root)
        self.assertTrue(any("入力契約" in issue for issue in issues))

    def test_broken_template_worksheet_detected(self):
        write(self.root / "templates" / "worksheet.html", "<p>壊れた雛形</p>\n")
        issues = check_repo(self.root)
        self.assertTrue(any("doctype" in issue for issue in issues))

    def test_template_without_answers_page_break_detected(self):
        write(
            self.root / "templates" / "worksheet.html",
            VALID_WORKSHEET.replace(
                "section.answers { break-before: page; page-break-before: always; }",
                "section.answers { margin-top: 8px; }",
            ),
        )
        issues = check_repo(self.root)
        self.assertTrue(any("改ページ" in issue for issue in issues))


class RealRepoTest(unittest.TestCase):
    """このリポジトリ自身（開発元／抽出後の両方）で構造検証が通ること。"""

    def test_repo_structure_is_clean(self):
        issues = check_repo(REPO_ROOT)
        self.assertEqual(issues, [], msg="\n".join(issues))


if __name__ == "__main__":
    unittest.main()
