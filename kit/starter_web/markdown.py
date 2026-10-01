"""依存を増やさずMarkdownの必要部分だけを読む補助関数。"""

import html
import re
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from .model import MaterialLink, Status


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
DATE_SLASH_RE = re.compile(r"(?<!\d)(?:(\d{4})-)?(\d{1,2})/(\d{1,2})(?!\d)")
DATE_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
TAG_RE = re.compile(r"<[^>]+>")
MARK_RE = re.compile(r"[*_~`]+")


def split_table_row(line: str) -> List[str]:
    """Markdown表の1行を、エスケープされたpipeを保って分割する。"""

    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]

    cells = []
    current = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
            current.append(char)
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def is_separator_row(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def strip_markdown(value: str) -> str:
    """Webの短い表示用にMarkdown装飾をプレーンテキスト化する。"""

    text = LINK_RE.sub(lambda match: match.group(1), value)
    text = TAG_RE.sub("", text)
    text = MARK_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def summarize(value: str, limit: int = 180) -> str:
    text = strip_markdown(value)
    if len(text) <= limit:
        return text
    candidates = [
        text.rfind("。", 0, limit),
        text.rfind("／", 0, limit),
        text.rfind("、", 0, limit),
    ]
    cut = max(candidates)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip("。、／ ") + "…"


def parse_iso_date(value: str) -> date:
    match = DATE_ISO_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("日付はYYYY-MM-DD形式で指定してください: {}".format(value))
    return date(*(int(part) for part in match.groups()))


def first_date(value: str, anchor: date) -> Optional[date]:
    """セル内の最初の日付をanchorの年に解決する。"""

    plain = strip_markdown(value)
    iso_match = DATE_ISO_RE.search(plain)
    slash_match = DATE_SLASH_RE.search(plain)
    if iso_match and (
        slash_match is None or iso_match.start() <= slash_match.start()
    ):
        return date(*(int(part) for part in iso_match.groups()))

    if not slash_match:
        return None
    explicit_year, month_text, day_text = slash_match.groups()
    month = int(month_text)
    day = int(day_text)
    year = int(explicit_year) if explicit_year else anchor.year
    if not explicit_year:
        if anchor.month >= 10 and month <= 3:
            year += 1
        elif anchor.month <= 3 and month >= 10:
            year -= 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def status_from_text(value: str, default: Status = Status.PLANNED) -> Status:
    """現行表現とstarter表現を共通状態へ寄せる。"""

    text = strip_markdown(value)
    first_marker = re.search(r"【([^】]+)】", text)
    primary = first_marker.group(1) if first_marker else text[:80]

    if "一部実施" in primary or "前半のみ実施" in primary:
        return Status.PARTIAL
    if "実施有無未確認" in primary:
        return Status.UNKNOWN
    if "未実施" in primary:
        return Status.MISSED
    if "繰越" in primary or "繰り越し" in primary:
        return Status.DEFERRED
    if "実施済" in primary or "完了" in primary or primary == "実施":
        return Status.COMPLETED
    if (
        "作成済" in primary
        or "取り込み済" in primary
        or "準備済" in primary
        or "クリーン問題" in primary
    ):
        return Status.READY
    if "未作成" in primary or "未取り込み" in primary:
        return Status.PLANNED
    return default


STARTER_STATUS_MAP = {
    "予定": Status.PLANNED,
    "準備済み": Status.READY,
    "作成済み": Status.READY,
    "完了": Status.COMPLETED,
    "一部実施": Status.PARTIAL,
    "未実施": Status.MISSED,
    "実施有無未確認": Status.UNKNOWN,
    "繰越": Status.DEFERRED,
}


def starter_status(value: str) -> Status:
    normalized = strip_markdown(value)
    try:
        return STARTER_STATUS_MAP[normalized]
    except KeyError as exc:
        allowed = "、".join(STARTER_STATUS_MAP)
        raise ValueError(
            "未対応の状態です: {}（使用可能: {}）".format(normalized, allowed)
        ) from exc


def extract_links(value: str) -> List[Tuple[str, str]]:
    return [(strip_markdown(label), target.strip()) for label, target in LINK_RE.findall(value)]


def is_external_link(target: str) -> bool:
    return urlsplit(target).scheme in {"http", "https"}


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def decoded_link_path(target: str) -> str:
    """リンクのパス部分を、percent encodingを戻して返す。

    fragment・queryは含めない。**教材リンクの中身から意味を判定する処理は、
    この関数を通す**＝生の文字列で見ると、``kanji-master`` を ``%6B%61%6E…``
    と書いただけで教科名の入力契約や重複検査をすり抜けられる。

    ⚠️ 「リンクを見る処理すべて」ではない。``input_contract`` の
    ``.md`` 正本委譲は、リンク先を**allowlistの実体と同一かどうかで照合する**
    別セマンティクスなので、ここへ寄せない（理由は当該箇所のコメント）。
    """

    return unquote(urlsplit(target).path)


def link_target_path(source_path: Path, link_path: str) -> Path:
    """Markdownリンクの相対パスを絶対パスへ解決する。

    percent encodingは**解決より先に**戻す。理由は2つ。

    1. コピー値・実在判定を人が読める文字で行う（``%E5%9B%BD`` ではなく ``国``）
    2. encodedな ``..``（``%2E%2E``）が許可ルート検査をすり抜けないようにする

    ``resolve_links()`` と ``checkup`` が同じ規則で解決するための共通関数で、
    片方だけデコードすると「画面では開けるのにcheckupがリンク切れと言う」
    といった食い違いが出る。

    復号結果がパスとして使えない場合（``%00`` がNUL文字へ戻る等）は
    ``ValueError`` にする。素の ``ValueError: embedded null byte`` を素通り
    させると、呼び出し側が入力エラーとして報告できずクラッシュする。
    """

    decoded = unquote(link_path)
    if "\x00" in decoded:
        raise ValueError(
            "リンクに使えない文字が含まれています: {}".format(link_path)
        )
    try:
        return (source_path.parent / decoded).resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(
            "リンクを解決できません: {} ({})".format(link_path, exc)
        ) from exc


def relative_copy_path(target_path: Path, allowed_root: Path) -> Optional[str]:
    """許可ルート基準の相対POSIXパス（コピー用の論理パス）を返す。

    区切りはOSに依存せず ``/`` にする。絶対パス・空値・``..`` によるルート外
    参照は作らず、作れない場合は ``None`` を返してコピー対象から外す。
    """

    try:
        relative = target_path.resolve().relative_to(allowed_root.resolve())
    except ValueError:
        return None
    if not relative.parts or ".." in relative.parts:
        return None
    text = relative.as_posix()
    if not text or text == "." or text.startswith("/"):
        return None
    return text


def resolve_links(
    value: str,
    source_path: Path,
    allowed_root: Path,
    allow_external: bool = False,
) -> List[MaterialLink]:
    """Markdownリンクを安全な絶対対象へ解決する。

    ローカルリンクには、許可ルート基準の相対パス（``copy_path``）も持たせる。
    表示・配信で変わる ``target`` と違い、こちらは作業ルートからの安定した
    論理パスで、画面のコピー操作が使う正本になる。
    """

    resolved = []
    for label, raw_target in extract_links(value):
        if raw_target.startswith("#"):
            continue
        if is_external_link(raw_target):
            if allow_external:
                resolved.append(MaterialLink(label=label, target=raw_target))
            continue

        split = urlsplit(raw_target)
        if split.scheme or split.netloc:
            raise ValueError("未対応のリンク形式です: {}".format(raw_target))
        target_path = link_target_path(source_path, split.path)
        if not is_within(target_path, allowed_root):
            raise ValueError(
                "許可ルート外へのリンクです: {} ({})".format(
                    raw_target, source_path
                )
            )
        target = str(target_path)
        if split.fragment:
            # 表示時に再エンコードするので、ここでも素の文字へ戻しておく。
            target += "#" + unquote(split.fragment)
        resolved.append(
            MaterialLink(
                label=label,
                target=target,
                # フラグメント・クエリは論理パスに含めない。
                copy_path=relative_copy_path(target_path, allowed_root),
            )
        )
    return resolved


def markdown_tables(lines: Sequence[str]) -> Iterable[Tuple[int, List[str], List[List[str]]]]:
    """Markdown表を(header line, header, rows)で列挙する。"""

    index = 0
    while index + 1 < len(lines):
        header = split_table_row(lines[index])
        separator = split_table_row(lines[index + 1])
        if header and is_separator_row(separator) and len(header) == len(separator):
            rows = []
            row_index = index + 2
            while row_index < len(lines):
                cells = split_table_row(lines[row_index])
                if not cells or is_separator_row(cells):
                    break
                if len(cells) == len(header):
                    rows.append(cells)
                row_index += 1
            yield index + 1, header, rows
            index = row_index
        else:
            index += 1
