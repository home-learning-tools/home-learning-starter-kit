"""private/demo共通Web MVPの契約・安全性テスト。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

from kit.starter_web.__main__ import _assert_demo_isolation, main
from kit.starter_web.adapters import (
    StarterMarkdownAdapter,
)
from kit.starter_web.adapter_support import activity_status, enrich_schedule
from kit.starter_web.markdown import (
    first_date,
    link_target_path,
    resolve_links,
    split_table_row,
)
from kit.starter_web.model import (
    ActivityItem,
    DashboardData,
    MaterialLink,
    MaterialRequirement,
    MaterialStatus,
    PrepIssue,
    ScheduleItem,
    ScheduleKind,
    Status,
    TestEvent,
    activity_matches_schedule,
)
from kit.starter_web.presentation import (
    CATALOG_ADVANCED,
    CATALOG_BASICS,
    CATALOG_EXTRAS,
    CatalogGroup,
    CatalogItem,
    CatalogView,
    ExtraSection,
    GuideTerm,
    GuideView,
    MaterialLinkScope,
    PendingBadgeMode,
    Presentation,
    catalog_progress,
)
from kit.starter_web.requirements import parse_requirements
from kit.starter_web.sync_report import sync_issues
from kit.starter_web.render import (
    _duration_minutes,
    _month_day,
    _week_category,
    escape_text,
    render_dashboard,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "examples" / "demo"


def _ai_operations_text() -> str:
    """AI運用ルールの正本を読む。

    privateでは ``kit/ai/``、public抽出後は ``docs/`` に置かれるため、
    どちらの配置でも動くようにする。
    """

    for relative in ("kit/ai/AI-OPERATIONS.md", "docs/AI-OPERATIONS.md"):
        path = REPO_ROOT / relative
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise AssertionError("AI-OPERATIONS.md が見つかりません")

# 日付変更スクリプトを擬似DOM・擬似時計で動かし、最初の反応だけを記録する。
# 実ブラウザではreload後にページが作り直されるため、1件目より後は再現しない。
AUTO_RELOAD_HARNESS = """
const fs = require("fs");
const [, , scriptPath, startIso, tickCount] = process.argv;
const script = fs.readFileSync(scriptPath, "utf8");
const start = new Date(startIso);
let now = new Date(start.getTime());
const events = [];
const FakeDate = class extends Date {
  constructor(...args) {
    if (args.length) { super(...args); } else { super(now.getTime()); }
  }
  static now() { return now.getTime(); }
};
const timers = [];
const location = { reload: () => events.push("reload") };
const document = {
  hidden: false,
  body: { firstChild: null, insertBefore: () => events.push("banner") },
  createElement: () => ({ appendChild() {}, addEventListener() {} }),
  addEventListener: () => {},
};
new Function("Date", "setInterval", "location", "document", script)(
  FakeDate, (fn, ms) => timers.push({ fn: fn, ms: ms }), location, document
);
// 擬似時計は、スクリプトが実際に登録した間隔だけ時刻を進める。
const step = timers.length ? Math.min(...timers.map((t) => t.ms)) : 10000;
for (let i = 0; i < Number(tickCount) && events.length === 0; i += 1) {
  now = new Date(now.getTime() + step);
  timers.forEach((t) => t.fn());
}
process.stdout.write(JSON.stringify({
  events: events,
  intervals: timers.map((t) => t.ms),
  elapsedSeconds: (now.getTime() - start.getTime()) / 1000,
}));
"""


COPY_PATH_HARNESS = """
const fs = require("fs");
const [, , scriptPath, scenario] = process.argv;
const script = fs.readFileSync(scriptPath, "utf8");

// クリップボードの成否とexecCommandの成否だけを差し替え、あとは最小のDOMを置く。
// scenario = "<mode>[:exec-ok|:exec-ng][:rapid][:stale-reject]"
// mode=pending は結果を保留し、あとから任意の順で完了させる（並行完了の再現）。
const parts = scenario.split(":");
const clipboardMode = parts[0];
const execOk = parts.indexOf("exec-ok") !== -1;
const pending = [];

function classList() {
  const names = new Set();
  return {
    add: (name) => names.add(name),
    remove: (name) => names.delete(name),
    contains: (name) => names.has(name),
  };
}
function copyButton(path) {
  const list = classList();
  list.add("copy-path");
  return {
    nodeType: 1,
    classList: list,
    getAttribute: (name) => (name === "data-copy-path" ? path : null),
    parentNode: null,
  };
}
const buttons = [copyButton("materials/a.html"), copyButton("materials/b.html")];
const status = { textContent: "" };
const manual = { hidden: true };
const manualField = { value: "", focus() {}, select() {} };
const elements = {
  "copy-path-status": status,
  "copy-path-manual": manual,
  "copy-path-manual-field": manualField,
  "copy-path-manual-close": { addEventListener() {} },
};
let clickHandler = null;
const document = {
  body: { appendChild() {}, removeChild() {} },
  getElementById: (id) => elements[id] || null,
  addEventListener: (type, fn) => { if (type === "click") { clickHandler = fn; } },
  createElement: () => ({ style: {}, setAttribute() {}, select() {}, value: "" }),
  execCommand: () => execOk,
};
const navigator = clipboardMode === "none" ? {} : {
  clipboard: {
    writeText: (text) => {
      if (clipboardMode === "throw") { throw new Error("blocked"); }
      // .then(onFulfilled, onRejected) だけを使う実装に合わせたスタブ。
      return { then: (ok, ng) => {
        if (clipboardMode === "pending") { pending.push({ ok: ok, ng: ng }); return; }
        if (clipboardMode === "ok") { ok(); } else { ng(new Error("denied")); }
      } };
    },
  },
};
let nextTimer = 1;
const timers = new Map();
const setTimeout = (fn) => { const id = nextTimer; nextTimer += 1; timers.set(id, fn); return id; };
const clearTimeout = (id) => { timers.delete(id); };

new Function("document", "navigator", "setTimeout", "clearTimeout", script)(
  document, navigator, setTimeout, clearTimeout
);

const snapshots = [];
function snapshot() {
  snapshots.push({
    statusText: status.textContent,
    manualHidden: manual.hidden,
    manualValue: manualField.value,
    copied: buttons.map((button) => button.classList.contains("is-copied")),
    timerCount: timers.size,
  });
}
function click(button) {
  let prevented = false;
  clickHandler({ target: button, preventDefault: () => { prevented = true; } });
  snapshots.push({ prevented: prevented });
}
click(buttons[0]);
snapshot();
if (parts.indexOf("rapid") !== -1) {
  click(buttons[1]);
  snapshot();
}
// 新しいクリックの結果を先に、古いクリックの結果をあとから完了させる。
for (let i = pending.length - 1; i >= 0; i -= 1) {
  const stale = i < pending.length - 1;
  if (stale && parts.indexOf("stale-reject") !== -1) {
    pending[i].ng(new Error("denied"));
  } else {
    pending[i].ok();
  }
  snapshot();
}
Array.from(timers.values()).forEach((fn) => fn());
snapshot();
process.stdout.write(JSON.stringify(snapshots));
"""


class _DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.hrefs = []
        self.week_titles = {
            "previous": [],
            "current": [],
            "next": [],
        }
        self._week_row = None
        self._week_section_depth = 0
        self._week_title_parts = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        # ナビゲーションが指すセクションIDだけを集める（コピー通知欄などの
        # 部品IDは対象外）。
        if tag == "section" and "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "a" and "href" in attributes:
            self.hrefs.append(attributes["href"])
        if tag == "section":
            if self._week_row is not None:
                self._week_section_depth += 1
            else:
                for row in self.week_titles:
                    if "week-row-{}".format(row) in classes:
                        self._week_row = row
                        self._week_section_depth = 1
                        break
        if (
            tag == "span"
            and "week-item-title" in classes
            and self._week_row is not None
        ):
            self._week_title_parts = []

    def handle_data(self, data):
        if self._week_title_parts is not None:
            self._week_title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self._week_title_parts is not None:
            self.week_titles[self._week_row].append(
                "".join(self._week_title_parts)
            )
            self._week_title_parts = None
        if tag == "section" and self._week_row is not None:
            self._week_section_depth -= 1
            if self._week_section_depth == 0:
                self._week_row = None


class StarterWebMarkdownTest(unittest.TestCase):
    def test_escaped_pipe_is_not_split_into_another_cell(self):
        self.assertEqual(
            split_table_row(r"| 学習 | A \| B |"),
            ["学習", r"A \| B"],
        )

    def test_link_outside_allowed_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "demo"
            workspace.mkdir()
            source = workspace / "schedule.md"
            source.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "許可ルート外"):
                resolve_links(
                    "[秘密](../secret.pdf)",
                    source,
                    workspace,
                )


    def test_copy_path_drops_fragments_and_keeps_readable_characters(self):
        """フラグメントは含めず、空白・日本語はそのままの文字で持つ。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "schedule.md"
            source.write_text("", encoding="utf-8")
            folder = root / "materials" / "国語 まとめ"
            folder.mkdir(parents=True)
            (folder / "第1回.html").write_text("", encoding="utf-8")

            links = resolve_links(
                "[まとめ](materials/国語 まとめ/第1回.html#q3)", source, root
            )

            self.assertEqual(
                links[0].copy_path, "materials/国語 まとめ/第1回.html"
            )
            self.assertNotIn("#", links[0].copy_path)
            self.assertNotIn("%", links[0].copy_path)

    def test_percent_encoded_link_is_decoded_before_copying(self):
        """percent encodingされたリンクも、読める文字でコピーできる。

        Markdownリンク側がURLエンコードされていることがあるため、解決前に
        戻す。戻さないと `%E5%9B%BD%E8%AA%9E` のままコピーされ、リンク先の
        実在判定も外れる。
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "schedule.md"
            source.write_text("", encoding="utf-8")
            folder = root / "materials"
            folder.mkdir()
            (folder / "国語 まとめ.html").write_text("", encoding="utf-8")

            links = resolve_links(
                "[まとめ](materials/%E5%9B%BD%E8%AA%9E%20%E3%81%BE%E3%81%A8"
                "%E3%82%81.html)",
                source,
                root,
            )

            self.assertEqual(links[0].copy_path, "materials/国語 まとめ.html")
            self.assertNotIn("%", links[0].copy_path)
            # 解決したtargetも実在ファイルを指す。
            self.assertTrue(Path(links[0].target).is_file())

    def test_percent_encoded_parent_reference_is_still_rejected(self):
        """encodedな `..` が許可ルート検査をすり抜けない。

        デコードを検査より後に置くと、`%2E%2E` が「ルート内の普通の名前」と
        見なされて通ってしまう。デコードは必ず解決・検査より先に行う。
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (root / "secret.pdf").write_text("", encoding="utf-8")
            source = workspace / "schedule.md"
            source.write_text("", encoding="utf-8")

            for encoded in ("%2E%2E/secret.pdf", "%2e%2e/secret.pdf"):
                with self.subTest(encoded=encoded):
                    with self.assertRaisesRegex(ValueError, "許可ルート外"):
                        resolve_links(
                            "[秘密]({})".format(encoded), source, workspace
                        )

    def test_checkup_resolves_encoded_links_the_same_way(self):
        """checkupの実在判定も同じ規則で解決する（食い違いを作らない）。

        片方だけデコードすると、画面では開ける教材をcheckupが
        「リンク切れ」と誤って報告する。
        """

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "schedule.md"
            source.write_text("", encoding="utf-8")
            (workspace / "materials").mkdir()
            (workspace / "materials" / "国語.html").write_text(
                "", encoding="utf-8"
            )
            encoded = "materials/%E5%9B%BD%E8%AA%9E.html"

            self.assertEqual(
                link_target_path(source, encoded),
                (workspace / "materials" / "国語.html").resolve(),
            )
            self.assertTrue(link_target_path(source, encoded).is_file())

    def test_undecodable_link_is_an_input_error_not_a_crash(self):
        """`%00` のような復号できない値は、例外送出ではなく入力エラーにする。

        素の `ValueError: embedded null byte` を素通りさせると、呼び出し側が
        入力エラーとして報告できず checkup ごと落ちる。
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "schedule.md"
            source.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "リンクに使えない文字"):
                link_target_path(source, "materials/a%00b.html")
            # resolve_links からも同じ扱いで伝わる。
            with self.assertRaisesRegex(ValueError, "リンクに使えない文字"):
                resolve_links("[壊れ](materials/a%00b.html)", source, root)

    def test_copy_path_exists_even_when_the_material_file_is_missing(self):
        """リンク切れでも、許可ルート内へ解決できれば論理パスは作れる。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "schedule.md"
            source.write_text("", encoding="utf-8")

            links = resolve_links(
                "[未作成](materials/not-created-yet.html)", source, root
            )

            self.assertEqual(
                links[0].copy_path, "materials/not-created-yet.html"
            )
            self.assertFalse(Path(links[0].target).exists())

    def test_external_link_has_no_copy_path(self):
        """外部URLはローカルパスへ推測変換しない。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "schedule.md"
            source.write_text("", encoding="utf-8")

            links = resolve_links(
                "[外部](https://example.test/drill)",
                source,
                root,
                allow_external=True,
            )

            self.assertEqual(links[0].target, "https://example.test/drill")
            self.assertIsNone(links[0].copy_path)

    def test_copy_path_never_escapes_the_allowed_root(self):
        """ルート外参照は従来どおり拒否し、コピー値も作らない。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (root / "secret.pdf").write_text("", encoding="utf-8")
            source = workspace / "schedule.md"
            source.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "許可ルート外"):
                resolve_links("[秘密](../secret.pdf)", source, workspace)

            # 遠回りしてルートへ戻る書き方も、相対の見た目を残さない。
            (workspace / "materials").mkdir()
            (workspace / "materials" / "a.html").write_text(
                "", encoding="utf-8"
            )
            links = resolve_links(
                "[戻り](../workspace/materials/a.html)", source, workspace
            )
            self.assertEqual(links[0].copy_path, "materials/a.html")

    def test_first_date_uses_position_not_date_format_priority(self):
        self.assertEqual(
            first_date(
                "7/30 直前対策（2026-07-29に予定を更新）",
                date(2026, 7, 29),
            ),
            date(2026, 7, 30),
        )
        self.assertEqual(
            first_date(
                "2026-07-29に決定、8/1に実施",
                date(2026, 7, 29),
            ),
            date(2026, 7, 29),
        )


class AdapterContractTest(unittest.TestCase):
    def test_starter_uses_explicit_test_marker_instead_of_title_words(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2026-08-07 | learner-a | アオ | 通常 | 算数 | "
                "キッズBEE 本大会【本番】 | 予定 | — | — |\n"
                "| 2026-08-08 | learner-a | アオ | 通常 | 算数 | "
                "全国統一テスト 過去問2024 | 予定 | — | — |\n\n"
                "## テスト予定\n\n"
                "| 日付 | 学習者 | 種別 | テスト名 | 備考 |\n"
                "|---|---|---|---|---|\n"
                "| 2026-08-07 | アオ | 大会 | キッズBEE 本大会 | — |\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            data = StarterMarkdownAdapter(
                workspace,
                date(2026, 8, 5),
            ).load()

            self.assertEqual(
                [(item.title, item.kind) for item in data.schedule],
                [
                    ("キッズBEE 本大会", ScheduleKind.TEST),
                    ("全国統一テスト 過去問2024", ScheduleKind.DATED),
                ],
            )

    def test_historical_anchor_does_not_require_finished_test_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2000-08-18 | learner-a | アオ | 通常 | 算数 | "
                "過去の本番【本番】 | 完了 | — | — |\n\n"
                "## テスト予定\n\n"
                "| 日付 | 学習者 | 種別 | テスト名 | 備考 |\n"
                "|---|---|---|---|---|\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            data = StarterMarkdownAdapter(
                workspace,
                date(1999, 8, 1),
            ).load()

            self.assertEqual(data.schedule[0].kind, ScheduleKind.TEST)

    def test_test_event_does_not_require_duplicate_dated_schedule_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2026-08-05 | learner-a | アオ | 通常 | 国語 | "
                "読解 | 予定 | — | — |\n\n"
                "## テスト予定\n\n"
                "| 日付 | 学習者 | 種別 | テスト名 | 備考 |\n"
                "|---|---|---|---|---|\n"
                "| 2099-08-18 | アオ | 学校 | 夏休み確認テスト | — |\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            data = StarterMarkdownAdapter(
                workspace,
                date(2026, 8, 5),
            ).load()

            self.assertEqual(
                [event.title for event in data.test_events],
                ["夏休み確認テスト"],
            )
            self.assertEqual(data.schedule[0].kind, ScheduleKind.DATED)

    def test_historical_deferred_note_does_not_override_completed_activity(self):
        self.assertEqual(
            activity_status(
                "算数対策",
                "全問を実施した。前回からの繰越問題も回収した。",
            ),
            Status.COMPLETED,
        )

    def test_historical_partial_note_does_not_override_completed_activity(self):
        self.assertEqual(
            activity_status(
                "漢字マスター taisaku_02 後半",
                "後半25問を実施して完結。7/28は前半のみで問50は未実施。",
            ),
            Status.COMPLETED,
        )

    def test_partial_activity_requires_a_current_explicit_marker(self):
        self.assertEqual(
            activity_status(
                "算数ワークシート",
                "【一部実施】全12問のうち問1〜8まで実施。",
            ),
            Status.PARTIAL,
        )

    def test_historical_unknown_note_does_not_override_completed_activity(self):
        self.assertEqual(
            activity_status(
                "算数対策",
                "全問を実施した。7/28は実施有無未確認。",
            ),
            Status.COMPLETED,
        )

    def test_unknown_activity_requires_a_current_explicit_marker(self):
        self.assertEqual(
            activity_status(
                "算数ワークシート",
                "【実施有無未確認】実施報告を確認中。",
            ),
            Status.UNKNOWN,
        )

    def test_routine_does_not_match_unrelated_activity_in_same_subject(self):
        routine = ScheduleItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            slot="朝",
            subject="漢字",
            title="漢字マスター",
            status=Status.PLANNED,
            kind=ScheduleKind.ROUTINE,
        )
        unrelated = ActivityItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            subject="漢字",
            title="別の漢字ドリル",
            duration="10分",
            status=Status.COMPLETED,
            result_summary="全問正解。",
        )

        enrich_schedule([routine], [unrelated])

        self.assertEqual(routine.status, Status.PLANNED)

    def test_creation_only_activity_does_not_complete_routine(self):
        routine = ScheduleItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            slot="朝",
            subject="漢字",
            title="漢字マスター",
            status=Status.PLANNED,
            kind=ScheduleKind.ROUTINE,
        )
        creation_only = ActivityItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            subject="漢字",
            title="漢字マスター リベンジ版を作成",
            duration="—",
            status=Status.COMPLETED,
            result_summary="作成のみ・未実施。",
        )

        enrich_schedule([routine], [creation_only])

        self.assertEqual(routine.status, Status.PLANNED)

    def test_partial_activity_completes_the_matching_routine(self):
        routine = ScheduleItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            slot="夜",
            subject="国語",
            title="音読",
            status=Status.PLANNED,
            kind=ScheduleKind.ROUTINE,
        )
        partial_worksheet = ActivityItem(
            date=date(2026, 8, 5),
            learner_id="learner-a",
            learner_name="アオ",
            subject="国語",
            title="音読ワークシート",
            duration="10分",
            status=Status.PARTIAL,
            result_summary="【一部実施】全12問のうち問1〜8まで実施。",
        )

        enrich_schedule([routine], [partial_worksheet])

        self.assertEqual(routine.status, Status.COMPLETED)
        self.assertEqual(partial_worksheet.status, Status.PARTIAL)


    def test_demo_has_three_weeks_and_required_statuses(self):
        data = StarterMarkdownAdapter(
            DEMO_ROOT, date(2026, 8, 5)
        ).load()
        required = {
            Status.COMPLETED,
            Status.PARTIAL,
            Status.MISSED,
            Status.UNKNOWN,
            Status.DEFERRED,
        }
        self.assertTrue(required.issubset({item.status for item in data.schedule}))
        self.assertEqual(data.week_start, date(2026, 8, 3))
        self.assertEqual(data.week_end, date(2026, 8, 9))
        self.assertEqual(
            data.schedule_for_week(date(2026, 8, 3)),
            data.week_schedule(),
        )
        self.assertTrue(data.schedule_for_week(date(2026, 7, 27)))
        self.assertTrue(data.schedule_for_week(date(2026, 8, 10)))
        self.assertGreaterEqual(
            len({item.date for item in data.schedule}),
            9,
        )
        self.assertTrue(
            any(item.kind == ScheduleKind.ROUTINE for item in data.schedule)
        )
        activity_keys = {
            (item.date, item.learner_id, item.subject)
            for item in data.activities
        }
        for item in data.schedule:
            if item.status == Status.COMPLETED:
                self.assertIn(
                    (item.date, item.learner_id, item.subject),
                    activity_keys,
                )
        _assert_demo_isolation(data, DEMO_ROOT)


class UnpreparedMaterialTest(unittest.TestCase):
    """今週・来週の予定に対する教材準備状況の判定契約。"""

    ANCHOR = date(2026, 8, 5)  # 水曜。週初は8/3、来週末は8/16

    def _item(
        self,
        day,
        title,
        status=Status.PLANNED,
        kind=ScheduleKind.DATED,
        materials=(),
    ):
        return ScheduleItem(
            date=day,
            learner_id="learner-a",
            learner_name="アオ",
            slot="夜",
            subject="算数",
            title=title,
            status=status,
            kind=kind,
            materials=list(materials),
        )

    def _data(self, schedule):
        return DashboardData(
            mode="private",
            title="教材準備テスト",
            anchor_date=self.ANCHOR,
            schedule=schedule,
            activities=[],
        )

    def test_scope_is_future_dated_plans_within_this_and_next_week(self):
        data = self._data(
            [
                self._item(date(2026, 8, 4), "前日の予定"),
                self._item(date(2026, 8, 5), "基準日の予定"),
                self._item(date(2026, 8, 16), "来週末の予定"),
                self._item(date(2026, 8, 17), "再来週の予定"),
                self._item(
                    date(2026, 8, 6), "反復ルーティン", kind=ScheduleKind.ROUTINE
                ),
                self._item(date(2026, 8, 6), "本番テスト", kind=ScheduleKind.TEST),
                self._item(date(2026, 8, 6), "実施済み", status=Status.COMPLETED),
                self._item(date(2026, 8, 6), "未実施", status=Status.MISSED),
            ]
        )

        self.assertEqual(
            [entry.item.title for entry in data.unprepared_items()],
            ["基準日の予定", "来週末の予定"],
        )
        self.assertEqual(data.prep_horizon_end, date(2026, 8, 16))

    def test_material_file_decides_preparation_not_the_link_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worksheet = root / "keisan.html"
            worksheet.write_text("<h1>計算</h1>", encoding="utf-8")
            printout = root / "kokugo.pdf"
            printout.write_text("%PDF-1.4", encoding="utf-8")
            note = root / "memo.md"
            note.write_text("# メモ", encoding="utf-8")
            missing = root / "mihonrai.html"

            def link(label, path):
                return MaterialLink(label=label, target=str(path))

            data = self._data(
                [
                    self._item(date(2026, 8, 6), "教材リンクなし"),
                    self._item(
                        date(2026, 8, 6),
                        "メモだけ",
                        materials=[link("メモ", note)],
                    ),
                    self._item(
                        date(2026, 8, 6),
                        "WSあり",
                        materials=[link("計算", worksheet), link("メモ", note)],
                    ),
                    self._item(
                        date(2026, 8, 6),
                        "PDFあり",
                        materials=[link("国語", printout)],
                    ),
                    self._item(
                        date(2026, 8, 7),
                        "リンク切れ",
                        materials=[link("未作成WS", missing)],
                    ),
                    self._item(
                        date(2026, 8, 7),
                        "WSありでもリンク切れは報告",
                        materials=[link("計算", worksheet), link("未作成WS", missing)],
                    ),
                ]
            )

            entries = data.unprepared_items()

        self.assertEqual(
            [(entry.item.title, entry.issue) for entry in entries],
            [
                ("教材リンクなし", PrepIssue.NO_MATERIAL),
                ("メモだけ", PrepIssue.NO_MATERIAL),
                ("リンク切れ", PrepIssue.MISSING_FILE),
                ("WSありでもリンク切れは報告", PrepIssue.MISSING_FILE),
            ],
        )
        self.assertEqual(entries[2].missing_labels, ["未作成WS"])

    def test_external_url_from_markdown_does_not_count_as_prepared_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2026-08-05 | learner-a | アオ | 朝 | 算数 | 外部教材の予定 | "
                "予定 | [外部教材](https://example.test/drill) | — |\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            data = StarterMarkdownAdapter(workspace, self.ANCHOR).load()
            entries = data.unprepared_items()

        self.assertEqual(data.schedule[0].materials, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].item.title, "外部教材の予定")
        self.assertEqual(entries[0].issue, PrepIssue.NO_MATERIAL)

    def test_unprepared_plans_are_rendered_inside_the_attention_block(self):
        data = self._data(
            [
                self._item(date(2026, 8, 6), "教材リンクなしの予定"),
                self._item(date(2026, 8, 20), "対象外の予定"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        attention_block = document.split('id="attention"')[1].split("</section>")[0]
        self.assertIn("教材未準備（今週・来週）", attention_block)
        self.assertIn("教材リンクなしの予定", attention_block)
        self.assertIn("status-prep-no-material", attention_block)
        self.assertNotIn("対象外の予定", attention_block)
        self.assertIn(
            "<span>教材未準備</span><strong>1</strong>",
            document,
        )

    def test_prepared_week_shows_the_empty_message_and_zero_count(self):
        data = self._data([])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn("今週・来週で、教材が未準備の予定はありません。", document)
        self.assertIn("<span>教材未準備</span><strong>0</strong>", document)


class MaterialReadinessTest(unittest.TestCase):
    """予定枠の教材状態（項目単位の判定と、その集計）の契約。"""

    ANCHOR = date(2026, 8, 5)  # 水曜。週初は8/3、来週末は8/16

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.worksheet = self.root / "keisan.html"
        self.worksheet.write_text("<h1>計算</h1>", encoding="utf-8")
        self.printout = self.root / "kokugo.pdf"
        self.printout.write_text("%PDF-1.4", encoding="utf-8")
        self.note = self.root / "memo.md"
        self.note.write_text("# メモ", encoding="utf-8")
        self.missing = self.root / "mihonrai.html"
        self.addCleanup(self._temporary.cleanup)

    def _link(self, label, path):
        return MaterialLink(label=label, target=str(path))

    def _item(
        self,
        title,
        requirements=(),
        materials=(),
        reference_materials=(),
        **kwargs,
    ):
        return ScheduleItem(
            date=kwargs.pop("day", date(2026, 8, 6)),
            learner_id="learner-a",
            learner_name="アオ",
            slot="夜",
            subject="算数",
            title=title,
            status=kwargs.pop("status", Status.PLANNED),
            kind=kwargs.pop("kind", ScheduleKind.DATED),
            materials=list(materials),
            material_requirements=list(requirements),
            reference_materials=list(reference_materials),
            **kwargs,
        )

    def _data(self, schedule):
        return DashboardData(
            mode="private",
            title="教材状態テスト",
            anchor_date=self.ANCHOR,
            schedule=schedule,
            activities=[],
        )

    def _requirement(self, label, links=(), required=True, external=False):
        return MaterialRequirement(
            label=label,
            required=required,
            materials=tuple(links),
            external=external,
        )

    def test_single_item_with_an_existing_html_is_available(self):
        item = self._item(
            "計算", materials=[self._link("計算", self.worksheet)]
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_single_item_with_an_existing_pdf_is_available(self):
        item = self._item(
            "本番問題", materials=[self._link("本番問題", self.printout)]
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_single_item_without_any_link_is_missing(self):
        item = self._item("これから作る")
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)
        self.assertEqual(item.requirements()[0].issue(), PrepIssue.NO_MATERIAL)

    def test_markdown_note_alone_is_not_a_material(self):
        item = self._item("メモだけ", materials=[self._link("メモ", self.note)])
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)
        self.assertEqual(item.requirements()[0].issue(), PrepIssue.NO_MATERIAL)

    def test_external_url_alone_is_not_a_material(self):
        item = self._item(
            "外部教材",
            materials=[
                MaterialLink(label="外部", target="https://example.test/drill")
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)
        self.assertEqual(item.requirements()[0].issue(), PrepIssue.NO_MATERIAL)

    def test_explicit_external_material_is_available_without_a_link(self):
        item = self._item(
            "塾Web",
            requirements=[self._requirement("月例第1回", external=True)],
        )
        self.assertEqual(
            item.material_status(), MaterialStatus.EXTERNAL_AVAILABLE
        )
        self.assertIsNone(item.requirements()[0].issue())
        self.assertEqual(self._data([item]).unprepared_items(), [])

    def test_external_and_missing_local_material_make_the_cell_partial(self):
        item = self._item(
            "外部教材＋家庭教材",
            requirements=[
                self._requirement("塾Web", external=True),
                self._requirement("家庭プリント"),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.PARTIAL)

    def test_external_and_existing_local_material_make_the_cell_available(self):
        item = self._item(
            "外部教材＋家庭教材",
            requirements=[
                self._requirement("塾Web", external=True),
                self._requirement(
                    "家庭プリント", [self._link("計算", self.worksheet)]
                ),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_a_broken_note_link_does_not_hide_the_existing_material(self):
        """補助資料（.md）のリンク切れで「教材未準備」にしない。

        教材状態へ影響するのは .html / .pdf だけで、壊れたMarkdownリンクは
        `npm run validate:links` が別途検出する。
        """

        item = self._item(
            "実在HTML＋存在しないメモ",
            materials=[
                self._link("計算", self.worksheet),
                self._link("メモ", self.root / "nai.md"),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)
        self.assertEqual(item.requirements()[0].missing_link_labels(), [])

    def test_a_broken_link_keeps_the_item_unprepared(self):
        item = self._item(
            "作成済みと書いてあるが実体が無い",
            materials=[
                self._link("計算", self.worksheet),
                self._link("未作成WS", self.missing),
            ],
            status=Status.READY,
        )
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)
        self.assertEqual(item.requirements()[0].issue(), PrepIssue.MISSING_FILE)
        self.assertEqual(
            item.requirements()[0].missing_link_labels(), ["未作成WS"]
        )

    def test_all_prepared_items_make_the_cell_available(self):
        item = self._item(
            "2件とも準備済み",
            requirements=[
                self._requirement("計算", [self._link("計算", self.worksheet)]),
                self._requirement("国語", [self._link("国語", self.printout)]),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_one_missing_item_makes_the_cell_partial(self):
        item = self._item(
            "1件だけ未準備",
            requirements=[
                self._requirement("計算", [self._link("計算", self.worksheet)]),
                self._requirement("これから作る"),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.PARTIAL)

    def test_all_missing_items_make_the_cell_missing(self):
        item = self._item(
            "2件とも未準備",
            requirements=[
                self._requirement("これから作る①"),
                self._requirement("これから作る②", [self._link("メモ", self.note)]),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)

    def test_optional_item_is_outside_the_material_judgement(self):
        item = self._item(
            "教材必須＋教材不要",
            requirements=[
                self._requirement("計算", [self._link("計算", self.worksheet)]),
                self._requirement("口頭確認5分", required=False),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_only_optional_items_need_no_material(self):
        item = self._item(
            "口頭だけ",
            requirements=[self._requirement("口頭確認5分", required=False)],
        )
        self.assertEqual(item.material_status(), MaterialStatus.NOT_REQUIRED)
        self.assertEqual(self._data([item]).unprepared_items(), [])

    def test_item_label_may_contain_the_plus_sign(self):
        cell = (
            "8/6 《教材:物語短文＋修飾語 判定1回目》【未作成】"
            "＋《教材:ことば検定 B02》[B02](b02.html)"
        )
        parse = parse_requirements(cell)
        self.assertEqual(
            [marker.label for marker in parse.markers],
            ["物語短文＋修飾語 判定1回目", "ことば検定 B02"],
        )
        self.assertEqual(parse.title(), "物語短文＋修飾語 判定1回目＋ことば検定 B02")
        self.assertEqual(parse.errors, ())

    def test_the_same_material_can_serve_two_items(self):
        shared = self._link("週別パック", self.worksheet)
        item = self._item(
            "同じ教材を2項目で使う",
            requirements=[
                self._requirement("読解パート", [shared]),
                self._requirement("語句パート", [shared]),
            ],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)

    def test_reference_material_is_neither_an_item_nor_a_material(self):
        """取りやめ・在庫の教材は、実施項目にも教材判定にも数えない。

        必須項目として数えると、実在する在庫WSが分子に入って「一部未準備」に
        なり、その日の実施項目（未作成のスポット確認）が準備済みに見える。
        """

        cell = (
            "8/6 《教材:スポット確認》各1問。"
            "《参考:キッズBEE 第13回（取りやめ・在庫）》[在庫WS]({}) は残置".format(
                self.worksheet
            )
        )
        parse = parse_requirements(cell)
        self.assertEqual(parse.errors, ())
        # タイトルにも項目にも参考は出さない。
        self.assertEqual(parse.title(), "スポット確認")
        self.assertEqual(
            [marker.label for marker in parse.items], ["スポット確認"]
        )

        item = self._item(
            parse.title(),
            requirements=[
                self._requirement(marker.label, required=marker.required)
                for marker in parse.items
            ],
            # 在庫リンクはカードからたどれるよう、セルの教材リンクには残る。
            materials=[self._link("在庫WS", self.worksheet)],
        )
        self.assertEqual(item.material_status(), MaterialStatus.MISSING)
        entries = self._data([item]).unprepared_items()
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            [entry.requirement.label for entry in entries[0].requirements],
            ["スポット確認"],
        )

    def test_reference_material_is_not_used_for_activity_matching(self):
        """在庫を本人希望で実施しても、その日の予定は実施済みにしない。

        ``《参考》`` のリンクはカードから開けるよう ``materials`` に残るので、
        突合に ``materials`` を使うとファイル名一致で誤判定する。
        """

        item = self._item(
            "月例算数のスポット確認",
            requirements=[self._requirement("月例算数のスポット確認")],
            materials=[self._link("在庫WS", self.worksheet)],
            reference_materials=[self._link("在庫WS", self.worksheet)],
        )
        activity = ActivityItem(
            date=item.date,
            learner_id=item.learner_id,
            learner_name=item.learner_name,
            subject="算数",
            title="キッズBEE 第13回（本人希望）",
            duration="20分",
            status=Status.COMPLETED,
            result_summary="実施した。",
            materials=[self._link("在庫WS", self.worksheet)],
        )

        self.assertEqual(item.matching_materials(), [])
        self.assertFalse(activity_matches_schedule(item, activity))

        # 予定の状態も変わらない。
        schedule = [item]
        enrich_schedule(schedule, [activity])
        self.assertEqual(schedule[0].status, Status.PLANNED)

        # 同期警告も、参考教材の実績だけでは消えない。
        data = DashboardData(
            mode="private",
            title="参考教材テスト",
            anchor_date=item.date + timedelta(days=3),
            schedule=schedule,
            activities=[activity],
        )
        self.assertTrue(
            any(
                "月例算数のスポット確認" in issue.message
                for issue in sync_issues(data)
            )
        )

    def test_planned_material_still_matches_its_activity(self):
        """予定教材そのものを実施した場合は、従来どおり照合する。"""

        item = self._item(
            "計算",
            requirements=[
                self._requirement("計算", [self._link("計算", self.worksheet)])
            ],
            materials=[
                self._link("計算", self.worksheet),
                self._link("在庫WS", self.printout),
            ],
            reference_materials=[self._link("在庫WS", self.printout)],
        )
        activity = ActivityItem(
            date=item.date,
            learner_id=item.learner_id,
            learner_name=item.learner_name,
            subject="算数",
            title="計算",
            duration="12分",
            status=Status.COMPLETED,
            result_summary="全問正解。",
            materials=[self._link("計算", self.worksheet)],
        )

        self.assertEqual(
            [link.label for link in item.matching_materials()], ["計算"]
        )
        self.assertTrue(activity_matches_schedule(item, activity))
        schedule = [item]
        enrich_schedule(schedule, [activity])
        self.assertEqual(schedule[0].status, Status.COMPLETED)

    def test_reference_link_is_marked_on_the_card(self):
        """参考リンクを、予定教材と同じ見た目で並べない。"""

        item = self._item(
            "スポット確認",
            requirements=[self._requirement("スポット確認")],
            materials=[self._link("在庫WS", self.worksheet)],
            reference_materials=[self._link("在庫WS", self.worksheet)],
            day=self.ANCHOR,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(self._data([item]), output)
            document = output.read_text(encoding="utf-8").split("</style>", 1)[1]

        today = document.split('id="today"', 1)[1].split("</section>", 1)[0]
        self.assertIn("material-link material-link-reference", today)
        self.assertIn('<span class="material-link-tag">参考</span>', today)

    def test_planned_material_is_not_shown_as_a_reference(self):
        """実施項目の教材は、参考にも書かれていた場合でも参考表示にしない。"""

        link = self._link("共通WS", self.worksheet)
        item = self._item(
            "計算",
            requirements=[self._requirement("計算", [link])],
            materials=[link],
            reference_materials=[link],
            day=self.ANCHOR,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(self._data([item]), output)
            document = output.read_text(encoding="utf-8").split("</style>", 1)[1]

        today = document.split('id="today"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("material-link-reference", today)

    def test_same_file_in_an_item_and_in_reference_is_rejected(self):
        """同じ教材を実施項目と参考の両方へ登録させない。

        どちらの分類か決まらないと、教材状態は「教材あり」なのに実績照合では
        使われない、という食い違いが起きる。
        """

        duplicated = parse_requirements(
            "8/6 《教材:計算》[共通WS](x.html)。《参考:在庫》[同じWS](x.html)"
        )
        self.assertTrue(
            any("両方へ登録しています" in m for m in duplicated.errors),
            msg=duplicated.errors,
        )
        # 相対表記の違いでもすり抜けない。
        normalized = parse_requirements(
            "8/6 《教材:計算》[共通WS](../a/x.html)。《参考:在庫》[同じ](../a/./x.html)"
        )
        self.assertTrue(
            any("両方へ登録しています" in m for m in normalized.errors),
            msg=normalized.errors,
        )

    def test_same_file_shared_by_two_items_is_allowed(self):
        """同じ教材を複数の実施項目で共有するのは従来どおり許す。"""

        parse = parse_requirements(
            "8/6 《教材:A》[共通](x.html)＋《教材:B》[共通](x.html)"
        )
        self.assertEqual(parse.errors, ())
        self.assertEqual(parse.title(), "A＋B")

    def test_planned_material_is_never_dropped_by_a_reference_entry(self):
        """実施項目へ明示した教材は、参考に同じ行があっても突合対象に残す。"""

        link = self._link("共通WS", self.worksheet)
        item = self._item(
            "計算",
            requirements=[self._requirement("計算", [link])],
            materials=[link],
            reference_materials=[link],
        )
        self.assertEqual(item.material_status(), MaterialStatus.AVAILABLE)
        self.assertEqual(item.matching_materials(), [link])

    def test_reference_only_cell_is_rejected(self):
        """参考だけのセルは、実施項目が宣言されていないので通さない。"""

        parse = parse_requirements("8/6 《参考:在庫》[在庫WS](a.html)")
        self.assertFalse(parse.has_items)
        self.assertTrue(
            any("実施項目がありません" in message for message in parse.errors),
            msg=parse.errors,
        )

    def test_separator_must_lead_to_the_next_item_marker(self):
        """マーカーの外の ``＋`` が、項目を画面から落とす抜け道にならない。"""

        dangling = parse_requirements("8/6 《教材:A》[A](a.html)＋B")
        self.assertEqual(dangling.title(), "A")
        self.assertTrue(
            any("後ろが項目マーカーではありません" in m for m in dangling.errors),
            msg=dangling.errors,
        )

        trailing = parse_requirements("8/6 《教材:A》[A](a.html)＋")
        self.assertTrue(
            any("後ろが項目マーカーではありません" in m for m in trailing.errors),
            msg=trailing.errors,
        )

        chained = parse_requirements(
            "8/6 《教材:A》[A](a.html) ＋ **《教材:B》**[B](b.html)"
        )
        self.assertEqual(chained.errors, ())
        self.assertEqual(chained.title(), "A＋B")

    def test_separator_inside_a_link_label_is_not_an_item_break(self):
        """``[単位＋かけ算発展](…)`` の ``＋`` を区切りと誤認しない。"""

        parse = parse_requirements(
            "8/6 《教材:単位＋かけ算発展》[単位＋かけ算発展](a.html)"
            " ＋ **《教材:かさ》**[かさ](b.html)"
        )
        self.assertEqual(parse.errors, ())
        self.assertEqual(parse.title(), "単位＋かけ算発展＋かさ")

    def test_unprepared_list_counts_cells_not_items(self):
        item = self._item(
            "3項目のうち2項目が未準備",
            requirements=[
                self._requirement("準備済み", [self._link("計算", self.worksheet)]),
                self._requirement("未作成"),
                self._requirement("リンク切れ", [self._link("WS", self.missing)]),
            ],
        )
        entries = self._data([item]).unprepared_items()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].material_status, MaterialStatus.PARTIAL)
        self.assertEqual(
            [
                (entry.requirement.label, entry.issue)
                for entry in entries[0].requirements
            ],
            [
                ("未作成", PrepIssue.NO_MATERIAL),
                ("リンク切れ", PrepIssue.MISSING_FILE),
            ],
        )
        self.assertEqual(entries[0].issue, PrepIssue.MISSING_FILE)
        self.assertEqual(entries[0].missing_labels, ["WS"])

    def test_card_badge_and_unprepared_list_agree(self):
        schedule = [
            self._item("準備済み", materials=[self._link("計算", self.worksheet)]),
            self._item("未準備"),
            self._item(
                "一部未準備",
                requirements=[
                    self._requirement("済", [self._link("計算", self.worksheet)]),
                    self._requirement("未"),
                ],
            ),
            self._item(
                "教材不要",
                requirements=[self._requirement("口頭", required=False)],
            ),
        ]
        data = self._data(schedule)
        listed = {entry.item.title for entry in data.unprepared_items()}
        badge_unprepared = {
            item.title
            for item in schedule
            if item.material_status()
            in {MaterialStatus.PARTIAL, MaterialStatus.MISSING}
        }
        self.assertEqual(listed, badge_unprepared)
        self.assertEqual(listed, {"未準備", "一部未準備"})


class PendingBadgeModeTest(unittest.TestCase):
    """予定カードに教材状態を出す表示方式（private版だけで有効）の契約。"""

    ANCHOR = date(2026, 8, 5)

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.worksheet = self.root / "keisan.html"
        self.worksheet.write_text("<h1>計算</h1>", encoding="utf-8")
        self.addCleanup(self._temporary.cleanup)

    def _item(self, title, **kwargs):
        return ScheduleItem(
            date=kwargs.pop("day", self.ANCHOR),
            learner_id="learner-a",
            learner_name="アオ",
            slot="夜",
            subject="算数",
            title=title,
            status=kwargs.pop("status", Status.PLANNED),
            kind=kwargs.pop("kind", ScheduleKind.DATED),
            materials=list(kwargs.pop("materials", [])),
            material_requirements=list(kwargs.pop("requirements", [])),
            **kwargs,
        )

    def _document(self, schedule, presentation=None, activities=()):
        data = DashboardData(
            mode="private",
            title="表示方式テスト",
            anchor_date=self.ANCHOR,
            schedule=schedule,
            activities=list(activities),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            # CSSには全モードのクラスが入るため、本文だけを見る。
            return output.read_text(encoding="utf-8").split("</style>", 1)[1]

    def _readiness(self):
        return Presentation(
            mode_label="テスト",
            privacy_note="テスト",
            pending_badge_mode=PendingBadgeMode.MATERIAL_READINESS,
        )

    def test_default_mode_keeps_the_planned_status(self):
        document = self._document([self._item("これから")])
        self.assertIn(
            '<span class="status status-planned">'
            '<span aria-hidden="true">○</span>予定</span>',
            document,
        )
        self.assertNotIn("教材あり", document)
        self.assertIn("予定状態の凡例", document)

    def test_readiness_mode_replaces_planned_with_the_material_state(self):
        document = self._document(
            [
                self._item(
                    "準備済み",
                    materials=[
                        MaterialLink(label="計算", target=str(self.worksheet))
                    ],
                ),
                self._item("未準備", day=date(2026, 8, 6)),
            ],
            presentation=self._readiness(),
        )
        self.assertIn("status-material-available", document)
        self.assertIn("教材あり", document)
        self.assertIn("status-material-missing", document)
        self.assertNotIn(
            '<span class="status status-planned">', document
        )
        self.assertNotIn(
            '<span class="status status-ready">', document
        )

    def test_readiness_mode_shows_external_material_state(self):
        document = self._document(
            [
                self._item(
                    "塾Web",
                    requirements=[
                        MaterialRequirement(
                            label="月例第1回", external=True
                        )
                    ],
                )
            ],
            presentation=self._readiness(),
        )
        self.assertIn("status-material-external-available", document)
        self.assertIn("外部教材あり", document)

    def test_readiness_legend_separates_material_from_implementation(self):
        document = self._document(
            [self._item("未準備")], presentation=self._readiness()
        )
        self.assertIn("教材の準備状態の凡例", document)
        self.assertIn("実施状態の凡例", document)
        self.assertNotIn("予定状態の凡例", document)
        self.assertNotIn(
            '<span aria-hidden="true">○</span>予定</span>', document
        )
        self.assertNotIn("準備済み", document)
        self.assertIn("外部教材あり", document)

    def test_recorded_states_win_over_the_material_state(self):
        schedule = [
            self._item("完了", status=Status.COMPLETED),
            self._item("一部実施", status=Status.PARTIAL),
            self._item("未実施", status=Status.MISSED),
            self._item(
                "繰越",
                status=Status.DEFERRED,
                deferred_to=date(2026, 8, 7),
            ),
        ]
        document = self._document(schedule, presentation=self._readiness())
        for icon, label in (
            ("✓", "完了"),
            ("◐", "一部実施"),
            ("×", "未実施"),
            ("→", "繰越"),
        ):
            with self.subTest(label=label):
                self.assertIn(
                    '<span aria-hidden="true">{}</span>{}</span>'.format(
                        icon, label
                    ),
                    document,
                )
        # 実施状態が出ている枠には、教材状態のバッジを重ねない。
        self.assertNotIn("status-material-", document.split('id="week"')[0])

    def test_routine_and_test_keep_their_own_badge(self):
        document = self._document(
            [
                self._item("漢字マスター", kind=ScheduleKind.ROUTINE),
                self._item("月例テスト", kind=ScheduleKind.TEST),
            ],
            presentation=self._readiness(),
        )
        self.assertIn('<span aria-hidden="true">↻</span>反復</span>', document)
        self.assertIn('<span aria-hidden="true">◆</span>本番</span>', document)

    def test_past_plan_without_a_record_is_shown_as_unconfirmed(self):
        document = self._document(
            [self._item("前日の予定", day=date(2026, 8, 4))],
            presentation=self._readiness(),
        )
        self.assertIn(
            '<span aria-hidden="true">?</span>実施状況未確認</span>', document
        )

    def test_starter_default_is_not_affected_by_the_private_mode(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8").split("</style>", 1)[1]
        self.assertEqual(
            Presentation.for_mode("starter").pending_badge_mode,
            PendingBadgeMode.STATUS,
        )
        self.assertEqual(
            Presentation.for_mode("demo").pending_badge_mode,
            PendingBadgeMode.STATUS,
        )
        self.assertEqual(
            Presentation.for_mode("private").pending_badge_mode,
            PendingBadgeMode.STATUS,
        )
        self.assertIn("予定状態の凡例", document)
        self.assertIn('<span aria-hidden="true">○</span>予定</span>', document)
        self.assertNotIn("status-material-", document)


class MaterialLinkScopeTest(unittest.TestCase):
    """予定カード・3週間の見通しに出す教材リンクの範囲の契約。"""

    ANCHOR = date(2026, 8, 5)

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.worksheet = self.root / "keisan.html"
        self.worksheet.write_text("<h1>計算</h1>", encoding="utf-8")
        self.stock = self.root / "zaiko.html"
        self.stock.write_text("<h1>在庫</h1>", encoding="utf-8")
        self.note = self.root / "note.md"
        self.note.write_text("# 正本\n", encoding="utf-8")
        self.addCleanup(self._temporary.cleanup)

    def _link(self, label, path):
        return MaterialLink(label=label, target=str(path), copy_path=path.name)

    def _item(self, **kwargs):
        return ScheduleItem(
            date=kwargs.pop("day", self.ANCHOR),
            learner_id="learner-a",
            learner_name="アオ",
            slot="夜",
            subject="算数",
            title=kwargs.pop("title", "計算のたしかめ"),
            status=kwargs.pop("status", Status.PLANNED),
            kind=kwargs.pop("kind", ScheduleKind.DATED),
            materials=list(kwargs.pop("materials", [])),
            reference_materials=list(kwargs.pop("references", [])),
            material_requirements=list(kwargs.pop("requirements", [])),
            **kwargs,
        )

    def _document(self, schedule, scope=None):
        data = DashboardData(
            mode="private",
            title="教材リンクの範囲テスト",
            anchor_date=self.ANCHOR,
            schedule=schedule,
            activities=[],
        )
        presentation = Presentation(
            mode_label="テスト",
            privacy_note="テスト",
            **({"card_material_links": scope} if scope else {}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            # CSSには全モードのクラスが入るため、本文だけを見る。
            return output.read_text(encoding="utf-8").split("</style>", 1)[1]

    def _mixed_item(self):
        """教材・正本md・参考在庫の3種類のリンクを持つ予定。"""

        worksheet = self._link("計算プリント", self.worksheet)
        note = self._link("正本の該当節", self.note)
        stock = self._link("在庫プリント", self.stock)
        return self._item(
            materials=[worksheet, note, stock],
            references=[stock],
            requirements=[
                MaterialRequirement(
                    label="計算のたしかめ",
                    required=True,
                    materials=(worksheet, note),
                )
            ],
        )

    def _today(self, document):
        return document.split('id="today"', 1)[1].split('id="tests"', 1)[0]

    def test_default_scope_keeps_documents_and_stock_links(self):
        """既定（starter・demo）は従来どおり読めたリンクをすべて出す。"""

        today = self._today(self._document([self._mixed_item()]))
        self.assertIn("計算プリント", today)
        self.assertIn("正本の該当節", today)
        self.assertIn("在庫プリント", today)
        self.assertIn("material-link-reference", today)

    def test_material_files_scope_keeps_only_the_slot_materials(self):
        """privateの範囲では、その枠で使う教材ファイルだけを出す。"""

        today = self._today(
            self._document(
                [self._mixed_item()], scope=MaterialLinkScope.MATERIAL_FILES
            )
        )
        self.assertIn("計算プリント", today)
        self.assertNotIn("正本の該当節", today)
        self.assertNotIn("在庫プリント", today)
        self.assertNotIn("material-link-reference", today)

    def test_week_outlook_links_the_slot_materials(self):
        """見通しは教材名を出さず、アイコンリンクで教材へ入れる。"""

        document = self._document(
            [self._mixed_item()], scope=MaterialLinkScope.MATERIAL_FILES
        )
        week = document.split('id="week"', 1)[1].split('id="attention"', 1)[0]
        links = re.findall(r'<a class="week-item-link"[^>]*>.*?</a>', week)
        self.assertEqual(len(links), 1)
        self.assertIn("keisan.html", links[0])
        self.assertIn('title="教材を開く: 計算プリント"', links[0])
        self.assertIn(
            '<span class="sr-only">教材を開く: 計算プリント（別タブで開く）</span>',
            links[0],
        )
        # 見通しではコピーbuttonを増やさない（コピーは予定カード側で行う）。
        self.assertNotIn("copy-path", week)
        self.assertNotIn("正本の該当節", week)
        self.assertNotIn("在庫プリント", week)

    def test_week_outlook_merges_the_same_material(self):
        """同じ教材を2回リンクしても、見通しのアイコンは1つにまとめる。"""

        worksheet = self._link("計算プリント", self.worksheet)
        again = self._link("計算プリント（再掲）", self.worksheet)
        document = self._document([self._item(materials=[worksheet, again])])
        week = document.split('id="week"', 1)[1].split('id="attention"', 1)[0]
        self.assertEqual(
            len(re.findall(r'<a class="week-item-link"', week)), 1
        )

    def test_week_outlook_merges_the_same_material_across_fragments(self):
        """同じ教材の別の見出しへのリンクも、教材としては1つに数える。"""

        first = MaterialLink(
            label="計算プリント 問1",
            target="{}#q1".format(self.worksheet),
            copy_path=self.worksheet.name,
        )
        second = MaterialLink(
            label="計算プリント 問2",
            target="{}#q2".format(self.worksheet),
            copy_path=self.worksheet.name,
        )
        document = self._document([self._item(materials=[first, second])])
        week = document.split('id="week"', 1)[1].split('id="attention"', 1)[0]
        links = re.findall(r'<a class="week-item-link"[^>]*>', week)
        self.assertEqual(len(links), 1)
        # 残すのは最初に現れたリンク（fragmentもそのまま開ける）。
        self.assertIn("keisan.html#q1", links[0])

    def test_week_outlook_omits_reference_stock_in_every_scope(self):
        """在庫・参考は、参考の見た目を作れない見通しには出さない。"""

        document = self._document([self._mixed_item()])
        week = document.split('id="week"', 1)[1].split('id="attention"', 1)[0]
        self.assertIn("keisan.html", week)
        self.assertNotIn("zaiko.html", week)

    def test_default_scope_is_kept_for_every_core_mode(self):
        """公開契約の既定は従来表示（絞り込みは下流の指定で入れる）。"""

        for mode in ("starter", "demo", "private"):
            self.assertEqual(
                Presentation.for_mode(mode).card_material_links,
                MaterialLinkScope.ALL,
            )


class RenderAndSafetyTest(unittest.TestCase):
    def test_month_day_format_is_platform_independent(self):
        self.assertEqual(_month_day(date(2026, 8, 5)), "8/5")

    def test_unmeasured_date_is_not_counted_as_minutes(self):
        self.assertEqual(
            _duration_minutes("未測定（2026-07-28・報告なし）"),
            0,
        )
        self.assertEqual(_duration_minutes("18分"), 18)

    def test_three_week_edge_category_uses_subject_and_real_test(self):
        def item(
            subject,
            title,
            status=Status.PLANNED,
            kind=ScheduleKind.DATED,
        ):
            return ScheduleItem(
                date=date(2026, 8, 5),
                learner_id="learner-a",
                learner_name="アオ",
                slot="通常",
                subject=subject,
                title=title,
                status=status,
                kind=kind,
            )

        cases = (
            (item("国語", "読解"), "japanese"),
            (item("算数", "計算"), "math"),
            (item("理科", "植物"), "science"),
            (item("社会", "地図"), "social"),
            (item("漢字", "漢字マスター"), "kanji"),
            (
                item(
                    "算数",
                    "キッズBEE 本大会",
                    kind=ScheduleKind.TEST,
                ),
                "test",
            ),
            (
                item("算数", "数検5級 本番", kind=ScheduleKind.TEST),
                "test",
            ),
            (item("国語", "かんじ五十もんテストの失点回収"), "japanese"),
            (item("算数", "全国統一テスト 過去問2024"), "math"),
            (item("算数", "灘テスト対策②"), "math"),
            (item("国語", "月例テストの再実施"), "japanese"),
            (item("国語", "ことばミニテスト"), "japanese"),
            (item("英語", "音読"), "other"),
        )

        for schedule_item, expected in cases:
            with self.subTest(title=schedule_item.title):
                self.assertEqual(_week_category(schedule_item), expected)

        self.assertEqual(
            _week_category(item("算数", "計算", Status.COMPLETED)),
            "math",
        )

    def test_routine_is_not_counted_as_weekly_completion(self):
        data = DashboardData(
            mode="private",
            title="反復集計テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="漢字",
                    title="反復ルーティン",
                    status=Status.COMPLETED,
                    kind=ScheduleKind.ROUTINE,
                ),
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="夜",
                    subject="算数",
                    title="日付付き予定",
                    status=Status.COMPLETED,
                ),
            ],
            activities=[],
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn(
            "<span>今週の完了</span><strong>1</strong>",
            document,
        )
        self.assertIn(
            'study-card status-edge-completed schedule-kind-routine',
            document,
        )

    def test_upcoming_tests_include_the_third_month_and_separate_undated(self):
        data = DashboardData(
            mode="private",
            title="テスト予定境界テスト",
            anchor_date=date(2026, 8, 1),
            schedule=[],
            activities=[],
            test_events=[
                TestEvent(date(2026, 7, 31), "アオ", "塾", "過去"),
                TestEvent(date(2026, 8, 1), "アオ", "塾", "当日"),
                TestEvent(date(2026, 8, 31), "アオ", "塾", "30日後"),
                TestEvent(date(2026, 11, 1), "アオ", "塾", "3か月後"),
                TestEvent(date(2026, 11, 2), "アオ", "塾", "3か月と1日後"),
                TestEvent(None, "アオ", "外部", "日程未定", "告知待ち"),
            ],
        )

        self.assertEqual(
            [item.title for item in data.upcoming_tests()],
            ["当日", "30日後", "3か月後"],
        )
        self.assertEqual(
            [item.title for item in data.undated_tests()],
            ["日程未定"],
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn('<section class="block" id="tests">', document)
        self.assertIn("今後3か月のテスト予定", document)
        self.assertIn("あと30日", document)
        self.assertIn("あと92日", document)
        self.assertIn("日程未定", document)
        self.assertIn("対象: アオ", document)
        self.assertIn("備考: 告知待ち", document)
        self.assertNotIn("アオ・告知待ち", document)
        self.assertNotIn(">過去<", document)
        self.assertNotIn(">3か月と1日後<", document)

    def test_upcoming_tests_clamp_the_window_to_the_end_of_a_short_month(self):
        """基準日 11/30 の3か月後は 2/28（うるう年は 2/29）へ丸める。"""

        data = DashboardData(
            mode="private",
            title="月末丸めテスト",
            anchor_date=date(2026, 11, 30),
            schedule=[],
            activities=[],
            test_events=[
                TestEvent(date(2027, 2, 28), "アオ", "塾", "丸めた末日"),
                TestEvent(date(2027, 3, 1), "アオ", "塾", "期間外"),
            ],
        )

        self.assertEqual(
            [item.title for item in data.upcoming_tests()],
            ["丸めた末日"],
        )

        leap = DashboardData(
            mode="private",
            title="うるう年の月末丸めテスト",
            anchor_date=date(2027, 11, 30),
            schedule=[],
            activities=[],
            test_events=[
                TestEvent(date(2028, 2, 29), "アオ", "塾", "うるう日"),
                TestEvent(date(2028, 3, 1), "アオ", "塾", "期間外"),
            ],
        )

        self.assertEqual(
            [item.title for item in leap.upcoming_tests()],
            ["うるう日"],
        )

    def test_undated_tests_still_show_the_empty_dated_message(self):
        data = DashboardData(
            mode="private",
            title="日程未定だけのテスト",
            anchor_date=date(2026, 8, 1),
            schedule=[],
            activities=[],
            test_events=[
                TestEvent(None, "アオ", "外部", "公開テスト", "日程告知待ち"),
            ],
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn("今後3か月のテスト予定はありません。", document)
        self.assertIn("公開テスト", document)
        self.assertIn("対象: アオ", document)
        self.assertIn("備考: 日程告知待ち", document)

    def test_morning_schedule_is_sorted_before_other_slots(self):
        data = DashboardData(
            mode="private",
            title="枠順テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="夜",
                    subject="算数",
                    title="夜枠",
                    status=Status.PLANNED,
                ),
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="家庭学習",
                    subject="国語",
                    title="通常枠",
                    status=Status.PLANNED,
                ),
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="漢字",
                    title="朝枠",
                    status=Status.PLANNED,
                    kind=ScheduleKind.ROUTINE,
                ),
            ],
            activities=[],
        )

        self.assertEqual(
            [item.title for item in data.today_schedule()],
            ["朝枠", "通常枠", "夜枠"],
        )
        self.assertEqual(
            [item.title for item in data.week_schedule()],
            ["朝枠", "通常枠", "夜枠"],
        )

    def test_common_renderer_contains_all_mvp_sections(self):
        data = StarterMarkdownAdapter(
            DEMO_ROOT, date(2026, 8, 5)
        ).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "dist" / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")
            parser = _DocumentParser()
            parser.feed(document)

            self.assertEqual(
                {
                    "today",
                    "tests",
                    "week",
                    "attention",
                    "records",
                    "materials",
                    "ai",
                },
                parser.ids,
            )
            # starter/demoでは「今週の教材」を独立セクションとして出す。
            self.assertIn("<h2>今週の教材</h2>", document)
            self.assertIn('<a href="#materials">教材</a>', document)
            self.assertIn('class="material-tile"', document)
            self.assertIn("夏の到達度チェック", document)
            self.assertIn("公開総合チャレンジ", document)
            self.assertIn("次</span>", document)
            self.assertIn("正本 L", document)
            self.assertIn("計算カード", document)
            self.assertEqual(document.count('<section class="week-row '), 3)
            # 共通CSSにはprivate用の規則が入るため、本文で固定3週表示を確認する。
            self.assertNotIn(
                "week-row-label", document.split("</style>", 1)[1]
            )
            self.assertIn('aria-label="前週 7/27から8/2"', document)
            self.assertIn('aria-label="今週 8/3から8/9"', document)
            self.assertIn('aria-label="翌週 8/10から8/16"', document)
            self.assertIn(
                'role="group" aria-label="予定状態の凡例"',
                document,
            )
            self.assertIn(
                'role="group" aria-label="予定種別の色の凡例"',
                document,
            )
            for category, label in (
                ("japanese", "国語"),
                ("math", "算数"),
                ("science", "理科"),
                ("social", "社会"),
                ("kanji", "漢字"),
                ("test", "テスト"),
                ("other", "その他"),
            ):
                self.assertIn(
                    'week-category-swatch-{}" aria-hidden="true"></span>{}</span>'.format(
                        category,
                        label,
                    ),
                    document,
                )
            self.assertIn("○</span>予定</span>", document)
            self.assertIn("●</span>準備済み</span>", document)
            self.assertIn("✓</span>完了</span>", document)
            self.assertIn('<span class="sr-only">完了</span>', document)
            self.assertIn("<h2>3週間の見通し</h2>", document)
            # 予定そのものはリンクにしない（教材だけをリンクにする）。
            self.assertNotIn('<a class="week-item"', document)
            week_links = re.findall(
                r'<a class="week-item-link"[^>]*>.*?</a>', document
            )
            self.assertTrue(week_links)
            for link in week_links:
                self.assertIn('target="_blank"', link)
                self.assertIn('rel="noopener"', link)
                self.assertIn('<span class="sr-only">教材を開く: ', link)
            material_anchors = re.findall(
                r'<a class="material-(?:link|tile)"[^>]*>', document
            )
            self.assertTrue(material_anchors)
            for anchor in material_anchors:
                self.assertIn('target="_blank"', anchor)
                self.assertIn('rel="noopener"', anchor)
            material_links = re.findall(
                r'<a class="material-link"[^>]*>.*?</a>', document
            )
            self.assertTrue(material_links)
            for link in material_links:
                self.assertIn(
                    '<span class="sr-only">（別タブで開く）</span>',
                    link,
                )
            self.assertNotIn("fonts.googleapis.com", document)
            self.assertNotIn("http://fonts.", document)
            self.assertNotIn("Georgia", document)
            self.assertNotIn("Yu Mincho", document)

    def test_three_week_view_places_each_schedule_in_matching_row(self):
        data = DashboardData(
            mode="demo",
            title="週別配置テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[
                ScheduleItem(
                    date=date(2026, 7, 29),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="理科",
                    title="前週だけの予定",
                    status=Status.COMPLETED,
                ),
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="国語",
                    title="今週だけの予定",
                    status=Status.READY,
                ),
                ScheduleItem(
                    date=date(2026, 8, 12),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="算数",
                    title="翌週だけの予定",
                    status=Status.PLANNED,
                ),
            ],
            activities=[],
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            parser = _DocumentParser()
            document = output.read_text(encoding="utf-8")
            parser.feed(document)

        self.assertEqual(parser.week_titles["previous"], ["前週だけの予定"])
        self.assertEqual(parser.week_titles["current"], ["今週だけの予定"])
        self.assertEqual(parser.week_titles["next"], ["翌週だけの予定"])

        self.assertIn(
            'week-item week-category-science schedule-kind-dated',
            document,
        )
        self.assertIn(
            'week-item week-category-japanese schedule-kind-dated',
            document,
        )
        self.assertIn(
            'week-item week-category-math schedule-kind-dated',
            document,
        )
        self.assertNotIn('week-item status-edge-', document)

    def test_private_three_week_view_can_move_one_week_inside_generated_range(self):
        anchor = date(2026, 8, 5)
        data = DashboardData(
            mode="private",
            title="週移動テスト",
            anchor_date=anchor,
            schedule=[
                ScheduleItem(
                    date=date(2026, 7, 20) + timedelta(days=7 * offset),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="夜",
                    subject="算数",
                    title="第{}週の予定".format(offset + 1),
                    status=Status.PLANNED,
                )
                for offset in range(6)
            ],
            activities=[],
            week_view_start=date(2026, 7, 20),
            week_view_end=date(2026, 8, 24),
        )
        presentation = Presentation(
            mode_label="private",
            privacy_note="テスト",
            week_navigation_enabled=True,
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            document = output.read_text(encoding="utf-8")

        body = document.split("</style>", 1)[1]
        self.assertEqual(body.count('<section class="week-row '), 6)
        self.assertEqual(
            len(re.findall(r'<section class="week-row [^>]* hidden>', body)),
            3,
        )
        self.assertIn('data-default-week-start="2026-07-27"', body)
        self.assertIn('data-week-start="2026-07-20"', body)
        self.assertIn('data-week-start="2026-08-24"', body)
        self.assertIn('data-week-action="previous"', body)
        self.assertIn('data-week-action="reset"', body)
        self.assertIn('data-week-action="next"', body)
        self.assertIn('aria-live="polite"', body)
        self.assertIn("2026/7/27〜2026/8/16", body)
        self.assertIn(
            "予定と実績はWeb入力元にある範囲だけを表示しています。", body
        )
        self.assertIn(
            "実績をアーカイブへ移した過去週は、実施状況未確認になる場合があります。",
            body,
        )
        self.assertIn('data-week-navigation hidden', body)
        self.assertIn("navigation.hidden = false;", body)
        self.assertIn('<span class="week-row-current-badge">今週</span>', body)
        self.assertIn("startIndex -= 1; render();", body)
        self.assertIn("startIndex += 1; render();", body)
        self.assertIn("startIndex = defaultIndex;", body)

    def test_week_navigation_script_moves_resets_and_stops_at_boundaries(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("nodeが無いため週移動スクリプトの実行は検証しません")

        data = DashboardData(
            mode="private",
            title="週移動スクリプトテスト",
            anchor_date=date(2026, 8, 5),
            schedule=[],
            activities=[],
            week_view_start=date(2026, 7, 20),
            week_view_end=date(2026, 8, 24),
        )
        presentation = Presentation(
            mode_label="private",
            privacy_note="テスト",
            week_navigation_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            document = output.read_text(encoding="utf-8")

        script = next(
            match.group(1)
            for match in re.finditer(r"<script>([\s\S]*?)</script>", document)
            if "data-week-navigation" in match.group(1)
        )
        harness = r"""
const starts = [
  "2026-07-20", "2026-07-27", "2026-08-03",
  "2026-08-10", "2026-08-17", "2026-08-24"
];
const ends = [
  "2026-07-26", "2026-08-02", "2026-08-09",
  "2026-08-16", "2026-08-23", "2026-08-30"
];
function makeButton() {
  return {
    disabled: false,
    handler: null,
    addEventListener: function (_name, handler) { this.handler = handler; },
    click: function () { if (!this.disabled) { this.handler(); } }
  };
}
const previous = makeButton();
const reset = makeButton();
const next = makeButton();
const period = {textContent: ""};
const rows = starts.map(function (start, index) {
  return {
    hidden: false,
    getAttribute: function (name) {
      if (name === "data-week-start") { return start; }
      if (name === "data-period-end") { return ends[index]; }
      return null;
    }
  };
});
const navigation = {
  hidden: true,
  getAttribute: function () { return "2026-07-27"; },
  querySelector: function (selector) {
    return {
      '[data-week-action="previous"]': previous,
      '[data-week-action="reset"]': reset,
      '[data-week-action="next"]': next,
      '[data-week-period]': period
    }[selector];
  }
};
const stack = {querySelectorAll: function () { return rows; }};
global.document = {
  querySelector: function (selector) {
    return selector === "[data-week-navigation]" ? navigation : stack;
  }
};
eval(process.argv[1]);
function snapshot() {
  return {
    visible: rows.filter(function (row) { return !row.hidden; })
      .map(function (row) { return row.getAttribute("data-week-start"); }),
    previousDisabled: previous.disabled,
    resetDisabled: reset.disabled,
    nextDisabled: next.disabled,
    navigationHidden: navigation.hidden,
    period: period.textContent
  };
}
const initial = snapshot();
next.click();
const moved = snapshot();
next.click();
const boundary = snapshot();
next.click();
const stopped = snapshot();
reset.click();
const restored = snapshot();
console.log(JSON.stringify({initial, moved, boundary, stopped, restored}));
"""
        result = subprocess.run(
            [node, "-e", harness, script],
            capture_output=True,
            text=True,
            check=True,
        )
        states = json.loads(result.stdout)

        self.assertEqual(
            states["initial"]["visible"],
            ["2026-07-27", "2026-08-03", "2026-08-10"],
        )
        self.assertTrue(states["initial"]["resetDisabled"])
        self.assertFalse(states["initial"]["navigationHidden"])
        self.assertEqual(
            states["moved"]["visible"],
            ["2026-08-03", "2026-08-10", "2026-08-17"],
        )
        self.assertFalse(states["moved"]["resetDisabled"])
        self.assertEqual(
            states["boundary"]["visible"],
            ["2026-08-10", "2026-08-17", "2026-08-24"],
        )
        self.assertTrue(states["boundary"]["nextDisabled"])
        self.assertEqual(states["stopped"], states["boundary"])
        self.assertEqual(states["restored"], states["initial"])

    def test_default_three_week_view_has_no_week_navigation_controls(self):
        data = DashboardData(
            mode="demo",
            title="固定3週間テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[],
            activities=[],
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            body = output.read_text(encoding="utf-8").split("</style>", 1)[1]

        self.assertEqual(body.count('<section class="week-row '), 3)
        self.assertNotIn("data-week-navigation", body)
        self.assertNotIn("data-week-action", body)
        self.assertNotIn("data-week-start", body)
        self.assertNotIn("data-period-start", body)

    def test_private_view_discloses_routine_tracking_scope(self):
        data = StarterMarkdownAdapter(
            DEMO_ROOT, date(2026, 8, 5)
        ).load()
        data.mode = "private"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")
            self.assertIn(
                "活動ログに同日の実績があれば実施状態を表示します",
                document,
            )
            self.assertIn("反復</span>", document)

    def test_demo_view_opens_the_screen_guide_by_default(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn('<details class="guide" open>', document)
        self.assertIn("はじめての方へ — この画面の読み方", document)
        # 画面固有の用語だけを説明し、運用手順の正本を画面へ複製しない。
        for term in (
            "要確認",
            "教材未準備",
            "繰越",
            "反復ルーティン",
            "テスト実施回",
        ):
            self.assertIn("<dt>{}</dt>".format(term), document)
        self.assertIn("チュートリアルを始めて", document)
        # ガイドは静的表示のまま。埋め込みscriptは教材パスのコピー処理だけで、
        # ガイド用のスクリプトは持たない。
        self.assertEqual(document.count("<script>"), 1)
        self.assertIn("copy-path-manual-close", document)

    def test_own_data_view_keeps_the_screen_guide_collapsed(self):
        for mode in ("starter",):
            with self.subTest(mode=mode):
                data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
                data.mode = mode
                with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
                    output = Path(temporary) / "index.html"
                    render_dashboard(data, output)
                    document = output.read_text(encoding="utf-8")

                self.assertIn('<details class="guide">', document)
                self.assertNotIn('<details class="guide" open>', document)
                self.assertIn("この画面の読み方", document)
                self.assertNotIn("はじめての方へ", document)
                self.assertIn("<dt>教材未準備</dt>", document)

                guide = re.search(
                    r'<details class="guide".*?</details>', document, re.S
                )
                self.assertIsNotNone(guide)
                # 画面ガイドからはチュートリアルへ誘導せず、Markdownの更新を促す。
                self.assertNotIn("チュートリアルを始めて", guide.group(0))
                self.assertIn("生成コマンドを実行し直して", guide.group(0))

    def test_ai_catalog_marks_progress_from_the_recorded_workspace(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn('<section class="block" id="ai">', document)
        self.assertIn('<a href="#ai">AIにできること</a>', document)
        # demoの活動ログには週次振り返りの見出しが無いため、そこだけ「未」。
        self.assertFalse(data.has_weekly_review)
        self.assertIn("<strong>5/6</strong>", document)
        self.assertIn(
            '<p class="ai-ask">「今週を振り返って、来週の計画を作ってください」</p>',
            document,
        )
        badges = dict(
            re.findall(
                r'data-ask="(.+?)".*?ai-item-badge">([^<]+)</span>', document, re.S
            )
        )
        self.assertEqual(badges["review"], "未")
        # 同梱demoには material-type="worksheet" の教材が1件ある。
        self.assertEqual(badges["worksheet"], "済")
        self.assertEqual(document.count('class="ai-item-badge">未</span>'), 1)
        self.assertEqual(document.count('class="ai-item-badge">済</span>'), 7)
        self.assertEqual(
            document.count('class="ai-item-badge">任意</span>'),
            len(CATALOG_ADVANCED),
        )

    def test_ai_catalog_shows_the_advanced_tutorial_as_optional(self):
        """発展編を表示しつつ、基本の済／未集計には混ぜない。"""

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn("<h3>発展チュートリアル</h3>", document)
        self.assertIn("必要な項目だけ試せます（実施状況は集計しません）", document)
        for item_id, ask, effect in CATALOG_ADVANCED:
            with self.subTest(item_id=item_id):
                self.assertIn(
                    'class="ai-item ai-item-optional" data-ask="{}"'.format(
                        item_id
                    ),
                    document,
                )
                self.assertIn("「{}」".format(ask), document)
                self.assertIn(effect, document)

        self.assertIn("<strong>5/6</strong>", document)
        self.assertEqual(
            document.count('class="ai-item-badge">任意</span>'),
            len(CATALOG_ADVANCED),
        )
        self.assertIn("日付だけ決まったテスト予定を登録", document)
        self.assertIn("問題作成から結果登録までの練習は発展編", document)

    def test_catalog_ignores_signals_weaker_than_the_stated_condition(self):
        """反復ルーティンだけ・不在ファイル・無関係な実績で「済」にしない。"""

        schedule = (
            "# 予定\n\n"
            "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "\n## 反復ルーティン\n\n"
            "| 開始日 | 終了日 | 曜日 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 教材 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-01 | — | 毎日 | learner-1 | アオ | 朝 | 算数 | 計算 | "
            "[ない](materials/nonexistent.pdf) |\n"
        )
        activity = (
            "# ログ\n\n"
            "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-04 | learner-1 | アオ | 国語 | 対応する予定が無い | — | 5分 | — | — |\n"
            "\n## 週次振り返り\n\n### 書き方（まだ未実施）\n"
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workspace = Path(temporary)
            (workspace / "materials").mkdir()
            (workspace / "schedule.md").write_text(schedule, encoding="utf-8")
            (workspace / "activity-log.md").write_text(activity, encoding="utf-8")
            data = StarterMarkdownAdapter(
                workspace, date(2026, 8, 5), mode="starter"
            ).load()
            output = workspace / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        badges = dict(
            re.findall(
                r'data-ask="(.+?)".*?ai-item-badge">([^<]+)</span>', document, re.S
            )
        )
        # 常に真の2件（workspace存在・この画面の生成）と、実在する反復のみ。
        self.assertIn("<strong>2/6</strong>", document)
        self.assertEqual(badges["plan"], "未")  # 日付付き予定が無い
        self.assertEqual(badges["worksheet"], "未")  # リンク先が実在しない
        self.assertEqual(badges["record"], "未")  # 対応予定が実施済みでない
        self.assertEqual(badges["review"], "未")  # 案内見出しだけ
        self.assertEqual(badges["test-event"], "未")
        self.assertEqual(badges["routine"], "済")

    def test_record_needs_the_activity_to_match_the_planned_slot(self):
        """同日・同教科でも、別内容の実績では「済」にしない。"""

        plan = ScheduleItem(
            date=date(2026, 8, 3),
            learner_id="learner-1",
            learner_name="アオ",
            slot="夜",
            subject="算数",
            title="計算ドリル",
            status=Status.COMPLETED,
        )
        unrelated = ActivityItem(
            date=date(2026, 8, 3),
            learner_id="learner-1",
            learner_name="アオ",
            subject="算数",
            title="来週分の教材作成",
            duration="20分",
            status=Status.COMPLETED,
            result_summary="作成のみ。",
        )
        data = DashboardData(
            mode="starter",
            title="照合テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[plan],
            activities=[unrelated],
        )
        self.assertFalse(catalog_progress(data)["record"])

        matching = ActivityItem(
            date=date(2026, 8, 3),
            learner_id="learner-1",
            learner_name="アオ",
            subject="算数",
            title="計算ドリル（前半）",
            duration="15分",
            status=Status.COMPLETED,
            result_summary="8問中6問正解。",
        )
        data.activities = [unrelated, matching]
        self.assertTrue(catalog_progress(data)["record"])

        # 所要時間が「—」の教材作成行は実施実績にしない。
        matching.duration = "—"
        self.assertFalse(catalog_progress(data)["record"])

    def test_catalog_keeps_test_setup_complete_after_the_attempt(self):
        """予定カードが消えた後も、テスト実績があれば「済」を維持する。"""

        result = ActivityItem(
            date=date(2026, 8, 3),
            learner_id="learner-1",
            learner_name="アオ",
            subject="算数",
            title="到達度チェック 第1回",
            duration="20分",
            status=Status.COMPLETED,
            result_summary="8問中6問正解。",
            is_test=True,
        )
        data = DashboardData(
            mode="starter",
            title="照合テスト",
            anchor_date=date(2026, 8, 5),
            schedule=[],
            activities=[result],
            test_events=[],
        )

        self.assertTrue(catalog_progress(data)["test-event"])

    def test_catalog_asks_match_the_documented_procedure(self):
        """画面の依頼文が正本から独立して古くならないようにする。"""

        walkthrough = (REPO_ROOT / "examples" / "walkthrough.md").read_text(
            encoding="utf-8"
        )
        for item_id, ask, _ in CATALOG_BASICS:
            with self.subTest(ask=item_id):
                self.assertIn(ask, walkthrough)

        # 発展編と「そのほか」も、依頼例の正本＝AI-OPERATIONSへ揃える。
        operations = _ai_operations_text()
        for item_id, ask, _ in CATALOG_ADVANCED + CATALOG_EXTRAS:
            with self.subTest(ask=item_id):
                self.assertIn(ask, operations)

        # Webの発展項目がwalkthroughの発展章から増減していないことも固定する。
        advanced_chapters = {
            "learner-add": "子どもを追加する",
            "shared-material": "共通教材を追加する",
            "personal-revenge": "子ども固有のリベンジWSを追加する",
            "one-off-test": "単体のテスト（問題・成績）を追加する",
            "recurring-test": "定期的なテスト（問題・成績）を追加する",
            "migrate": "いま使っている記録を移行する",
        }
        self.assertEqual(
            {item_id for item_id, _, _ in CATALOG_ADVANCED},
            set(advanced_chapters),
        )
        for item_id, chapter in advanced_chapters.items():
            with self.subTest(chapter=item_id):
                self.assertRegex(
                    walkthrough,
                    r"(?m)^## \d+\. {}$".format(re.escape(chapter)),
                )

    def test_unknown_mode_keeps_rendering_without_starter_guidance(self):
        """独自modeの下流を壊さず、starter向けの案内だけを省く。"""

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        data.mode = "kiosk"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertNotIn('<details class="guide"', document)
        self.assertNotIn('id="ai"', document)
        self.assertNotIn("AIにできること", document)
        # 画面の本体は通常どおり出る。
        self.assertIn("<h2>今日の学習</h2>", document)
        self.assertIn("<h2>3週間の見通し</h2>", document)

    def test_presentation_defaults_reproduce_the_mode_view(self):
        """既定のPresentationがmodeごとの従来表示と一致する。"""

        demo = Presentation.for_mode("demo")
        starter = Presentation.for_mode("starter")
        private = Presentation.for_mode("private")
        unknown = Presentation.for_mode("kiosk")

        self.assertTrue(demo.guide.opened)
        self.assertFalse(starter.guide.opened)
        self.assertIsNone(private.guide)
        self.assertIsNone(unknown.guide)
        self.assertIsNone(unknown.catalog)
        for presentation in (demo, starter, private):
            self.assertIsNotNone(presentation.catalog)
        self.assertEqual(demo.routine_note, "")
        self.assertTrue(starter.routine_note)
        self.assertTrue(private.routine_note)

        # 明示的に渡しても、省略時と同じHTMLになる。
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            default_output = Path(temporary) / "default.html"
            explicit_output = Path(temporary) / "explicit.html"
            render_dashboard(data, default_output)
            render_dashboard(data, explicit_output, presentation=demo)
            self.assertEqual(
                default_output.read_text(encoding="utf-8"),
                explicit_output.read_text(encoding="utf-8"),
            )

    def test_presentation_replaces_the_catalog_wording_and_judgement(self):
        """下流の文言へ差し替え、判定なし表示も選べる。"""

        catalog = CatalogView(
            groups=(
                CatalogGroup(
                    items=(
                        CatalogItem(
                            "note",
                            "今日のメモを書いて",
                            "notes/ の当日ファイルへ追記します。",
                        ),
                    ),
                    heading="毎日の記録",
                ),
            ),
            lead="この運用でそのまま頼める言い方です。",
            heading="頼めること",
            subheading="この家の言い方",
        )
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        data.mode = "kiosk"
        presentation = Presentation(
            mode_label="KIOSK",
            privacy_note="この端末の外へ出しません。",
            catalog=catalog,
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            document = output.read_text(encoding="utf-8")

        self.assertIn('<section class="block" id="ai">', document)
        self.assertIn("<h2>頼めること</h2>", document)
        self.assertIn('<a href="#ai">頼めること</a>', document)
        self.assertIn('<p class="ai-ask">「今日のメモを書いて」</p>', document)
        self.assertIn("<h3>毎日の記録</h3>", document)
        self.assertIn('<span class="mode-pill">KIOSK</span>', document)
        self.assertIn("この端末の外へ出しません。", document)
        # 判定関数を渡していないので、済／未も「N/M」も出さない。
        self.assertNotIn('class="ai-item-badge"', document)
        self.assertNotIn('class="ai-progress"', document)
        # starter既定の文言は残らない。
        self.assertNotIn("初期設定をお願いします", document)
        self.assertNotIn("materials/ にでき", document)

    def test_presentation_can_replace_the_progress_judgement(self):
        """下流の判定関数を使い、未定義の項目は「未」に倒す。"""

        catalog = CatalogView(
            groups=(
                CatalogGroup(
                    items=(
                        CatalogItem("kept", "続けている依頼", "効果1"),
                        CatalogItem("todo", "まだの依頼", "効果2"),
                        CatalogItem("unlisted", "判定に無い依頼", "効果3"),
                    ),
                    counted=True,
                ),
            ),
            lead="未完のときの案内",
            complete_lead="全部そろったときの案内",
            counter_label="この家の流れ",
            progress=lambda data: {"kept": True, "todo": False},
        )
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(
                data,
                output,
                presentation=Presentation(
                    mode_label="STARTER・端末内のみ",
                    privacy_note="—",
                    catalog=catalog,
                ),
            )
            document = output.read_text(encoding="utf-8")

        badges = dict(
            re.findall(
                r'data-ask="(.+?)".*?ai-item-badge">([^<]+)</span>', document, re.S
            )
        )
        self.assertEqual(badges["kept"], "済")
        self.assertEqual(badges["todo"], "未")
        self.assertEqual(badges["unlisted"], "未")
        self.assertIn(
            '<span>この家の流れ</span><strong>1/3</strong>', document
        )
        self.assertIn("未完のときの案内", document)
        self.assertNotIn("全部そろったときの案内", document)

    def test_presentation_replaces_the_term_guide(self):
        guide = GuideView(
            summary="この画面の見かた",
            lead="正本は notes/ のMarkdownです。",
            terms=(GuideTerm("宿題枠", "その日に出た宿題の枠です。"),),
            week_note="左端の色は教科です。",
            next_step="notes/ を更新して生成し直してください。",
            opened=True,
        )
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(
                data,
                output,
                presentation=Presentation(
                    mode_label="KIOSK", privacy_note="—", guide=guide
                ),
            )
            document = output.read_text(encoding="utf-8")

        self.assertIn('<details class="guide" open>', document)
        self.assertIn("<summary>この画面の見かた</summary>", document)
        self.assertIn("<dt>宿題枠</dt>", document)
        self.assertNotIn("<dt>教材未準備</dt>", document)
        self.assertNotIn("チュートリアルを始めて", document)

    def test_presentation_adds_a_section_after_a_core_section(self):
        """追加セクションを指定位置へ入れ、ナビにも出す。"""

        def body(data, output_dir):
            self.assertTrue(output_dir.is_dir())
            return "<p>{}</p>".format(
                escape_text("{} 件の実績 <集計>".format(len(data.activities)))
            )

        extra = ExtraSection(
            id="scores",
            heading="直近の成績",
            body=body,
            subheading="正本からの読み取り専用",
            nav_label="成績",
            after="records",
        )
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        base = Presentation.for_mode("starter")
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(
                data,
                output,
                presentation=dataclasses.replace(
                    base, extra_sections=(extra,)
                ),
            )
            document = output.read_text(encoding="utf-8")

        self.assertIn('<section class="block" id="scores">', document)
        self.assertIn("<h2>直近の成績</h2>", document)
        self.assertIn('<a href="#scores">成績</a>', document)
        # 本文は escape_text を通した形で入り、生のタグにはならない。
        self.assertIn(
            "<p>{} 件の実績 &lt;集計&gt;</p>".format(len(data.activities)),
            document,
        )
        self.assertLess(
            document.index('id="records"'), document.index('id="scores"')
        )
        self.assertLess(
            document.index('id="scores"'), document.index('id="materials"')
        )

    def test_presentation_keeps_the_order_of_extra_sections(self):
        """同じ位置を指す追加セクションを、定義順のまま並べる。"""

        def section(section_id, after):
            return ExtraSection(
                id=section_id,
                heading=section_id,
                body=lambda data, output_dir: "",
                after=after,
            )

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        base = Presentation.for_mode("starter")
        extras = (
            section("first", "records"),
            # 追加セクションを起点にした指定は、その直後（後続の兄弟より前）。
            section("nested", "first"),
            section("second", "records"),
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(
                data,
                output,
                presentation=dataclasses.replace(base, extra_sections=extras),
            )
            document = output.read_text(encoding="utf-8")

        order = re.findall(r'<section class="block" id="([^"]+)"', document)
        self.assertEqual(
            order,
            [
                "today",
                "tests",
                "week",
                "attention",
                "records",
                "first",
                "nested",
                "second",
                "materials",
                "ai",
            ],
        )
        # ナビの並びも同じ。
        self.assertIn(
            '<a href="#records">実績</a><a href="#first">first</a>'
            '<a href="#nested">nested</a><a href="#second">second</a>'
            '<a href="#materials">教材</a>',
            document,
        )

    def test_presentation_rejects_an_unusable_extra_section(self):
        """挿入位置が無い・IDが重複する追加セクションは黙って落とさない。"""

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        base = Presentation.for_mode("starter")
        cases = {
            "unknown-anchor": ExtraSection(
                id="scores",
                heading="直近の成績",
                body=lambda data, output_dir: "",
                after="nowhere",
            ),
            "duplicate-id": ExtraSection(
                id="records",
                heading="別の実績",
                body=lambda data, output_dir: "",
                after="today",
            ),
        }
        for label, extra in cases.items():
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
                    output = Path(temporary) / "index.html"
                    with self.assertRaises(ValueError):
                        render_dashboard(
                            data,
                            output,
                            presentation=dataclasses.replace(
                                base, extra_sections=(extra,)
                            ),
                        )

    def test_presentation_hides_a_core_section_from_the_page_and_nav(self):
        """非表示にしたcoreセクションは、本体もナビも出さない。"""

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        base = Presentation.for_mode("starter")
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(
                data,
                output,
                presentation=dataclasses.replace(
                    base, hidden_section_ids=("materials",)
                ),
            )
            document = output.read_text(encoding="utf-8")

        self.assertNotIn('id="materials"', document)
        self.assertNotIn("今週の教材", document)
        self.assertNotIn('<a href="#materials">', document)
        self.assertNotIn('class="material-tile"', document)
        # 予定・実績カード内の教材リンクは残る。
        self.assertIn('class="material-link"', document)
        # ほかのセクションは従来どおり。
        self.assertIn('<section class="block" id="records">', document)
        self.assertIn('<a href="#records">実績</a>', document)

    def test_presentation_controls_the_tab_icon(self):
        """favicon を渡したときだけ icon の link を出す。"""

        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        base = Presentation.for_mode("starter")
        icon = "data:image/png;base64,iVBORw0KGgo="
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            with_icon = Path(temporary) / "with.html"
            render_dashboard(
                data, with_icon, presentation=dataclasses.replace(base, favicon=icon)
            )
            without_icon = Path(temporary) / "without.html"
            render_dashboard(data, without_icon, presentation=base)
            with_document = with_icon.read_text(encoding="utf-8")
            without_document = without_icon.read_text(encoding="utf-8")

        self.assertIn('<link rel="icon" href="{}">'.format(icon), with_document)
        # 未指定は空hrefではなく要素ごと出さない（ページ自身を取りに行かせない）。
        self.assertNotIn('rel="icon"', without_document)

    def test_presentation_rejects_a_favicon_that_is_not_embedded(self):
        """生成物が単体で表示できなくなる値を、構成時に落とす。

        空文字は「指定しない」と同じにしない（ブラウザがページ自身を
        アイコンとして取りに行くため）。外部URL・相対パスは、生成物を
        渡した先で画像が出ず、閲覧のたびに外部への通信も起きる。
        """

        base = Presentation.for_mode("starter")
        for label, value in (
            ("空文字", ""),
            ("空白のみ", "   "),
            ("https", "https://example.com/icon.png"),
            ("http", "http://example.com/icon.png"),
            ("プロトコル相対", "//example.com/icon.png"),
            ("相対パス", "./assets/icon.png"),
            ("ルート相対", "/assets/icon.png"),
            ("ファイル名だけ", "favicon.ico"),
            ("file スキーム", "file:///tmp/icon.png"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    dataclasses.replace(base, favicon=value)

    def test_presentation_accepts_an_embedded_favicon(self):
        """`data:` URI は大文字表記・前後の空白があっても受け付ける。"""

        base = Presentation.for_mode("starter")
        for label, value in (
            ("png", "data:image/png;base64,iVBORw0KGgo="),
            ("svg", "data:image/svg+xml,%3Csvg%2F%3E"),
            ("大文字スキーム", "DATA:image/png;base64,iVBORw0KGgo="),
            ("前後の空白", "  data:image/png;base64,iVBORw0KGgo=  "),
        ):
            with self.subTest(case=label):
                self.assertEqual(
                    dataclasses.replace(base, favicon=value).favicon, value
                )

    def test_presentation_rejects_an_unknown_hidden_section_id(self):
        """知らないセクションIDを黙って無視しない。"""

        base = Presentation.for_mode("starter")
        with self.assertRaises(ValueError):
            dataclasses.replace(base, hidden_section_ids=("material",))
        with self.assertRaises(ValueError):
            dataclasses.replace(base, hidden_section_ids=("materials", "notes"))
        with self.assertRaises(ValueError):
            dataclasses.replace(base, recent_activity_days=0)

    def test_presentation_selects_recent_activities_by_recorded_days(self):
        """直近N実施日を日単位で選び、件数で日の途中を切らない。"""

        def _activity(day: date, index: int) -> ActivityItem:
            return ActivityItem(
                date=day,
                learner_id="learner-a",
                learner_name="アオ",
                subject="算数",
                title="{}の{}件目".format(day.isoformat(), index),
                duration="10分",
                status=Status.COMPLETED,
                result_summary="できました。",
            )

        data = DashboardData(
            mode="kiosk",
            title="実施日単位テスト",
            anchor_date=date(2026, 8, 10),
            schedule=[],
            activities=(
                # 未来日は「最近」に含めない。
                [_activity(date(2026, 8, 11), 1)]
                + [_activity(date(2026, 8, 10), i) for i in range(1, 5)]
                # 8/9 だけで12件を超え、既定の12件では日の途中で切れる。
                + [_activity(date(2026, 8, 9), i) for i in range(1, 13)]
                # 8/8 との間の 8/7・8/6 は実績が無く、3日には数えない。
                + [_activity(date(2026, 8, 8), i) for i in range(1, 3)]
                + [_activity(date(2026, 8, 5), 1)]
            ),
        )

        selected = dataclasses.replace(
            Presentation.for_mode("kiosk"), recent_activity_days=3
        ).recent_activities(data)
        self.assertEqual(
            [item.date for item in selected],
            [date(2026, 8, 10)] * 4
            + [date(2026, 8, 9)] * 12
            + [date(2026, 8, 8)] * 2,
        )
        # 同じ日の中の並びは入力順のまま。
        self.assertEqual(
            [item.title for item in selected if item.date == date(2026, 8, 10)],
            ["2026-08-10の{}件目".format(index) for index in range(1, 5)],
        )
        # 既定（12件）は従来どおり。
        self.assertEqual(len(data.recent_activities()), 12)
        # 実施日が足りなければ、あるだけ返す。
        self.assertEqual(len(data.recent_activities_by_days(99)), 19)

    def test_recent_minutes_sum_only_the_displayed_activities(self):
        """「最近の学習時間」を、画面に出す実績と同じ範囲で集計する。"""

        def _activity(day: date, index: int, minutes: int) -> ActivityItem:
            return ActivityItem(
                date=day,
                learner_id="learner-a",
                learner_name="アオ",
                subject="算数",
                title="{}の{}件目".format(day.isoformat(), index),
                duration="{}分".format(minutes),
                status=Status.COMPLETED,
                result_summary="できました。",
            )

        data = DashboardData(
            mode="kiosk",
            title="学習時間テスト",
            anchor_date=date(2026, 8, 10),
            schedule=[],
            activities=(
                [_activity(date(2026, 8, 10), i, 10) for i in range(1, 4)]
                + [_activity(date(2026, 8, 9), i, 10) for i in range(1, 4)]
                # 表示対象外の日（4日目以前）は集計にも入れない。
                + [_activity(date(2026, 8, 3), i, 100) for i in range(1, 4)]
            ),
        )
        presentation = dataclasses.replace(
            Presentation.for_mode("kiosk"), recent_activity_days=2
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, presentation=presentation)
            document = output.read_text(encoding="utf-8")

        self.assertEqual(document.count('<article class="activity-card">'), 6)
        self.assertIn(
            "<span>最近の学習時間</span><strong>60<small>分</small></strong>",
            document,
        )
        self.assertNotIn("2026-08-03の1件目", document)


    def test_ai_catalog_counts_the_weekly_review_once_it_is_written(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        data.has_weekly_review = True
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn("<strong>6/6</strong>", document)
        self.assertNotIn('class="ai-item-badge">未</span>', document)
        self.assertIn("基本の流れはひととおり記録に残っています", document)

    def test_private_view_shows_the_catalog_without_the_term_guide(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        data.mode = "private"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        self.assertIn('<section class="block" id="ai">', document)
        self.assertIn('<a href="#ai">AIにできること</a>', document)
        self.assertIn("<h3>発展チュートリアル</h3>", document)
        self.assertIn("学習者をもう1人追加してください", document)
        # 運用中の画面に用語の説明は出さない。
        self.assertNotIn('<details class="guide"', document)
        self.assertNotIn("この画面の読み方", document)

    def test_weekly_review_flag_needs_a_week_heading_not_just_the_section(self):
        schedule = (
            "# 予定\n\n"
            "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-03 | learner-1 | アオ | 夜 | 算数 | たしざん | 完了 | — | — |\n"
        )
        activity = (
            "# ログ\n\n"
            "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-03 | learner-1 | アオ | 算数 | たしざん | — | 10分 | 全問正解。 | — |\n"
            "\n## 週次振り返り\n\n"
            "週ごとの要点をこの下へ追記します。\n"
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workspace = Path(temporary)
            (workspace / "materials").mkdir()
            (workspace / "schedule.md").write_text(schedule, encoding="utf-8")
            log = workspace / "activity-log.md"
            log.write_text(activity, encoding="utf-8")
            adapter = StarterMarkdownAdapter(
                workspace, date(2026, 8, 5), mode="starter"
            )
            # 見出しと説明だけの段階では、まだ振り返り済みにしない。
            self.assertFalse(adapter.load().has_weekly_review)

            base = log.read_text(encoding="utf-8")
            # 日付を持たない案内見出しも振り返り済みにしない。
            log.write_text(
                base + "\n### 書き方のメモ\n\nここに書きます。\n",
                encoding="utf-8",
            )
            self.assertFalse(adapter.load().has_weekly_review)

            # 節の外にある週見出しも数えない。
            log.write_text(
                "## メモ\n\n### 2026-08-03週\n\n" + base, encoding="utf-8"
            )
            self.assertFalse(adapter.load().has_weekly_review)

            log.write_text(
                base + "\n### 2026-08-03週\n\n国語の音読が続いた。\n",
                encoding="utf-8",
            )
            self.assertTrue(adapter.load().has_weekly_review)

    def test_auto_reload_is_absent_by_default(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")

        # 既定ではauto-reloadのscriptを出さない（コピー処理の1つだけが残る）。
        self.assertEqual(document.count("<script>"), 1)
        self.assertNotIn("location.reload()", document)
        self.assertNotIn("var anchor = ", document)

    def test_auto_reload_reloads_only_after_the_anchor_date_passes(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output, auto_reload=True)
            document = output.read_text(encoding="utf-8")

        self.assertIn('var anchor = "2026-08-05";', document)
        self.assertIn("location.reload()", document)
        # 読み込み時点で既にずれている場合は再読み込みを繰り返さず告知に留める。
        self.assertIn("showBanner();", document)
        self.assertIn("stale-banner", document)

    def _run_auto_reload_script(self, anchor: date, start: str, ticks: int):
        """埋め込みスクリプトをnodeで実行し、最初の反応と経過秒を返す。"""

        node = shutil.which("node")
        if node is None:
            self.skipTest("nodeが無いため埋め込みスクリプトの実行は検証しません")

        data = StarterMarkdownAdapter(DEMO_ROOT, anchor).load()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workdir = Path(temporary)
            output = workdir / "index.html"
            render_dashboard(data, output, auto_reload=True)
            document = output.read_text(encoding="utf-8")
            # ページには教材パスのコピー処理も埋め込まれるので、基準日を持つ
            # auto-reload側のscriptだけを取り出す。
            body = next(
                (
                    match
                    for match in re.finditer(
                        r"<script>([\s\S]*?)</script>", document
                    )
                    if "var anchor = " in match.group(1)
                ),
                None,
            )
            self.assertIsNotNone(body)
            script = workdir / "auto-reload.js"
            script.write_text(body.group(1), encoding="utf-8")
            harness = workdir / "harness.js"
            harness.write_text(AUTO_RELOAD_HARNESS, encoding="utf-8")
            result = subprocess.run(
                [node, str(harness), str(script), start, str(ticks)],
                capture_output=True,
                text=True,
                check=True,
            )
        return json.loads(result.stdout)

    def test_auto_reload_does_nothing_while_the_date_is_unchanged(self):
        result = self._run_auto_reload_script(
            date(2026, 8, 5), "2026-08-05T09:00:00", 8
        )

        self.assertEqual(result["events"], [])

    def test_auto_reload_waits_for_the_grace_period_after_midnight(self):
        result = self._run_auto_reload_script(
            date(2026, 8, 5), "2026-08-05T23:59:50", 10
        )

        # 10秒間隔で確認し、日付が変わってから20秒（＝開始の30秒後）に再読み込みする。
        self.assertEqual(result["intervals"], [10000])
        self.assertEqual(result["events"], ["reload"])
        self.assertEqual(result["elapsedSeconds"], 30)

    def test_auto_reload_shows_a_banner_instead_of_looping_when_stale_on_load(self):
        result = self._run_auto_reload_script(
            date(2026, 7, 25), "2026-08-05T09:00:00", 20
        )

        self.assertEqual(result["events"], ["banner"])

    def _write_starter_workspace(self, workspace: Path, schedule_dates=()):
        workspace.mkdir(parents=True, exist_ok=True)
        rows = "".join(
            "| {} | learner-a | アオ | 朝 | 算数 | 計算 | 予定 | — | — |\n".format(
                day.isoformat()
            )
            for day in schedule_dates
        )
        (workspace / "schedule.md").write_text(
            "# 予定\n\n"
            "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + rows,
            encoding="utf-8",
        )
        (workspace / "activity-log.md").write_text(
            "# 実績\n\n"
            "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 |"
            " 結果 | 次アクション |\n"
            "|---|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )

    def test_schedule_derived_anchor_gets_no_auto_reload_even_if_it_is_today(self):
        """予定から選んだ中央日が実行日と一致しても、固定基準日として扱う。"""

        today = date.today()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workspace = Path(temporary) / "workspace"
            self._write_starter_workspace(
                workspace,
                (today - timedelta(days=1), today, today + timedelta(days=1)),
            )
            output = Path(temporary) / "site" / "index.html"
            self.assertEqual(
                main(
                    [
                        "--mode",
                        "starter",
                        "--repo-root",
                        str(REPO_ROOT),
                        "--workspace",
                        str(workspace),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            document = output.read_text(encoding="utf-8")

        # 中央日＝実行日なので、基準日の値だけでは実行日基準と区別できない。
        self.assertIn("基準日 {}".format(today.isoformat()), document)
        self.assertNotIn("location.reload()", document)

    def test_cli_enables_auto_reload_only_for_the_current_date(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workspace = Path(temporary) / "workspace"
            self._write_starter_workspace(workspace)
            today_output = Path(temporary) / "today" / "index.html"
            pinned_output = Path(temporary) / "pinned" / "index.html"
            base = [
                "--mode",
                "starter",
                "--repo-root",
                str(REPO_ROOT),
                "--workspace",
                str(workspace),
            ]
            self.assertEqual(main(base + ["--output", str(today_output)]), 0)
            self.assertEqual(
                main(
                    base
                    + ["--date", "2026-08-05", "--output", str(pinned_output)]
                ),
                0,
            )

            today_document = today_output.read_text(encoding="utf-8")
            pinned_document = pinned_output.read_text(encoding="utf-8")

        self.assertIn(
            'var anchor = "{}";'.format(date.today().isoformat()),
            today_document,
        )
        self.assertNotIn("location.reload()", pinned_document)

    def test_demo_cli_without_date_selects_a_useful_fixture_week(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            result = main(
                [
                    "--mode",
                    "demo",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--output",
                    str(output),
                ]
            )
            document = output.read_text(encoding="utf-8")
            self.assertEqual(result, 0)
            self.assertIn("2026-08-05（水）", document)
            self.assertIn("アオ", document)
            self.assertIn("コハク", document)
            parser = _DocumentParser()
            parser.feed(document)
            linked_files = []
            for href in parser.hrefs:
                if href.startswith("#"):
                    continue
                linked_files.append(
                    (output.parent / href.split("#", 1)[0]).resolve()
                )
            self.assertTrue(linked_files)
            self.assertTrue(all(path.is_file() for path in linked_files))
            self.assertTrue(
                all(
                    path.is_relative_to(output.parent / "demo-content")
                    for path in linked_files
                )
            )

    def test_starter_mode_is_alias_of_demo_for_bundled_fixture(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            demo_output = Path(temporary) / "demo" / "index.html"
            starter_output = Path(temporary) / "starter" / "index.html"
            for mode, output in (
                ("demo", demo_output),
                ("starter", starter_output),
            ):
                result = main(
                    [
                        "--mode",
                        mode,
                        "--repo-root",
                        str(REPO_ROOT),
                        "--output",
                        str(output),
                    ]
                )
                self.assertEqual(result, 0)
            self.assertEqual(
                demo_output.read_text(encoding="utf-8"),
                starter_output.read_text(encoding="utf-8"),
            )

    def test_copied_demo_workspace_builds_as_starter_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "my-workspace"
            shutil.copytree(DEMO_ROOT, workspace)
            output = root / "site" / "index.html"

            result = main(
                [
                    "--mode",
                    "starter",
                    "--workspace",
                    str(workspace),
                    "--output",
                    str(output),
                ]
            )
            document = output.read_text(encoding="utf-8")

            self.assertEqual(result, 0)
            self.assertIn("STARTER・端末内のみ", document)
            self.assertIn(
                "個人情報を含むため、生成物を公開・共有しないでください。",
                document,
            )
            self.assertNotIn("架空です", document)
            parser = _DocumentParser()
            parser.feed(document)
            content_root = (output.parent / "demo-content").resolve()
            linked_files = [
                (output.parent / href.split("#", 1)[0]).resolve()
                for href in parser.hrefs
                if not href.startswith("#")
            ]
            self.assertTrue(linked_files)
            self.assertTrue(
                all(path.is_relative_to(content_root) for path in linked_files)
            )

    def test_duplicate_learner_id_with_different_names_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2026-08-03 | learner-a | アオ | 朝 | 算数 | 計算 | 予定 | — | — |\n"
                "| 2026-08-04 | learner-a | コハク | 夜 | 国語 | 読解 | 予定 | — | — |\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "learner-a が複数の学習者名"
            ):
                StarterMarkdownAdapter(workspace, date(2026, 8, 5)).load()

    def test_empty_learner_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "schedule.md").write_text(
                "# 予定\n\n"
                "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 2026-08-03 | | アオ | 朝 | 算数 | 計算 | 予定 | — | — |\n"
                "| 2026-08-03 | | コハク | 夜 | 算数 | 計算 | 予定 | — | — |\n",
                encoding="utf-8",
            )
            (workspace / "activity-log.md").write_text(
                "# 実績\n\n"
                "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
                "|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "学習者IDが空の行"):
                StarterMarkdownAdapter(workspace, date(2026, 8, 5)).load()


class _CopyButtonParser(HTMLParser):
    """コピーbuttonの位置・属性と、リンクへの入れ子を調べる。"""

    def __init__(self):
        super().__init__()
        self.buttons = []
        self.nested_in_link = False
        self.wrappers = 0
        self._link_depth = 0
        self._wrapper_depth = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        self._depth += 1
        if "material-item" in classes:
            self.wrappers += 1
            self._wrapper_depth = self._depth
        if tag == "a":
            self._link_depth += 1
        if "copy-path" in classes and tag == "button":
            if self._link_depth:
                self.nested_in_link = True
            self.buttons.append(
                {
                    "path": attributes.get("data-copy-path"),
                    "type": attributes.get("type"),
                    "label": attributes.get("aria-label"),
                    "title": attributes.get("title"),
                    "in_wrapper": self._wrapper_depth is not None,
                }
            )

    def handle_endtag(self, tag):
        if tag == "a" and self._link_depth:
            self._link_depth -= 1
        if self._wrapper_depth == self._depth:
            self._wrapper_depth = None
        self._depth -= 1


class MaterialPathCopyTest(unittest.TestCase):
    """教材リンク横のコピー操作（作業ルート相対のパスをコピーする）。"""

    def _render(self, data: DashboardData) -> str:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            render_dashboard(data, output)
            return output.read_text(encoding="utf-8")

    def _parse(self, document: str) -> _CopyButtonParser:
        parser = _CopyButtonParser()
        parser.feed(document)
        return parser

    def _workspace_data(self, temporary: Path) -> DashboardData:
        """予定・実績・本番予定・参考教材をひと通り持つworkspaceを作る。"""

        workspace = temporary / "workspace"
        (workspace / "materials").mkdir(parents=True)
        for name in ("keisan.html", "kokugo.html", "kakomon.pdf", "zaiko.html"):
            (workspace / "materials" / name).write_text("", encoding="utf-8")
        (workspace / "schedule.md").write_text(
            "# 予定\n\n"
            "| 日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-05 | learner-a | アオ | 朝 | 算数 | 計算 | 予定 | "
            "《教材:計算》[計算](materials/keisan.html)"
            "《参考:在庫》[在庫](materials/zaiko.html) | — |\n",
            encoding="utf-8",
        )
        (workspace / "activity-log.md").write_text(
            "# 実績\n\n"
            "| 日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 | 所要時間 | 結果 | 次アクション |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-08-04 | learner-a | アオ | 国語 | 読解 | "
            "[読解](materials/kokugo.html) | 15分 | 完了 | — |\n",
            encoding="utf-8",
        )
        return StarterMarkdownAdapter(workspace, date(2026, 8, 5)).load()

    def test_every_local_material_link_gets_an_adjacent_copy_button(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = self._workspace_data(Path(temporary))
            document = self._render(data)

        parser = self._parse(document)
        paths = sorted({button["path"] for button in parser.buttons})

        # 予定（実施項目）・参考・実績のリンクすべてに出る。
        self.assertEqual(
            paths,
            [
                "materials/keisan.html",
                "materials/kokugo.html",
                "materials/zaiko.html",
            ],
        )
        # starter/demoの「今週の教材」タイルにも同じ操作を出す。
        self.assertEqual(
            document.count('class="material-item material-tile-item"'),
            document.count('class="material-tile"'),
        )
        self.assertGreater(document.count('class="material-tile"'), 0)
        for button in parser.buttons:
            self.assertEqual(button["type"], "button")
            self.assertTrue(button["in_wrapper"])
            self.assertTrue(button["label"].startswith("教材パスをコピー: "))
            self.assertIn("教材パスをコピー", button["title"])
        # リンクの中へ入れない（入れるとクリックで教材が開いてしまう）。
        self.assertFalse(parser.nested_in_link)
        self.assertEqual(parser.wrappers, len(parser.buttons))

    def test_copy_value_has_no_origin_fragment_or_absolute_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = self._workspace_data(Path(temporary))
            document = self._render(data)
            parser = self._parse(document)
            for button in parser.buttons:
                path = button["path"]
                with self.subTest(path=path):
                    self.assertFalse(path.startswith("/"))
                    self.assertNotIn("://", path)
                    self.assertNotIn("..", path)
                    self.assertNotIn("#", path)
                    self.assertNotIn("?", path)
                    # コピー値へ絶対ローカルパスを出さない。
                    self.assertNotIn(temporary, path)

    def test_copy_script_and_live_region_appear_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = self._workspace_data(Path(temporary))
            document = self._render(data)

        self.assertEqual(document.count("<script>"), 1)
        self.assertEqual(document.count('id="copy-path-status"'), 1)
        self.assertEqual(document.count('id="copy-path-manual"'), 1)
        self.assertIn('role="status"', document)
        self.assertIn('aria-live="polite"', document)
        # 失敗時は成功表示を出さず、手動コピーへ落とす。
        self.assertIn("document.execCommand", document)
        self.assertIn("コピーできませんでした", document)
        # クリップボード以外へ送らない。
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "navigator.sendBeacon"):
            self.assertNotIn(forbidden, document)

    def test_copy_ui_is_hidden_when_printed(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = self._workspace_data(Path(temporary))
            document = self._render(data)

        print_block = document.split("@media print {")[1].split("}\n    }")[0]
        for selector in (".copy-path", "#copy-path-status", ".copy-path-manual"):
            self.assertIn(selector, print_block)

    def test_link_without_copy_path_renders_without_a_button(self):
        """下流が2項目だけで組み立てたリンクも従来どおり表示できる。"""

        material = MaterialLink(label="下流の教材", target="materials/x.html")
        self.assertIsNone(material.copy_path)
        data = DashboardData(
            title="下流",
            anchor_date=date(2026, 8, 5),
            mode="starter",
            activities=[],
            schedule=[
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="算数",
                    title="計算",
                    status=Status.PLANNED,
                    materials=[
                        material,
                        MaterialLink(
                            label="外部",
                            target="https://example.test/drill",
                        ),
                    ],
                )
            ],
        )

        document = self._render(data)
        parser = self._parse(document)

        self.assertIn("下流の教材", document)
        self.assertIn("https://example.test/drill", document)
        # copy_pathが無いリンクと外部URLにはbuttonを出さない。
        self.assertEqual(parser.buttons, [])

    def test_copy_value_is_escaped_in_the_attribute(self):
        """`&`・引用符・日本語がHTML属性を壊さない。"""

        material = MaterialLink(
            label='計算 & "まとめ"',
            target="/tmp/a&b.html",
            copy_path='materials/a&b "1".html',
        )
        data = DashboardData(
            title="エスケープ",
            anchor_date=date(2026, 8, 5),
            mode="starter",
            activities=[],
            schedule=[
                ScheduleItem(
                    date=date(2026, 8, 5),
                    learner_id="learner-a",
                    learner_name="アオ",
                    slot="朝",
                    subject="算数",
                    title="計算",
                    status=Status.PLANNED,
                    materials=[material],
                )
            ],
        )

        document = self._render(data)
        parser = self._parse(document)

        self.assertIn("&amp;", document)
        self.assertIn("&quot;", document)
        # 予定カードと「今週の教材」タイルの両方に出る。
        self.assertEqual(len(parser.buttons), 2)
        # パーサが復元した値が元のパスと一致する＝属性が壊れていない。
        for button in parser.buttons:
            self.assertEqual(button["path"], 'materials/a&b "1".html')
            self.assertEqual(
                button["label"], '教材パスをコピー: 計算 & "まとめ"'
            )

    def test_demo_staging_keeps_the_workspace_relative_copy_value(self):
        """demoの複製後もコピー値は元のworkspace相対のまま。"""

        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            output = Path(temporary) / "index.html"
            self.assertEqual(
                main(
                    [
                        "--mode",
                        "demo",
                        "--repo-root",
                        str(REPO_ROOT),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            document = output.read_text(encoding="utf-8")

        parser = self._parse(document)
        self.assertTrue(parser.buttons)
        for button in parser.buttons:
            path = button["path"]
            with self.subTest(path=path):
                self.assertFalse(path.startswith("demo-content/"))
                self.assertFalse(path.startswith("/"))
                self.assertNotIn("..", path)
                self.assertTrue(
                    (DEMO_ROOT / path).is_file(),
                    "workspace相対で解決できること",
                )
        # 開くためのhrefは複製先を指す（targetとcopy_pathの役割を分ける）。
        self.assertIn('href="demo-content/', document)

    def _run_copy_script(self, scenario: str):
        """埋め込みスクリプトを最小のDOMスタブ上でnode実行し、状態を返す。"""

        node = shutil.which("node")
        if node is None:
            self.skipTest("nodeが無いため埋め込みスクリプトの実行は検証しません")

        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            workdir = Path(temporary)
            data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 5)).load()
            output = workdir / "index.html"
            render_dashboard(data, output)
            document = output.read_text(encoding="utf-8")
            body = next(
                match
                for match in re.finditer(r"<script>([\s\S]*?)</script>", document)
                if "data-copy-path" in match.group(1)
            )
            script = workdir / "copy-path.js"
            script.write_text(body.group(1), encoding="utf-8")
            harness = workdir / "harness.js"
            harness.write_text(COPY_PATH_HARNESS, encoding="utf-8")
            result = subprocess.run(
                [node, str(harness), str(script), scenario],
                capture_output=True,
                text=True,
                check=True,
            )
        return json.loads(result.stdout)

    def test_clipboard_success_marks_only_the_pressed_button(self):
        clicked, copied, restored = self._run_copy_script("ok")

        # コピー操作では教材リンクを開かない。
        self.assertTrue(clicked["prevented"])
        self.assertEqual(copied["copied"], [True, False])
        self.assertEqual(
            copied["statusText"], "コピーしました: materials/a.html"
        )
        self.assertTrue(copied["manualHidden"])
        # 一定時間後にチェック表示を戻す。
        self.assertEqual(restored["copied"], [False, False])

    def test_rejected_clipboard_promise_falls_back_to_exec_command(self):
        *_, copied, restored = self._run_copy_script("reject:exec-ok:rapid")

        self.assertEqual(restored["copied"], [False, False])
        self.assertEqual(copied["copied"], [False, True])
        self.assertEqual(
            copied["statusText"], "コピーしました: materials/b.html"
        )
        # 連続操作しても、古い復元timerは残さない。
        self.assertEqual(copied["timerCount"], 1)

    def test_late_clipboard_success_does_not_override_the_newer_copy(self):
        """遅れて届いた古いクリックの成功で、新しい表示を上書きしない。

        `writeText()` は並行して進むため、A→Bの順に押してBが先に成功した
        あとでAが成功することがある。世代番号で古い結果を無効化する。
        """

        result = self._run_copy_script("pending:rapid")
        # 新しいB→古いAの順に完了させたあとの状態。
        settled = result[-2]

        self.assertEqual(settled["copied"], [False, True])
        self.assertEqual(
            settled["statusText"], "コピーしました: materials/b.html"
        )
        # 復元timerも新しい方の1本だけ。
        self.assertEqual(settled["timerCount"], 1)
        self.assertEqual(result[-1]["copied"], [False, False])

    def test_late_clipboard_rejection_does_not_show_the_manual_box(self):
        """古いクリックが遅れて失敗しても、手動コピー欄を割り込ませない。"""

        result = self._run_copy_script("pending:rapid:stale-reject")
        settled = result[-2]

        self.assertEqual(settled["copied"], [False, True])
        self.assertTrue(settled["manualHidden"])
        self.assertEqual(
            settled["statusText"], "コピーしました: materials/b.html"
        )

    def test_blocked_clipboard_api_falls_back_without_throwing(self):
        _clicked, copied, _restored = self._run_copy_script("throw:exec-ok")

        self.assertEqual(copied["copied"], [True, False])

    def test_missing_clipboard_api_uses_exec_command(self):
        _clicked, copied, _restored = self._run_copy_script("none:exec-ok")

        self.assertEqual(copied["copied"], [True, False])

    def test_both_copy_paths_failing_shows_manual_copy_without_success(self):
        _clicked, failed, _restored = self._run_copy_script("reject:exec-ng")

        # 成功表示は出さない。
        self.assertEqual(failed["copied"], [False, False])
        self.assertIn("コピーできませんでした", failed["statusText"])
        # 手動でコピーできる状態にする。
        self.assertFalse(failed["manualHidden"])
        self.assertEqual(failed["manualValue"], "materials/a.html")



class TestEventExtraContractTests(unittest.TestCase):
    def test_extra_only_for_visible_dated_cards_and_empty_is_compatible(self):
        from kit.starter_web.render import _test_event_section
        data = DashboardData('starter', '架空表示', date(2026, 8, 1), [], [], [
            TestEvent(date(2026, 8, 3), 'アオ', '検定', '試験', attempt_id='attempt-demo'),
            TestEvent(None, 'アオ', '検定', '日程未定'),
            TestEvent(date(2026, 7, 1), 'アオ', '検定', '過去'),
        ])
        output = Path('/tmp')
        base = _test_event_section(data, output)
        self.assertEqual(base, _test_event_section(data, output, lambda *args: ''))
        seen = []
        def extra(event, dashboard, directory):
            seen.append((event.attempt_id, dashboard is data, directory))
            return '<aside>追加表示</aside>'
        html = _test_event_section(data, output, extra)
        self.assertEqual(seen, [('attempt-demo', True, output)])
        self.assertIn('<aside>追加表示</aside></div>', html)

    def test_workspace_attempt_identity_reaches_event(self):
        data = StarterMarkdownAdapter(DEMO_ROOT, date(2026, 8, 1)).load()
        from kit.starter_web.workspace_contract import load_workspace_records
        planned = {x.attempt_id for x in load_workspace_records(DEMO_ROOT).test_attempts
                   if x.state in ('予定', '準備済み')}
        self.assertEqual({x.attempt_id for x in data.test_events}, planned)


if __name__ == "__main__":
    unittest.main()
