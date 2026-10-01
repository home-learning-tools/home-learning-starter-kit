"""入力アダプタが共有するMarkdown変換と予定・実績照合。"""

import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .markdown import markdown_tables, parse_iso_date, strip_markdown
from .model import (
    ActivityItem,
    ScheduleItem,
    ScheduleKind,
    SourceRef,
    Status,
    TestEvent,
)
from .test_events import TEST_EVENT_HEADER, TEST_SCHEDULE_MARKER, UNSCHEDULED_TEST_DATE


def nearest_heading(lines: Sequence[str], before_line: int, level: int) -> str:
    prefix = "#" * level + " "
    for index in range(min(before_line - 1, len(lines) - 1), -1, -1):
        if lines[index].startswith(prefix):
            return strip_markdown(lines[index][len(prefix) :])
    return ""


def schedule_kind(value: str) -> ScheduleKind:
    """予定セルの明示マーカーから予定種別を得る。"""

    return (
        ScheduleKind.TEST
        if TEST_SCHEDULE_MARKER in strip_markdown(value)
        else ScheduleKind.DATED
    )


def without_test_marker(value: str) -> str:
    return strip_markdown(value).replace(TEST_SCHEDULE_MARKER, "").strip()


def _event_has_learner(event: TestEvent, learner_name: str) -> bool:
    learners = {
        part.strip()
        for part in event.learner_name.split("・")
        if part.strip()
    }
    return learner_name in learners


def assert_future_test_markers_match(
    schedule: Sequence[ScheduleItem],
    test_events: Sequence[TestEvent],
    validation_date: date,
) -> None:
    """実行日時点で将来の本番マーカーを、正本の予定表と照合する。

    表示用のanchor_dateは過去画面の再現にも使う。終了済みの本番を
    現在のテスト予定表へ戻さないよう、入力契約の基準日とは分ける。
    """

    for item in schedule:
        if item.kind != ScheduleKind.TEST or item.date < validation_date:
            continue
        if any(
            event.date == item.date
            and _event_has_learner(event, item.learner_name)
            for event in test_events
        ):
            continue
        line = item.source.line if item.source else 0
        raise ValueError(
            "将来の{}予定は、テスト予定表の同じ日付・学習者にも"
            "記載してください: {} L{}".format(
                TEST_SCHEDULE_MARKER,
                item.source.path if item.source else "schedule.md",
                line,
            )
        )


def activity_status(title: str, result: str) -> Status:
    plain_title = strip_markdown(title)
    plain_result = strip_markdown(result)
    if "未実施" in plain_title or re.match(
        r"^(?:【)?未実施", plain_result
    ):
        return Status.MISSED
    partial_markers = (
        "一部実施",
        "前半のみ",
        "後半は未着手",
        "途中で終了",
    )
    if any(marker in plain_title for marker in partial_markers) or re.match(
        r"^(?:【)?(?:一部実施|前半のみ|後半は未着手|途中で終了)",
        plain_result,
    ):
        return Status.PARTIAL
    if "実施有無未確認" in plain_title or re.match(
        r"^(?:【)?実施有無未確認", plain_result
    ):
        return Status.UNKNOWN
    if "繰越" in plain_title or re.match(r"^(?:【)?繰越", plain_result):
        return Status.DEFERRED
    return Status.COMPLETED


def optional_iso_date(value: str) -> Optional[date]:
    normalized = strip_markdown(value)
    if normalized in {"", "—", "-"}:
        return None
    return parse_iso_date(normalized)


def parse_test_event_tables(
    source_path: Path,
    required_heading: Optional[str] = None,
) -> List[TestEvent]:
    """所定の表からテスト予定を読み、必須値と重複を検証する。"""

    lines = source_path.read_text(encoding="utf-8").splitlines()
    items = []
    seen = set()
    for header_line, header, rows in markdown_tables(lines):
        if tuple(strip_markdown(cell) for cell in header) != TEST_EVENT_HEADER:
            continue
        if required_heading and (
            nearest_heading(lines, header_line, 3) != required_heading
        ):
            continue
        for row_offset, row in enumerate(rows):
            date_text, learner_name, category, title, note = row
            source_line = header_line + 2 + row_offset
            normalized_date = strip_markdown(date_text)
            event_date = (
                None
                if normalized_date == UNSCHEDULED_TEST_DATE
                else parse_iso_date(normalized_date)
            )
            normalized_learner = strip_markdown(learner_name)
            normalized_category = strip_markdown(category)
            normalized_title = strip_markdown(title)
            for label, value in (
                ("学習者", normalized_learner),
                ("種別", normalized_category),
                ("テスト名", normalized_title),
            ):
                if not value:
                    raise ValueError(
                        "テスト予定の{}は空にできません: L{}".format(
                            label, source_line
                        )
                    )
            key = (
                normalized_date,
                normalized_learner,
                normalized_category,
                normalized_title,
            )
            if key in seen:
                raise ValueError(
                    "同じテスト予定が重複しています: L{}".format(source_line)
                )
            seen.add(key)
            items.append(
                TestEvent(
                    date=event_date,
                    learner_name=normalized_learner,
                    category=normalized_category,
                    title=normalized_title,
                    note=(
                        ""
                        if strip_markdown(note) in {"", "—", "-"}
                        else strip_markdown(note)
                    ),
                    source=SourceRef(path=str(source_path), line=source_line),
                )
            )
    return items


def enrich_schedule(
    schedule: List[ScheduleItem], activities: List[ActivityItem]
) -> None:
    """日付付き予定と反復ルーティンへ、対応する活動状態を反映する。"""

    activity_index: Dict[Tuple[date, str], List[ActivityItem]] = {}
    for activity in activities:
        if strip_markdown(activity.duration).startswith(("—", "-")):
            # 教材作成だけの行は、名前やリンクが一致しても実施実績ではない。
            continue
        activity_index.setdefault(
            (activity.date, activity.learner_id), []
        ).append(activity)

    for item in schedule:
        candidates = activity_index.get((item.date, item.learner_id), [])
        if item.kind == ScheduleKind.ROUTINE:
            for activity in candidates:
                if (
                    activity.subject == item.subject
                    and activity.title.casefold().startswith(
                        item.title.casefold()
                    )
                ):
                    # 反復ルーティンは予定量の消化率ではなく、その日の習慣を
                    # 実施したかを表す。通常WSが一部実施でも、対応する実施が
                    # あればルーティン自体は完了とする。
                    item.status = (
                        Status.COMPLETED
                        if activity.status == Status.PARTIAL
                        else activity.status
                    )
                    break
            continue
        if item.status not in {Status.PLANNED, Status.READY, Status.UNKNOWN}:
            continue
        # 参考・在庫（《参考:…》）は突合に使わない。詳細は
        # ScheduleItem.matching_materials() を参照。
        item_targets = {
            Path(link.target.split("#", 1)[0]).name
            for link in item.matching_materials()
        }
        for activity in candidates:
            activity_targets = {
                Path(link.target.split("#", 1)[0]).name
                for link in activity.materials
            }
            if item_targets and item_targets.intersection(activity_targets):
                item.status = activity.status
                break
