"""starter workspaceのスナップショット取得と復元（移行支援コマンド v1）。

移行では、AIが元データを解釈してworkspaceの正本へ投入する。このモジュールは
その前後で「投入前の状態へ確実に戻せる」ことだけを担当し、元データの解析・
変換は一切扱わない。契約の正本は ``docs/adr/0006-migrate-existing-records.md``。

- ``snapshot``: 対象一式を ``import/`` 配下へ原子的にコピーし、manifestを作る
- ``restore``: 指定されたスナップショットの完全性を確認し、現在の状態を退避して
  から対象を置換して戻す

対象はallowlistで固定する（``learners.md``・``schedule.md``・``activity-log.md``・
``tests.md``・``materials/`` 配下の全ファイル）。``import/``・``dist/``・
``notes.md`` などは、スナップショット・復元のいずれの対象にもしない。

manifestが持つのはファイルだけで、空ディレクトリは記録しない。復元は「新規
ファイルを残さない完全置換」のため、置換後に ``materials/`` 配下へ残った空の
ディレクトリを削除する。スナップショット時点で空だったディレクトリ（作ったが
まだ教材を置いていない ``materials/learners/<ID>/`` など）は復元されない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, TextIO

# manifestの版。未知の版は読まずに失敗する。
SCHEMA_VERSION = 1

IMPORT_DIR_NAME = "import"
MANIFEST_NAME = "manifest.json"
PAYLOAD_DIR_NAME = "payload"
# 作成途中の一時ディレクトリ。IDの正規形式に一致しないため、restoreの選択対象に
# ならない（改名が完了して初めて正式なスナップショットになる）。
TEMPORARY_PREFIX = ".tmp-"

CANONICAL_FILES = (
    "learners.md",
    "schedule.md",
    "activity-log.md",
    "tests.md",
)
MATERIALS_DIR_NAME = "materials"

SNAPSHOT_ID_PATTERN = re.compile(
    r"^snapshot-(?P<date>\d{4}-\d{2}-\d{2})-(?P<stage>[CD])-(?P<attempt>[1-9]\d*)$"
)
STAGES = ("C", "D")


class MigrationError(Exception):
    """移行支援コマンドが安全に中断したことを表す。"""


class ManifestEntry(NamedTuple):
    """manifestの1エントリ（workspace相対のPOSIXパス・サイズ・SHA-256）。"""

    path: str
    size: int
    sha256: str


class RestorePlan(NamedTuple):
    """復元で起きるパス単位の変化。"""

    added: List[str]
    changed: List[str]
    removed: List[str]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)


def _copy_file(source: Path, destination: Path) -> None:
    """1ファイルをコピーする（テストが途中失敗を差し込めるよう関数に分ける）。"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_regular_file(path: Path, label: str) -> None:
    """通常ファイルであることを確認する（symlink・特殊ファイルを拒否する）。"""

    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise MigrationError("{}: symlinkは扱えません: {}".format(label, path))
    if not stat.S_ISREG(info.st_mode):
        raise MigrationError(
            "{}: 通常ファイルではありません: {}".format(label, path)
        )


def _assert_no_symlink_in_path(root: Path, target: Path, label: str) -> None:
    """``root`` から ``target`` までの構成要素にsymlinkが無いことを確認する。"""

    try:
        relative = target.relative_to(root)
    except ValueError:
        raise MigrationError(
            "{}: 対象外の場所を指しています: {}".format(label, target)
        )
    current = root
    for part in relative.parts:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            raise MigrationError(
                "{}: 経路にsymlinkがあります: {}".format(label, current)
            )


def _assert_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise MigrationError("{}: symlinkは扱えません: {}".format(label, path))
    if not path.is_dir():
        raise MigrationError("{}: ディレクトリがありません: {}".format(label, path))


def _resolved_workspace(workspace: Path) -> Path:
    """``--workspace`` の指定自体を検査してから解決する。

    先に ``resolve()`` すると、指定そのものがsymlinkだった事実が消える。
    利用者が指し示した場所と実際に読み書きする場所を一致させるため、解決前に
    ``lstat`` で検査する。
    """

    if workspace.is_symlink():
        raise MigrationError(
            "workspace: symlinkは扱えません: {}".format(workspace)
        )
    resolved = workspace.resolve()
    _assert_directory(resolved, "workspace")
    return resolved


def is_allowlisted(relative: str) -> bool:
    """allowlist（4つの正本と ``materials/`` 配下）に含まれるパスかを返す。"""

    if relative in CANONICAL_FILES:
        return True
    parts = PurePosixPath(relative).parts
    return len(parts) > 1 and parts[0] == MATERIALS_DIR_NAME


def _assert_safe_relative(relative: str, label: str) -> None:
    """manifestのパスがworkspace相対のPOSIX表記であることを確認する。"""

    if not relative or relative != relative.strip():
        raise MigrationError("{}: パスが空か空白を含みます: {!r}".format(label, relative))
    if "\\" in relative:
        raise MigrationError(
            "{}: パス区切りは / だけです: {!r}".format(label, relative)
        )
    if relative.startswith("/"):
        raise MigrationError("{}: 絶対パスは使えません: {!r}".format(label, relative))
    parts = PurePosixPath(relative).parts
    if any(part in ("..", ".") for part in parts):
        raise MigrationError(
            "{}: .. や . を含むパスは使えません: {!r}".format(label, relative)
        )
    if not is_allowlisted(relative):
        raise MigrationError(
            "{}: allowlist外のパスです: {!r}".format(label, relative)
        )


def collect_targets(workspace: Path) -> List[str]:
    """workspace内のallowlist対象を、workspace相対のPOSIXパスで集める。"""

    workspace = workspace.resolve()
    _assert_directory(workspace, "workspace")
    found: List[str] = []
    for name in CANONICAL_FILES:
        path = workspace / name
        if path.is_symlink():
            raise MigrationError("workspace: symlinkは扱えません: {}".format(path))
        if not path.exists():
            continue
        _assert_regular_file(path, "workspace")
        found.append(name)

    materials = workspace / MATERIALS_DIR_NAME
    if materials.is_symlink():
        raise MigrationError("workspace: symlinkは扱えません: {}".format(materials))
    if materials.is_dir():
        for current_root, directories, files in os.walk(str(materials)):
            current = Path(current_root)
            for directory in sorted(directories):
                if (current / directory).is_symlink():
                    raise MigrationError(
                        "workspace: symlinkは扱えません: {}".format(
                            current / directory
                        )
                    )
            for file_name in sorted(files):
                path = current / file_name
                _assert_regular_file(path, "workspace")
                found.append(path.relative_to(workspace).as_posix())
    return sorted(found)


def _entries_for(workspace: Path, relatives: Iterable[str]) -> List[ManifestEntry]:
    entries = []
    for relative in relatives:
        path = workspace / relative
        entries.append(
            ManifestEntry(relative, path.stat().st_size, _hash_file(path))
        )
    return sorted(entries, key=lambda entry: entry.path)


def _assert_workspace_unchanged(
    workspace: Path, expected: Sequence[ManifestEntry], message: str
) -> None:
    """workspaceを再収集し、記録した状態と完全に一致することを確かめる。

    コピーや退避には時間がかかり、その間の変更は記録に含まれない。取り返しの
    つかない操作（正式名への改名・配置）へ進む直前に、必ずここを通す。
    """

    current = _entries_for(workspace, collect_targets(workspace))
    if [tuple(entry) for entry in current] != [tuple(entry) for entry in expected]:
        raise MigrationError(message)


def _manifest_document(snapshot_id: str, entries: Sequence[ManifestEntry]) -> Dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot": snapshot_id,
        "files": [
            {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
            for entry in entries
        ],
    }


def _write_manifest(directory: Path, document: Dict) -> None:
    (directory / MANIFEST_NAME).write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_manifest(directory: Path) -> List[ManifestEntry]:
    """manifestを読み、契約（版・パス・型）を検査して返す。"""

    path = directory / MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        raise MigrationError("manifestがありません: {}".format(path))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise MigrationError("manifestを読めません: {}: {}".format(path, error))
    if not isinstance(document, dict):
        raise MigrationError("manifestの形式が不正です: {}".format(path))
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise MigrationError(
            "未知のmanifest版です（対応は{}）: {!r}".format(SCHEMA_VERSION, version)
        )
    raw_files = document.get("files")
    if not isinstance(raw_files, list):
        raise MigrationError("manifestにfilesがありません: {}".format(path))

    entries: List[ManifestEntry] = []
    seen = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise MigrationError("manifestのエントリが不正です: {!r}".format(raw))
        relative = raw.get("path")
        size = raw.get("size")
        digest = raw.get("sha256")
        if not isinstance(relative, str):
            raise MigrationError("manifest: pathがありません: {!r}".format(raw))
        _assert_safe_relative(relative, "manifest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise MigrationError("manifest: sizeが不正です: {!r}".format(raw))
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MigrationError("manifest: sha256が不正です: {!r}".format(raw))
        if relative in seen:
            raise MigrationError("manifest: パスが重複しています: {}".format(relative))
        seen.add(relative)
        entries.append(ManifestEntry(relative, size, digest))
    return sorted(entries, key=lambda entry: entry.path)


def _payload_files(payload_root: Path) -> List[str]:
    """payload配下の実ファイルを、workspace相対相当のPOSIXパスで集める。"""

    if payload_root.is_symlink():
        raise MigrationError(
            "スナップショット: symlinkは扱えません: {}".format(payload_root)
        )
    if not payload_root.is_dir():
        raise MigrationError("payloadがありません: {}".format(payload_root))
    found = []
    for current_root, directories, files in os.walk(str(payload_root)):
        current = Path(current_root)
        for directory in sorted(directories):
            if (current / directory).is_symlink():
                raise MigrationError(
                    "スナップショット: symlinkは扱えません: {}".format(
                        current / directory
                    )
                )
        for file_name in sorted(files):
            path = current / file_name
            _assert_regular_file(path, "スナップショット")
            found.append(path.relative_to(payload_root).as_posix())
    return sorted(found)


def verify_snapshot(snapshot_dir: Path) -> List[ManifestEntry]:
    """manifestとpayloadの完全性を検査する（workspaceには触れない）。"""

    _assert_directory(snapshot_dir, "スナップショット")
    entries = load_manifest(snapshot_dir)
    payload_root = snapshot_dir / PAYLOAD_DIR_NAME
    actual = set(_payload_files(payload_root))
    declared = {entry.path for entry in entries}
    missing = sorted(declared - actual)
    if missing:
        raise MigrationError(
            "payloadに実体がありません: {}".format(", ".join(missing))
        )
    extra = sorted(actual - declared)
    if extra:
        raise MigrationError(
            "manifestに無いファイルがpayloadにあります: {}".format(", ".join(extra))
        )
    for entry in entries:
        path = payload_root / entry.path
        _assert_no_symlink_in_path(payload_root, path, "スナップショット")
        info = path.stat()
        if info.st_size != entry.size:
            raise MigrationError("サイズが一致しません: {}".format(entry.path))
        if _hash_file(path) != entry.sha256:
            raise MigrationError("ハッシュが一致しません: {}".format(entry.path))
    return entries


def _import_dir(workspace: Path) -> Path:
    import_dir = workspace / IMPORT_DIR_NAME
    if import_dir.is_symlink():
        raise MigrationError("import: symlinkは扱えません: {}".format(import_dir))
    return import_dir


def validate_snapshot_id(value: str) -> str:
    """``--snapshot`` の値を、パスではなく名前として検証する。"""

    if any(character in value for character in ("/", "\\", ".")):
        raise MigrationError(
            "スナップショットIDにパス区切りや . は使えません: {!r}".format(value)
        )
    if not SNAPSHOT_ID_PATTERN.fullmatch(value):
        raise MigrationError(
            "スナップショットIDの形式が違います"
            "（snapshot-YYYY-MM-DD-C|D-連番）: {!r}".format(value)
        )
    return value


def resolve_snapshot_dir(workspace: Path, snapshot_id: str) -> Path:
    """IDから ``import/`` 直下のスナップショットを解決する。"""

    validate_snapshot_id(snapshot_id)
    import_dir = _import_dir(workspace)
    _assert_directory(import_dir, "import")
    candidate = import_dir / snapshot_id
    resolved = candidate.resolve()
    if resolved.parent != import_dir.resolve():
        raise MigrationError(
            "スナップショットはimport/直下だけです: {}".format(candidate)
        )
    _assert_directory(candidate, "スナップショット")
    return candidate


def _snapshot_id(migration_date: str, stage: str, attempt: int) -> str:
    return "snapshot-{}-{}-{}".format(migration_date, stage, attempt)


def _next_attempt(import_dir: Path, migration_date: str, stage: str) -> int:
    """未使用の試行番号を返す（既存のスナップショットは上書きしない）。"""

    attempt = 1
    # exists()はsymlinkを追うため、壊れたsymlinkが置かれたIDを空きと誤判定する。
    while os.path.lexists(str(import_dir / _snapshot_id(migration_date, stage, attempt))):
        attempt += 1
    return attempt


def create_snapshot(
    workspace: Path,
    migration_date: str,
    stage: str,
    *,
    writer: Optional[TextIO] = None,
) -> str:
    """allowlist対象を ``import/`` 配下へ原子的にコピーし、IDを返す。"""

    out = writer if writer is not None else sys.stdout
    workspace = _resolved_workspace(workspace)
    if stage not in STAGES:
        raise MigrationError("段階はCまたはDです: {!r}".format(stage))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", migration_date):
        raise MigrationError("移行基準日はYYYY-MM-DDです: {!r}".format(migration_date))
    try:
        date.fromisoformat(migration_date)
    except ValueError:
        raise MigrationError(
            "移行基準日が実在しません: {!r}".format(migration_date)
        )

    relatives = collect_targets(workspace)
    entries = _entries_for(workspace, relatives)

    import_dir = _import_dir(workspace)
    import_dir.mkdir(parents=True, exist_ok=True)
    attempt = _next_attempt(import_dir, migration_date, stage)
    snapshot_id = _snapshot_id(migration_date, stage, attempt)
    final_dir = import_dir / snapshot_id

    temporary = Path(
        tempfile.mkdtemp(prefix=TEMPORARY_PREFIX + snapshot_id + "-", dir=str(import_dir))
    )
    try:
        payload_root = temporary / PAYLOAD_DIR_NAME
        payload_root.mkdir()
        for entry in entries:
            _copy_file(workspace / entry.path, payload_root / entry.path)
        _write_manifest(temporary, _manifest_document(snapshot_id, entries))
        # コピー中に元ファイルが変わっていた場合、payloadはmanifestと一致しない。
        # 復元できないものを「作成しました」と報告しないため、改名前に検証する。
        verify_snapshot(temporary)
        # payloadの整合だけでは、コピー済みファイルの後からの変更や、コピー中に
        # 増えたファイル（manifestから漏れる）を検出できない。workspace全体を
        # 取り直して、記録した状態と一致することまで確かめる。
        _assert_workspace_unchanged(
            workspace,
            entries,
            "コピー中にworkspaceが変わりました。取り直してください"
            "（スナップショットは作成していません）",
        )
        # 改名が完了して初めて正式なスナップショットになる。既存IDは上書きしない
        # （壊れたsymlinkが置かれている場合もexists()ではなくlexists()で見る）。
        if os.path.lexists(str(final_dir)):
            raise MigrationError(
                "同じIDのスナップショットが既にあります: {}".format(snapshot_id)
            )
        os.rename(str(temporary), str(final_dir))
    except BaseException:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise

    print(
        "スナップショットを作成しました: {}（{}件）".format(snapshot_id, len(entries)),
        file=out,
    )
    return snapshot_id


def plan_restore(workspace: Path, entries: Sequence[ManifestEntry]) -> RestorePlan:
    """復元で追加・変更・削除されるパスを求める（workspaceは変更しない）。"""

    return _plan_from(_entries_for(workspace, collect_targets(workspace)), entries)


def _plan_from(
    current_entries: Sequence[ManifestEntry], entries: Sequence[ManifestEntry]
) -> RestorePlan:
    """現在の状態とmanifestの差からRestorePlanを作る。"""

    current = {entry.path: entry for entry in current_entries}
    declared = {entry.path: entry for entry in entries}
    added = sorted(path for path in declared if path not in current)
    removed = sorted(path for path in current if path not in declared)
    changed = sorted(
        path
        for path, entry in declared.items()
        if path in current and current[path].sha256 != entry.sha256
    )
    return RestorePlan(added=added, changed=changed, removed=removed)


def _describe_plan(plan: RestorePlan, snapshot_id: str) -> str:
    lines = ["{} へ復元します。".format(snapshot_id)]
    for label, paths in (
        ("追加（復元で作られる）", plan.added),
        ("変更（復元で上書きされる）", plan.changed),
        ("削除（復元で消える）", plan.removed),
    ):
        lines.append("{}: {}件".format(label, len(paths)))
        lines.extend("  - {}".format(path) for path in paths)
    if plan.is_empty:
        lines.append("（現在の状態はスナップショットと同一です）")
    return "\n".join(lines)


def _confirm(reader: TextIO, writer: TextIO) -> bool:
    """復元してよいか確認する。EOF・未回答・否定は復元しない。"""

    print("復元しますか？ [y/N]: ", end="", file=writer)
    writer.flush()
    try:
        answer = reader.readline()
    except EOFError:
        return False
    if not answer:
        return False
    return answer.strip().lower() in ("y", "yes")


def _rollback_id(snapshot_id: str, take: int) -> str:
    return "rollback-{}-{}".format(snapshot_id, take)


def _matches_current(directory: Path, entries: Sequence[ManifestEntry]) -> bool:
    """既存の退避が、現在の状態と完全に一致しているかを返す。"""

    try:
        stored = verify_snapshot(directory)
    except MigrationError:
        return False
    return [tuple(entry) for entry in stored] == [tuple(entry) for entry in entries]


def _stash_current(
    workspace: Path, import_dir: Path, snapshot_id: str, entries: Sequence[ManifestEntry]
) -> Path:
    """現在の対象一式を退避する。完全で同一な退避があれば再利用する。"""

    take = 1
    while True:
        candidate = import_dir / _rollback_id(snapshot_id, take)
        if not candidate.exists():
            break
        if _matches_current(candidate, entries):
            return candidate
        take += 1

    temporary = Path(
        tempfile.mkdtemp(
            prefix=TEMPORARY_PREFIX + _rollback_id(snapshot_id, take) + "-",
            dir=str(import_dir),
        )
    )
    try:
        payload_root = temporary / PAYLOAD_DIR_NAME
        payload_root.mkdir()
        for entry in entries:
            _copy_file(workspace / entry.path, payload_root / entry.path)
        _write_manifest(
            temporary, _manifest_document(_rollback_id(snapshot_id, take), entries)
        )
        verify_snapshot(temporary)
        if os.path.lexists(str(candidate)):
            raise MigrationError("退避先が既にあります: {}".format(candidate))
        os.rename(str(temporary), str(candidate))
    except BaseException:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise
    return candidate


def _prune_empty_directories(root: Path) -> None:
    """``materials/`` 配下に残った空ディレクトリを片付ける（root自体は残す）。"""

    if not root.is_dir():
        return
    for current_root, directories, _files in os.walk(str(root), topdown=False):
        current = Path(current_root)
        if current == root:
            continue
        if not any(current.iterdir()):
            current.rmdir()


def _place(workspace: Path, payload_root: Path, entries: Sequence[ManifestEntry]) -> None:
    """allowlist対象をスナップショットの内容で完全に置換する。"""

    declared = {entry.path for entry in entries}
    for relative in collect_targets(workspace):
        if relative not in declared:
            (workspace / relative).unlink()
    _prune_empty_directories(workspace / MATERIALS_DIR_NAME)
    for entry in entries:
        _copy_file(payload_root / entry.path, workspace / entry.path)


def restore_snapshot(
    workspace: Path,
    snapshot_id: str,
    *,
    reader: Optional[TextIO] = None,
    writer: Optional[TextIO] = None,
) -> bool:
    """スナップショットの内容へ戻す。復元したらTrue、中止ならFalseを返す。"""

    out = writer if writer is not None else sys.stdout
    source = reader if reader is not None else sys.stdin
    workspace = _resolved_workspace(workspace)

    # 1. workspaceへ触れる前に、復元材料の完全性を確認する。
    snapshot_dir = resolve_snapshot_dir(workspace, snapshot_id)
    entries = verify_snapshot(snapshot_dir)

    # 2. 変化するパスを提示し、確認を得る。
    shown = _entries_for(workspace, collect_targets(workspace))
    print(_describe_plan(_plan_from(shown, entries), snapshot_id), file=out)
    if not _confirm(source, out):
        print("復元を中止しました（変更していません）。", file=out)
        return False

    # 3. 提示した内容のまま復元する。確認の待ち時間にworkspaceが変わっていたら、
    #    画面に出ていない変更を上書きしてしまうため、退避も復元も行わない。
    current = _entries_for(workspace, collect_targets(workspace))
    if [tuple(entry) for entry in current] != [tuple(entry) for entry in shown]:
        raise MigrationError(
            "確認中にworkspaceが変わりました。差分を確認し直してください"
            "（変更していません）"
        )

    import_dir = _import_dir(workspace)
    stash = _stash_current(workspace, import_dir, snapshot_id, current)

    # 4. 退避に含まれないものを消さないため、破壊的な配置の直前にもう一度確かめる。
    #    退避中に増えたファイルは、rollbackへ入らないまま_place()で消えてしまう。
    _assert_workspace_unchanged(
        workspace,
        current,
        "退避中にworkspaceが変わりました。差分を確認し直してください"
        "（退避: {} / 復元していません）".format(stash),
    )

    payload_root = snapshot_dir / PAYLOAD_DIR_NAME
    try:
        _place(workspace, payload_root, entries)
    except BaseException as error:
        raise MigrationError(
            "復元が未完了です。退避: {} / スナップショット: {} / 原因: {}".format(
                stash, snapshot_dir, error
            )
        )

    # 5. 配置後の状態がmanifestと一致することを確認する。
    after = plan_restore(workspace, entries)
    if not after.is_empty:
        raise MigrationError(
            "復元が未完了です（内容が一致しません）。退避: {} / スナップショット: {}".format(
                stash, snapshot_dir
            )
        )
    print(
        "復元しました: {}（退避: {}）".format(snapshot_id, stash.name),
        file=out,
    )
    return True


def _parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "starter workspaceのスナップショット取得と復元。"
            "元データの解析・変換は行いません。"
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="対象一式をimport/へコピーする")
    snapshot.add_argument("--workspace", type=Path, required=True)
    snapshot.add_argument("--date", required=True, help="移行基準日（YYYY-MM-DD）")
    snapshot.add_argument("--stage", required=True, choices=STAGES)

    restore = sub.add_parser("restore", help="スナップショットの内容へ戻す")
    restore.add_argument("--workspace", type=Path, required=True)
    restore.add_argument(
        "--snapshot", required=True, help="スナップショットID（最新は暗黙に選ばない）"
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parse(argv)
    try:
        if arguments.command == "snapshot":
            create_snapshot(arguments.workspace, arguments.date, arguments.stage)
            return 0
        restored = restore_snapshot(arguments.workspace, arguments.snapshot)
        return 0 if restored else 1
    except MigrationError as error:
        print("NG: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
