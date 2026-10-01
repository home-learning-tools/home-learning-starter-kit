"""Web表示に使う共通の派生モデル。"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Union
from urllib.parse import urlsplit


class Status(str, Enum):
    """予定と実績をWebで表示するための共通状態。"""

    PLANNED = "planned"
    READY = "ready"
    COMPLETED = "completed"
    PARTIAL = "partial"
    MISSED = "missed"
    UNKNOWN = "unknown"
    DEFERRED = "deferred"


class ScheduleKind(str, Enum):
    """予定の入力種別。"""

    DATED = "dated"
    ROUTINE = "routine"
    TEST = "test"


STATUS_LABELS = {
    Status.PLANNED: "予定",
    Status.READY: "準備済み",
    Status.COMPLETED: "完了",
    Status.PARTIAL: "一部実施",
    Status.MISSED: "未実施",
    Status.UNKNOWN: "実施有無未確認",
    Status.DEFERRED: "繰越",
}

ATTENTION_STATUSES = {
    Status.PARTIAL,
    Status.MISSED,
    Status.UNKNOWN,
    Status.DEFERRED,
}

# これから実施する予定だけを、教材の準備状況の点検対象にする。
PREPARABLE_STATUSES = {
    Status.PLANNED,
    Status.READY,
}

# 実施に使う教材そのものとみなす拡張子。Markdownは補助メモ扱いとし、
# それだけでは「教材が用意できている」と判定しない。
MATERIAL_SUFFIXES = {".html", ".htm", ".pdf"}


class PrepIssue(str, Enum):
    """予定に対して教材が用意できていない理由。"""

    NO_MATERIAL = "no-material"
    MISSING_FILE = "missing-file"


PREP_ISSUE_LABELS = {
    PrepIssue.NO_MATERIAL: "教材なし",
    PrepIssue.MISSING_FILE: "リンク切れ",
}


class MaterialStatus(str, Enum):
    """予定枠に対して、教材が実際に使える状態かどうか。

    予定表へ載っているかどうか（``Status.PLANNED``）ではなく、リンク先の
    実在ファイルだけから決める。``【作成済】`` と書いてあっても実体が無ければ
    ``MISSING`` になる。
    """

    AVAILABLE = "available"
    EXTERNAL_AVAILABLE = "external-available"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_REQUIRED = "not-required"


MATERIAL_STATUS_LABELS = {
    MaterialStatus.AVAILABLE: "教材あり",
    MaterialStatus.EXTERNAL_AVAILABLE: "外部教材あり",
    MaterialStatus.PARTIAL: "一部未準備",
    MaterialStatus.MISSING: "教材未準備",
    MaterialStatus.NOT_REQUIRED: "教材不要",
}

# 「教材未準備」一覧へ載せる教材状態（準備済み・教材不要は載せない）。
UNPREPARED_MATERIAL_STATUSES = (MaterialStatus.PARTIAL, MaterialStatus.MISSING)


def add_months(value: date, months: int) -> date:
    """暦の月数を足した日付を返す。

    「3か月後」は日数ではなく暦の同じ日で数える。足した先に同じ日が
    無い場合（1/31 の1か月後など）は、その月の末日へ丸める。
    """

    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def local_material_path(link: "MaterialLink") -> Optional[Path]:
    """教材リンクのうち、リポジトリ内のローカルパスだけを返す。

    外部URLは教材として数えない。教材はリポジトリ内の実在ファイルへ
    リンクする運用のため、外部URLしか無い項目は「教材なし」になる。
    """

    target = link.target.split("#", 1)[0]
    if urlsplit(target).scheme in {"http", "https"}:
        return None
    return Path(target)


def _schedule_sort_key(item: "ScheduleItem") -> tuple:
    """朝枠を先頭、夜枠を末尾にし、同順位では入力順を保つ。"""

    if "朝" in item.slot:
        slot_order = 0
    elif "夜" in item.slot:
        slot_order = 2
    else:
        slot_order = 1
    return item.date, slot_order


@dataclass(frozen=True)
class MaterialLink:
    """教材名と、リポジトリ内で解決済みの対象。

    ``target`` は「ブラウザで開くための解決済みの対象」で、demoのstagingや
    ローカル配信時の書き換えで変わってよい。``copy_path`` は「作業ルートから
    見た安定した論理パス」で、``resolve_links()`` が許可ルート基準の相対POSIX
    パスとして作る（private＝リポジトリルート相対／starter・demo＝workspace
    ルート相対）。**``target`` や ``href`` からコピー値を逆算しない**＝配信時に
    ``href`` が教材オリジンのURLへ変わっても ``copy_path`` は変わらない。

    ``copy_path`` は省略可能で、既定は ``None``。下流が
    ``MaterialLink(label, target)`` の2項目だけで組み立てても例外にせず、その
    リンクにはコピーbuttonを出さない（相対ルートを ``target`` から推測しない）。
    """

    label: str
    target: str
    copy_path: Optional[str] = None


@dataclass(frozen=True)
class SourceRef:
    """正本Markdownの参照位置。"""

    path: str
    line: int


@dataclass(frozen=True)
class MaterialRequirement:
    """1つの予定枠のなかで、教材の要否を独立に判定する単位。

    予定は1セル＝1件のまま扱い、セル内に独立した実施項目が複数あるときだけ
    この単位を複数持たせる。``required`` が偽の項目（教材なしで実施できる
    口頭確認など）は、教材判定の分母に含めない。``external`` が真の項目は、
    リポジトリ外に教材が存在することを入力で明示した準備済み項目として扱う。
    """

    label: str
    required: bool = True
    materials: Tuple[MaterialLink, ...] = ()
    external: bool = False

    def local_paths(self) -> List[Path]:
        """外部URLを除いた、この項目のリンク先パス。"""

        paths = [local_material_path(link) for link in self.materials]
        return [path for path in paths if path is not None]

    def missing_link_labels(self) -> List[str]:
        """リンク先が実在しない教材のラベル（リンク切れ）。

        教材状態へ影響するのは ``.html`` / ``.pdf`` だけなので、リンク切れも
        その拡張子に限る。補助資料（``.md``）のリンク切れで「教材未準備」に
        しない。壊れたMarkdownリンクは ``npm run validate:links`` が別途検出する。
        """

        return [
            link.label
            for link in self.materials
            if (path := local_material_path(link)) is not None
            and path.suffix.lower() in MATERIAL_SUFFIXES
            and not path.is_file()
        ]

    def has_material_file(self) -> bool:
        """実在する ``.html`` / ``.pdf`` を1件以上持つかどうか。"""

        return any(
            path.is_file() and path.suffix.lower() in MATERIAL_SUFFIXES
            for path in self.local_paths()
        )

    def is_ready(self) -> bool:
        """この項目の教材が実際に使える状態かどうか。"""

        if not self.required:
            return True
        if self.external:
            return True
        if self.missing_link_labels():
            return False
        return self.has_material_file()

    def issue(self) -> Optional[PrepIssue]:
        """準備できていない理由。準備済み・教材不要では ``None``。"""

        if self.is_ready():
            return None
        if self.missing_link_labels():
            return PrepIssue.MISSING_FILE
        return PrepIssue.NO_MATERIAL


@dataclass
class ScheduleItem:
    """予定表から得た1つの学習枠。"""

    date: date
    learner_id: str
    learner_name: str
    slot: str
    subject: str
    title: str
    status: Status
    kind: ScheduleKind = ScheduleKind.DATED
    materials: List[MaterialLink] = field(default_factory=list)
    deferred_to: Optional[date] = None
    source: Optional[SourceRef] = None
    material_requirements: List[MaterialRequirement] = field(default_factory=list)
    reference_materials: List[MaterialLink] = field(default_factory=list)

    def matching_materials(self) -> List[MaterialLink]:
        """この予定枠の教材として扱うリンク。

        カードから開ける ``materials`` には、その日に使わない参考・在庫
        （``《参考:…》``）も含まれる。実績の突合にそれを使うと、在庫を本人希望で
        実施しただけで別項目が実施済みになってしまうため、教材状態と同じく
        **実施項目に属する教材だけ**を返す。

        項目を明示したセルは実施項目からリンクを取れるので、そのまま返す
        （参考は最初から入らない）。項目を明示していない従来のセルはセル全体が
        1項目なので ``materials`` を使い、念のため参考ぶんだけ除く。同じ
        ファイルを実施項目と参考の両方へ書くことは入力契約で禁止している。
        """

        if self.material_requirements:
            return [
                link
                for requirement in self.material_requirements
                for link in requirement.materials
            ]
        excluded = {link.target for link in self.reference_materials}
        return [link for link in self.materials if link.target not in excluded]

    def material_files(self) -> List[MaterialLink]:
        """実施項目の教材のうち、実際に開いて使う教材ファイルだけ。

        ``matching_materials()`` には、その枠の説明として書かれた ``.md`` の
        正本・活動ログへのリンクも入る。カードに「その枠で使う教材」だけを
        出したい画面はこちらを使う。教材とみなす拡張子は教材状態の判定と
        同じ ``MATERIAL_SUFFIXES``（``.html`` / ``.pdf``）に合わせる。

        実在判定はしない＝リンク先が未作成でも、その枠の教材として書かれた
        ものは出す（準備できていないことは教材状態のバッジと「要確認」が
        示す）。外部URLは教材として数えないため除く。

        ただし**未作成リンクが画面まで届くのは、下流が独自実装でレンダラーへ
        直接渡した場合だけ**＝公開CLI（``python3 -m kit.starter_web``）は参照
        ファイルを生成先へ複製するため実在を要求し、``checkup`` もリンク切れを
        エラーにする。privateも ``npm run validate:links`` が同じく止める。
        """

        files = []
        for link in self.matching_materials():
            path = local_material_path(link)
            if path is None:
                continue
            if path.suffix.lower() in MATERIAL_SUFFIXES:
                files.append(link)
        return files

    def requirements(self) -> List[MaterialRequirement]:
        """教材判定に使う項目の一覧。

        明示の項目を持たない予定（従来の1セル1項目の書き方、starter形式の
        教材列）は、セル全体を教材必須の1項目として扱う。
        """

        if self.material_requirements:
            return list(self.material_requirements)
        return [
            MaterialRequirement(
                label=self.title,
                required=True,
                materials=tuple(self.materials),
            )
        ]

    def material_status(self) -> MaterialStatus:
        """予定枠全体の教材状態。"""

        required = [item for item in self.requirements() if item.required]
        if not required:
            return MaterialStatus.NOT_REQUIRED
        ready = [item for item in required if item.is_ready()]
        if len(ready) == len(required):
            if all(item.external for item in required):
                return MaterialStatus.EXTERNAL_AVAILABLE
            return MaterialStatus.AVAILABLE
        return MaterialStatus.PARTIAL if ready else MaterialStatus.MISSING


@dataclass
class ActivityItem:
    """活動ログから得た1つの実施記録。"""

    date: date
    learner_id: str
    learner_name: str
    subject: str
    title: str
    duration: str
    status: Status
    result_summary: str
    next_action: str = ""
    materials: List[MaterialLink] = field(default_factory=list)
    source: Optional[SourceRef] = None
    is_test: bool = False


@dataclass
class TestEvent:
    """本番日を把握するためのテスト予定。"""

    date: Optional[date]
    learner_name: str
    category: str
    title: str
    note: str = ""
    source: Optional[SourceRef] = None
    learner_id: str = ""
    materials: List[MaterialLink] = field(default_factory=list)
    attempt_id: str = ""


@dataclass(frozen=True)
class UnpreparedRequirement:
    """準備できていない項目と、その理由。"""

    requirement: MaterialRequirement
    issue: PrepIssue
    missing_labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UnpreparedItem:
    """教材が用意できていない予定と、その理由。

    件数は予定セル単位で数える。1セル内に不足項目が複数あっても1件とし、
    内訳は ``requirements`` に持つ。``issue`` と ``missing_labels`` はセル全体
    の代表値で、リンク切れが1件でもあれば ``MISSING_FILE`` になる。
    """

    item: "ScheduleItem"
    issue: PrepIssue
    missing_labels: List[str] = field(default_factory=list)
    material_status: MaterialStatus = MaterialStatus.MISSING
    requirements: Tuple[UnpreparedRequirement, ...] = ()


def activity_matches_schedule(
    item: "ScheduleItem", activity: "ActivityItem"
) -> bool:
    """活動ログの行が、その予定枠の実施記録かどうかを返す。

    アダプタは予定種別ごとに、日付付き予定なら教材ファイル名、反復ルーティン
    なら教科と内容名で突合している。ここでは同じ2つの手掛かりを併用し、
    日付と学習者が一致したうえでどちらかが合う場合だけ対応とみなす。
    所要時間が `—` の教材作成行は実施実績ではないため除外する。

    突合に使う教材は ``ScheduleItem.matching_materials()``＝実施項目の予定教材
    だけで、``《参考:…》`` の在庫・参考リンクは使わない。在庫を本人希望で実施
    しただけの記録が、その日の予定を実施済みに見せないようにするため。
    """

    if item.date != activity.date or item.learner_id != activity.learner_id:
        return False
    if activity.duration.strip().lstrip("*_ ").startswith(("—", "-")):
        return False

    item_targets = {
        Path(link.target.split("#", 1)[0]).name
        for link in item.matching_materials()
    }
    activity_targets = {
        Path(link.target.split("#", 1)[0]).name for link in activity.materials
    }
    if item_targets and item_targets & activity_targets:
        return True
    return item.subject == activity.subject and activity.title.casefold().startswith(
        item.title.casefold()
    )


@dataclass
class DashboardData:
    """1ページのWeb表示に必要なデータ。"""

    mode: str
    title: str
    anchor_date: date
    schedule: List[ScheduleItem]
    activities: List[ActivityItem]
    test_events: List[TestEvent] = field(default_factory=list)
    has_weekly_review: bool = False
    # 「3週間の見通し」で生成済みの最初／最後の週（月曜）。省略時は
    # 従来どおり前週・今週・翌週だけを表示対象とする。画面全体の基準日
    # ``anchor_date`` とは分け、見通しを移動しても今日の予定や集計を変えない。
    week_view_start: Optional[date] = None
    week_view_end: Optional[date] = None

    @property
    def week_start(self) -> date:
        return self.anchor_date - timedelta(days=self.anchor_date.weekday())

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)

    @property
    def available_week_start(self) -> date:
        """見通しで利用できる最初の週（月曜）。"""

        return self.week_view_start or self.week_start - timedelta(days=7)

    @property
    def available_week_end(self) -> date:
        """見通しで利用できる最後の週（月曜）。"""

        return self.week_view_end or self.week_start + timedelta(days=7)

    def week_schedule(self) -> List[ScheduleItem]:
        return self.schedule_for_week(self.week_start)

    def schedule_for_week(self, week_start: date) -> List[ScheduleItem]:
        end = week_start + timedelta(days=6)
        return sorted(
            [
                item
                for item in self.schedule
                if week_start <= item.date <= end
            ],
            key=_schedule_sort_key,
        )

    def today_schedule(self) -> List[ScheduleItem]:
        return sorted(
            [item for item in self.schedule if item.date == self.anchor_date],
            key=_schedule_sort_key,
        )

    def attention_items(self) -> List[Union[ScheduleItem, ActivityItem]]:
        schedule_items = [
            item
            for item in self.week_schedule()
            if item.kind != ScheduleKind.ROUTINE
            and item.status in ATTENTION_STATUSES
        ]
        test_items = [
            item
            for item in self.activities
            if item.is_test
            and self.week_start <= item.date <= self.week_end
            and item.status in ATTENTION_STATUSES
        ]
        return sorted(schedule_items + test_items, key=lambda item: item.date)

    @property
    def prep_horizon_end(self) -> date:
        """教材の準備状況を点検する範囲の末日（今週＋来週）。"""

        return self.week_start + timedelta(days=13)

    def unprepared_items(self) -> List["UnpreparedItem"]:
        """今週・来週の予定のうち、教材が用意できていない枠を返す。

        判定は予定カードと同じ ``ScheduleItem.material_status()`` を使い、
        別の判定ロジックを持たない。``PARTIAL``（一部未準備）と ``MISSING``
        （教材未準備）を載せ、``AVAILABLE``・``EXTERNAL_AVAILABLE``・
        ``NOT_REQUIRED`` は載せない。

        反復ルーティンは日ごとに教材を固定しないため対象外とし、テスト本番も
        ワークシートを持たないため対象外とする。基準日より前の予定は準備では
        なく実施の問題なので「要確認」側で扱う。
        """

        results = []
        for item in sorted(self.schedule, key=_schedule_sort_key):
            if item.kind != ScheduleKind.DATED:
                continue
            if not (self.anchor_date <= item.date <= self.prep_horizon_end):
                continue
            if item.status not in PREPARABLE_STATUSES:
                continue
            status = item.material_status()
            if status not in UNPREPARED_MATERIAL_STATUSES:
                continue
            unprepared = []
            for requirement in item.requirements():
                issue = requirement.issue()
                if issue is None:
                    continue
                unprepared.append(
                    UnpreparedRequirement(
                        requirement=requirement,
                        issue=issue,
                        missing_labels=tuple(requirement.missing_link_labels()),
                    )
                )
            missing_labels = [
                label for entry in unprepared for label in entry.missing_labels
            ]
            results.append(
                UnpreparedItem(
                    item=item,
                    issue=(
                        PrepIssue.MISSING_FILE
                        if missing_labels
                        else PrepIssue.NO_MATERIAL
                    ),
                    missing_labels=missing_labels,
                    material_status=status,
                    requirements=tuple(unprepared),
                )
            )
        return results

    def upcoming_tests(self, months: int = 3) -> List[TestEvent]:
        """基準日から指定した暦月数後の同日までの、日付が確定したテストを返す。"""

        end = add_months(self.anchor_date, months)
        return sorted(
            [
                item
                for item in self.test_events
                if item.date is not None
                and self.anchor_date <= item.date <= end
            ],
            key=lambda item: (item.date, item.learner_name, item.title),
        )

    def undated_tests(self) -> List[TestEvent]:
        """日程未定として記録されたテストを入力順で返す。"""

        return [item for item in self.test_events if item.date is None]

    def recent_activities(self, limit: int = 12) -> List[ActivityItem]:
        return sorted(
            self._eligible_activities(), key=lambda item: item.date, reverse=True
        )[:limit]

    def recent_activities_by_days(self, days: int) -> List[ActivityItem]:
        """実績のある日付を新しい順に ``days`` 日ぶん選び、その全件を返す。

        件数で切らないため、同じ日の実績が途中で切れない。実績のない暦日は
        数えず、``days`` 日ぶんに満たない場合は存在する実績をすべて返す。
        同じ日の中の並びは入力順のまま（``sorted`` は同順位を入れ替えない）。
        """

        if days < 1:
            raise ValueError("days は1以上にしてください: {}".format(days))
        eligible = self._eligible_activities()
        selected = set(sorted({item.date for item in eligible}, reverse=True)[:days])
        return sorted(
            [item for item in eligible if item.date in selected],
            key=lambda item: item.date,
            reverse=True,
        )

    def _eligible_activities(self) -> List[ActivityItem]:
        """基準日以前の実績（未来日の記録は「最近」に含めない）。"""

        return [item for item in self.activities if item.date <= self.anchor_date]
