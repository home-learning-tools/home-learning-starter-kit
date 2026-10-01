"""workspace内で共有する固定フォントの参照と配布物を検査する。"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import urlsplit


HASH = re.compile(r"[0-9a-f]{64}\Z")
CSS_NAME = re.compile(r"[0-9a-f]{64}\.css\Z")
FONT_NAME = re.compile(r"fonts/[0-9a-f]{64}\.woff2\Z")
FAMILY = re.compile(r"(?P<quote>['\"])[^'\"]+(?P=quote)\Z")
WEIGHT = re.compile(r"(?:[1-9]00)(?:\s+[1-9]00)?\Z")
RANGE = re.compile(r"U\+[0-9A-Fa-f?-]+(?:\s*,\s*U\+[0-9A-Fa-f?-]+)*\Z")
SOURCE = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<font>fonts/[0-9a-f]{64}\.woff2)(?P=quote)\s*\)"
    r"\s*format\(\s*['\"]woff2['\"]\s*\)\Z"
)
COMMENT = re.compile(r"/\*[\s\S]*?\*/")
FACE = re.compile(r"@font-face\s*\{([^{}]*)\}", re.S)
DECLARATION = {"font-family", "font-style", "font-weight", "font-display", "src", "unicode-range"}
DECLARED_FORMAT = "workspace-local-v1"


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifestのJSONキーが重複しています: " + key)
        result[key] = value
    return result


def _file(path: Path, root: Path) -> bytes:
    if not path.is_relative_to(root):
        raise ValueError("共有資産の参照がworkspace外です")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("共有資産にsymlinkは使えません: " + str(current))
    if not stat.S_ISREG(mode):
        raise ValueError("共有資産が通常ファイルではありません: " + str(path))
    return path.read_bytes()


def _relative(path: str) -> None:
    if (not isinstance(path, str) or not path or "\\" in path or "%" in path
            or "?" in path or "#" in path or ":" in path
            or unicodedata.normalize("NFC", path) != path
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        raise ValueError("共有資産の相対パスが不正です: " + str(path))


def css_dependencies(data: bytes) -> tuple[list[str], list[str]]:
    """font-face以外のCSSを拒否し、familyとWOFF2の参照を返す。"""
    text = data.decode("utf-8")
    if text.startswith("\ufeff") or "/*" in COMMENT.sub("", text):
        raise ValueError("共有CSSのBOMまたはコメントが不正です")
    clean = COMMENT.sub("", text)
    faces = FACE.findall(clean)
    if not faces or FACE.sub("", clean).strip():
        raise ValueError("共有CSSにfont-face以外の規則があります")
    families, fonts = [], []
    for body in faces:
        fields = {}
        for item in body.split(";"):
            if not item.strip():
                continue
            name, sep, value = item.partition(":")
            name, value = name.strip(), value.strip()
            if not sep or name not in DECLARATION or name in fields:
                raise ValueError("共有CSSのfont-face宣言が不正です")
            fields[name] = value
        if set(fields) != DECLARATION:
            raise ValueError("共有CSSのfont-face宣言が不足しています")
        src = SOURCE.fullmatch(fields["src"])
        if (not src or not FAMILY.fullmatch(fields["font-family"])
                or fields["font-style"] != "normal"
                or not WEIGHT.fullmatch(fields["font-weight"])
                or fields["font-display"] != "swap"
                or not RANGE.fullmatch(fields["unicode-range"])):
            raise ValueError("共有CSSの字形・weight・src指定が不正です")
        families.append(fields["font-family"][1:-1])
        fonts.append(src["font"])
    return sorted(set(families)), sorted(set(fonts))


def verify_manifest(group: Path, materials: Path, *, allowed_unregistered: frozenset[str] = frozenset()) -> dict:
    """manifest、全登録資産、実際のCSS依存、未登録ファイルを照合する。"""
    base = materials / "shared" / "assets" / "fonts"
    if group.parent != base or not group.is_dir() or group.is_symlink():
        raise ValueError("共有フォントの配置が不正です")
    raw = _file(group / "manifest.json", materials)
    document = json.loads(raw, object_pairs_hook=_pairs)
    if set(document) != {"schema_version", "source_lock_sha256", "files", "css"}:
        raise ValueError("共有フォントmanifestの項目が不正です")
    if (type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["source_lock_sha256"], str)
            or not HASH.fullmatch(document["source_lock_sha256"])):
        raise ValueError("共有フォントmanifestの版またはsource lockが不正です")
    rows = document["files"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("共有フォントmanifestに資産がありません")
    paths = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes", "kind"}:
            raise ValueError("共有フォントmanifestの資産行が不正です")
        path = row["path"]
        _relative(path)
        if not isinstance(row["kind"], str) or row["kind"] not in {"css", "woff2", "license"}:
            raise ValueError("共有フォント資産の種別が不正です")
        if (row["kind"] == "css" and not CSS_NAME.fullmatch(path)
                or row["kind"] == "woff2" and not FONT_NAME.fullmatch(path)
                or row["kind"] == "license" and not path.startswith("licenses/")):
            raise ValueError("共有フォント資産の配置が不正です: " + path)
        if (not isinstance(row["sha256"], str) or not HASH.fullmatch(row["sha256"])
                or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise ValueError("共有フォント資産のハッシュが不正です")
        data = _file(group / path, materials)
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("共有フォント資産の内容がmanifestと異なります: " + path)
        if row["kind"] in {"css", "woff2"} and path.split("/")[-1].split(".")[0] != row["sha256"]:
            raise ValueError("共有フォント資産のハッシュ名が不正です: " + path)
        if row["kind"] == "woff2" and (
            len(data) < 48 or data[:4] != b"wOF2"
            or int.from_bytes(data[8:12], "big") != len(data)
            or int.from_bytes(data[12:14], "big") == 0
            or data[14:16] != b"\0\0"
            or int.from_bytes(data[16:20], "big") == 0
            or int.from_bytes(data[20:24], "big") == 0
        ):
            raise ValueError("共有フォント資産がWOFF2ではありません: " + path)
        paths.append(path)
    if paths != sorted(set(paths)) or len({p.casefold() for p in paths}) != len(paths):
        raise ValueError("共有フォントmanifestのパスが重複または非昇順です")
    css = document["css"]
    if (not isinstance(css, list) or any(not isinstance(item, dict) for item in css)
            or any(not isinstance(item.get("path"), str) for item in css)
            or [item["path"] for item in css] != sorted(set(item["path"] for item in css))):
        raise ValueError("共有フォントCSSの一覧が不正です")
    kinds = {row["path"]: row["kind"] for row in rows}
    if "license" not in kinds.values():
        raise ValueError("共有フォントのライセンスがありません")
    for item in css:
        if not isinstance(item, dict) or set(item) != {"path", "families", "woff2"}:
            raise ValueError("共有フォントCSS行が不正です")
        path = item["path"]
        if kinds.get(path) != "css":
            raise ValueError("共有フォントCSSが未登録です")
        families, fonts = css_dependencies(_file(group / path, materials))
        if item["families"] != families or item["woff2"] != fonts:
            raise ValueError("共有フォントCSS依存がmanifestと異なります: " + path)
        if any(kinds.get(font) != "woff2" for font in fonts):
            raise ValueError("共有フォントWOFF2が未登録です: " + path)
    if {item["path"] for item in css} != {path for path, kind in kinds.items() if kind == "css"}:
        raise ValueError("共有フォントCSS一覧が不完全です")
    actual = set()
    for path in group.rglob("*"):
        if path.is_symlink():
            raise ValueError("共有資産にsymlinkは使えません")
        if path.is_file():
            actual.add(path.relative_to(group).as_posix())
    if actual != set(paths) | {"manifest.json"} | allowed_unregistered:
        raise ValueError("共有フォント資産に未登録・欠落ファイルがあります")
    return document


class _HTMLAssets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.declarations = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError("HTML属性が重複しています")
        if tag == "head":
            self.in_head = True
        elif tag == "meta" and values.get("name") == "material-assets":
            self.declarations.append((self.in_head, values.get("content")))
        elif tag == "link":
            self.links.append((self.in_head, values))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def worksheet_references(path: Path, text: str) -> list[Path]:
    """宣言と実際のlink参照を照合し、使う共有CSSを返す。"""
    parser = _HTMLAssets()
    parser.feed(text)
    parser.close()
    if len(parser.declarations) > 1 or any(not head or value != DECLARED_FORMAT
                                           for head, value in parser.declarations):
        raise ValueError("material-assets宣言が重複・未知値・head外です")
    materials = next((p for p in path.parents if p.name == "materials"), None)
    if materials is None:
        if parser.declarations:
            raise ValueError("共有フォント宣言はmaterials内だけで使えます")
        return []
    base = materials / "shared" / "assets" / "fonts"
    found = []
    for head, values in parser.links:
        href = values.get("href", "")
        shared = values.get("data-shared-font")
        if shared is None and values.get("rel") == "stylesheet":
            raise ValueError("共有フォント以外の外部CSSは使えません")
        if shared is None and "assets/fonts/" not in href:
            continue
        if (not head or not shared or set(values) != {"href", "rel", "data-shared-font"}
                or values["rel"] != "stylesheet"):
            raise ValueError("共有フォントlinkの属性または位置が不正です")
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or parts.query or parts.fragment or "%" in href or "\\" in href:
            raise ValueError("共有フォントlinkのURLが不正です")
        target = Path(os.path.normpath(str(path.parent / href)))
        if target.parent.parent != base or not CSS_NAME.fullmatch(target.name):
            raise ValueError("共有フォントlinkが共有資産外を指しています")
        _file(target, materials)
        found.append(target)
    if bool(found) != bool(parser.declarations):
        raise ValueError("共有フォントlinkとmaterial-assets宣言が一致しません")
    return found
