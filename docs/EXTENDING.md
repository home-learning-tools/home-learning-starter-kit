# 拡張ガイド（下流リポジトリ向けの安定契約）

このリポジトリは家庭学習運用の**汎用core**です。家庭固有の拡張（独自のWS種別、
独自ルール、別形式の入力アダプタなど）は、このリポジトリを利用する側の
リポジトリ（以下「下流」。fork や非公開の拡張リポジトリ）で行います。

このリポジトリに `extensions/` などの拡張用ディレクトリは設けません。
その代わり、下流が依存してよい**安定した契約面**をここに定義します。
下流はこの契約面だけに依存し、coreの内部実装を直接改変しないでください。

## 安定API

| API | 内容 |
|---|---|
| `kit.starter_web.model` の公開データ型 | `DashboardData`・`ScheduleItem`・`ActivityItem`・`TestEvent`・`MaterialLink`・`MaterialRequirement`・`MaterialStatus`・`SourceRef`・`Status`・`ScheduleKind`、および `ScheduleItem.matching_materials()`（実績突合に使う教材）・`ScheduleItem.material_files()`（画面に出す教材ファイル） |
| 入力アダプタ契約 | `load() -> DashboardData` を持つクラス。上記の公開データ型と下記の変換APIを利用して構築する |
| 画面生成 | `kit.starter_web.render.render_dashboard(data: DashboardData, output_path: Path, *, auto_reload: bool = False, presentation: Optional[Presentation] = None) -> Path` |
| 画面の文言・セクション構成 | `kit.starter_web.presentation` の `Presentation`・`CatalogView`・`CatalogGroup`・`CatalogItem`・`GuideView`・`GuideTerm`・`ExtraSection`・`PendingBadgeMode`・`MaterialLinkScope`・`CORE_SECTION_IDS`、および `Presentation.for_mode(mode)`（core既定）|
| HTMLエスケープ | `kit.starter_web.render.escape_text(value) -> str`（追加セクションの本文で使う）|

- `auto_reload=True` は、基準日（`DashboardData.anchor_date`）が生成時の実行日と
  一致する場合にだけ指定します。日付変更を検知して再読み込みするスクリプトが入るため、
  基準日を固定した生成では既定の `False` のままにします
- `MaterialLink` は「開くための対象」（`target`）と「コピーするための論理パス」
  （`copy_path`・省略可）を分けて持ちます。`copy_path` は
  `kit.starter_web.markdown.resolve_links()` が**許可ルート基準の相対POSIXパス**として
  作るので、独自アダプタでも同関数を通せば自動で入ります。自前で組み立てる場合は
  `MaterialLink(label, target)` の2項目のままでよく、そのリンクにはコピーbuttonが
  出ないだけです（`target` から相対ルートを推測することはしません）

  ```python
  # 許可ルート＝作業ルート。private はリポジトリルート、starter は workspace ルート。
  links = resolve_links(cell_text, source_path, allowed_root)
  links[0].copy_path  # -> "materials/learners/learner-a/example.html"
  ```

### `DashboardData.mode`

`mode` は画面上部のラベルと、新規利用者向けセクション（用語ガイド・
「AIにできること」）の出し分けに使います。coreが定義する値は次の3つです。

| mode | 用語ガイド | AIにできること | 用途 |
|---|---|---|---|
| `demo` | 展開 | 表示 | 同梱の架空demo |
| `starter` | 折りたたみ | 表示 | starter形式の自分のworkspace |
| `private` | 非表示 | 表示 | 開発元の互換アダプタ |

**上記以外の値も渡せます。** その場合、生成は通常どおり成功し、上の2つの
セクションだけを省きます。coreが用意した案内文はstarter形式の
`workspace/`・`materials/` を前提にしているため、独自の運用へ推測で
当てはめないためです。予定・実績・要確認など画面の本体は同じように出ます。

独自modeで案内セクションを出したい場合は、既知のmode値を流用せず、次の
「画面の文言・セクション構成」で自分の運用に合った文言を渡してください。

- **合成は下流の責務**: 下流は自前のCLI・スクリプトで「独自アダプタの
  `load()` → `render_dashboard()`」を組み合わせます。coreのCLI
  （`kit.starter_web.__main__`）や `render.py`・`checkup.py` を書き換えません
- **下流形式の検証**: 独自入力形式の検証は、下流側で `load()` の前に実行します。
  starter形式のworkspaceにはcoreの `checkup` をそのまま使います
- 独自形式の互換アダプタは下流側へ置きます。開発元の家庭専用アダプタは
  coreに同梱せず、下流からこの契約を利用します

## アダプタの変換・照合API

`kit.starter_web.adapter_support` は両形式で同じ意味になる処理だけを持ちます。
家庭の名前・ディレクトリ・教科推定・入力allowlistは持ちません。

| API | 契約 |
|---|---|
| `nearest_heading(lines, before_line, level) -> str` | 指定行より前の指定レベルの見出し。行番号は1始まり、未検出は空文字 |
| `schedule_kind(value) -> ScheduleKind` | 明示した本番マーカーだけで予定種別を判断 |
| `without_test_marker(value) -> str` | Markdown装飾と本番マーカーを除いた本文 |
| `activity_status(title, result) -> Status` | 内容と結果の先頭にある実施状態を読む。過去経緯の記述で状態を上書きしない |
| `optional_iso_date(value) -> Optional[date]` | 空・ダッシュはNone、それ以外はISO日付。不正値はValueError |
| `parse_test_event_tables(source_path, required_heading=None) -> List[TestEvent]` | 旧形式の5列表の読取。見出し指定時は直前の第3レベル見出しと照合。必須値・重複はValueError |
| `assert_future_test_markers_match(schedule, test_events, validation_date) -> None` | 指定基準日以降の本番予定をテスト表の日付・学習者と照合。不一致はValueError |
| `enrich_schedule(schedule, activities) -> None` | 予定リストの状態を対応実績から更新。実績は変更しない。参考リンクは突合対象外 |

Markdownの字句処理・表・日付・リンク解決は `kit.starter_web.markdown` の
`markdown_tables`、`split_table_row`、`strip_markdown`、`parse_iso_date`、
`first_date`、`resolve_links` を利用できます。`resolve_links` には入力元と許可ルートを
明示し、リンクが許可範囲外ならエラーとします。教材マーカーの読取は
`kit.starter_web.requirements.parse_requirements`、反復日付は
`kit.starter_web.recurrence.parse_weekdays` / `routine_dates` を使います。

同期警告は `kit.starter_web.sync_report.sync_issues(data, anchor_date=None, grace_days=1)`
が `List[SyncIssue]`（`path: Path`、`line: int`、`message: str`）として返します。
この関数はファイルを読まず、予定/実績を変更しません。CLI固有のseverity・表示・
終了コードへの変換は下流の責務です。記録なしを未実施と確定する用途には使いません。

## 画面の文言・セクション構成

coreの案内文はstarter形式（`workspace/`・`materials/`・`docs/AI-OPERATIONS.md`
の依頼例）を前提に書かれています。別の配置・別の言い方で運用する下流は、
`render_dashboard()` の `presentation` へ `Presentation` を1つ渡して差し替え
ます。セクションごとに引数を増やさないための集約点です。

```python
from dataclasses import replace

from kit.starter_web.presentation import (
    CatalogGroup, CatalogItem, CatalogView, ExtraSection, Presentation,
)
from kit.starter_web.render import escape_text, render_dashboard

catalog = CatalogView(
    groups=(CatalogGroup(items=(CatalogItem("plan", "来週の計画を作って", "…"),)),),
    lead="そのまま頼める言い方です。",
)
base = Presentation.for_mode("starter")            # core既定から始める
render_dashboard(data, output, presentation=replace(base, catalog=catalog))
```

差し替えられるもの:

| 要素 | 内容 |
|---|---|
| `mode_label`・`privacy_note` | 画面上部のラベルと注意書き |
| `guide` | 用語ガイド（`None` で非表示）|
| `catalog` | 「AIにできること」の見出し・案内文・行（`None` で非表示）|
| `routine_note` | 「今日の学習」の反復ルーティンについての注記 |
| `extra_sections` | 下流独自の追加セクション |
| `hidden_section_ids` | 非表示にするcoreセクション（本体もナビも出しません）|
| `recent_activity_days` | 「最近の実績」を件数ではなく実施日数で選ぶ場合の日数 |
| `pending_badge_mode` | 未実施の枠に、予定表上の状態と教材の準備状態のどちらを出すか |
| `favicon` | ブラウザのタブに出すアイコン（`None` でリンクを出しません）|
| `week_navigation_enabled` | 「3週間の見通し」を1週間ずつ前後へ移動できるようにするか |

- **進捗判定**: `CatalogView.progress` は `DashboardData` から項目IDごとの「済」を
  返す関数です。下流の判定に差し替えられ、`None` を渡すと判定を持たない表示に
  なり、済／未のバッジと「N/M」の集計を出しません。判定材料が常にそろっていて
  集計が情報を持たない運用では、`None` を選んでください
- **追加セクション**: `ExtraSection(id, heading, body, ..., after=…)` の `body` は
  `(data, output_dir) -> HTML文字列` で、`after` に指定したセクションの直後へ
  入ります。指定できるIDは `CORE_SECTION_IDS` と、先に置いた追加セクションの
  IDです。同じ位置を指す追加セクションは `extra_sections` の定義順のまま並び
  ます。挿入位置が見つからない場合とIDが重複する場合はエラーになります
  （黙って末尾へ落としません）。本文のエスケープは下流の責務で、
  `escape_text()` を使ってください
- **セクションの非表示**: `hidden_section_ids` に挙げたcoreセクションは、本体も
  ページ内ナビも出しません。`CORE_SECTION_IDS` に無いIDはエラーになります
- **タブのアイコン**: `favicon` は `<link rel="icon">` の `href` にそのまま入ります。
  生成物は単一HTMLで配れることを前提にしているため、**`data:` URI だけを受け付け
  ます**（例: `data:image/png;base64,…`）。`None`（core既定）ならリンク要素ごと
  出しません。次の値は構成時に `ValueError` です。
  - `https://…` などの外部URL・`./icon.png` などの相対パス: 生成物を渡した先で
    画像が出ず、閲覧のたびに外部への通信も起きるため
  - 空文字: ブラウザがページ自身をアイコンとして取りに行くため、「指定しない」と
    同じにしません
  （黙って無視しません）。非表示にしたセクションを `after` に指定した追加
  セクションも、挿入位置なしとしてエラーになります
- **未実施の枠に出す状態**: `pending_badge_mode` の既定は `PendingBadgeMode.STATUS`
  で、従来どおり `○ 予定`・`● 準備済み` を出します。
  `PendingBadgeMode.MATERIAL_READINESS` を渡すと、代わりに教材の準備状態
  （`● 教材あり` / `● 外部教材あり` / `◐ 一部未準備` / `⚠ 教材未準備` / `— 教材不要`）を出し、凡例も
  「教材」「実施」「その他」に分かれます。判定は状態の記載ではなく実在ファイルで
  行うため、「作成済み」と書いてあっても実体が無い枠は `教材未準備` になります。
  実施状態（完了・一部実施・未実施・繰越）→ 反復ルーティン → テスト本番 →
  過去日で実績が未反映の枠（`? 実施状況未確認`）→ 教材状態、の順に決まります。
  教材の要否を1つの予定のなかで分けたい場合は、アダプタ側で
  `ScheduleItem.material_requirements` に `MaterialRequirement` を並べます
  （予定は1件のままで、教材判定だけが項目単位になります）。「教材未準備」一覧も
  同じ `ScheduleItem.material_status()` を使うため、カードと一覧は必ず一致します。
  その日に使わない参考・在庫は `ScheduleItem.reference_materials` へ入れます。
  教材判定と実績の突合はどちらも `ScheduleItem.matching_materials()` を通るため、
  参考リンクのファイル名一致で予定が実施済みになることはありません
- **カードに出す教材リンクの範囲**: `card_material_links` の既定は
  `MaterialLinkScope.ALL` で、従来どおり予定セルから読めたリンクをすべて出します
  （`《参考:…》` の在庫は `参考` の印付き）。`MaterialLinkScope.MATERIAL_FILES` を
  渡すと、「今日の学習」と「3週間の見通し」は**その枠で使う教材ファイル**
  （`ScheduleItem.material_files()`＝実施項目の `.html` / `.pdf`）だけを出し、
  正本Markdownなどの参照リンクと参考・在庫リンクを出しません。予定セルへ経緯や
  参照先を書き込む運用（private）で、カードが教材以外のリンクで埋まるのを防ぐため
  の指定です。**表示の範囲だけが変わり、教材状態・「教材未準備」の判定と件数は
  どちらの値でも同じ**です。「3週間の見通し」は幅が狭いため、どちらの値でも教材名
  を出さずアイコンリンクにし、参考・在庫は並べません
- **「3週間の見通し」の週移動**: `week_navigation_enabled` の既定は `False`
  です。`True` にすると、初期表示の前週・今週・翌週を1週間ずつ前後へ移動する
  ボタンを出します。移動しても `DashboardData.anchor_date` と他セクションの集計範囲は
  変わりません。下流adapterは、表示してよい週の両端を
  `DashboardData.week_view_start` / `week_view_end` に渡し、その範囲へ予定と反復
  ルーティンを展開してください。両端を省略した場合は初期3週間だけになり、ボタンは
  境界で無効になります。予定の入力元をレンダラーが探索・補完することはありません
- **「最近の実績」の範囲**: `recent_activity_days` を指定すると、件数（既定12件）
  ではなく、実績のある直近N日ぶんを日単位で全件出します。同じ日の記録が件数の
  途中で切れず、実績のない暦日は数えません。実績カード・「最近の学習時間」・
  「今週の教材」は `Presentation.recent_activities(data)` の同じ結果を使うため、
  表示と集計の対象が食い違いません
- **省略時**: `presentation` を渡さない場合は `Presentation.for_mode(data.mode)`
  ＝core既定で、従来どおりの表示になります

差し替えても、下の「普遍ルール」は変わりません。特に、実施していない学習を
「済」と表示する判定や、実データを含む画面の公開・共有は、この拡張点でも
認められません。

## 契約面の全体像

1. **入力アダプタ契約**（上記）
2. **画面の文言・セクション構成**: `Presentation` で差し替える（上記）
3. **AIルール層**: `docs/AI-OPERATIONS.md` が基本層。下流は入口
   （`CLAUDE.md`・`AGENTS.md`）から自分の追加ルール文書を重ねられる。
   より具体的な指示として追加層が優先される
4. **データ契約の拡張**: starter形式の列・状態・教材種別（`material-type`）を
   増やす変更は、coreの入力契約・検証・テスト・移行手順を同じ変更で更新する
   場合に限る。下流だけで列や状態を増やさない
   現行契約の正本は [WORKSPACE-CONTRACT.md](WORKSPACE-CONTRACT.md)
5. **検証の追加**: 下流は独自検証を追加してよい。coreの検証を置き換えたり
   緩めたりしない
6. **汎用改善の還元**: 下流で生まれた汎用的な改善は、個人情報・家庭固有の
   内容を除いて一般化し、このリポジトリへIssue・PRで先に還元する

## ルールの二層（何を上書きできるか）

**普遍ルール（どの下流でも上書き不可）**

- 公開の場（publicリポジトリ・共有先・デプロイ先）へ、実在の個人データ・
  学習記録・第三者教材（塾・市販・過去問の本文、設問、解答、スキャン）を
  出さない
- 実績を利用者の報告なしに推測・捏造して記録しない
- coreに同梱された検証（`checkup`・kitテスト）を無効化・緩和して合格を
  装わない

**下流が明示的に再定義できるもの**

- 予定・実績の正本の配置（例: `workspace/` 以外の独自ディレクトリ構成）
- 画面に出す文言・セクション構成（`Presentation`。用語ガイド・「AIにできること」の
  行と進捗判定・追加セクション。ただし未実施を「済」と見せる判定は不可）
- Git管理方針（例: 非公開の下流リポジトリで、実データを複数PC共有のために
  Git管理する運用）
- 許可するディレクトリ構成・トップレベル領域
- 利用する権利を確認済みの教材の保管方針

再定義する場合は、下流のルール文書（`CLAUDE.md`・`AGENTS.md` から参照される
文書）に明記し、下流側の検証もその方針に合わせて更新してください。
暗黙の逸脱は、AIにも人にも「事故」と区別できません。

## 漏えい検査について

このリポジトリ（public）に漏えい検査スクリプトは同梱されていません。現在の
漏えい検査は、開発元の非公開下流リポジトリがpublicリリース候補を抽出する際の
ゲートとして運用されています。下流からの還元PR向けにpublic側で使える汎用の
漏えい検査は、将来のリリースで提供を検討しています。それまで下流からの還元は、
上記の普遍ルールを満たすことを下流側の責任で確認してください。

### テスト予定カード内の追加表示

`Presentation.test_event_extra` は任意の `(event, data, output_dir) -> str` 関数。
日付が確定し表示対象となった各予定カードの本文末尾へ、返したHTMLを追加する。
未指定・空文字では既存の表示を維持し、日付未定の行には呼び出さない。
HTMLのエスケープとリンクの検証は信頼済みの下流コードの責務。
`TestEvent.attempt_id` はworkspaceのテスト実施IDを引き継ぐ任意文字列。
旧形式などIDのない経路では空文字で、タイトルや日付から生成しない。
下流の判定・データ形式はcoreへ持ち込まない。
