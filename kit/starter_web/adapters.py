"""starter Markdownを共通表示モデルへ変換する。"""

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .markdown import (
    markdown_tables,
    parse_iso_date,
    resolve_links,
    starter_status,
    strip_markdown,
    summarize,
)
from .model import (
    ActivityItem,
    DashboardData,
    ScheduleItem,
    ScheduleKind,
    SourceRef,
    Status,
    TestEvent,
)
from .recurrence import STARTER_ROUTINE_HEADER, parse_weekdays, routine_dates
from .test_events import TEST_EVENT_HEADER, TEST_SCHEDULE_MARKER, UNSCHEDULED_TEST_DATE
from .workspace_contract import (
    TestScoreRecord,
    WorkspaceRecords,
    optional_workspace_records,
)
from .adapter_support import (
    schedule_kind,
    without_test_marker,
    assert_future_test_markers_match,
    activity_status,
    optional_iso_date,
    parse_test_event_tables,
    enrich_schedule,
)

STARTER_WEEKLY_REVIEW_HEADING = re.compile(r"###\s+\d{4}-\d{2}-\d{2}週")


class StarterMarkdownAdapter:
    """架空demoの固定Markdown表を共通モデルへ変換する。"""

    SCHEDULE_HEADER = [
        "日付",
        "学習者ID",
        "学習者",
        "枠",
        "教科",
        "内容",
        "状態",
        "教材",
        "繰越先",
    ]
    ACTIVITY_HEADER = [
        "日付",
        "学習者ID",
        "学習者",
        "教科",
        "内容",
        "教材",
        "所要時間",
        "結果",
        "次アクション",
    ]
    TEST_EVENT_HEADER = list(TEST_EVENT_HEADER)

    def __init__(
        self,
        workspace_root: Path,
        anchor_date: date,
        schedule_path: Optional[Path] = None,
        activity_path: Optional[Path] = None,
        mode: str = "demo",
    ) -> None:
        if mode not in ("demo", "starter"):
            raise ValueError(
                "starter adapterのmodeはdemoまたはstarterです: {}".format(mode)
            )
        self.workspace_root = workspace_root.resolve()
        self.anchor_date = anchor_date
        self.mode = mode
        self.schedule_path = (
            schedule_path or self.workspace_root / "schedule.md"
        ).resolve()
        self.activity_path = (
            activity_path or self.workspace_root / "activity-log.md"
        ).resolve()

    def load(self) -> DashboardData:
        records = optional_workspace_records(self.workspace_root)
        if records is not None:
            self._assert_no_legacy_test_table()
            self._assert_all_registered_source_rows(records)
        schedule = self._parse_schedule()
        if records is not None:
            self._assert_valid_schedule_deferrals(schedule)
            self._assert_no_test_schedule_items(schedule)
        activities = self._parse_activities()
        if records is None:
            test_events = self._parse_test_events()
        else:
            test_events, test_activities = self._parse_test_records(records)
            activities.extend(test_activities)
            self._assert_registered_learners(records, schedule, activities)
            self._assert_registered_materials(records, schedule, activities)
        self._assert_consistent_learners(schedule, activities)
        if records is None:
            assert_future_test_markers_match(
                schedule,
                test_events,
                date.today(),
            )
        enrich_schedule(schedule, activities)
        return DashboardData(
            mode=self.mode,
            title=(
                "家庭学習スターター・デモ"
                if self.mode == "demo"
                else "家庭学習ダッシュボード"
            ),
            anchor_date=self.anchor_date,
            schedule=schedule,
            activities=activities,
            test_events=test_events,
            has_weekly_review=self._has_weekly_review(),
        )

    def _assert_no_legacy_test_table(self) -> None:
        """新tests.mdと旧schedule内テスト表の二重正本を拒否する。"""

        lines = self.schedule_path.read_text(encoding="utf-8").splitlines()
        for header_line, header, _ in markdown_tables(lines):
            if tuple(strip_markdown(cell) for cell in header) == tuple(
                self.TEST_EVENT_HEADER
            ):
                raise ValueError(
                    "tests.md導入後はschedule.mdのテスト予定表を削除してください: "
                    "{} L{}".format(self.schedule_path, header_line)
                )

    @staticmethod
    def _assert_no_test_schedule_items(schedule: Sequence[ScheduleItem]) -> None:
        """新tests.mdとscheduleの本番行を重複させない。"""

        for item in schedule:
            if item.kind != ScheduleKind.TEST:
                continue
            line = item.source.line if item.source else 0
            raise ValueError(
                "tests.md導入後はschedule.mdに{}行を作らず、テスト実施回だけに"
                "記録してください: {} L{}".format(
                    TEST_SCHEDULE_MARKER,
                    item.source.path if item.source else "schedule.md",
                    line,
                )
            )

    @staticmethod
    def _assert_valid_schedule_deferrals(
        schedule: Sequence[ScheduleItem],
    ) -> None:
        """新契約では通常予定の繰越先を必須・未来日にする。"""

        for item in schedule:
            line = item.source.line if item.source else 0
            source_path = item.source.path if item.source else "schedule.md"
            if item.status == Status.DEFERRED and item.deferred_to is None:
                raise ValueError(
                    "繰越した予定には繰越先の日付を記載してください: "
                    "{} L{}".format(source_path, line)
                )
            if item.deferred_to is not None and item.deferred_to <= item.date:
                raise ValueError(
                    "繰越先は元の予定日より後にしてください: {} L{}".format(
                        source_path, line
                    )
                )

    def _assert_all_registered_source_rows(
        self, records: WorkspaceRecords
    ) -> None:
        """表示期間外を含む全行の学習者と教材参照を台帳照合する。"""

        learners = {item.learner_id: item.name for item in records.learners}
        materials = {
            item.material.target.split("#", 1)[0]: item
            for item in records.materials
        }
        specifications = (
            (self.schedule_path, tuple(self.SCHEDULE_HEADER), 1, 2, 7),
            (self.schedule_path, tuple(STARTER_ROUTINE_HEADER), 3, 4, 8),
            (self.activity_path, tuple(self.ACTIVITY_HEADER), 1, 2, 5),
        )
        for path, expected_header, id_index, name_index, material_index in specifications:
            lines = path.read_text(encoding="utf-8").splitlines()
            for header_line, header, rows in markdown_tables(lines):
                if tuple(strip_markdown(cell) for cell in header) != expected_header:
                    continue
                for offset, row in enumerate(rows):
                    line = header_line + 2 + offset
                    learner_id = strip_markdown(row[id_index])
                    learner_name = strip_markdown(row[name_index])
                    if learners.get(learner_id) != learner_name:
                        raise ValueError(
                            "学習者IDと呼び名がlearners.mdに一致しません: "
                            "{} L{}".format(path, line)
                        )
                    links = resolve_links(
                        row[material_index], path, self.workspace_root,
                        allow_external=False,
                    )
                    for link in links:
                        material = materials.get(link.target.split("#", 1)[0])
                        if material is None:
                            raise ValueError(
                                "教材リンクがmaterials/index.mdに登録されていません: "
                                "{} L{}".format(path, line)
                            )
                        if (
                            material.audience == "個別"
                            and material.learner_id != learner_id
                        ):
                            raise ValueError(
                                "他の学習者の個別教材を参照しています: "
                                "{} L{}".format(path, line)
                            )

    def _parse_test_records(
        self, records: WorkspaceRecords
    ) -> Tuple[List[TestEvent], List[ActivityItem]]:
        """tests.mdの予定を本番カード、結果を最近の実績へ変換する。"""

        definitions = {item.test_id: item for item in records.test_definitions}
        scores_by_attempt = {}
        for score in records.test_scores:
            scores_by_attempt.setdefault(score.attempt_id, []).append(score)
        events = []
        activities = []
        for attempt in records.test_attempts:
            definition = definitions[attempt.test_id]
            title = definition.title
            if attempt.edition not in ("—", "-"):
                title += " " + attempt.edition
            note_parts = []
            if attempt.level not in ("—", "-"):
                note_parts.append("受験区分: " + attempt.level)
            if definition.note not in ("", "—", "-"):
                note_parts.append(definition.note)
            note = "・".join(note_parts)
            if attempt.state in ("予定", "準備済み"):
                events.append(
                    TestEvent(
                        date=(
                            None
                            if attempt.date_text == UNSCHEDULED_TEST_DATE
                            else parse_iso_date(attempt.date_text)
                        ),
                        learner_name=attempt.learner_name,
                        category=definition.category,
                        title=title,
                        note=note,
                        source=attempt.source,
                        attempt_id=attempt.attempt_id,
                        learner_id=attempt.learner_id,
                        materials=list(attempt.materials),
                    )
                )
                continue
            score_summary = self._test_score_summary(
                scores_by_attempt.get(attempt.attempt_id, [])
            )
            deferred_note = ""
            if attempt.deferred_to not in ("—", "-"):
                deferred_label = attempt.deferred_to
                if deferred_label != UNSCHEDULED_TEST_DATE:
                    deferred_date = parse_iso_date(deferred_label)
                    deferred_label = "{}/{}".format(
                        deferred_date.month, deferred_date.day
                    )
                deferred_note = "繰越先 {}".format(deferred_label)
            next_action = (
                "" if attempt.next_action in ("—", "-")
                else attempt.next_action
            )
            if deferred_note:
                next_action = "。".join(
                    part for part in (deferred_note, next_action) if part
                )
            activities.append(
                ActivityItem(
                    date=parse_iso_date(attempt.date_text),
                    learner_id=attempt.learner_id,
                    learner_name=attempt.learner_name,
                    subject=definition.subject,
                    title=title,
                    duration=attempt.duration,
                    status=starter_status(attempt.state),
                    result_summary=summarize(score_summary + attempt.result),
                    next_action=summarize(next_action, 150),
                    materials=list(attempt.materials),
                    source=attempt.source,
                    is_test=True,
                )
            )
        return events, activities

    @staticmethod
    def _test_score_summary(scores: Sequence[TestScoreRecord]) -> str:
        """教科別得点・平均・偏差値・順位・判定を短い1文へまとめる。"""

        parts = []
        for score in scores:
            metrics = []
            if score.score not in ("—", "-"):
                metrics.append("{}/{}点".format(score.score, score.maximum))
            if score.average not in ("—", "-"):
                metrics.append("平均{}".format(score.average))
            if score.deviation not in ("—", "-"):
                metrics.append("偏差値{}".format(score.deviation))
            if score.rank not in ("—", "-"):
                rank = "順位{}".format(score.rank)
                if score.participants not in ("—", "-"):
                    rank += "/{}".format(score.participants)
                metrics.append(rank)
            elif score.participants not in ("—", "-"):
                metrics.append("受験者{}".format(score.participants))
            if score.judgment not in ("—", "-"):
                metrics.append("判定{}".format(score.judgment))
            if score.note not in ("—", "-"):
                metrics.append(score.note)
            parts.append("{} {}".format(score.area, "・".join(metrics)))
        return ("／".join(parts) + "。") if parts else ""

    @staticmethod
    def _assert_registered_learners(
        records: WorkspaceRecords,
        schedule: Sequence[ScheduleItem],
        activities: Sequence[ActivityItem],
    ) -> None:
        """予定・実績の学習者をlearners.mdの登録内容と照合する。"""

        registered = {item.learner_id: item.name for item in records.learners}
        for item in list(schedule) + list(activities):
            if registered.get(item.learner_id) == item.learner_name:
                continue
            line = item.source.line if item.source else 0
            raise ValueError(
                "学習者IDと呼び名がlearners.mdに一致しません: {} L{}".format(
                    item.source.path if item.source else "workspace", line
                )
            )

    @staticmethod
    def _assert_registered_materials(
        records: WorkspaceRecords,
        schedule: Sequence[ScheduleItem],
        activities: Sequence[ActivityItem],
    ) -> None:
        """教材リンクを台帳へ接続し、個別教材の取り違えを拒否する。"""

        registered = {
            item.material.target.split("#", 1)[0]: item
            for item in records.materials
        }
        for item in list(schedule) + list(activities):
            for link in item.materials:
                material = registered.get(link.target.split("#", 1)[0])
                line = item.source.line if item.source else 0
                if material is None:
                    raise ValueError(
                        "教材リンクがmaterials/index.mdに登録されていません: "
                        "{} L{}".format(
                            item.source.path if item.source else "workspace", line
                        )
                    )
                if (
                    material.audience == "個別"
                    and material.learner_id != item.learner_id
                ):
                    raise ValueError(
                        "他の学習者の個別教材を参照しています: {} L{}".format(
                            item.source.path if item.source else "workspace", line
                        )
                    )

    def _parse_test_events(self) -> List[TestEvent]:
        """新台帳が無い旧starterの任意テスト予定表を互換読み取りする。"""

        return parse_test_event_tables(self.schedule_path)

    def _has_weekly_review(self) -> bool:
        """`## 週次振り返り` の下に週の見出しが1つでもあるかを返す。

        雛形は見出しと書き方の説明だけを持つ。`### YYYY-MM-DD週` の形式に
        一致して初めて「振り返り済み」とみなす。`### 書き方` のような
        案内見出しでは真にしない。
        """

        lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        inside = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                inside = stripped[3:].strip() == "週次振り返り"
                continue
            if inside and STARTER_WEEKLY_REVIEW_HEADING.fullmatch(stripped):
                return True
        return False

    @staticmethod
    def _assert_consistent_learners(
        schedule: List[ScheduleItem], activities: List[ActivityItem]
    ) -> None:
        """空の学習者IDと、同じIDが複数の学習者名で使われる混在を拒否する。"""

        names_by_id: Dict[str, Dict[str, int]] = {}
        empty_lines = []
        for item in list(schedule) + list(activities):
            line = item.source.line if item.source else 0
            if not item.learner_id:
                # 空IDを許すと、実績照合キーが学習者間で衝突し表示が混在する。
                empty_lines.append(line)
                continue
            first_lines = names_by_id.setdefault(item.learner_id, {})
            first_lines.setdefault(item.learner_name, line)
        if empty_lines:
            described = "、".join(
                "L{}".format(line) for line in sorted(set(empty_lines))
            )
            raise ValueError(
                "学習者IDが空の行があります: {}。"
                "学習者ごとに一意のIDを記載してください".format(described)
            )
        for learner_id, first_lines in names_by_id.items():
            if len(first_lines) > 1:
                described = "、".join(
                    "{}（L{}）".format(name, line)
                    for name, line in sorted(
                        first_lines.items(), key=lambda entry: entry[1]
                    )
                )
                raise ValueError(
                    "学習者ID {} が複数の学習者名で使われています: {}。"
                    "学習者ごとに一意のIDを割り当ててください".format(
                        learner_id, described
                    )
                )

    def _parse_schedule(self) -> List[ScheduleItem]:
        lines = self.schedule_path.read_text(encoding="utf-8").splitlines()
        tables = list(markdown_tables(lines))
        items = []
        found_dated_table = False
        for header_line, header, rows in tables:
            normalized_header = [strip_markdown(cell) for cell in header]
            if normalized_header == self.SCHEDULE_HEADER:
                found_dated_table = True
                for row_offset, row in enumerate(rows):
                    (
                        date_text,
                        learner_id,
                        learner_name,
                        slot,
                        subject,
                        title,
                        status,
                        materials,
                        deferred_to,
                    ) = row
                    scheduled_date = parse_iso_date(strip_markdown(date_text))
                    normalized_status = strip_markdown(status)
                    deferred_date = optional_iso_date(deferred_to)
                    source_line = header_line + 2 + row_offset
                    items.append(
                        ScheduleItem(
                            date=scheduled_date,
                            learner_id=strip_markdown(learner_id),
                            learner_name=strip_markdown(learner_name),
                            slot=strip_markdown(slot),
                            subject=strip_markdown(subject),
                            title=without_test_marker(title),
                            status=starter_status(normalized_status),
                            kind=schedule_kind(title),
                            materials=resolve_links(
                                materials,
                                self.schedule_path,
                                self.workspace_root,
                                allow_external=False,
                            ),
                            deferred_to=deferred_date,
                            source=SourceRef(
                                path=str(self.schedule_path),
                                line=source_line,
                            ),
                        )
                    )
                continue
            if tuple(normalized_header) == STARTER_ROUTINE_HEADER:
                items.extend(self._parse_recurring_rows(header_line, rows))
        if found_dated_table:
            return items
        raise ValueError(
            "schedule.mdに所定の表がありません: {}".format(self.schedule_path)
        )

    def _parse_recurring_rows(
        self,
        header_line: int,
        rows: Sequence[Sequence[str]],
    ) -> List[ScheduleItem]:
        week_start = self.anchor_date - timedelta(days=self.anchor_date.weekday())
        horizon_start = week_start - timedelta(days=7)
        horizon_end = week_start + timedelta(days=13)
        items = []
        for row_offset, row in enumerate(rows):
            (
                start_text,
                end_text,
                weekday_text,
                learner_id,
                learner_name,
                slot,
                subject,
                title,
                materials,
            ) = row
            start = parse_iso_date(strip_markdown(start_text))
            end = optional_iso_date(end_text)
            weekdays = parse_weekdays(strip_markdown(weekday_text))
            source_line = header_line + 2 + row_offset
            for scheduled_date in routine_dates(
                start,
                end,
                weekdays,
                horizon_start,
                horizon_end,
            ):
                items.append(
                    ScheduleItem(
                        date=scheduled_date,
                        learner_id=strip_markdown(learner_id),
                        learner_name=strip_markdown(learner_name),
                        slot=strip_markdown(slot),
                        subject=strip_markdown(subject),
                        title=strip_markdown(title),
                        status=Status.PLANNED,
                        kind=ScheduleKind.ROUTINE,
                        materials=resolve_links(
                            materials,
                            self.schedule_path,
                            self.workspace_root,
                            allow_external=False,
                        ),
                        source=SourceRef(
                            path=str(self.schedule_path), line=source_line
                        ),
                    )
                )
        return items

    def _parse_activities(self) -> List[ActivityItem]:
        lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        tables = list(markdown_tables(lines))
        for header_line, header, rows in tables:
            if [strip_markdown(cell) for cell in header] != self.ACTIVITY_HEADER:
                continue
            items = []
            for row_offset, row in enumerate(rows):
                (
                    date_text,
                    learner_id,
                    learner_name,
                    subject,
                    title,
                    materials,
                    duration,
                    result,
                    next_action,
                ) = row
                plain_title = strip_markdown(title)
                items.append(
                    ActivityItem(
                        date=parse_iso_date(strip_markdown(date_text)),
                        learner_id=strip_markdown(learner_id),
                        learner_name=strip_markdown(learner_name),
                        subject=strip_markdown(subject),
                        title=plain_title,
                        duration=strip_markdown(duration),
                        status=activity_status(plain_title, result),
                        result_summary=summarize(result),
                        next_action=summarize(next_action, 150),
                        materials=resolve_links(
                            materials,
                            self.activity_path,
                            self.workspace_root,
                            allow_external=False,
                        ),
                        source=SourceRef(
                            path=str(self.activity_path),
                            line=header_line + 2 + row_offset,
                        ),
                    )
                )
            return items
        raise ValueError(
            "activity-log.mdに所定の表がありません: {}".format(
                self.activity_path
            )
        )
