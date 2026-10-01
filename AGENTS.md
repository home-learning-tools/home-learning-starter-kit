# Codex向け入口

このリポジトリでのAI運用ルールの正本は
[docs/AI-OPERATIONS.md](docs/AI-OPERATIONS.md) です。作業前に必ず読み、
その内容に従ってください。

- このファイルと `CLAUDE.md` は入口だけを提供し、ルール本文を持ちません
- ルールの追加・変更は `docs/AI-OPERATIONS.md` へ反映します（ここへ転記しない）
- 矛盾がある場合は `docs/AI-OPERATIONS.md` を優先します

最重要の3点だけ再掲します。

1. 家庭のデータは `workspace/`（Git管理外）だけに書く
2. 実績は利用者の報告があってから記録する（推測・捏造しない）
3. 変更後は `python3 -m kit.starter_web.checkup` 系の検証を実行し、結果を報告する
