"""明示allowlistから配布manifestを生成し、配布ツリーの完全性を検証する。"""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path


ALLOWLIST = "kit/distribution-files.json"
MANIFEST = "kit/distribution-manifest.json"
ROOT = Path(__file__).resolve().parents[1]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSONのキーが重複しています")
        result[key] = value
    return result


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _paths(values):
    if not isinstance(values, list) or not values:
        raise ValueError("配布一覧は空でない配列が必要です")
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError("配布パスは空でない文字列が必要です")
        parts = value.split("/")
        key = unicodedata.normalize("NFC", value).casefold()
        if (any(part.casefold() in {"", ".", "..", ".git", "__pycache__"} for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in value)
                or parts[0].casefold() in {"workspace", "dist"}
                or key == MANIFEST.casefold()):
            raise ValueError("配布できないパスです: " + value)
        if key in seen:
            raise ValueError("配布パスが重複しています: " + value)
        seen.add(key)
    return sorted(values)


def _regular_file(root, name):
    current = root
    for part in name.split("/"):
        current = current / part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("symlinkは配布できません: " + name)
    if not stat.S_ISREG(mode):
        raise ValueError("通常ファイルではありません: " + name)
    return current


def _inventory(root):
    """Git管理情報とPythonのキャッシュ以外は、未知のファイルも検出する。"""
    found = set()
    def walk(directory):
        for path in directory.iterdir():
            name = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("symlinkは配布できません: " + name)
            if name == ".git":
                continue
            if stat.S_ISDIR(mode):
                if path.name != "__pycache__":
                    walk(path)
            elif stat.S_ISREG(mode):
                found.add(name)
            else:
                raise ValueError("特殊ファイルは配布できません: " + name)
    walk(root)
    return found


def expected_manifest(root):
    root = Path(root).resolve()
    allowlist = _read_json(_regular_file(root, ALLOWLIST))
    if (not isinstance(allowlist, dict) or set(allowlist) != {"schema_version", "files"}
            or type(allowlist["schema_version"]) is not int
            or allowlist["schema_version"] != 1):
        raise ValueError("未対応の配布一覧形式です")
    paths = _paths(allowlist["files"])
    if ALLOWLIST not in paths or "package.json" not in paths:
        raise ValueError("配布一覧自身とpackage.jsonも配布対象にしてください")
    actual = _inventory(root) - {MANIFEST}
    if actual != set(paths):
        raise ValueError("配布一覧との不一致: missing={}, extra={}".format(
            sorted(set(paths) - actual), sorted(actual - set(paths))))
    package = _read_json(_regular_file(root, "package.json"))
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("package.jsonに正式版のversionが必要です")
    entries = []
    for name in paths:
        content = _regular_file(root, name).read_bytes()
        entries.append({"path": name, "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()})
    return {"schema_version": 1, "version": version, "files": entries}


def generate(root):
    root = Path(root).resolve()
    expected = expected_manifest(root)
    # 失敗・中断で既存manifestを途中まで上書きしない。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root / "kit",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, root / MANIFEST)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify(root):
    root = Path(root).resolve()
    expected = expected_manifest(root)
    actual = _read_json(_regular_file(root, MANIFEST))
    # 型も含めて正規化比較し、trueと1などの暗黙の等価判定を避ける。
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("配布manifestとファイルの内容が一致しません")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            generate(args.root)
        result = verify(args.root)
    except (OSError, ValueError) as error:
        parser.exit(1, "ERROR: {}\n".format(error))
    print("OK: v{} / {} files (+ manifest)".format(result["version"], len(result["files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
