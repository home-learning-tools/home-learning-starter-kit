"""理想形starter workspaceの学習者・教材・テスト契約。"""

import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from kit.starter_web.adapters import StarterMarkdownAdapter
from kit.starter_web.checkup import check_workspace
from kit.starter_web.render import render_dashboard


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = REPO_ROOT / "examples" / "demo"


class WorkspaceContractTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        shutil.copytree(DEMO_ROOT, self.workspace)

    def rewrite(self, relative, old, new):
        path = self.workspace / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_demo_exercises_one_off_and_recurring_test_lifecycle(self):
        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 3), mode="starter"
        ).load()

        self.assertEqual(check_workspace(self.workspace, date(2026, 8, 3)), [])
        self.assertIn(
            "夏の到達度チェック 2026年度",
            [item.title for item in data.test_events],
        )
        next_weekly = next(
            item
            for item in data.test_events
            if item.title == "算数スキルチェック 第4回"
        )
        self.assertEqual(next_weekly.materials[0].label, "問題")
        result = next(
            item
            for item in data.activities
            if item.title == "算数スキルチェック 第3回"
        )
        self.assertEqual(
            result.result_summary,
            "算数 8/10点・平均7.2・順位6/24。わり算のあまりを2問誤った。",
        )
        self.assertEqual(result.materials[0].label, "問題")

        output = Path(self._tmp.name) / "dashboard.html"
        render_dashboard(
            StarterMarkdownAdapter(
                self.workspace, date(2026, 8, 1), mode="starter"
            ).load(),
            output,
        )
        document = output.read_text(encoding="utf-8")
        self.assertIn(
            '<span>今週の完了</span><strong>3</strong>', document
        )

    def test_empty_templates_form_a_valid_workspace_skeleton(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        templates = REPO_ROOT / "kit" / "templates"
        if not templates.is_dir():
            templates = REPO_ROOT / "templates"
        for name in ("learners.md", "schedule.md", "activity-log.md", "tests.md"):
            shutil.copy2(templates / name, empty / name)
        (empty / "materials").mkdir()
        shutil.copy2(
            templates / "materials" / "index.md",
            empty / "materials" / "index.md",
        )
        (empty / "materials" / "shared").mkdir()
        (empty / "materials" / "learners").mkdir()
        self.assertEqual(check_workspace(empty), [])

    def test_advanced_walkthrough_examples_match_naming_and_test_rules(self):
        walkthrough = (REPO_ROOT / "examples" / "walkthrough.md").read_text(
            encoding="utf-8"
        )

        def chapter(title):
            match = re.search(
                r"^## \d+\. {}\n(?P<body>.*?)(?=^## |\Z)".format(
                    re.escape(title)
                ),
                walkthrough,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, msg=title)
            return match.group("body")

        # AI-OPERATIONSの命名規則どおり、共通教材は作成日から、個別教材は
        # 作成日＋学習者IDから始める。過去の曖昧な例を再導入しない。
        self.assertRegex(
            walkthrough,
            r"materials/shared/<作成日YYYYMMDD>-[a-z0-9-]+\.html",
        )
        self.assertRegex(
            walkthrough,
            r"materials/learners/(?P<learner>[a-z0-9-]+)/"
            r"<作成日YYYYMMDD>-(?P=learner)-[a-z0-9-]+\.html",
        )
        self.assertNotIn("materials/shared/number-bonds-v1.html", walkthrough)
        self.assertNotIn("<日付>-addition-revenge-v1.html", walkthrough)

        # 単体・定期の各章に結果報告があり、予定と結果を同じ基準日で
        # 確認できる。周期は利用者が明示した値と一致させる。
        one_off = chapter("単体のテスト（問題・成績）を追加する")
        recurring = chapter("定期的なテスト（問題・成績）を追加する")
        final_check = chapter("発展編の最終確認")
        basic_dashboard = chapter("Webダッシュボードで確認")
        self.assertGreaterEqual(one_off.count("練習として"), 1)
        self.assertGreaterEqual(recurring.count("練習として"), 1)
        self.assertIn("方式=定期・周期=毎週木曜", recurring)

        # 最終確認で指定する基準日ラベルを、単体・定期テストの
        # 両方が使う。章ごとの独立した文字列固定にしない。
        anchor_label_match = re.search(
            r"実施する想定日の `(?P<label><[^>]+>)`", final_check
        )
        self.assertIsNotNone(
            anchor_label_match, msg="13章の基準日ラベルが見つからない"
        )
        anchor_label = anchor_label_match.group("label")
        self.assertIn(anchor_label, one_off)
        self.assertIn(anchor_label, recurring)

        # シェル上の <...> はリダイレクトと解釈されるため、
        # --dateには実日付のコピペ可能な例だけを載せる。
        self.assertNotRegex(walkthrough, r"--date\s+<[^>]+>")
        basic_date_match = re.search(
            r"--date (?P<date>\d{4}-\d{2}-\d{2})", basic_dashboard
        )
        advanced_date_match = re.search(
            r"--date (?P<date>\d{4}-\d{2}-\d{2})", final_check
        )
        self.assertIsNotNone(basic_date_match)
        self.assertIsNotNone(advanced_date_match)
        basic_date = date.fromisoformat(basic_date_match.group("date"))
        advanced_date = date.fromisoformat(advanced_date_match.group("date"))
        self.assertEqual(basic_date.weekday(), 0)
        self.assertEqual(advanced_date, basic_date + timedelta(days=3))

    def test_attempt_on_anchor_date_is_visible_before_and_after_result(self):
        anchor = date(2026, 8, 6)
        title = "夏の到達度チェック 2026年度"
        original = (
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-18 | learner-a | アオ | 予定 | — | — | — | — | — |"
        )
        planned = original.replace(
            "attempt-summer-20260818", "attempt-summer-20260806"
        ).replace("2026-08-18", "2026-08-06")
        self.rewrite("tests.md", original, planned)

        self.assertEqual(check_workspace(self.workspace, anchor), [])
        planned_data = StarterMarkdownAdapter(
            self.workspace, anchor, mode="starter"
        ).load()
        self.assertIn(title, [item.title for item in planned_data.upcoming_tests()])

        output = Path(self._tmp.name) / "tutorial-dashboard.html"
        render_dashboard(planned_data, output)
        self.assertIn(title, output.read_text(encoding="utf-8"))

        completed = (
            "| attempt-summer-20260806 | test-summer | 2026年度 | 小3 | "
            "2026-08-06 | learner-a | アオ | 完了 | — | 12分 | "
            "10点中8点。 | 同じ型を2問確認する。 | — |"
        )
        self.rewrite("tests.md", planned, completed)
        tests_path = self.workspace / "tests.md"
        tests_path.write_text(
            tests_path.read_text(encoding="utf-8").rstrip()
            + "\n| attempt-summer-20260806 | 算数 | 8 | 10 | — | — | — | — | — | — |\n",
            encoding="utf-8",
        )

        self.assertEqual(check_workspace(self.workspace, anchor), [])
        completed_data = StarterMarkdownAdapter(
            self.workspace, anchor, mode="starter"
        ).load()
        completed_item = next(
            item for item in completed_data.recent_activities() if item.title == title
        )
        self.assertIn("算数 8/10点", completed_item.result_summary)
        self.assertNotIn(
            title, [item.title for item in completed_data.upcoming_tests()]
        )

        render_dashboard(completed_data, output)
        self.assertIn(title, output.read_text(encoding="utf-8"))

    def test_recurring_test_requires_a_cadence(self):
        self.rewrite(
            "tests.md",
            "| test-skill-series | 算数スキルチェック | 定期 | 学習教室 | 算数 | 隔週土曜 |",
            "| test-skill-series | 算数スキルチェック | 定期 | 学習教室 | 算数 | — |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("定期テストには周期" in issue for issue in issues))

    def test_score_cannot_exceed_the_maximum(self):
        self.rewrite(
            "tests.md",
            "| attempt-skill-20260801 | 算数 | 8 | 10 |",
            "| attempt-skill-20260801 | 算数 | 11 | 10 |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("得点は0以上満点以下" in issue for issue in issues))

    def test_score_cannot_be_recorded_before_the_attempt(self):
        tests_path = self.workspace / "tests.md"
        tests_path.write_text(
            tests_path.read_text(encoding="utf-8").rstrip()
            + "\n| attempt-skill-20260808 | 算数 | 8 | 10 | — | — | — | — | — | — |\n",
            encoding="utf-8",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("実施前の回へ成績内訳" in issue for issue in issues))

    def test_rank_cannot_exceed_participant_count(self):
        self.rewrite(
            "tests.md",
            "| 7.2 | — | 6 | 24 |",
            "| 7.2 | — | 25 | 24 |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("順位は受験者数以下" in issue for issue in issues))

    def test_custom_series_share_one_extensible_attempt_and_score_contract(self):
        self.rewrite(
            "tests.md",
            "| attempt-open-pending | test-open-series | 2026秋 | 小3 | 日程未定 | "
            "learner-a | アオ | 予定 | — | — | — | — | — |",
            "| attempt-open-pending | test-open-series | 2026秋 | 小3 | 2026-08-02 | "
            "learner-a | アオ | 完了 | "
            "[算数](materials/shared/division-check.html)・"
            "[国語](materials/shared/reading-note.html) | 90分 | "
            "二教科を受験した。 | 誤答分野を確認する。 | — |",
        )
        tests_path = self.workspace / "tests.md"
        tests_path.write_text(
            tests_path.read_text(encoding="utf-8").rstrip()
            + "\n| attempt-open-pending | 算数 | 82 | 100 | 65.4 | 58.2 | "
            "120 | 2400 | A | — |\n"
            + "| attempt-open-pending | 国語 | 76 | 100 | 69.1 | 54.3 | "
            "— | — | — | 記述を復習 |\n"
            + "| attempt-open-pending | 2教科総合 | 158 | 200 | — | 56.4 | "
            "98 | 2400 | A | — |\n",
            encoding="utf-8",
        )

        self.assertEqual(check_workspace(self.workspace, date(2026, 8, 3)), [])
        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 3), mode="starter"
        ).load()
        result = next(
            item
            for item in data.activities
            if item.title == "公開総合チャレンジ 2026秋"
        )
        self.assertEqual([item.label for item in result.materials], ["算数", "国語"])
        self.assertIn("算数 82/100点・平均65.4・偏差値58.2・順位120/2400・判定A", result.result_summary)
        self.assertIn("国語 76/100点・平均69.1・偏差値54.3・記述を復習", result.result_summary)
        self.assertIn("2教科総合 158/200点・偏差値56.4・順位98/2400・判定A", result.result_summary)

    def test_missed_test_attempt_appears_in_attention(self):
        self.rewrite(
            "tests.md",
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-18 | learner-a | アオ | 予定 | — | — | — | — | — |",
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-04 | learner-a | アオ | 未実施 | — | 0分 | "
            "当日は受けられなかった。 | 日程を決め直す。 | — |",
        )

        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 5), mode="starter"
        ).load()
        attention = data.attention_items()
        missed = next(
            item
            for item in attention
            if item.title == "夏の到達度チェック 2026年度"
        )
        self.assertEqual(missed.status.name, "MISSED")

        output = Path(self._tmp.name) / "dashboard.html"
        render_dashboard(data, output)
        document = output.read_text(encoding="utf-8")
        self.assertIn('<span>要確認</span><strong>5</strong>', document)
        self.assertIn("日程を決め直す。", document)

    def test_test_result_and_next_action_are_summarized(self):
        long_result = "誤答の詳しい記録" * 30
        long_action = "次回に確認する観点" * 30
        self.rewrite(
            "tests.md",
            "わり算のあまりを2問誤った。 | 次回の前に同じ型を2問確認する。 | — |",
            "{} | {} | — |".format(long_result, long_action),
        )

        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 3), mode="starter"
        ).load()
        result = next(
            item
            for item in data.activities
            if item.title == "算数スキルチェック 第3回"
        )
        self.assertLess(len(result.result_summary), len(long_result))
        self.assertLess(len(result.next_action), len(long_action))
        self.assertTrue(result.result_summary.endswith("…"))
        self.assertTrue(result.next_action.endswith("…"))

    def test_deferred_test_requires_destination_and_an_explicit_new_attempt(self):
        planned = (
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-18 | learner-a | アオ | 予定 | — | — | — | — | — |"
        )
        missing_destination = (
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-04 | learner-a | アオ | 繰越 | — | 0分 | "
            "当日は受けられなかった。 | — | — |"
        )
        self.rewrite("tests.md", planned, missing_destination)
        issues = check_workspace(self.workspace, date(2026, 8, 5))
        self.assertTrue(any("繰越先の日付" in issue for issue in issues))

        self.rewrite(
            "tests.md",
            missing_destination,
            missing_destination.rsplit("| — |", 1)[0] + "| 2026-08-12 |",
        )
        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 5), mode="starter"
        ).load()
        deferred = next(
            item
            for item in data.attention_items()
            if item.title == "夏の到達度チェック 2026年度"
        )
        self.assertEqual(deferred.next_action, "繰越先 8/12")
        self.assertFalse(
            any(
                item.title == "夏の到達度チェック 2026年度"
                for item in data.test_events
            )
        )

        tests_path = self.workspace / "tests.md"
        text = tests_path.read_text(encoding="utf-8")
        marker = "\n\n## 成績内訳"
        self.assertIn(marker, text)
        tests_path.write_text(
            text.replace(
                marker,
                "\n| attempt-summer-20260812 | test-summer | 2026年度 | 小3 | "
                "2026-08-12 | learner-a | アオ | 予定 | — | — | — | — | — |\n"
                "\n## 成績内訳",
                1,
            ),
            encoding="utf-8",
        )
        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 5), mode="starter"
        ).load()
        rescheduled = [
            item
            for item in data.test_events
            if item.title == "夏の到達度チェック 2026年度"
        ]
        self.assertEqual(len(rescheduled), 1)
        self.assertEqual(rescheduled[0].date, date(2026, 8, 12))

    def test_undated_deferred_test_stays_in_attention_only(self):
        self.rewrite(
            "tests.md",
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-18 | learner-a | アオ | 予定 | — | — | — | — | — |",
            "| attempt-summer-20260818 | test-summer | 2026年度 | 小3 | "
            "2026-08-04 | learner-a | アオ | 繰越 | — | 0分 | "
            "当日は受けられなかった。 | — | 日程未定 |",
        )

        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 5), mode="starter"
        ).load()
        deferred = next(
            item
            for item in data.attention_items()
            if item.title == "夏の到達度チェック 2026年度"
        )
        self.assertEqual(deferred.next_action, "繰越先 日程未定")
        self.assertFalse(
            any(
                item.title == "夏の到達度チェック 2026年度"
                for item in data.test_events
            )
        )

    def test_arbitrary_test_category_is_not_interpreted_by_core(self):
        self.rewrite(
            "tests.md",
            "| test-skill-series | 算数スキルチェック | 定期 | 学習教室 |",
            "| test-skill-series | 算数スキルチェック | 定期 | 家庭独自カテゴリ |",
        )
        self.assertEqual(check_workspace(self.workspace, date(2026, 8, 3)), [])
        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 3), mode="starter"
        ).load()
        event = next(
            item
            for item in data.test_events
            if item.title == "算数スキルチェック 第4回"
        )
        self.assertEqual(event.category, "家庭独自カテゴリ")

    def test_other_learner_cannot_use_an_individual_material(self):
        source = self.workspace / "materials" / "shared" / "division-check.html"
        target = (
            self.workspace
            / "materials"
            / "learners"
            / "learner-a"
            / "division-revenge.html"
        )
        target.parent.mkdir(parents=True)
        shutil.copy2(source, target)
        index = self.workspace / "materials" / "index.md"
        index.write_text(
            index.read_text(encoding="utf-8").rstrip()
            + "\n| material-revenge | 個別 | learner-a | 算数 | worksheet | "
            "リベンジ | [問題](learners/learner-a/division-revenge.html) | "
            "自作 | 利用中 |\n",
            encoding="utf-8",
        )
        self.rewrite(
            "schedule.md",
            "| 2026-08-07 | learner-b | コハク | 夜 | 算数 | "
            "分数を図で比べる | 準備済み | "
            "[計算カード](materials/shared/calculation-card.html) | — |",
            "| 2026-08-07 | learner-b | コハク | 夜 | 算数 | "
            "分数を図で比べる | 準備済み | "
            "[問題](materials/learners/learner-a/division-revenge.html) | — |",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("他の学習者の個別教材" in issue for issue in issues))

    def test_material_file_must_be_registered(self):
        source = self.workspace / "materials" / "shared" / "division-check.html"
        shutil.copy2(source, self.workspace / "materials" / "shared" / "orphan.html")
        issues = check_workspace(self.workspace)
        self.assertTrue(any("教材台帳" in issue and "登録されていません" in issue for issue in issues))

    def test_out_of_horizon_routine_still_requires_a_registered_learner(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-03 | — | 毎日 | learner-a | アオ | 朝 | 算数 |",
            "| 2099-08-03 | — | 毎日 | learner-x | 未登録 | 朝 | 算数 |",
        )
        issues = check_workspace(self.workspace, date(2026, 8, 3))
        self.assertTrue(any("learners.mdに一致" in issue for issue in issues))

    def test_new_test_ledger_cannot_coexist_with_legacy_test_table(self):
        schedule = self.workspace / "schedule.md"
        schedule.write_text(
            schedule.read_text(encoding="utf-8")
            + "\n## テスト予定\n\n"
            + "| 日付 | 学習者 | 種別 | テスト名 | 備考 |\n"
            + "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("二重正本" in issue or "テスト予定表を削除" in issue for issue in issues))

    def test_new_test_ledger_rejects_a_test_marker_in_schedule(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-05 | learner-a | アオ | 朝 | 国語 | 短い文章を要約する |",
            "| 2026-08-05 | learner-a | アオ | 朝 | 国語 | 【本番】短い文章を要約する |",
        )
        issues = check_workspace(self.workspace, date(2026, 8, 3))
        self.assertTrue(
            any(
                "tests.md導入後はschedule.mdに【本番】行" in issue
                for issue in issues
            )
        )

    def test_deferred_schedule_requires_a_later_destination(self):
        self.rewrite(
            "schedule.md",
            "| 2026-08-05 | learner-b | コハク | 夜 | 国語 | "
            "読書メモの続きを書く | 繰越 | "
            "[読書メモ](materials/shared/reading-note.html) | 2026-08-07 |",
            "| 2026-08-05 | learner-b | コハク | 夜 | 国語 | "
            "読書メモの続きを書く | 繰越 | "
            "[読書メモ](materials/shared/reading-note.html) | — |",
        )
        issues = check_workspace(self.workspace, date(2026, 8, 5))
        self.assertTrue(any("繰越先の日付" in issue for issue in issues))

    def test_legacy_workspace_keeps_accepting_a_deferred_row_without_target(self):
        self.rewrite(
            "schedule.md",
            "読書メモの続きを書く | 繰越 | "
            "[読書メモ](materials/shared/reading-note.html) | 2026-08-07 |",
            "読書メモの続きを書く | 繰越 | "
            "[読書メモ](materials/shared/reading-note.html) | — |",
        )
        (self.workspace / "learners.md").unlink()
        (self.workspace / "tests.md").unlink()
        (self.workspace / "materials" / "index.md").unlink()

        data = StarterMarkdownAdapter(
            self.workspace, date(2026, 8, 5), mode="starter"
        ).load()
        deferred = next(
            item
            for item in data.schedule
            if item.title == "読書メモの続きを書く"
            and item.status.name == "DEFERRED"
        )
        self.assertIsNone(deferred.deferred_to)

    def test_duplicate_ledger_table_is_rejected(self):
        learners = self.workspace / "learners.md"
        learners.write_text(
            learners.read_text(encoding="utf-8")
            + "\n| 学習者ID | 呼び名 | 学年目安 | 状態 | メモ |\n"
            + "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
        issues = check_workspace(self.workspace)
        self.assertTrue(any("同じ正本表が複数" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
