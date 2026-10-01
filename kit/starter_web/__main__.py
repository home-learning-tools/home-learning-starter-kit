"""家庭学習Web MVPのコマンドライン入口（starter形式）。

下流の拡張は、このCLIを書き換えずに自前のCLIから
``kit.starter_web.render.render_dashboard`` を呼ぶ（契約は
``docs/EXTENDING.md``）。
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from .adapters import StarterMarkdownAdapter
from .markdown import is_within
from .model import MaterialLink, ScheduleKind, SourceRef
from .render import render_dashboard


def _arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Markdown正本から読み取り専用の静的Webを生成します。"
    )
    parser.add_argument(
        "--mode",
        choices=("starter", "demo"),
        required=True,
        help="starter=公式サポートのstarter形式（demoは互換別名）",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="表示の基準日（YYYY-MM-DD、未指定時は今日）",
    )
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def _assert_demo_isolation(data, demo_root: Path) -> None:
    """demoの参照先が架空データのディレクトリ内に閉じていることを確認する。"""

    for item in list(data.schedule) + list(data.activities) + list(data.test_events):
        if item.source and not is_within(Path(item.source.path), demo_root):
            raise ValueError("demoの正本参照がdemo外です: {}".format(item.source.path))
    for item in list(data.schedule) + list(data.activities) + list(data.test_events):
        for material in item.materials:
            path_text = material.target.split("#", 1)[0]
            if not is_within(Path(path_text), demo_root):
                raise ValueError("demoの教材参照がdemo外です: {}".format(path_text))


def _stage_demo_content(data, demo_root: Path, output_dir: Path) -> None:
    """demoが生成先だけで閲覧できるよう、参照ファイルのみを複製する。"""

    content_root = output_dir.resolve() / "demo-content"
    copied = {}

    def copy_target(path_text: str) -> Path:
        source = Path(path_text).resolve()
        if not is_within(source, demo_root):
            raise ValueError("demo外のファイルは複製できません: {}".format(source))
        if not source.is_file():
            raise ValueError("demoの参照ファイルがありません: {}".format(source))
        if source not in copied:
            destination = content_root / source.relative_to(demo_root.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))
            copied[source] = destination.resolve()
        return copied[source]

    source_items = list(data.schedule) + list(data.activities) + list(data.test_events)
    for item in source_items:
        if item.source:
            staged_source = copy_target(item.source.path)
            item.source = SourceRef(
                path=str(staged_source),
                line=item.source.line,
            )
    for item in list(data.schedule) + list(data.activities) + list(data.test_events):
        staged_materials = []
        for material in item.materials:
            path_text, separator, fragment = material.target.partition("#")
            staged_target = str(copy_target(path_text))
            if separator:
                staged_target += "#" + fragment
            staged_materials.append(
                # コピー値はworkspace相対のまま持ち回る。複製先（demo-content/）
                # から見た相対表現に置き換えない。
                MaterialLink(
                    label=material.label,
                    target=staged_target,
                    copy_path=material.copy_path,
                )
            )
        item.materials = staged_materials


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _arguments(argv)
    repo_root = args.repo_root.resolve()

    # 基準日が「実行日」なのか「固定した日」なのかは、値ではなく決め方で判断する。
    # 固定した日がたまたま実行日と一致することがあり、値では区別できない。
    anchor_is_fixed = args.date is not None

    default_demo = (repo_root / "examples" / "demo").resolve()
    workspace = (args.workspace or default_demo).resolve()
    output = args.output or repo_root / "dist" / "starter" / "demo" / "index.html"
    anchor = args.date or date.today()
    mode = "demo" if workspace == default_demo else "starter"
    data = StarterMarkdownAdapter(workspace, anchor, mode=mode).load()
    if args.date is None and data.schedule:
        ordered_dates = sorted(
            {
                item.date
                for item in data.schedule
                if item.kind != ScheduleKind.ROUTINE
            }
        )
        if ordered_dates:
            anchor = ordered_dates[len(ordered_dates) // 2]
            anchor_is_fixed = True
            data = StarterMarkdownAdapter(workspace, anchor, mode=mode).load()
    _assert_demo_isolation(data, workspace)
    _stage_demo_content(data, workspace, Path(output).resolve().parent)

    # 固定した基準日に日付変更の自動再読み込みを付けると、意図した固定日から
    # 勝手に離れてしまうため、実行日を基準にした生成にだけ付ける。
    destination = render_dashboard(data, output, auto_reload=not anchor_is_fixed)
    print("生成しました: {}".format(destination))
    return 0


def cli(argv: Optional[Sequence[str]] = None) -> int:
    """CLIでは入力エラーをtracebackにせず、要点だけ表示する。"""

    try:
        return main(argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
