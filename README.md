# home-learning-starter-kit

VS Code上のAIアシスタント（Claude Code、Codex等）を**AI家庭学習マネージャー**
として使い、家庭学習を「計画 → ワークシート → 実施 → 記録 → 次の調整」の
ループで続けるためのスターターキットです。

AIへ自然言語で依頼すると、共通ルールに従って次の一連が進みます。

1. **初期設定** — 質問に答えると、Git管理外の `workspace/` に自分の記録場所ができる
2. **学習者・教材・テスト追加** — 正本台帳へ安全に登録する
3. **週間学習計画** — 目標と使える時間から `schedule.md` に予定を作る
4. **ワークシート作成** — 公開テンプレートから印刷用HTMLを作り、検証する
5. **実績登録** — 報告された結果を通常実績またはテスト実施回へ記録する
6. **週次振り返り** — 実績から翌週の継続・調整を提案する
7. **Webダッシュボード** — 結果を読み取り専用の静的ページで確認する

架空の家族で動かした画面は、インストールせずに [公開demo](https://home-learning-tools.github.io/home-learning-starter-kit/) で見られます。

Webは主役ではなく、AIが管理した結果を見る画面です。正本の構成は
[workspace契約](docs/WORKSPACE-CONTRACT.md) で定義し、
入力フォーム、認証、クラウド同期、AI APIの組み込み、外部送信機能はありません。
AIを使わず、同じファイルを手で編集して運用することもできます。

> [!CAUTION]
> **実データをpublic forkへcommitしないでください。**
> 実名、学習記録、成績、詳細日程、教材、帳票、スキャンを一度commitすると、
> 後からファイルを削除してもGit履歴に残ります。実データは同梱の `.gitignore` で
> 除外される `workspace/` またはリポジトリ外に保存してください。
> 自分のデータから生成した `dist/` も、公開・共有・GitHub Pagesへ配置しないで
> ください。

## 必要なもの

- Python 3.9以降
- Git（cloneする場合）
- AIで運用する場合: VS Code＋Claude CodeまたはCodex等のAIコーディングアシスタント

追加のPythonパッケージは不要です。Windowsで `python3` が見つからない場合は、
以下のコマンドを `python` に読み替えてください。

## 1. リポジトリを取得する

GitHubの「Code」ボタンで表示されるClone URLを使います。
`YOUR_CLONE_URL` は実際のURLへ置き換えてください。

```sh
git clone YOUR_CLONE_URL
cd home-learning-starter-kit
```

ZIPで取得した場合は、展開したディレクトリへ移動してください。

## 2. 架空demoを見る

同じ画面は [公開demo](https://home-learning-tools.github.io/home-learning-starter-kit/) でも見られます。手元で生成する場合は次を実行します。

```sh
python3 -m kit.starter_web --mode starter
```

生成された `dist/starter/demo/index.html` をブラウザで開きます。ここに表示される
人物、予定、実績はすべて架空で、教材は公開用に作成したオリジナルです。

上部の「はじめての方へ」に画面の用語の説明が、下部の「AIにできること」に
**そのまま頼める言い方と、そのとき起きること**が並びます。後者は自分の
workspaceで生成すると、実際に行ったものが「済」になり、次に何を頼めるかが
分かります。基本の流れの下には、学習者・共通教材・個別リベンジWS・単体／
定期テスト・既存記録の移行を必要なものだけ試せる「発展チュートリアル」も
表示されます。

ローカルHTTPサーバーを使う場合は、外部端末へ公開しないようloopbackだけに
bindします。

```sh
python3 -m http.server \
  --bind 127.0.0.1 \
  --directory dist/starter/demo \
  8000
```

ブラウザで `http://127.0.0.1:8000/` を開き、終了時は `Ctrl+C` を押します。

## 3. AIとチュートリアルを一巡する

VS CodeでこのリポジトリをClaude CodeまたはCodexと開き、次のように
依頼します。

> チュートリアルを始めて。

AIは `CLAUDE.md`／`AGENTS.md` から共通ルール
[docs/AI-OPERATIONS.md](docs/AI-OPERATIONS.md) を読み、練習用の
`workspace/tutorial/` を使って、初期設定・週間計画・ワークシート作成・
実績登録・週次振り返り・ダッシュボード生成を**1ステップずつ一緒に**
実行します。各ステップで変更したファイルと検証結果が報告され、
あなたが返事をするまで次へ進みません。

手順は [examples/walkthrough.md](examples/walkthrough.md) にあり、読むだけでも
流れを追えます。基本編の後には、子ども・共通教材・子ども固有のリベンジWS・
単体テスト・定期テストを追加する発展編があります。必要な章だけ試せます。
練習用workspaceは終わったら削除して構いません。

すでに表計算やノートで記録している場合は、ゼロから始め直す必要はありません。
「いま使っている記録を移行してください」と頼むと、棚卸しと対応づけを確認しながら
直近分を取り込みます。投入前の状態へ戻せるスナップショットを取ってから進めるので、
納得できなければやり直せます。

## 4. AIに初期設定を依頼する

実運用の記録場所は次の依頼で作られます。

> 初期設定をお願いします。

AIは学習者の呼び名（仮名を推奨）と時間の目安を質問したうえで、`templates/` の
雛形からGit管理外の `workspace/` にあなたの記録場所を作ります。続けて次のように
依頼できます。

> 来週の家庭学習計画を作ってください。
> 月曜の算数ワークシートを作ってください。
> 月曜のたしざん、15分で8問中6問正解でした。
> 今週を振り返って、来週の計画を作ってください。

AIを使わない場合は、`templates/` のMarkdown雛形を相対位置を保って
`workspace/my-family/` へ配置し、同じ列構成のまま記入します。

学習者IDは `learner-1` のようなリポジトリ内だけの識別子にし、学校・塾などの
会員番号を使わないでください。同じIDを別の学習者へ使うと検証と生成が失敗します。

## 5. 記録の形式

予定は `workspace/<家庭名>/schedule.md` の表で管理します。列名と順番は
変更しません。

```text
日付 | 学習者ID | 学習者 | 枠 | 教科 | 内容 | 状態 | 教材 | 繰越先
```

状態には次のいずれかを使います。

```text
予定 / 準備済み / 作成済み / 完了 / 一部実施 /
未実施 / 実施有無未確認 / 繰越
```

実施後は `activity-log.md` に1行追加します。こちらも列名と順番を保ちます。

```text
日付 | 学習者ID | 学習者 | 教科 | 内容 | 教材 |
所要時間 | 結果 | 次アクション
```

教材はMarkdownからの相対リンクで指定します。最初は教材なしを表す `—` でも
構いません。リンクする場合はworkspace内の `materials/` に置き、第三者の
教材本文、解答、スキャンを複製しないでください。

今週・来週の予定のうち、`.html` や `.pdf` の教材リンクがまだ無いものは、
Webの「要確認」へ「教材未準備」として表示されます。準備の抜けを当日ではなく
前もって気付くための表示なので、教材ができたら教材リンクを埋めてください。
外部URLは教材として扱わず、`checkup` でも入力エラーになります。

単体・定期テストは `tests.md` の「テスト定義」「テスト実施回」「成績内訳」へ
記載します。問題は教材台帳へ先に登録し、実施後は同じ実施行を更新したうえで、
返却された教科別・総合成績を成績内訳へ記録します。
詳しい列値・ID・保存先は [workspace契約](docs/WORKSPACE-CONTRACT.md) が正本です。

未実施や一部実施は隠さず、状態と繰越先を更新します。記入内容は
次のコマンドでいつでも検証できます（AIは変更のたびに実行します）。

```sh
python3 -m kit.starter_web.checkup --workspace workspace/my-family
```

## 6. 自分のダッシュボードを生成する

```sh
python3 -m kit.starter_web \
  --mode starter \
  --workspace workspace/my-family \
  --output dist/starter/my-family/index.html
```

生成された `dist/starter/my-family/index.html` をブラウザで開きます。表示される
「正本」リンクと教材リンクは、生成先だけで参照できるよう `demo-content/` へ
コピーされます。そのため、生成先にも実データが含まれます。

基準日を固定して確認する場合は `--date YYYY-MM-DD` を追加してください。
workspaceをリポジトリ外（例: `../my-family-learning`）に置いた場合は
`--workspace` へそのパスを指定します。

## 実データを守る

`workspace/` は同梱の `.gitignore` で除外されています。commit前に
必ず次を実行します。

```sh
git check-ignore -v workspace/my-family/schedule.md
git status --short
```

1つ目のコマンドに `.gitignore` の該当ルールが表示され、2つ目に
`workspace/`・`dist/` のファイルが出ないことを確認してください。

- `.gitignore` は `git add -f` や設定変更までは防げません
- 実データを含む生成HTMLを外部へデプロイしないでください
- public forkでは、架空demoと公開許可済みの教材だけを扱ってください
- 実データを誤ってcommitした場合は、公開を止めてGit履歴からの除去を検討して
  ください。通常のファイル削除だけでは過去commitから消えません

### AIサービス利用時の注意

AIアシスタントへ入力した内容の送信・保存・学習利用・保持期間は、利用する
各サービス（Claude Code、Codex等）の規約と設定に従います。利用開始前に
各サービスの規約・プライバシー設定を確認し、記録には実名でなく仮名と
必要最小限の情報を使ってください。本キットはサービス別の設定手順を
管理しません。

## テストと検証

```sh
python3 -m kit.starter_web.checkup
```

`checkup` はディレクトリ構造・正本の重複・Git除外・workspaceの入力契約・
ワークシートの最小構造を検証します。AIが許可外の場所へファイルを作ったり
形式を崩したりした場合、ここで失敗します。

npmを使う場合は、同じ操作を次の短縮コマンドでも実行できます。

```sh
npm run build:web:demo
npm run check
```

ユニットテストは毎日 JST 3:17 の夜間CIだけで実行します。通常の `npm run check`、
push、PRのCIには含まれません。

## 独自形式との接続

正式サポート対象はstarter形式のみです。独自のMarkdown形式を使う場合は、
下流側でアダプタを実装し、共通表示モデルと描画APIへ接続します。
開発元の家庭専用アダプタは同梱しません。
契約と利用方法は [拡張ガイド](docs/EXTENDING.md) を参照してください。

## ライセンス

- Pythonコード、テスト、設定: [MIT License](LICENSE)
- このREADME、`CLAUDE.md`・`AGENTS.md`、`docs/`・`templates/`・`examples/` の
  文書・雛形・データ・教材:
  [Creative Commons Attribution 4.0 International](LICENSE-CONTENT.md)

帰属名義は `home-learning-starter-kit contributors` です。自分のworkspaceに
追加した記録や教材へ自動的にこのライセンスが適用されることはありません。

## バージョンと配布

コード・文書・雛形を同じ固定版で配布します。配布manifestの検証、タグ確定、
0.1.0候補の互換性変更は [リリース手順](docs/RELEASING.md) を参照してください。
