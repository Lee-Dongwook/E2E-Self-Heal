# AI 駆動 E2E テスト自己修復エンジン

[English](README.md) · [한국어](README.ko.md) · **日本語** · [简体中文](README.zh-CN.md)

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

壊れた Playwright E2E テストを自動修復します。UI 変更で要素の名前や構造が変わり、
テストのセレクタが壊れた場合、エンジンが失敗原因を診断し、壊れたセレクタ／待機条件を
パッチし、**実際の DOM で新しいセレクタを検証したうえで**、テストが通るかリトライ上限に
達するまで再実行し、修正を書き戻します。ローカル **CLI** としても、パッチ PR を開く
**CI GitHub Action** としても動作します。

> **スコープのガードレール:** エンジンは **失敗した locator と待機条件のみ** を修正します。
> アサーションやテストロジックには一切触れず、すべてのパッチは人間がレビュー可能な形で
> 残ります。

![e2e-healer デモ — 診断、実 DOM 検証、再実行、修復完了](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## 仕組み

4 つのレイヤーが LangGraph 修復ループを駆動します。

1. **CLI コア** — 単一のエントリポイント（`e2e-healer`）。CI を含むすべての呼び出しがここに集約されます。
2. **データ前処理（Preprocessor）** — 生の Playwright ログと `git diff` を、幻覚（hallucination）に
   強いコンパクトなコンテキスト（失敗したセレクタ + 変更された DOM 属性）に抽象化します。
3. **LangGraph エージェント** — `Diagnoser → Patch Generator → Selector Verifier → Test Runner` が
   条件付き Router 経由でループし、テストが通るか `max_loops` に達するまで繰り返します。
4. **Selector Verifier** — パッチされた各セレクタを実ページの DOM に対して検証し、**ちょうど 1 要素**
   に解決されることを確認します（Node/Playwright ヘルパー）。幻覚（0 件）や曖昧（2 件以上）な
   セレクタは、フルテスト実行前に元に戻して再パッチします。
5. **Test Runner** — 各試行を検証するため `npx playwright test` をサブプロセスで実行します。

```
   ┌──────────┐    ┌─────────────────┐    ┌───────────────────┐    ┌─────────────┐
──▶│ Diagnoser│──▶ │ Patch Generator │──▶ │ Selector Verifier │─┬─▶│ Test Runner │──┐
   └──────────┘    └─────────────────┘    └───────────────────┘ │  └─────────────┘  │
        ▲                   ▲  検証失敗(0/2+ 一致) → 再パッチ ──┘                    │
        │                   └──────────────────────────────────────────────────────┘ │
        │                          失敗 & loop_count < max                            │
        └───────────────────────────────  Router  ◀───────────────────────────────────┘
                                            │ 成功またはループ上限
                                            ▼
                                          [End]
```

> Selector Verifier は `E2E_HEALER_APP_URL` が空、またはページに到達できない場合
> （例: Node/Playwright 未インストール）に **スキップ** し、ループは未検証のまま続行します —
> ツールの問題で修復が止まることはありません。

詳細な設計は [`docs/design.md`](docs/design.md) を参照してください。

## デモ（エンドツーエンドで検証済み）

[`examples/`](examples/) プロジェクトは実際の破損を再現します。ボタンの id が
`submit-btn` → `submit` にリネームされ、`example.spec.ts` がタイムアウトします。
（有効な NVIDIA キーで）ヒーラーを実行すると:

```text
diagnoser_finished
patch_generator_finished        instruction_count=1
selector_verify_started         selector_count=1 url=http://localhost:4173
selector_verify_passed          counts={'#submit': 1}
test_runner_passed              loop_count=0
fixed after 0 loop(s)
```

```diff
- await page.click("#submit-btn");
+ await page.click("#submit");        # "Thanks!" アサーションはそのまま
```

自分で再現する: [`examples/README.md`](examples/README.md) を参照。

## 実運用での例

ランディングページ CTA テストに対する実際の実行例です。UI リファクタでボタン id が
`#enter-demo-btn` → `#demo-cta-btn` に変わり、`demo-cta.spec.ts` が `locator.click` で
失敗し始めました。ファイルを指定すると、エンジンが `git diff` に照らして原因を診断し、
**壊れたセレクタのみ** をパッチ（`toHaveURL` アサーションはそのまま）、スイートを再実行し、
初回で成功 — NVIDIA NIM（`integrate.api.nvidia.com`, `openai/gpt-oss-120b`）上で
エンドツーエンド:

![実運用実行: リネームされた CTA セレクタを診断 → パッチ → 再実行 → 0 ループで修復](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/usecase-demo-cta.png)

```text
playwright_run_finished     passed=False                    # 元のセレクタがタイムアウト
diagnoser_started           loop_count=0
diagnoser_finished
patch_generator_finished    instruction_count=1
test_runner_started
playwright_run_finished     passed=True
repair_run_finished         is_success=True loop_count=0
fixed after 0 loop(s)
```

```diff
  test('guest enters the demo workspace from the landing CTA', async ({ page }) => {
    await page.goto('/')
-   await page.click('#enter-demo-btn')
+   await page.click('#demo-cta-btn')
    await expect(page).toHaveURL(/\/w\//)   // アサーションはそのまま
  })
```

## インストール

Python 3.13+ と、リポジトリ内の Playwright（Node）プロジェクトが必要です。

```bash
uv sync                 # または（公開後）: pipx install ai-driven-e2e
cp .env.example .env    # その後 E2E_HEALER_NVIDIA_API_KEY を設定
```

無料の NVIDIA NIM API キーは [build.nvidia.com](https://build.nvidia.com/) で取得できます
（デフォルトモデルは `openai/gpt-oss-120b`）。

### 自分のプロジェクトで実行（グローバル CLI）

PyPI 公開前でも、このリポジトリから CLI を **グローバルインストール** すれば、任意の
Playwright プロジェクトで `e2e-healer` を実行できます:

```bash
uv tool install /path/to/this/repo     # グローバル `e2e-healer` をインストール（隔離環境）

cd ~/work/your-real-web-app             # 実際の Playwright スイートがある場所
export E2E_HEALER_NVIDIA_API_KEY=nvapi-...
e2e-healer                              # スイート全体をその場で修復
e2e-healer tests/login.spec.ts --dry-run   # 単一 spec のプレビュー（書き込みなし）
```

CLI は `E2E_HEALER_*` 環境変数（またはプロジェクトディレクトリの `.env`）から設定を読み取り、
カレントディレクトリで `npx playwright test` を実行し、すべて修復されれば終了コード `0` を
返します — プロジェクトごとの追加セットアップなしで実リポジトリにそのまま使えます。
変更を取り込むには `uv tool install --force /path/to/this/repo` を再実行してください。

## 使い方（CLI）

```bash
# スイート全体を修復 — 全テストを実行し、失敗したファイルごとに修復（集約サマリー）:
uv run e2e-healer

# 単一の失敗テストを修復（--log なしの場合、ツールがテストを実行して失敗ログを取得）:
uv run e2e-healer tests/example.spec.ts

# プレビューのみ — ループは実行するがファイルには書き込まない:
uv run e2e-healer tests/example.spec.ts --dry-run

# 事前にキャプチャしたログと PR スコープの diff を渡す（CI パス）:
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json

# 実行中アプリに対するライブ DOM セレクタ検証を有効化:
uv run e2e-healer tests/example.spec.ts --app-url http://localhost:4173
```

テストが修復されれば終了コード `0`、そうでなければ非ゼロです。`--json` は機械可読な
`RepairSummary` を stdout に出力し（人間向け出力は stderr）、CI が分岐できます。

## 使い方（CI / GitHub Action）

スイートを実行し、失敗時に自動修復してレビュー用パッチ PR を開きます:

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.2.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173 # 任意: ライブセレクタ検証を有効化

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

Action の `outcome` 出力は `passed` | `healed` | `unhealed` です。Playwright スイートが
サブディレクトリにある場合は `working-directory:` を指定してください。このリポジトリの
`examples/` プロジェクトを自己修復する **実行可能なセルフデモ** は
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml) にあります。

## 設定

すべての設定は `E2E_HEALER_` プレフィックスを使用します（[`.env.example`](.env.example) 参照）:

| 変数                           | デフォルト                            | 用途                                           |
| ------------------------------ | ------------------------------------- | ---------------------------------------------- |
| `E2E_HEALER_NVIDIA_API_KEY`    | —                                     | NVIDIA NIM API キー                            |
| `E2E_HEALER_NVIDIA_BASE_URL`   | `https://integrate.api.nvidia.com/v1` | OpenAI 互換エンドポイント                      |
| `E2E_HEALER_NVIDIA_MODEL`      | `openai/gpt-oss-120b`                 | Structured Outputs 対応モデル                  |
| `E2E_HEALER_NVIDIA_MAX_TOKENS` | `4096`                                | 完了トークン上限（推論モデル用の余裕）         |
| `E2E_HEALER_MAX_LOOPS`         | `3`                                   | 修復ループ上限                                 |
| `E2E_HEALER_PLAYWRIGHT_CMD`    | `npx playwright test`                 | Playwright 呼び出し                            |
| `E2E_HEALER_VERIFY_SELECTORS`  | `true`                                | ライブ DOM セレクタ検証の on/off               |
| `E2E_HEALER_APP_URL`           | —                                     | Selector Verifier が読み込む URL（空 = スキップ） |
| `E2E_HEALER_NODE_CMD`          | `node`                                | 検証用 Node 実行ファイル                       |
| `E2E_HEALER_SANDBOX_MODE`      | `relaxed`                             | `strict`, `relaxed`, `off` のいずれか          |
| `E2E_HEALER_WORKSPACE_ROOT`    | `.`                                   | strict モードのパスチェックルート              |
| `E2E_HEALER_WRITE_GLOBS`       | `*.spec.js,...`                       | 書き込み可能なテストファイル glob              |
| `E2E_HEALER_DENY_GLOBS`        | `.env,.git/**,...`                    | サンドボックスがブロックするパス               |
| `E2E_HEALER_ALLOW_TEMP_HELPER` | `true`                                | セレクタ検証ヘルパー一時ファイルを許可         |

> `--app-url` CLI フラグは `E2E_HEALER_APP_URL` を上書きします。ローカルでセレクタ検証を
> 実際に実行するには、Playwright プロジェクトにブラウザがインストールされている必要があります
> （`npm install && npx playwright install`）。

## 開発

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

詳細は [`CONTRIBUTING.md`](CONTRIBUTING.md) を参照してください。

## 制限事項

- セレクタと待機条件のみ修正 — アサーションや制御フローには触れません。
- JSX/TSX diff アナライザは v0.1 時点では正規表現ヒューリスティック（tree-sitter アップグレード予定）。
- Selector Verifier は v1 では `APP_URL` の **エントリページ状態** を基準に検証します。クリックや
  ナビゲーション後にのみ現れる要素はこの段階では検証されず、最終判定は Test Runner が担います
  （失敗時点のスナップショット取得は予定）。
- 修復品質は LLM と `git diff` の明確さに依存します。

## ライセンス

[MIT](LICENSE)
