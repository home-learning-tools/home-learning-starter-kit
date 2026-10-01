"""starter workspaceの学習者・教材・テスト台帳契約。"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .markdown import markdown_tables, resolve_links, strip_markdown
from .model import MaterialLink, SourceRef
from .test_events import UNSCHEDULED_TEST_DATE


LEARNER_HEADER = ("学習者ID", "呼び名", "学年目安", "状態", "メモ")
MATERIAL_HEADER = (
    "教材ID",
    "対象",
    "学習者ID",
    "教科",
    "種別",
    "タイトル",
    "ファイル",
    "出典",
    "状態",
)
TEST_DEFINITION_HEADER = (
    "テストID",
    "テスト名",
    "方式",
    "種別",
    "教科",
    "周期",
    "状態",
    "備考",
)
TEST_ATTEMPT_HEADER = (
    "実施ID",
    "テストID",
    "回・版",
    "受験区分",
    "日付",
    "学習者ID",
    "学習者",
    "状態",
    "問題",
    "所要時間",
    "結果",
    "次アクション",
    "繰越先",
)
TEST_SCORE_HEADER = (
    "実施ID",
    "教科・領域",
    "得点",
    "満点",
    "平均点",
    "偏差値",
    "順位",
    "受験者数",
    "判定",
    "備考",
)

ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LEARNER_STATES = {"利用中", "休止"}
MATERIAL_AUDIENCES = {"共通", "個別"}
MATERIAL_KINDS = {"worksheet", "reference", "pdf"}
MATERIAL_ORIGINS = {"自作", "家庭保管"}
MATERIAL_STATES = {"利用中", "保管"}
TEST_MODES = {"単体", "定期"}
TEST_STATES = {"利用中", "終了"}
ATTEMPT_STATES = {
    "予定",
    "準備済み",
    "完了",
    "一部実施",
    "未実施",
    "実施有無未確認",
    "繰越",
}
PLANNED_ATTEMPT_STATES = {"予定", "準備済み"}
DASH = {"—", "-"}


@dataclass(frozen=True)
class LearnerRecord:
    learner_id: str
    name: str
    grade: str
    state: str
    note: str
    source: SourceRef


@dataclass(frozen=True)
class MaterialRecord:
    material_id: str
    audience: str
    learner_id: str
    subject: str
    kind: str
    title: str
    material: MaterialLink
    origin: str
    state: str
    source: SourceRef


@dataclass(frozen=True)
class TestDefinitionRecord:
    test_id: str
    title: str
    mode: str
    category: str
    subject: str
    cadence: str
    state: str
    note: str
    source: SourceRef


@dataclass(frozen=True)
class TestAttemptRecord:
    attempt_id: str
    test_id: str
    edition: str
    level: str
    date_text: str
    learner_id: str
    learner_name: str
    state: str
    materials: List[MaterialLink]
    duration: str
    result: str
    next_action: str
    deferred_to: str
    source: SourceRef


@dataclass(frozen=True)
class TestScoreRecord:
    attempt_id: str
    area: str
    score: str
    maximum: str
    average: str
    deviation: str
    rank: str
    participants: str
    judgment: str
    note: str
    source: SourceRef


@dataclass(frozen=True)
class WorkspaceRecords:
    learners: List[LearnerRecord]
    materials: List[MaterialRecord]
    test_definitions: List[TestDefinitionRecord]
    test_attempts: List[TestAttemptRecord]
    test_scores: List[TestScoreRecord]


def _table_rows(path: Path, expected: Sequence[str]) -> List[Tuple[int, List[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = []
    for header_line, header, rows in markdown_tables(lines):
        if tuple(strip_markdown(cell) for cell in header) != tuple(expected):
            continue
        matches.append((header_line, rows))
    if not matches:
        raise ValueError("{}に所定の表がありません: {}".format(path.name, path))
    if len(matches) > 1:
        raise ValueError(
            "{}に同じ正本表が複数あります: {}".format(path.name, path)
        )
    header_line, rows = matches[0]
    return [
        (header_line + 2 + offset, [cell.strip() for cell in row])
        for offset, row in enumerate(rows)
    ]


def _source(path: Path, line: int) -> SourceRef:
    return SourceRef(path=str(path), line=line)


def _require_id(value: str, label: str, source: SourceRef) -> None:
    if not ID_RE.fullmatch(value):
        raise ValueError(
            "{}は半角小文字・数字・ハイフンのIDにしてください: {} L{}".format(
                label, source.path, source.line
            )
        )


def _unique(values: Sequence[Tuple[str, SourceRef]], label: str) -> None:
    seen: Dict[str, SourceRef] = {}
    for value, source in values:
        if value in seen:
            raise ValueError(
                "{} {} が重複しています: {} L{}".format(
                    label, value, source.path, source.line
                )
            )
        seen[value] = source


def load_workspace_records(workspace: Path) -> WorkspaceRecords:
    """3つの台帳を読み、相互参照を含む不変条件を検証する。"""

    workspace = workspace.resolve()
    learners_path = workspace / "learners.md"
    materials_path = workspace / "materials" / "index.md"
    tests_path = workspace / "tests.md"

    learners = []
    for line, row in _table_rows(learners_path, LEARNER_HEADER):
        source = _source(learners_path, line)
        learner_id, name, grade, state, note = [
            strip_markdown(cell) for cell in row
        ]
        _require_id(learner_id, "学習者ID", source)
        if not name:
            raise ValueError("呼び名が空です: {} L{}".format(source.path, line))
        if state not in LEARNER_STATES:
            raise ValueError("学習者の状態が不正です: {} L{}".format(source.path, line))
        learners.append(
            LearnerRecord(learner_id, name, grade, state, note, source)
        )
    _unique([(item.learner_id, item.source) for item in learners], "学習者ID")
    learner_by_id = {item.learner_id: item for item in learners}

    materials = []
    for line, row in _table_rows(materials_path, MATERIAL_HEADER):
        source = _source(materials_path, line)
        material_id, audience, learner_id, subject, kind, title = [
            strip_markdown(cell) for cell in row[:6]
        ]
        link = row[6]
        origin, state = [strip_markdown(cell) for cell in row[7:]]
        _require_id(material_id, "教材ID", source)
        if not subject or not title:
            raise ValueError(
                "教材の教科とタイトルは空にできません: {} L{}".format(
                    source.path, line
                )
            )
        if audience not in MATERIAL_AUDIENCES or kind not in MATERIAL_KINDS:
            raise ValueError(
                "教材の対象または種別が不正です: {} L{}".format(
                    source.path, line
                )
            )
        if origin not in MATERIAL_ORIGINS or state not in MATERIAL_STATES:
            raise ValueError(
                "教材の出典または状態が不正です: {} L{}".format(
                    source.path, line
                )
            )
        if audience == "共通" and learner_id not in DASH:
            raise ValueError(
                "共通教材の学習者IDは—にしてください: {} L{}".format(
                    source.path, line
                )
            )
        if audience == "個別" and learner_id not in learner_by_id:
            raise ValueError(
                "個別教材の学習者IDが台帳にありません: {} L{}".format(
                    source.path, line
                )
            )
        links = resolve_links(link, materials_path, workspace, allow_external=False)
        if len(links) != 1:
            raise ValueError(
                "教材のファイルはリンク1件にしてください: {} L{}".format(
                    source.path, line
                )
            )
        target = Path(links[0].target.split("#", 1)[0]).resolve()
        expected_parent = (
            workspace / "materials" / "shared"
            if audience == "共通"
            else workspace / "materials" / "learners" / learner_id
        ).resolve()
        if expected_parent not in target.parents:
            raise ValueError(
                "教材の対象と保存先が一致しません: {} L{}".format(
                    source.path, line
                )
            )
        if target.suffix.lower() not in {".html", ".htm", ".pdf"}:
            raise ValueError(
                "教材ファイルはHTMLまたはPDFにしてください: {} L{}".format(
                    source.path, line
                )
            )
        if target.suffix.lower() == ".pdf" and kind != "pdf":
            raise ValueError("PDF教材の種別はpdfにしてください: {} L{}".format(source.path, line))
        if target.suffix.lower() in {".html", ".htm"} and kind == "pdf":
            raise ValueError("HTML教材の種別をpdfにはできません: {} L{}".format(source.path, line))
        materials.append(
            MaterialRecord(
                material_id,
                audience,
                learner_id,
                subject,
                kind,
                title,
                links[0],
                origin,
                state,
                source,
            )
        )
    _unique([(item.material_id, item.source) for item in materials], "教材ID")
    _unique(
        [
            (item.material.target.split("#", 1)[0], item.source)
            for item in materials
        ],
        "教材ファイル",
    )

    definitions = []
    for line, row in _table_rows(tests_path, TEST_DEFINITION_HEADER):
        source = _source(tests_path, line)
        test_id, title, mode, category, subject, cadence, state, note = [
            strip_markdown(cell) for cell in row
        ]
        _require_id(test_id, "テストID", source)
        if not title:
            raise ValueError(
                "テスト名は空にできません: {} L{}".format(source.path, line)
            )
        if not category or not subject:
            raise ValueError(
                "テストの種別・教科は空にできません: {} L{}".format(
                    source.path, line
                )
            )
        if mode not in TEST_MODES or state not in TEST_STATES:
            raise ValueError("テストの方式または状態が不正です: {} L{}".format(source.path, line))
        if mode == "単体" and cadence not in DASH:
            raise ValueError("単体テストの周期は—にしてください: {} L{}".format(source.path, line))
        if mode == "定期" and cadence in DASH:
            raise ValueError("定期テストには周期を記載してください: {} L{}".format(source.path, line))
        definitions.append(
            TestDefinitionRecord(
                test_id,
                title,
                mode,
                category,
                subject,
                cadence,
                state,
                note,
                source,
            )
        )
    _unique([(item.test_id, item.source) for item in definitions], "テストID")
    definition_by_id = {item.test_id: item for item in definitions}

    attempts = []
    for line, row in _table_rows(tests_path, TEST_ATTEMPT_HEADER):
        source = _source(tests_path, line)
        (
            attempt_id,
            test_id,
            edition,
            level,
            date_text,
            learner_id,
            learner_name,
            state,
        ) = [
            strip_markdown(cell) for cell in row[:8]
        ]
        problem = row[8]
        duration, result, next_action, deferred_to = [
            strip_markdown(cell) for cell in row[9:]
        ]
        _require_id(attempt_id, "実施ID", source)
        if test_id not in definition_by_id:
            raise ValueError(
                "実施回のテストIDが定義にありません: {} L{}".format(
                    source.path, line
                )
            )
        definition = definition_by_id[test_id]
        learner = learner_by_id.get(learner_id)
        if learner is None or learner.name != learner_name:
            raise ValueError(
                "実施回の学習者が台帳と一致しません: {} L{}".format(
                    source.path, line
                )
            )
        if state not in ATTEMPT_STATES:
            raise ValueError("実施回の状態が不正です: {} L{}".format(source.path, line))
        if date_text == "日程未定":
            if state not in PLANNED_ATTEMPT_STATES:
                raise ValueError("日程未定の実施回は予定または準備済みにしてください: {} L{}".format(source.path, line))
        else:
            try:
                date.fromisoformat(date_text)
            except ValueError as exc:
                raise ValueError("実施回の日付はYYYY-MM-DDまたは日程未定です: {} L{}".format(source.path, line)) from exc
        links = (
            []
            if problem in DASH
            else resolve_links(
                problem, tests_path, workspace, allow_external=False
            )
        )
        if not links and problem not in DASH:
            raise ValueError(
                "テスト問題は—または教材リンクにしてください: {} L{}".format(
                    source.path, line
                )
            )
        for link in links:
            target = link.target.split("#", 1)[0]
            registered = next(
                (
                    item
                    for item in materials
                    if item.material.target.split("#", 1)[0] == target
                ),
                None,
            )
            if registered is None:
                raise ValueError("テスト問題が教材台帳に登録されていません: {} L{}".format(source.path, line))
            if registered.audience == "個別" and registered.learner_id != learner_id:
                raise ValueError("他の学習者の個別教材を参照しています: {} L{}".format(source.path, line))
        if state in PLANNED_ATTEMPT_STATES:
            if definition.state == "終了":
                raise ValueError(
                    "終了したテストへ新しい予定は追加できません: {} L{}".format(
                        source.path, line
                    )
                )
            if any(
                value not in DASH
                for value in (duration, result, next_action, deferred_to)
            ):
                raise ValueError("実施前の結果欄は—にしてください: {} L{}".format(source.path, line))
        else:
            if not duration or duration in DASH or not result or result in DASH:
                raise ValueError("実施後は所要時間と結果を記載してください: {} L{}".format(source.path, line))
            if duration != "未測定" and not re.fullmatch(r"\d+分", duration):
                raise ValueError(
                    "所要時間はN分または未測定にしてください: {} L{}".format(
                        source.path, line
                    )
                )
        if state == "繰越":
            if deferred_to in DASH:
                raise ValueError(
                    "繰越した実施回には繰越先の日付または日程未定を記載して"
                    "ください: {} L{}".format(source.path, line)
                )
            if deferred_to != UNSCHEDULED_TEST_DATE:
                try:
                    deferred_date = date.fromisoformat(deferred_to)
                except ValueError as exc:
                    raise ValueError(
                        "繰越先はYYYY-MM-DD・日程未定・—のいずれかです: "
                        "{} L{}".format(source.path, line)
                    ) from exc
                if deferred_date <= date.fromisoformat(date_text):
                    raise ValueError(
                        "繰越先は元の実施日より後にしてください: {} L{}".format(
                            source.path, line
                        )
                    )
        elif deferred_to not in DASH:
            raise ValueError(
                "繰越以外の実施回の繰越先は—にしてください: {} L{}".format(
                    source.path, line
                )
            )
        attempts.append(
            TestAttemptRecord(
                attempt_id,
                test_id,
                edition,
                level,
                date_text,
                learner_id,
                learner_name,
                state,
                links,
                duration,
                result,
                next_action,
                deferred_to,
                source,
            )
        )
    _unique([(item.attempt_id, item.source) for item in attempts], "実施ID")
    attempt_by_id = {item.attempt_id: item for item in attempts}

    scores = []
    for line, row in _table_rows(tests_path, TEST_SCORE_HEADER):
        source = _source(tests_path, line)
        (
            attempt_id,
            area,
            score,
            maximum,
            average,
            deviation,
            rank,
            participants,
            judgment,
            note,
        ) = [strip_markdown(cell) for cell in row]
        attempt = attempt_by_id.get(attempt_id)
        if attempt is None:
            raise ValueError(
                "成績内訳の実施IDが実施回にありません: {} L{}".format(
                    source.path, line
                )
            )
        if attempt.state in PLANNED_ATTEMPT_STATES:
            raise ValueError(
                "実施前の回へ成績内訳は記録できません: {} L{}".format(
                    source.path, line
                )
            )
        if not area:
            raise ValueError(
                "成績内訳の教科・領域は空にできません: {} L{}".format(
                    source.path, line
                )
            )
        if (score in DASH) != (maximum in DASH):
            raise ValueError(
                "得点と満点は両方記載するか両方—にしてください: {} L{}".format(
                    source.path, line
                )
            )
        if score not in DASH:
            try:
                score_value = float(score)
                maximum_value = float(maximum)
            except ValueError as exc:
                raise ValueError(
                    "得点と満点は数値にしてください: {} L{}".format(
                        source.path, line
                    )
                ) from exc
            if not 0 <= score_value <= maximum_value:
                raise ValueError(
                    "得点は0以上満点以下にしてください: {} L{}".format(
                        source.path, line
                    )
                )
        for label, value in (("平均点", average), ("偏差値", deviation)):
            if value in DASH:
                continue
            try:
                float(value)
            except ValueError as exc:
                raise ValueError(
                    "{}は数値または—にしてください: {} L{}".format(
                        label, source.path, line
                    )
                ) from exc
        numeric_ranks = {}
        for label, value in (("順位", rank), ("受験者数", participants)):
            if value in DASH:
                continue
            try:
                numeric_ranks[label] = int(value)
            except ValueError as exc:
                raise ValueError(
                    "{}は整数または—にしてください: {} L{}".format(
                        label, source.path, line
                    )
                ) from exc
            if numeric_ranks[label] <= 0:
                raise ValueError(
                    "{}は1以上にしてください: {} L{}".format(
                        label, source.path, line
                    )
                )
        if (
            "順位" in numeric_ranks
            and "受験者数" in numeric_ranks
            and numeric_ranks["順位"] > numeric_ranks["受験者数"]
        ):
            raise ValueError(
                "順位は受験者数以下にしてください: {} L{}".format(
                    source.path, line
                )
            )
        metrics = (
            score,
            maximum,
            average,
            deviation,
            rank,
            participants,
            judgment,
            note,
        )
        if all(value in DASH for value in metrics):
            raise ValueError(
                "成績内訳には少なくとも1つの値を記載してください: {} L{}".format(
                    source.path, line
                )
            )
        scores.append(
            TestScoreRecord(
                attempt_id,
                area,
                score,
                maximum,
                average,
                deviation,
                rank,
                participants,
                judgment,
                note,
                source,
            )
        )
    _unique(
        [
            ("{}:{}".format(item.attempt_id, item.area), item.source)
            for item in scores
        ],
        "実施IDと教科・領域",
    )
    return WorkspaceRecords(learners, materials, definitions, attempts, scores)


def optional_workspace_records(workspace: Path) -> Optional[WorkspaceRecords]:
    """新契約3ファイルがすべて無い旧workspaceだけ互換入力として許可する。"""

    paths = [
        workspace / "learners.md",
        workspace / "materials" / "index.md",
        workspace / "tests.md",
    ]
    present = [path.is_file() for path in paths]
    if not any(present):
        return None
    if not all(present):
        missing = ", ".join(
            path.name for path, exists in zip(paths, present) if not exists
        )
        raise ValueError(
            "新workspace契約のファイルが一部だけあります。不足: {}".format(
                missing
            )
        )
    return load_workspace_records(workspace)
