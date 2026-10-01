"""予定と活動ログの同期漏れを、構造エラーとは別の警告として報告する。

入力契約の検査は「書き方が契約どおりか」だけを見るため、予定はあるのに
活動ログへ実施行が無い、という**中身の食い違い**は素通りする。Webは予定の
状態を活動ログから補うので、実施行が無い日は静かに未実施として表示され、
人が気付くまで実行の欠落と記録の欠落を区別できない。

ここでは同じ突合器（``model.activity_matches_schedule``）を再利用して、
過去の予定に対応する実績があるかだけを確認し、警告として一覧に出す。
未実施か報告漏れかの判断は人に委ね、Web生成や品質ゲートは止めない。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Sequence

from dataclasses import dataclass
from .model import (
    ActivityItem,
    DashboardData,
    ScheduleItem,
    ScheduleKind,
    Status,
    activity_matches_schedule,
)

@dataclass(frozen=True)
class SyncIssue:
    """入力形式に依存しない同期警告。各CLIが自身の診断形式へ変換する。"""

    path: Path
    line: int
    message: str


#: 実績の報告は数日遅れることがあるため、直近の予定は検査対象から外す。
#: 1日だけ猶予すると、基準日8/7では8/6を除外し8/5以前を検査する。
REPORT_GRACE_DAYS = 1

#: 予定側が「実施した」と表示している状態。実績行が無ければ記録漏れを疑う。
RECORDED_STATUSES = frozenset({Status.COMPLETED, Status.PARTIAL})

#: 未実施・繰越として人が明示済みの状態。確認を促す必要がない。
RESOLVED_STATUSES = frozenset({Status.MISSED, Status.DEFERRED})


def _display_horizon_start(anchor_date: date) -> date:
    """Webが表示する3週間（前週・今週・翌週）の初日。"""

    week_start = anchor_date - timedelta(days=anchor_date.weekday())
    return week_start - timedelta(days=7)


def _description(item: ScheduleItem) -> str:
    kind = "反復ルーティン" if item.kind == ScheduleKind.ROUTINE else "予定"
    return "{} {} {} {}「{}」".format(
        item.date.isoformat(),
        item.learner_name,
        item.subject,
        kind,
        item.title,
    )


def sync_issues(
    data: DashboardData,
    anchor_date: Optional[date] = None,
    grace_days: int = REPORT_GRACE_DAYS,
) -> List[SyncIssue]:
    """表示3週間の過去分について、予定と実績の食い違いを警告で返す。

    - A-1: 予定はあるが対応する実施行が無い（未実施か報告漏れかを確認する）
    - A-2: 予定は実施済み表示なのに実施行が無い（活動ログの記録漏れ）

    同じ予定へ両方は出さない。テスト本番の予定と、未実施・繰越として明示
    済みの予定は対象外とする。
    """

    anchor = anchor_date or data.anchor_date
    horizon_start = _display_horizon_start(anchor)
    cutoff = anchor - timedelta(days=grace_days)
    activities: Sequence[ActivityItem] = data.activities

    issues = []
    for item in sorted(
        data.schedule, key=lambda item: (item.date, item.learner_name, item.title)
    ):
        if item.kind == ScheduleKind.TEST:
            continue
        if not (horizon_start <= item.date < cutoff):
            continue
        if item.status in RESOLVED_STATUSES:
            continue
        if any(
            activity_matches_schedule(item, activity) for activity in activities
        ):
            continue
        if item.status in RECORDED_STATUSES:
            message = (
                "予定は実施済み表示ですが、活動ログに対応する実施行がありません。"
                "活動ログの記録漏れを確認してください: {}".format(_description(item))
            )
        else:
            message = (
                "予定に対応する活動ログの実施行がありません。"
                "未実施か報告漏れかを確認してください: {}".format(_description(item))
            )
        issues.append(
            SyncIssue(
                Path(item.source.path) if item.source else Path("schedule"),
                item.source.line if item.source else 0,
                message,
            )
        )
    return issues
