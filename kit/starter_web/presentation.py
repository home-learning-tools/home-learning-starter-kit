"""画面に出す文言・セクション構成の差し替え点（下流向けの安定API）。

``render_dashboard(data, output_path, presentation=...)`` へ渡す1つの値で、
案内文・用語ガイド・「AIにできること」の行と判定・追加セクションをまとめて
差し替える。セクションごとに個別の引数を増やさないための集約点であり、
省略時は ``Presentation.for_mode(data.mode)``＝core既定（従来表示）を使う。

coreの既定文言はstarter形式（``workspace/``・``materials/``）の運用を前提に
書かれている。別の配置・別の言い方で運用する下流は、ここを差し替えて自分の
運用と一致する文言を出す。差し替えても、実データを公開しない・実績を捏造
しない・core同梱の検証を無効化しないという普遍ルールは変わらない。

契約の説明は ``docs/EXTENDING.md``（抽出元 ``kit/ai/EXTENDING.md``）。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Mapping, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

from .materials import material_type
from .model import (
    ActivityItem,
    DashboardData,
    TestEvent,
    MaterialLink,
    ScheduleKind,
    Status,
    activity_matches_schedule,
)


class PendingBadgeMode(str, Enum):
    """まだ実施していない予定に、どちらの状態を表示するか。

    ``STATUS``（既定）は従来どおり予定表上の状態（``予定``・``準備済み``）を
    出す。``MATERIAL_READINESS`` は代わりに教材の準備状態（``教材あり``・
    ``外部教材あり``・``一部未準備``・``教材未準備``・``教材不要``）を出す。
    実施済み・一部実施・未実施・繰越などの実施状態は、どちらのモードでも
    実施状態を優先する。
    """

    STATUS = "status"
    MATERIAL_READINESS = "material-readiness"


class MaterialLinkScope(str, Enum):
    """予定のカードに出す教材リンクの範囲。

    ``ALL``（既定）は従来どおり、予定セルから読めたリンクをすべて出す
    （``《参考:…》`` の在庫・参考リンクは参考の見た目で出す）。
    ``MATERIAL_FILES`` は実施項目の教材ファイル（``.html`` / ``.pdf``）だけに
    絞り、正本Markdownなどの参照リンクと参考・在庫リンクは出さない。
    予定セルへ経緯や参照先を書き込む運用では、カードが「その枠で使う教材」
    以外のリンクで埋まるため、そちらを画面から外すための指定。
    """

    ALL = "all"
    MATERIAL_FILES = "material-files"


# 実施結果が報告され、予定側も更新済みであることを示す状態。
RECORDED_STATUSES = frozenset(
    {Status.COMPLETED, Status.PARTIAL, Status.MISSED, Status.DEFERRED}
)

# 画面本体のセクションID。追加セクションの挿入位置（``after``）に使える。
CORE_SECTION_IDS = (
    "today",
    "tests",
    "week",
    "attention",
    "records",
    "materials",
    "ai",
)


class CatalogItem(NamedTuple):
    """「こう頼めば、こうなる」の1行。

    ``id`` は進捗判定の辞書のキーで、画面の ``data-ask`` にも出る。
    """

    id: str
    ask: str
    effect: str


class GuideTerm(NamedTuple):
    """用語ガイドの1項目。"""

    term: str
    description: str


@dataclass(frozen=True)
class CatalogGroup:
    """カタログ内の1まとまり。

    ``counted`` は見出し横の「N/M」に数えるか、``judged`` は済／未を判定して
    出すかを表す。判定関数を持たないカタログでは、どちらも効果を持たない。
    """

    items: Tuple[CatalogItem, ...]
    heading: str = ""
    note: str = ""
    counted: bool = False
    judged: bool = True


@dataclass(frozen=True)
class CatalogView:
    """「AIにできること」セクションの文言・行・判定。

    ``progress`` は ``DashboardData`` から項目IDごとの「済」を返す関数。
    ``None`` を渡すと判定を持たない表示モードになり、済／未のバッジも
    「N/M」の集計も出さない（常に全項目が済になる運用など、判定が情報を
    持たない下流向け）。
    """

    groups: Tuple[CatalogGroup, ...]
    lead: str
    complete_lead: str = ""
    heading: str = "AIにできること"
    subheading: str = "そのまま頼める言い方と、起きること"
    counter_label: str = "基本の流れ"
    progress: Optional[Callable[[DashboardData], Mapping[str, bool]]] = None


@dataclass(frozen=True)
class GuideView:
    """画面の読み方（折りたたみ）の文言。"""

    summary: str
    lead: str
    terms: Tuple[GuideTerm, ...]
    week_note: str
    next_step: str
    opened: bool = False


@dataclass(frozen=True)
class ExtraSection:
    """下流が追加する画面セクション。

    ``body`` は ``(data, output_dir) -> HTML文字列`` の関数で、``output_dir``
    は生成先ディレクトリ（相対リンクの基点）。返すHTMLのエスケープは下流の
    責務で、``kit.starter_web.render.escape_text`` を利用できる。``after`` は
    挿入位置のセクションID（``CORE_SECTION_IDS`` か、先に置いた追加
    セクションのID）。
    """

    id: str
    heading: str
    body: Callable[[DashboardData, Path], str]
    subheading: str = ""
    nav_label: str = ""
    after: str = "records"


@dataclass(frozen=True)
class AttentionExtra:
    """「要確認」へ下流が足す内容。

    ``count`` は上部の「要確認」件数へ加える数、``html`` は要確認セクションの
    末尾へ差し込むHTML（小見出しを含めて下流が組み立てる）。core は中身を
    解釈せず、下流が何を足したかを知らない。
    """

    count: int = 0
    html: str = ""


@dataclass(frozen=True)
class Presentation:
    """1画面分の文言・セクション構成。

    ``favicon`` は ``<link rel="icon">`` の ``href`` にそのまま入れる値。
    生成物は単一HTMLで配れることを前提にしているため、``data:`` URI だけを
    受け付ける。外部URL・相対パスは、生成物を渡した先で画像が出ないうえ、
    private運用では閲覧のたびに外部への通信が起きるため、構成時に落とす。
    ``None`` なら icon リンク自体を出さない（coreの既定＝ブラウザ任せ）。
    """

    mode_label: str
    privacy_note: str
    guide: Optional[GuideView] = None
    catalog: Optional[CatalogView] = None
    routine_note: str = ""
    extra_sections: Tuple[ExtraSection, ...] = field(default_factory=tuple)
    hidden_section_ids: Tuple[str, ...] = field(default_factory=tuple)
    recent_activity_days: Optional[int] = None
    pending_badge_mode: PendingBadgeMode = PendingBadgeMode.STATUS
    favicon: Optional[str] = None
    # 「要確認」へ下流が行と件数を足す点。core のセクションを増やさずに
    # 同じセクション内へ差し込めるようにする（レンダラーへ mode の分岐を
    # 足さないための拡張点）。**公開契約なので必ず末尾へ足す**：途中へ
    # 入れると、位置引数で組み立てている既存の呼び出しが1つずつずれる。
    attention_extra: Optional[Callable[[DashboardData, Path], AttentionExtra]] = None
    # 予定カード・3週間の見通しに出す教材リンクの範囲。**同じく末尾へ足す**。
    card_material_links: MaterialLinkScope = MaterialLinkScope.ALL
    # 「3週間の見通し」を1週間ずつ移動できる操作。private版で先行して
    # dogfoodingし、starter/demoの既定表示は変えない。**同じく末尾へ足す**。
    week_navigation_enabled: bool = False
    # 下流の信頼済み表示関数。HTMLのエスケープは下流の責務。
    test_event_extra: Optional[Callable[[TestEvent, DashboardData, Path], str]] = None

    def __post_init__(self) -> None:
        """既定値からの逸脱を、生成時ではなく構成時に落とす。

        知らないセクションIDを黙って無視すると、非表示にしたつもりの
        セクションが出続けたことに気付けないため、ここでエラーにする。
        """

        unknown = [
            section_id
            for section_id in self.hidden_section_ids
            if section_id not in CORE_SECTION_IDS
        ]
        if unknown:
            raise ValueError(
                "非表示にできないセクションIDです: {} (指定可能: {})".format(
                    "・".join(unknown), "・".join(CORE_SECTION_IDS)
                )
            )
        if self.recent_activity_days is not None and self.recent_activity_days < 1:
            raise ValueError(
                "recent_activity_days は1以上にしてください: {}".format(
                    self.recent_activity_days
                )
            )
        if self.favicon is not None:
            favicon = self.favicon.strip()
            if not favicon:
                raise ValueError(
                    "favicon は空文字にできません（出さないときは None にしてください）"
                )
            if not favicon.lower().startswith("data:"):
                raise ValueError(
                    "favicon は data: URI にしてください（外部URL・相対パスは"
                    "生成物が単体で表示できなくなるため受け付けません）: {}".format(
                        favicon
                    )
                )

    def recent_activities(self, data: DashboardData) -> List[ActivityItem]:
        """「最近の実績」に出す実績を返す。

        ``recent_activity_days`` を指定した場合は、実績のある直近N日ぶんを
        日単位で全件返す（同じ日の実績が件数で切れない）。既定は従来どおり
        ``DashboardData.recent_activities()``＝新しい順の12件。

        実績カード・「最近の学習時間」・「今週の教材」は、この1か所の結果を
        共有する。同じ抽出を別々に実装して表示と集計を食い違わせないため。
        """

        if self.recent_activity_days is None:
            return data.recent_activities()
        return data.recent_activities_by_days(self.recent_activity_days)

    @classmethod
    def for_mode(cls, mode: str) -> "Presentation":
        """modeに対応するcore既定を返す。

        既知の3モード以外では、starter形式を前提にした案内（用語ガイド・
        「AIにできること」）を出さない。coreの案内文が独自運用と一致すると
        推測しないためで、画面の本体は同じように描画する。
        """

        if mode == "demo":
            return cls(
                mode_label=MODE_LABELS["demo"],
                privacy_note=DEMO_PRIVACY_NOTE,
                guide=starter_guide(opened=True),
                catalog=starter_catalog(),
            )
        if mode == "starter":
            return cls(
                mode_label=MODE_LABELS["starter"],
                privacy_note=PRIVACY_NOTE,
                guide=starter_guide(opened=False),
                catalog=starter_catalog(),
                routine_note=ROUTINE_NOTE,
            )
        if mode == "private":
            return cls(
                mode_label=MODE_LABELS["private"],
                privacy_note=PRIVACY_NOTE,
                catalog=starter_catalog(),
                routine_note=ROUTINE_NOTE,
            )
        return cls(
            mode_label=DEFAULT_MODE_LABEL,
            privacy_note=PRIVACY_NOTE,
        )


MODE_LABELS = {
    "demo": "架空データ DEMO",
    "starter": "STARTER・端末内のみ",
    "private": "PRIVATE・端末内のみ",
}
DEFAULT_MODE_LABEL = "端末内のみ"

DEMO_PRIVACY_NOTE = "この画面の人物・予定・記録はすべて架空です。"
PRIVACY_NOTE = "個人情報を含むため、生成物を公開・共有しないでください。"

ROUTINE_NOTE = (
    "反復ルーティンは活動ログに同日の実績があれば実施状態を表示します。"
    "完了集計・要確認の対象にはせず、当日の教材は状況に応じて選びます。"
)

# 依頼文は examples/walkthrough.md と同じ言い方に揃える（契約テストで検査）。
CATALOG_BASICS = (
    CatalogItem(
        "setup",
        "初期設定をお願いします",
        "学習者・予定・実績・テスト・教材台帳が workspace に用意されます。",
    ),
    CatalogItem(
        "plan",
        "来週の計画を作ってください",
        "使える時間を聞いたうえで、予定の行が「予定」の状態で追加されます。",
    ),
    CatalogItem(
        "worksheet",
        "月曜の算数ワークシートを作ってください",
        "印刷用HTMLが materials/ にでき、予定へ教材リンクが入ります。",
    ),
    CatalogItem(
        "record",
        "月曜のたしざん、15分で実施、8問中6問正解でした",
        "予定の状態と活動ログの1行が、両方そろって更新されます。",
    ),
    CatalogItem(
        "review",
        "今週を振り返って、来週の計画を作ってください",
        "実施率の集計と翌週の調整案が出て、合意した分だけ反映されます。",
    ),
    CatalogItem(
        "dashboard",
        "ダッシュボードを更新してください",
        "この画面が最新のMarkdownから作り直されます。",
    ),
)

CATALOG_ADVANCED = (
    CatalogItem(
        "learner-add",
        "学習者をもう1人追加してください",
        "学習者台帳へ新しいIDを追加し、既存の学習者・予定・実績は変更しません。",
    ),
    CatalogItem(
        "shared-material",
        "2人で使う共通教材を追加してください",
        "共通の教材ファイルと教材台帳の行がセットで追加されます。",
    ),
    CatalogItem(
        "personal-revenge",
        "報告済みの間違いから、学習者固有のリベンジWSを作ってください",
        "報告済みの誤答だけを根拠に類題を作り、本人専用の教材として登録します。",
    ),
    CatalogItem(
        "one-off-test",
        "単体テストを1回追加し、問題も作ってください",
        "確定した1回だけをテスト台帳へ追加します。結果は「12分で10点中8点でした」"
        "と報告すると、同じ実施回と成績内訳へ記録されます。",
    ),
    CatalogItem(
        "recurring-test",
        "毎週の確認テストを追加してください",
        "定期テストの定義と確定した回だけを追加します。結果は「15分で10点中7点でした」"
        "と報告すると、同じ実施回と成績内訳へ記録されます。",
    ),
    CatalogItem(
        "migrate",
        "いま使っている記録を移行してください",
        "元の記録の棚卸しと対応づけを承認したうえで、直近分だけを正本へ取り込みます。"
        "投入前へ戻せるスナップショットも作ります。",
    ),
)

CATALOG_EXTRAS = (
    CatalogItem(
        "routine",
        "毎朝10分の計算を習慣にしたい",
        "日付行を増やさず、反復ルーティンの1行として登録されます。",
    ),
    CatalogItem(
        "test-event",
        "9月14日にテストを受けます",
        "日付だけ決まったテスト予定を登録し、3か月前から表示します。"
        "問題作成から結果登録までの練習は発展編で試せます。",
    ),
)

GUIDE_TERMS = (
    GuideTerm(
        "要確認",
        "未実施・一部実施・繰越など、そのままにすると流れてしまう枠です。"
        "上の指標にも件数が出ます。",
    ),
    GuideTerm(
        "教材未準備",
        "今週・来週の予定のうち、.html や .pdf の教材リンクがまだ無い枠です。"
        "当日ではなく前もって気付くために表示します。",
    ),
    GuideTerm(
        "繰越",
        "できなかった予定を別の日へ移した状態です。元の行は消さず、"
        "繰越先の日付を残します。",
    ),
    GuideTerm(
        "反復ルーティン",
        "毎日・特定曜日の習慣です。日ごとの予定行は作らず1行で書き、"
        "表示のときだけ各日へ展開します。完了数と要確認には数えません。",
    ),
    GuideTerm(
        "テスト実施回",
        "単体・定期テストの1回分です。予定と報告後の結果を同じ行で管理し、"
        "今後の予定または最近の実績へ表示します。",
    ),
)

GUIDE_LEAD = (
    "この画面は schedule.md と activity-log.md から生成した"
    "読み取り専用のビューです。ここでは変更できません。"
)
GUIDE_WEEK_NOTE = (
    "「3週間の見通し」は、左端の色が予定の種別、右端の記号が状態です。"
    "凡例は表のすぐ上にあります。"
)


def starter_guide(*, opened: bool) -> GuideView:
    """starter形式の用語ガイド。

    ``opened`` は新規利用者向けに展開するかどうか。ここには運用手順を書かない。
    手順の正本は ``docs/AI-OPERATIONS.md`` と ``examples/walkthrough.md`` で、
    この節はWeb画面固有の用語だけを扱う。
    """

    if opened:
        summary = "はじめての方へ — この画面の読み方"
        next_step = (
            "AIアシスタントに「チュートリアルを始めて」と頼むと、"
            "初期設定からこの画面を自分の記録で出すところまで、"
            "1ステップずつ一緒に進みます。"
        )
    else:
        summary = "この画面の読み方"
        next_step = (
            "直したいところがあれば、この画面ではなく schedule.md と "
            "activity-log.md を更新して、生成コマンドを実行し直してください。"
        )
    return GuideView(
        summary=summary,
        lead=GUIDE_LEAD,
        terms=GUIDE_TERMS,
        week_note=GUIDE_WEEK_NOTE,
        next_step=next_step,
        opened=opened,
    )


def starter_catalog() -> CatalogView:
    """starter形式の「AIにできること」。

    依頼文は利用者がそのまま言える例で、手順の正本ではない。順序と条件の
    正本は ``docs/AI-OPERATIONS.md``、通し手順は ``examples/walkthrough.md``。
    依頼文が正本から外れないことは ``tests/test_kit_starter_web.py`` の
    契約テストが検査する。
    """

    return CatalogView(
        groups=(
            CatalogGroup(items=CATALOG_BASICS, counted=True),
            CatalogGroup(
                items=CATALOG_ADVANCED,
                heading="発展チュートリアル",
                note="必要な項目だけ試せます（実施状況は集計しません）",
                judged=False,
            ),
            CatalogGroup(
                items=CATALOG_EXTRAS,
                heading="そのほか頼めること",
                note="必要になったときだけで構いません",
            ),
        ),
        lead=(
            "「済」はこの記録で実際に行われたもの、「未」はまだのものです。"
            "はじめから順に試すなら「チュートリアルを始めて」と頼んでください。"
        ),
        complete_lead=(
            "基本の流れはひととおり記録に残っています。"
            "同じ言い方で、そのまま続けられます。"
        ),
        progress=catalog_progress,
    )


def catalog_progress(data: DashboardData) -> Dict[str, bool]:
    """starterカタログの項目IDごとに「済」かどうかを判定する。

    説明文に書いた完了条件より緩い判定をしない。教材は実在ファイルを求め、
    実績は対応する予定が実施済みへ変わっていることまで確認する。
    """

    dated = [item for item in data.schedule if item.kind == ScheduleKind.DATED]
    has_worksheet = any(
        is_worksheet_file(link) for item in dated for link in item.materials
    )
    has_recorded_result = any(
        activity_matches_schedule(item, activity)
        for item in dated
        if item.status in RECORDED_STATUSES
        for activity in data.activities
    )
    return {
        # 生成できている時点でworkspaceは存在する。
        "setup": True,
        "plan": bool(dated),
        "worksheet": has_worksheet,
        "record": has_recorded_result,
        "review": data.has_weekly_review,
        "dashboard": True,
        "routine": any(
            item.kind == ScheduleKind.ROUTINE for item in data.schedule
        ),
        "test-event": bool(data.test_events)
        or any(activity.is_test for activity in data.activities),
    }


def is_worksheet_file(link: MaterialLink) -> bool:
    """教材リンクが、実在するワークシートHTMLを指しているかを返す。

    リンクを書いただけ・拡張子が合っているだけでは作成済みとみなさない。
    読みもの等（``material-type="reference"``）もワークシートには数えない。
    """

    target = link.target.split("#", 1)[0]
    if urlsplit(target).scheme in {"http", "https"}:
        return False
    path = Path(target)
    if path.suffix.lower() not in {".html", ".htm"} or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return material_type(text) == "worksheet"
