"""予定セル内の「独立した実施項目」を明示マーカーから読む。

1つの予定セルに独立した項目が複数あるとき、`＋` だけを手掛かりに分割すると
誤る。`＋` は「同じ教材の内訳」「直前教材の△×確認」「1文中の並列」にも使う
ため、区切り記号としては曖昧だからである。そこで、項目を分けたいセルにだけ
明示マーカーを書く。

```md
8/14 《教材:過去問の再実施》[問題](materials/past-exam.pdf)
＋《教材:語彙の確認》[語彙ミニ](materials/vocab-mini.html)
＋《教材不要:口頭確認5分》
。《参考:昨年の同回（実施しない）》[昨年](materials/last-year.pdf)
```

- `《教材:項目名》` … 教材が必要な項目
- `《外部教材:項目名》` … 塾Webなど、リポジトリ外に教材が存在する項目
- `《教材不要:項目名》` … 教材なしで実施できる項目
- `《参考:名前》` … その日の実施項目ではない参考・在庫。タイトルにも教材判定にも
  含めない（取りやめた教材の在庫リンクなどを、予定教材と取り違えないため）
- 次のマーカーまでにあるMarkdownリンクを、その項目の教材として扱う
- マーカーが1つも無いセルは、セル全体を教材必須の1項目として扱う（従来どおり）
- マーカーを使うセルでは、マーカーの外にある ``＋`` は次のマーカーへ直接つなぐ。
  説明文の並列に ``＋`` を使うと、2つ目以降の項目が画面から消える書き方と
  区別できないため

契約違反（項目名が空・閉じていない・不明な種別・未分類項目との混在・
`《教材不要》` 内のローカル教材リンク・`《外部教材》` 内のリンク・
宙に浮いた ``＋``）は、例外で止めずに
指摘の一覧として返す。壊れた入力でもWebの生成と監査を続けられるようにするため。
契約の正本は ``docs/rules-private-web-input.md``。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from urllib.parse import urlsplit

from .markdown import LINK_RE, decoded_link_path, extract_links, strip_markdown
from .model import MATERIAL_SUFFIXES


#: 教材が必要な項目のマーカー種別。
KIND_REQUIRED = "教材"

#: 塾Webなど、リポジトリ外に教材が存在する項目のマーカー種別。
KIND_EXTERNAL = "外部教材"

#: 教材なしで実施できる項目のマーカー種別。
KIND_NOT_REQUIRED = "教材不要"

#: その日の実施項目ではない参考・在庫のマーカー種別。
KIND_REFERENCE = "参考"

KINDS = (KIND_REQUIRED, KIND_EXTERNAL, KIND_NOT_REQUIRED, KIND_REFERENCE)

#: 実施項目として数える種別（``参考`` はタイトルにも教材判定にも入れない）。
ITEM_KINDS = (KIND_REQUIRED, KIND_EXTERNAL, KIND_NOT_REQUIRED)

MARKER_OPEN = "《"
MARKER_CLOSE = "》"
MARKER_RE = re.compile(r"《([^《》]*)》")
KIND_SEPARATOR_RE = re.compile(r"[:：]")

#: 項目を並べる区切り記号。マーカーの外では次のマーカーへ直接つなぐ。
ITEM_SEPARATOR = "＋"

#: 区切りとマーカーのあいだに置いてよい装飾・空白。
SEPARATOR_PADDING = "*_ 　\t"

#: セル冒頭の日付（マーカーより前に書いてよい唯一の文字列）。
LEADING_DATE_RE = re.compile(r"^(?:\d{4}-)?\d{1,2}[/-]\d{1,2}")

#: タイトルの最大長。予定カードの表示幅に合わせて切り詰める。
TITLE_LIMIT = 100


@dataclass(frozen=True)
class RequirementMarker:
    """1つの明示マーカーと、その項目に属する本文。"""

    kind: str
    label: str
    body: str

    @property
    def required(self) -> bool:
        return self.kind in (KIND_REQUIRED, KIND_EXTERNAL)

    @property
    def external(self) -> bool:
        return self.kind == KIND_EXTERNAL

    @property
    def is_item(self) -> bool:
        """その日の実施項目かどうか（``参考`` は項目ではない）。"""

        return self.kind in ITEM_KINDS


@dataclass(frozen=True)
class RequirementParse:
    """予定セルの解析結果。``errors`` は入力契約違反の説明文。"""

    markers: Tuple[RequirementMarker, ...] = ()
    errors: Tuple[str, ...] = ()

    @property
    def items(self) -> Tuple[RequirementMarker, ...]:
        """実施項目のマーカーだけを返す。"""

        return tuple(marker for marker in self.markers if marker.is_item)

    @property
    def references(self) -> Tuple[RequirementMarker, ...]:
        """参考・在庫のマーカーだけを返す。"""

        return tuple(
            marker for marker in self.markers if marker.kind == KIND_REFERENCE
        )

    @property
    def has_items(self) -> bool:
        return bool(self.items)

    def title(self) -> str:
        """項目名を ``＋`` で連結した表示タイトル。"""

        return ITEM_SEPARATOR.join(
            marker.label for marker in self.items
        )[:TITLE_LIMIT]


def _local_material_paths(text: str) -> List[Tuple[str, str]]:
    """本文中のローカルな ``.html`` / ``.pdf`` リンクを ``(ラベル, パス)`` で返す。

    パスはfragmentを除いて正規化し、同じファイルを別の書き方でたどった場合も
    同一とみなせるようにする。
    """

    found = []
    for label, target in extract_links(text):
        split = urlsplit(target.split("#", 1)[0])
        if split.scheme in {"http", "https"} or not split.path:
            continue
        # 同じファイルをpercent encodingの有無で書き分けても同一とみなす。
        decoded = decoded_link_path(target)
        if Path(decoded).suffix.lower() in MATERIAL_SUFFIXES:
            found.append((label, posixpath.normpath(decoded)))
    return found


def _local_material_links(text: str) -> List[str]:
    """本文中の、ローカルな ``.html`` / ``.pdf`` リンクのラベル。"""

    return [label for label, _ in _local_material_paths(text)]


def _mask_links(text: str) -> str:
    """Markdownリンクを、位置を保ったまま伏せる。

    リンクの表示名やパスに含まれる ``＋``（例: ``[単位＋かけ算発展](…)``）を
    項目の区切りと取り違えないようにするため。
    """

    return LINK_RE.sub(lambda match: "\x00" * len(match.group(0)), text)


def _separator_errors(body: str, label: str, is_last: bool) -> List[str]:
    """マーカーの外に、次のマーカーへつながらない ``＋`` が無いか調べる。

    ``《教材:A》[A](a.html)＋B`` のような書き方を許すと、``B`` はどの項目にも
    ならず画面から消える。これは項目マーカーで解こうとしている「2つ目の項目が
    表示されない」問題そのものなので、書き方の誤りとして指摘する。
    """

    errors = []
    body = _mask_links(body)
    for match in re.finditer(re.escape(ITEM_SEPARATOR), body):
        tail = body[match.end():].strip(SEPARATOR_PADDING)
        if not is_last and not tail:
            continue
        errors.append(
            "「{separator}」の後ろが項目マーカーではありません（{label}）。"
            "項目を足すなら「{open}教材:項目名{close}」を続け、"
            "説明文の並列は「・」などにしてください: {tail}".format(
                separator=ITEM_SEPARATOR,
                label=label,
                open=MARKER_OPEN,
                close=MARKER_CLOSE,
                tail=strip_markdown(tail)[:40] or "（末尾）",
            )
        )
    return errors


def _duplicate_classification_errors(
    markers: Sequence[RequirementMarker],
) -> List[str]:
    """同じ教材を実施項目と参考の両方へ登録していないか調べる。

    どちらの分類か決まらないと、教材状態は「教材あり」なのに実績照合では
    使われない、といった食い違いが起きる。同じ教材を複数の実施項目で共有
    するのは従来どおり許す（分類は変わらないため）。
    """

    item_labels: Dict[str, str] = {}
    reference_labels: Dict[str, str] = {}
    for marker in markers:
        target = item_labels if marker.is_item else reference_labels
        for _, path in _local_material_paths(marker.body):
            target.setdefault(path, marker.label)

    return [
        "同じ教材を「{}」と「{}」の両方へ登録しています: {}"
        "（実施項目「{}」／参考「{}」）。どちらか一方にしてください".format(
            KIND_REQUIRED,
            KIND_REFERENCE,
            path,
            item_labels[path],
            reference_labels[path],
        )
        for path in item_labels
        if path in reference_labels
    ]


def parse_requirements(cell: str) -> RequirementParse:
    """予定セルから明示マーカーの項目を読み、契約違反を指摘として返す。"""

    matches = list(MARKER_RE.finditer(cell))
    errors: List[str] = []

    remainder = MARKER_RE.sub("", cell)
    if MARKER_OPEN in remainder or MARKER_CLOSE in remainder:
        errors.append(
            "項目マーカーが閉じていません。"
            "「{}教材:項目名{}」の形で書いてください".format(
                MARKER_OPEN, MARKER_CLOSE
            )
        )

    if not matches:
        return RequirementParse(markers=(), errors=tuple(errors))

    markers: List[RequirementMarker] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(cell)
        )
        body = cell[body_start:body_end]
        content = match.group(1)
        if extract_links(content):
            # マーカー内のリンクはどの項目の教材にもならない。黙って
            # 「教材なし」にせず、書き方の誤りとして指摘する。
            errors.append(
                "項目マーカーの中にリンクを書かないでください。"
                "教材リンクはマーカーの後ろへ置きます: "
                "{}{}{}".format(MARKER_OPEN, content, MARKER_CLOSE)
            )
            continue
        parts = KIND_SEPARATOR_RE.split(content, maxsplit=1)
        kind = strip_markdown(parts[0])
        label = strip_markdown(parts[1]) if len(parts) > 1 else ""
        if len(parts) < 2:
            errors.append(
                "項目マーカーは「種別:項目名」の形で書いてください: "
                "{}{}{}".format(MARKER_OPEN, content, MARKER_CLOSE)
            )
            continue
        if kind not in KINDS:
            errors.append(
                "項目マーカーの種別は「{}」のいずれかにしてください: {}".format(
                    "」「".join(KINDS), kind or "（空）"
                )
            )
            continue
        if not label:
            errors.append(
                "項目マーカーの項目名は空にできません: "
                "{}{}{}".format(MARKER_OPEN, content, MARKER_CLOSE)
            )
            continue
        if kind == KIND_NOT_REQUIRED:
            material_labels = _local_material_links(body)
            if material_labels:
                errors.append(
                    "「{}」の項目に教材リンクがあります。"
                    "教材が要る項目は「{}」に、その日に使わない在庫・参考は"
                    "「{}」にしてください: {}（{}）".format(
                        KIND_NOT_REQUIRED,
                        KIND_REQUIRED,
                        KIND_REFERENCE,
                        label,
                        "・".join(material_labels),
                    )
                )
        if kind == KIND_EXTERNAL and extract_links(body):
            errors.append(
                "「{}」の項目にはリンクを書かないでください。"
                "外部教材はマーカーだけで存在を表し、URLやログイン情報は"
                "予定表へ保存しません: {}".format(KIND_EXTERNAL, label)
            )
        errors.extend(
            _separator_errors(body, label, is_last=index == len(matches) - 1)
        )
        markers.append(RequirementMarker(kind=kind, label=label, body=body))

    prefix = cell[: matches[0].start()]
    if extract_links(prefix):
        errors.append(
            "項目マーカーより前に教材リンクがあります。"
            "同じセルで分類済みの項目と未分類の記載を混ぜないでください"
        )
    plain_prefix = LEADING_DATE_RE.sub("", strip_markdown(prefix)).strip()
    if plain_prefix:
        errors.append(
            "項目マーカーより前に、日付以外の記載があります。"
            "同じセルで分類済みの項目と未分類の記載を混ぜないでください: "
            "{}".format(plain_prefix[:40])
        )

    errors.extend(_duplicate_classification_errors(markers))

    if markers and not any(marker.is_item for marker in markers):
        # 参考だけのセルは、実施項目が1つも宣言されていない状態。ここで
        # 素通しすると、参考リンクをセル全体の教材として数えてしまう。
        errors.append(
            "「{}」だけのセルには実施項目がありません。"
            "「{}教材:項目名{}」・「{}外部教材:項目名{}」または"
            "「{}教材不要:項目名{}」を書いてください".format(
                KIND_REFERENCE,
                MARKER_OPEN,
                MARKER_CLOSE,
                MARKER_OPEN,
                MARKER_CLOSE,
                MARKER_OPEN,
                MARKER_CLOSE,
            )
        )

    return RequirementParse(markers=tuple(markers), errors=tuple(errors))


def requirement_issue_messages(cell: str) -> Sequence[str]:
    """入力検証から使う、契約違反の説明文だけを返す。"""

    return parse_requirements(cell).errors
