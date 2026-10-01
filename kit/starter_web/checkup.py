"""リポジトリ構造とstarter workspaceの機械検証。

docs/AI-OPERATIONS.md（AI運用ルール）の文章ルールのうち、機械的に検査できる
次の項目を検証する。

- 許可外のトップレベルディレクトリ・ファイル（公開ツリーのみ）
- 学習者・予定・実績・テスト・教材台帳の必須構成と相互参照
- AI運用レイヤーの必須ファイルの存在
- ``workspace/``・``dist/`` のGit除外（Gitリポジトリでは ``git check-ignore`` で
  実効状態を検証し、Git外では ``.gitignore`` の静的検査へフォールバック）
- starter workspaceの入力契約（列・状態・学習者ID・列数・表の列構成）
- 教材リンクの安全性（外部URL禁止・workspace内の実在ファイルのみ）
- ワークシートHTMLの最小構造（自己完結・A4印刷・問題/解答セクション・
  形式に応じた解答配置）

公開ツリー（抽出後リポジトリ）と開発元ツリーの両方で動く。開発元ツリーは
``kit/packaging/public-manifest.json`` の有無で判定し、公開対象の領域だけを
検査する。
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urlsplit

from .adapters import StarterMarkdownAdapter
# 種別宣言の読み取りは共通モジュールが正本（renderからも参照する）。
from .materials import MATERIAL_TYPES, material_type
from .worksheet_layout import WorksheetLayout
from .markdown import (
    extract_links,
    is_external_link,
    is_separator_row,
    is_within,
    link_target_path,
    split_table_row,
    strip_markdown,
)
from .recurrence import STARTER_ROUTINE_HEADER
from .shared_font_assets import verify_manifest, worksheet_references
from .workspace_contract import (
    LEARNER_HEADER,
    MATERIAL_HEADER,
    TEST_ATTEMPT_HEADER,
    TEST_DEFINITION_HEADER,
    TEST_SCORE_HEADER,
    load_workspace_records,
)

PRIVATE_SOURCE_MARKER = Path("kit") / "packaging" / "public-manifest.json"

# 公開ツリーで許可するトップレベル項目。追加が必要なときは、AI運用ルールに
# 従い利用者と合意のうえ、この一覧・文書・テストを同じ変更で更新する。
PUBLIC_TOP_LEVEL_ALLOWED = {
    ".git",
    ".github",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "LICENSE-CONTENT.md",
    "README.md",
    "dist",
    "docs",
    "examples",
    "kit",
    "package.json",
    "templates",
    "tests",
    "workspace",
}
# 開発環境の生成物としてトップレベルに存在してよい項目（検査対象外）。
TOLERATED_DEV_ENTRIES = {
    ".DS_Store",
    ".idea",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
    "package-lock.json",
    "venv",
}
# 走査中に中へ入らないディレクトリ。
SKIPPED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "workspace",
}

CANONICAL_BASENAMES = (
    "learners.md",
    "schedule.md",
    "activity-log.md",
    "tests.md",
)

PUBLIC_REQUIRED_FILES = (
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "docs/AI-OPERATIONS.md",
    "docs/EXTENDING.md",
    "docs/WORKSPACE-CONTRACT.md",
    "templates/learners.md",
    "templates/schedule.md",
    "templates/activity-log.md",
    "templates/tests.md",
    "templates/materials/index.md",
    "templates/worksheet.html",
    "templates/worksheet-red-sheet.html",
    "examples/demo/schedule.md",
    "examples/demo/activity-log.md",
    "examples/demo/learners.md",
    "examples/demo/tests.md",
    "examples/demo/materials/index.md",
)
SOURCE_REQUIRED_FILES = (
    "kit/ai/AI-OPERATIONS.md",
    "kit/ai/EXTENDING.md",
    "kit/ai/WORKSPACE-CONTRACT.md",
    "kit/ai/public-CLAUDE.md",
    "kit/ai/public-AGENTS.md",
    "kit/templates/schedule.md",
    "kit/templates/activity-log.md",
    "kit/templates/learners.md",
    "kit/templates/tests.md",
    "kit/templates/materials/index.md",
    "kit/templates/worksheet.html",
)

KNOWN_SCHEDULE_HEADERS = (
    tuple(StarterMarkdownAdapter.SCHEDULE_HEADER),
    tuple(STARTER_ROUTINE_HEADER),
    tuple(StarterMarkdownAdapter.TEST_EVENT_HEADER),
)
KNOWN_ACTIVITY_HEADERS = (tuple(StarterMarkdownAdapter.ACTIVITY_HEADER),)
KNOWN_LEARNER_HEADERS = (tuple(LEARNER_HEADER),)
KNOWN_MATERIAL_HEADERS = (tuple(MATERIAL_HEADER),)
KNOWN_TEST_HEADERS = (
    tuple(TEST_DEFINITION_HEADER),
    tuple(TEST_ATTEMPT_HEADER),
    tuple(TEST_SCORE_HEADER),
)

DOCTYPE_RE = re.compile(r"<!doctype\s+html", re.IGNORECASE)
HTML_LANG_RE = re.compile(r"<html[^>]*\slang\s*=", re.IGNORECASE)
CHARSET_RE = re.compile(r"<meta[^>]*charset\s*=", re.IGNORECASE)
TITLE_RE = re.compile(r"<title>\s*([^<]+?)\s*</title>", re.IGNORECASE)
# 引用符の有無にかかわらず、src/hrefでの外部URL読み込みを検出する。
EXTERNAL_RESOURCE_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", re.IGNORECASE
)
EXTERNAL_CSS_RE = re.compile(
    r"""(?:@import\b|url\(\s*["']?(?:https?:)?//)""", re.IGNORECASE
)
ABSOLUTE_REFERENCE_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']?(?:file://|/(?!/))""", re.IGNORECASE
)
PAGE_A4_RE = re.compile(r"@page\b[^{]*\{[^}]*size\s*:\s*A4", re.IGNORECASE)
PROBLEMS_SECTION_RE = re.compile(r"""class\s*=\s*["'][^"']*\bproblems\b""")
ANSWERS_SECTION_RE = re.compile(r"""class\s*=\s*["'][^"']*\banswers\b""")
PAGE_BREAK_DECLARATION_RE = re.compile(
    r"(?:page-break-before\s*:\s*always|break-before\s*:\s*page)", re.IGNORECASE
)
CSS_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")


class _StyleContents(HTMLParser):
    """HTML本文やscriptの文字列をCSS規則として走査しない。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_style = False
        self.styles: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)


def _answers_block_has_page_break(text: str) -> bool:
    """解答セレクタ（.answers）のCSSブロック内に改ページ指定があるか。"""

    parser = _StyleContents()
    parser.feed(text)
    parser.close()
    for style in parser.styles:
        for selector, body in CSS_BLOCK_RE.findall(CSS_COMMENT_RE.sub("", style)):
            if ".answers" in selector and PAGE_BREAK_DECLARATION_RE.search(body):
                return True
    return False


def is_private_source(root: Path) -> bool:
    """開発元（抽出前）ツリーかどうかを判定する。"""

    return (root / PRIVATE_SOURCE_MARKER).is_file()


def check_worksheet(path: Path, strict: Optional[bool] = None) -> List[str]:
    """HTML教材の最小構造（自己完結・種別宣言・必須要素）を検査する。

    strict=None の場合、種別宣言（meta name="material-type"）を必須とし、
    worksheet宣言のファイルへ厳格検査（A4・問題/解答構造・解答配置）を行う。
    解答配置は未指定なら別ページ、明示したred-sheetは同一紙面の構造を検査する。
    reference宣言は基本検査のみ。宣言なし・不明な種別は
    検証エラーになるため、宣言の削除で厳格検査を回避することはできない。
    strict=True は雛形（templates/worksheet.html）用で、worksheet宣言と
    厳格構造の両方を要求する。
    """

    issues: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return ["{}: 読めません（UTF-8のHTMLにしてください）: {}".format(path, exc)]

    try:
        worksheet_references(path, text)
    except (OSError, UnicodeError, ValueError) as exc:
        issues.append("{}: 共有フォント参照が不正です: {}".format(path, exc))

    if not DOCTYPE_RE.search(text):
        issues.append("{}: <!doctype html> がありません".format(path))
    if not HTML_LANG_RE.search(text):
        issues.append("{}: <html lang=...> がありません".format(path))
    if not CHARSET_RE.search(text):
        issues.append("{}: <meta charset=...> がありません".format(path))
    title = TITLE_RE.search(text)
    if not title or not title.group(1).strip():
        issues.append("{}: 空でない <title> がありません".format(path))
    if EXTERNAL_RESOURCE_RE.search(text) or EXTERNAL_CSS_RE.search(text):
        issues.append(
            "{}: 外部URLの読み込み（src/href/@import/url()）があります。"
            "ワークシートは1ファイルで自己完結させてください".format(path)
        )
    if ABSOLUTE_REFERENCE_RE.search(text):
        issues.append(
            "{}: 絶対パス・file://の参照があります。"
            "workspace内の相対パスだけを使ってください".format(path)
        )

    declared = material_type(text)
    layout = WorksheetLayout(text)
    issues.extend("{}: {}".format(path, error) for error in layout.errors)
    if layout.declarations and declared != "worksheet":
        issues.append("{}: answer-layout宣言はworksheet教材だけに指定できます".format(path))
    if strict is None:
        if declared is None or declared not in MATERIAL_TYPES:
            issues.append(
                '{}: 種別宣言 <meta name="material-type" content='
                '"worksheet|reference"> がありません（現在: {}）。'
                "ワークシートはworksheet、読みもの等はreferenceを宣言して"
                "ください".format(path, declared if declared else "宣言なし")
            )
            return issues
        strict = declared == "worksheet"
    if strict:
        if declared != "worksheet":
            issues.append(
                '{}: <meta name="material-type" content="worksheet"> が'
                "ありません。templates/worksheet.html をコピーして作成して"
                "ください".format(path)
            )
        if not PAGE_A4_RE.search(text):
            issues.append(
                "{}: A4印刷設定（@page {{ size: A4; ... }}）がありません".format(path)
            )
        if layout.layout == "red-sheet":
            issues.extend("{}: {}".format(path, error) for error in layout.red_sheet_errors())
            return issues
        if not PROBLEMS_SECTION_RE.search(text):
            issues.append(
                '{}: 問題セクション（class="problems"）がありません'.format(path)
            )
        if not ANSWERS_SECTION_RE.search(text):
            issues.append(
                '{}: 解答セクション（class="answers"）がありません'.format(path)
            )
        elif not _answers_block_has_page_break(text):
            issues.append(
                "{}: 解答セレクタ（.answers）のCSSブロック内に改ページ指定"
                "（page-break-before: always / break-before: page）が"
                "ありません。問題と解答を同じ印刷ページに出さないで"
                "ください".format(path)
            )
    return issues


def _check_markdown_tables(
    md_path: Path,
    workspace: Path,
    known_headers: Sequence[Sequence[str]],
) -> List[str]:
    """表の列構成・列数と、表中リンクの安全性・実在を検査する。

    markdown_tables() は列数不一致の行を黙って読み飛ばすため、ここでは
    生テキストに対して「壊れた行が1行でもあれば検証エラー」を保証する。
    """

    issues: List[str] = []
    try:
        lines = md_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return ["{}: 読めません: {}".format(md_path, exc)]

    known = {tuple(header) for header in known_headers}
    index = 0
    while index + 1 < len(lines):
        header = split_table_row(lines[index])
        separator = split_table_row(lines[index + 1])
        if not header or not is_separator_row(separator):
            index += 1
            continue
        normalized = tuple(strip_markdown(cell) for cell in header)
        header_line = index + 1
        if normalized not in known:
            issues.append(
                "{}:{}: 所定の列構成と一致しない表があります。"
                "この表はダッシュボードへ表示されません".format(md_path, header_line)
            )
        row_index = index + 2
        while row_index < len(lines):
            cells = split_table_row(lines[row_index])
            if not cells or is_separator_row(cells):
                break
            if len(cells) != len(header):
                issues.append(
                    "{}:{}: 列数が表の見出し（{}列）と一致しません（{}列）。"
                    "この行はダッシュボードから黙って消えます".format(
                        md_path, row_index + 1, len(header), len(cells)
                    )
                )
            issues.extend(
                _check_row_links(md_path, row_index + 1, lines[row_index], workspace)
            )
            row_index += 1
        index = row_index
    return issues


def _check_row_links(
    md_path: Path, line_number: int, line: str, workspace: Path
) -> List[str]:
    """表の行にあるリンクを、workspace内の実在ファイルに限定する。"""

    issues = []
    for _, raw_target in extract_links(line):
        if raw_target.startswith("#"):
            continue
        if is_external_link(raw_target):
            issues.append(
                "{}:{}: 外部URLの教材リンクがあります: {}。"
                "教材はworkspace内のmaterials/へ置いてください".format(
                    md_path, line_number, raw_target
                )
            )
            continue
        split = urlsplit(raw_target)
        if split.scheme or split.netloc:
            issues.append(
                "{}:{}: 未対応のリンク形式です: {}".format(
                    md_path, line_number, raw_target
                )
            )
            continue
        # 解決の規則は resolve_links() と共通にする（percent encodingを戻す）。
        try:
            target = link_target_path(md_path, split.path)
        except ValueError as exc:
            # 復号結果がパスとして使えない場合も、落とさず入力エラーにする。
            issues.append("{}:{}: {}".format(md_path, line_number, exc))
            continue
        if not is_within(target, workspace):
            issues.append(
                "{}:{}: workspace外へのリンクです: {}".format(
                    md_path, line_number, raw_target
                )
            )
        elif not target.is_file():
            issues.append(
                "{}:{}: リンク先が存在しません: {}".format(
                    md_path, line_number, raw_target
                )
            )
    return issues


def check_workspace(workspace: Path, anchor: Optional[date] = None) -> List[str]:
    """starter workspaceの入力契約とワークシート最小構造を検査する。"""

    issues: List[str] = []
    workspace = workspace.resolve()
    if not workspace.is_dir():
        return ["{}: workspaceディレクトリがありません".format(workspace)]

    for required in (
        "learners.md",
        "schedule.md",
        "activity-log.md",
        "tests.md",
        "materials/index.md",
    ):
        if not (workspace / required).is_file():
            issues.append(
                "{}: 必須ファイル {} がありません".format(workspace, required)
            )
    if issues:
        return issues

    try:
        StarterMarkdownAdapter(
            workspace, anchor or date.today(), mode="starter"
        ).load()
    except ValueError as exc:
        issues.append("{}: 入力契約に違反しています: {}".format(workspace, exc))

    issues.extend(
        _check_markdown_tables(
            workspace / "schedule.md", workspace, KNOWN_SCHEDULE_HEADERS
        )
    )
    issues.extend(
        _check_markdown_tables(
            workspace / "activity-log.md", workspace, KNOWN_ACTIVITY_HEADERS
        )
    )
    issues.extend(
        _check_markdown_tables(
            workspace / "learners.md", workspace, KNOWN_LEARNER_HEADERS
        )
    )
    issues.extend(
        _check_markdown_tables(
            workspace / "materials" / "index.md",
            workspace,
            KNOWN_MATERIAL_HEADERS,
        )
    )
    issues.extend(
        _check_markdown_tables(
            workspace / "tests.md", workspace, KNOWN_TEST_HEADERS
        )
    )

    materials = workspace / "materials"
    if materials.is_dir():
        font_root = materials / "shared" / "assets" / "fonts"
        if font_root.is_symlink():
            issues.append("{}: 共有フォント資産にsymlinkは使えません".format(font_root))
        if font_root.exists() and not font_root.is_symlink():
            for group in sorted(font_root.iterdir()):
                try:
                    verify_manifest(group, materials)
                except (OSError, UnicodeError, ValueError) as exc:
                    issues.append("{}: 共有フォント資産が不正です: {}".format(group, exc))
        for html_path in sorted(materials.rglob("*.html")):
            issues.extend(check_worksheet(html_path))
    try:
        records = load_workspace_records(workspace)
        registered = {
            Path(item.material.target.split("#", 1)[0]).resolve(): item
            for item in records.materials
        }
        actual = {
            path.resolve()
            for suffix in ("*.html", "*.htm", "*.pdf")
            for path in materials.rglob(suffix)
        }
        for path in sorted(actual - set(registered)):
            issues.append(
                "{}: 教材台帳 materials/index.md に登録されていません".format(path)
            )
        for path, item in registered.items():
            if path.suffix.lower() in (".html", ".htm"):
                try:
                    declared = material_type(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError):
                    # リンク・実在検査が具体的なパスを報告する。ここで台帳全体の
                    # 種別照合を中断したり、生のOSエラーを重ねたりしない。
                    continue
                if declared != item.kind:
                    issues.append(
                        "{}: 教材台帳の種別 {} とHTMLのmaterial-type {} が"
                        "一致しません".format(path, item.kind, declared or "宣言なし")
                    )
    except (OSError, UnicodeError, ValueError) as exc:
        # adapterのエラーと重複しても、台帳単体の原因を明示する。
        message = "{}: 台帳契約に違反しています: {}".format(workspace, exc)
        if message not in issues:
            issues.append(message)
    return issues


def _iter_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.is_dir():
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            for entry in sorted(current.iterdir()):
                if entry.is_dir():
                    if entry.name not in SKIPPED_DIR_NAMES:
                        stack.append(entry)
                elif entry.is_file():
                    yield entry


def _check_canonical_duplicates(
    repo_root: Path, scan_roots: Sequence[Path], allowed_parents: Sequence[Path]
) -> List[str]:
    issues = []
    resolved_parents = [parent.resolve() for parent in allowed_parents]
    for path in _iter_files(scan_roots):
        if path.name not in CANONICAL_BASENAMES:
            continue
        resolved = path.resolve()
        if any(
            parent in resolved.parents for parent in resolved_parents
        ):
            continue
        issues.append(
            "{}: starter正本と同名のファイルは、雛形とexamples以外へ"
            "置かないでください（正本の重複禁止）".format(
                resolved.relative_to(repo_root.resolve())
            )
        )
    return issues


def _git_effective_exclusion(
    root: Path, probes: Sequence[str]
) -> Optional[List[str]]:
    """git check-ignore で実効的な除外状態と除外元を検証する。

    .git/info/exclude やグローバル除外はclone先へ引き継がれないため、
    除外の決め手がリポジトリ直下の .gitignore であることまで確認する。
    Gitリポジトリでない・gitが使えない場合は None を返す（静的検査は
    呼び出し側で常に実行される）。
    """

    if not (root / ".git").exists():
        return None
    issues: List[str] = []
    for probe in probes:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", probe],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 1:
            issues.append(
                "Git除外が実効になっていません: {} が無視されません。"
                ".gitignore の後続ルール（!による再許可等）を確認してください".format(
                    probe
                )
            )
            continue
        if result.returncode != 0:
            return None
        try:
            verbose = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "--verbose", probe],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        # 出力形式: <source>:<linenum>:<pattern>\t<pathname>
        line = verbose.stdout.strip().splitlines()
        source = ""
        if line and "\t" in line[0]:
            left = line[0].split("\t", 1)[0]
            parts = left.rsplit(":", 2)
            if len(parts) == 3:
                source = parts[0]
        if source != ".gitignore":
            issues.append(
                "Git除外の決め手がリポジトリの .gitignore ではありません: "
                "{} の除外元は {} です。.git/info/exclude やグローバル除外は"
                "clone先へ引き継がれないため、リポジトリ直下の .gitignore に"
                "除外を書いてください".format(probe, source or "不明")
            )
    return issues


def _static_gitignore_check(
    root: Path, required_patterns: Sequence[str]
) -> List[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return ["{}: .gitignore がありません".format(root)]
    lines = [
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    issues = []
    for pattern in required_patterns:
        if pattern not in lines:
            issues.append(
                ".gitignore: {} の除外がありません。実データ・生成物を"
                "Git管理外に保ってください".format(pattern)
            )
    protected_prefixes = tuple(
        pattern.strip("/").split("/", 1)[0] for pattern in required_patterns
    )
    for line in lines:
        if line.startswith("!") and any(
            prefix in line for prefix in protected_prefixes
        ):
            issues.append(
                ".gitignore: 再許可ルール {} が保護対象（{}）へかかっています。"
                "除外を打ち消さないでください".format(
                    line, "・".join(protected_prefixes)
                )
            )
    return issues


def _check_git_exclusion(
    root: Path, required_patterns: Sequence[str], probes: Sequence[str]
) -> List[str]:
    # 静的検査（必須行・再許可ルール）は常に実行し、リポジトリ固有の
    # .gitignore に必要な除外が書かれていることを保証する。Gitリポジトリでは
    # さらに実効状態と除外元を検証する。
    issues = _static_gitignore_check(root, required_patterns)
    effective = _git_effective_exclusion(root, probes)
    if effective is not None:
        issues.extend(effective)
    return issues


def check_repo(root: Path) -> List[str]:
    """リポジトリ構造（境界・正本・必須ファイル・Git除外）を検査する。"""

    root = root.resolve()
    issues: List[str] = []
    source_mode = is_private_source(root)

    if source_mode:
        required_files = SOURCE_REQUIRED_FILES
        scan_roots = [root / "kit", root / "examples"]
        allowed_parents = [root / "kit" / "templates", root / "examples"]
        template_path = root / "kit" / "templates" / "worksheet.html"
        gitignore_patterns = ("workspace/",)
        gitignore_probes = ("workspace/checkup-probe/schedule.md",)
    else:
        required_files = PUBLIC_REQUIRED_FILES
        scan_roots = [root]
        allowed_parents = [root / "templates", root / "examples"]
        template_path = root / "templates" / "worksheet.html"
        gitignore_patterns = ("workspace/", "dist/")
        gitignore_probes = (
            "workspace/checkup-probe/schedule.md",
            "dist/checkup-probe/index.html",
        )
        for entry in sorted(root.iterdir()):
            name = entry.name
            if name in PUBLIC_TOP_LEVEL_ALLOWED or name in TOLERATED_DEV_ENTRIES:
                continue
            issues.append(
                "{}: 許可外のトップレベル項目です。家庭のデータはworkspace/へ、"
                "新しい領域は docs/AI-OPERATIONS.md の手順で合意してから"
                "追加してください".format(name)
            )

    for relative in required_files:
        if not (root / relative).is_file():
            issues.append("{}: 必須ファイルがありません".format(relative))

    issues.extend(_check_canonical_duplicates(root, scan_roots, allowed_parents))
    issues.extend(_check_git_exclusion(root, gitignore_patterns, gitignore_probes))
    if template_path.is_file():
        issues.extend(check_worksheet(template_path, strict=True))

    if not source_mode:
        red_sheet_template = root / "templates" / "worksheet-red-sheet.html"
        if red_sheet_template.is_file():
            issues.extend(check_worksheet(red_sheet_template, strict=True))
            try:
                red_layout = WorksheetLayout(red_sheet_template.read_text(encoding="utf-8"))
                if red_layout.layout != "red-sheet":
                    issues.append("{}: 赤シート雛形にはanswer-layout=red-sheetが必要です".format(red_sheet_template))
            except (OSError, UnicodeError):
                pass  # 読み取りエラーはcheck_worksheetの結果に含まれる。
        workspace_root = root / "workspace"
        if workspace_root.is_dir():
            for child in sorted(workspace_root.iterdir()):
                if child.is_dir():
                    issues.extend(check_workspace(child))
    return issues


def main(argv: Optional[Sequence[str]] = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="リポジトリ構造とstarter workspaceを検証します。"
    )
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="追加で検証するstarter workspace（省略時はworkspace/配下を自動検出）",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="反復ルーティン展開の基準日（YYYY-MM-DD、未指定時は今日）",
    )
    args = parser.parse_args(argv)

    issues = check_repo(args.repo_root)
    if args.workspace is not None:
        issues.extend(check_workspace(args.workspace, args.date))
    # workspace/配下は自動検出でも検査されるため、--workspace指定と重なった
    # 同一メッセージは1件へまとめる。
    issues = list(dict.fromkeys(issues))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print("NG: {}件の問題があります".format(len(issues)), file=sys.stderr)
        return 1
    print("OK: 構造・workspace検証に問題はありません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
