# AI 驱动的 E2E 测试自修复引擎

[English](README.md) · [한국어](README.ko.md) · **简体中文**

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

自动修复失败的 Playwright E2E 测试。当 UI 变更导致元素名称或结构发生变化，使测试中的
选择器随之失效时，本引擎会诊断失败原因，修复失效的选择器或等待条件，**在实际页面的
DOM 中验证新选择器**，然后重新运行测试。本引擎会反复执行这一流程，直到测试通过或达到
重试上限，然后将修复结果写回原文件。它既可作为本地 **CLI** 使用，也可作为 **CI 中的
GitHub Action**，自动创建修复 PR。

> **修复范围：**本引擎只修复**失效的定位器（locator）和等待条件**，绝不改动断言或
> 测试逻辑。所有补丁均可供人工审查。

![e2e-healer 演示：诊断故障、在实际页面的 DOM 中验证新选择器、重新运行测试并完成修复](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## 工作原理

LangGraph 修复循环由以下部分组成：

1. **CLI 核心**——唯一入口（`e2e-healer`）；无论本地还是 CI，所有调用都经由这一入口执行。
2. **数据预处理器（Data Preprocessor）**——将原始 Playwright 日志和 `git diff` 提炼为
   精简的上下文，重点包含失效的选择器和发生变化的 DOM 属性，以降低模型产生幻觉的风险。
3. **LangGraph 智能体**——各节点按
   `Diagnoser → Patch Generator → Selector Verifier → Test Runner` 的顺序执行，并由条件
   路由器（Router）控制循环，直到测试通过或达到 `max_loops`。
4. **选择器验证器（Selector Verifier）**——在实际页面的 DOM 中检查修复后的选择器，
   确保它**有且仅有一个**匹配元素。验证通过 Node.js/Playwright 辅助脚本完成。如果模型生成的
   选择器没有匹配到任何元素（0 个匹配），或匹配到多个元素（超过 1 个匹配）而存在歧义，
   系统会在运行完整测试前撤销当前补丁并重新生成新的补丁。
5. **测试运行器（Test Runner）**——通过子进程运行 `npx playwright test`，检验每次修复
   是否成功。

```
   ┌──────────┐    ┌─────────────────┐    ┌───────────────────┐    ┌─────────────┐
──▶│ Diagnoser│──▶ │ Patch Generator │──▶ │ Selector Verifier │─┬─▶│ Test Runner │──┐
   └──────────┘    └─────────────────┘    └───────────────────┘ │  └─────────────┘  │
        ▲                   ▲  verify fail (0/2+ match) → repatch ┘                   │
        │                   └───────────────────────────────────────────────────────┘│
        │                          fail & loop_count < max                            │
        └───────────────────────────────  Router  ◀───────────────────────────────────┘
                                            │ pass or loop cap
                                            ▼
                                          [End]
```

> 如果 `E2E_HEALER_APP_URL` 为空或页面无法访问（例如未安装 Node.js 或 Playwright），
> 选择器验证器会自动降级，**跳过验证**并继续执行修复循环。验证工具出现问题也不会中断
> 整个修复流程。

完整的设计说明见 [`docs/design.md`](docs/design.md)。

## 演示（已通过端到端验证）

[`examples/`](examples/) 项目复现了一个真实故障：页面中按钮的 ID 从 `submit-btn` 改为
`submit` 后，`example.spec.ts` 会因超时而失败。使用有效的 NVIDIA API 密钥运行本引擎后，
输出如下：

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
+ await page.click("#submit");        # assertion on "Thanks!" left untouched
```

复现步骤请参阅 [`examples/README.md`](examples/README.md)。

## 实际案例

下面展示一次落地页 CTA 测试的实际修复过程。UI 重构将按钮 ID 从 `#enter-demo-btn` 改为
`#demo-cta-btn`，导致 `demo-cta.spec.ts` 在执行 `locator.click` 时失败。针对该文件运行
本引擎后，系统结合 `git diff` 诊断了故障，**只修复了失效的选择器**（`toHaveURL` 断言
保持不变），随后重新运行测试套件。首次尝试即修复成功；整个端到端流程均通过 NVIDIA NIM
（`integrate.api.nvidia.com`、`openai/gpt-oss-120b`）完成：

![真实案例：诊断 CTA 选择器重命名问题、修复选择器并重新运行测试，首次尝试即成功](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/usecase-demo-cta.png)

```text
playwright_run_finished     passed=False                    # original selector times out
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
    await expect(page).toHaveURL(/\/w\//)   // assertion left untouched
  })
```

## 安装

需要 Python 3.13 或更高版本，并且仓库中需包含一个基于 Node.js 的 Playwright 项目。

```bash
uv sync                 # or, once published: pipx install ai-driven-e2e
cp .env.example .env    # then set E2E_HEALER_NVIDIA_API_KEY
```

前往 [build.nvidia.com](https://build.nvidia.com/) 即可免费获取 NVIDIA NIM API 密钥
（默认使用 `openai/gpt-oss-120b` 模型）。

### 在自己的项目中运行（全局 CLI）

在本项目正式发布到 PyPI 前，可以直接从本仓库全局安装 CLI。安装后，使用 `cd` 进入**任意**
Playwright 项目目录并运行 `e2e-healer`：

```bash
uv tool install /path/to/this/repo     # installs a global `e2e-healer` (isolated env)

cd ~/work/your-real-web-app             # your actual Playwright suite
export E2E_HEALER_NVIDIA_API_KEY=nvapi-...
e2e-healer                              # heal the whole suite, in place
e2e-healer tests/login.spec.ts --dry-run   # or preview a single spec, write nothing
```

CLI 会从 `E2E_HEALER_*` 环境变量（或项目目录中的 `.env` 文件）读取配置，在当前目录执行
`npx playwright test`，并在所有测试均修复成功后以退出码 `0` 结束。因此，无需在每个项目中
单独安装或集成本工具，即可在现有代码库中使用。若要使用本仓库的最新代码，请重新运行
`uv tool install --force /path/to/this/repo`。

## 用法（CLI）

```bash
# Heal the WHOLE suite — run every test, then repair each failing file (aggregate summary):
uv run e2e-healer

# Heal a single failing test (with no --log, the tool runs it to capture the failure):
uv run e2e-healer tests/example.spec.ts

# Preview only — run the loop but write nothing:
uv run e2e-healer tests/example.spec.ts --dry-run

# Feed a pre-captured log and a PR-scoped diff (the CI path):
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json

# Enable live-DOM selector verification against a running app:
uv run e2e-healer tests/example.spec.ts --app-url http://localhost:4173
```

修复成功时，命令的退出码为 `0`；否则为非零。`--json` 会将机器可读的
`RepairSummary` 输出到标准输出（stdout），面向用户的信息则写入标准错误（stderr），
CI 可据此决定后续流程。

## 用法（CI / GitHub Action）

运行测试套件，在测试失败时自动修复，并创建修复 PR 供人工审查：

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.2.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173 # optional: enables live selector verification

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

该 Action 的 `outcome` 取值为 `passed` \| `healed` \| `unhealed`。如果 Playwright
测试套件位于子目录，请设置 `working-directory:`。可直接运行的示例工作流见
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml)；它会对本仓库的
`examples/` 项目执行自修复。

## 配置

所有配置项均以 `E2E_HEALER_` 开头（详见 [`.env.example`](.env.example)）：

| 变量                           | 默认值                                | 用途                                             |
| ------------------------------ | ------------------------------------- | ------------------------------------------------ |
| `E2E_HEALER_NVIDIA_API_KEY`    | —                                     | NVIDIA NIM API 密钥                              |
| `E2E_HEALER_NVIDIA_BASE_URL`   | `https://integrate.api.nvidia.com/v1` | 兼容 OpenAI API 的端点                           |
| `E2E_HEALER_NVIDIA_MODEL`      | `openai/gpt-oss-120b`                 | 支持结构化输出（Structured Outputs）的模型       |
| `E2E_HEALER_NVIDIA_MAX_TOKENS` | `4096`                                | 单次补全的 token 上限（为推理预留余量）          |
| `E2E_HEALER_MAX_LOOPS`         | `3`                                   | 修复循环次数上限                                 |
| `E2E_HEALER_PLAYWRIGHT_CMD`    | `npx playwright test`                 | Playwright 执行命令                              |
| `E2E_HEALER_VERIFY_SELECTORS`  | `true`                                | 是否在实际页面的 DOM 中验证选择器                |
| `E2E_HEALER_APP_URL`           | —                                     | 选择器验证器访问的应用 URL（未设置时跳过验证）   |
| `E2E_HEALER_NODE_CMD`          | `node`                                | 选择器验证器使用的 Node.js 可执行程序            |
| `E2E_HEALER_SANDBOX_MODE`      | `relaxed`                             | `strict`、`relaxed` 或 `off`                     |
| `E2E_HEALER_WORKSPACE_ROOT`    | `.`                                   | `strict` 模式下进行路径检查时使用的根目录         |
| `E2E_HEALER_WRITE_GLOBS`       | `*.spec.js,...`                       | 可写测试文件的匹配模式（glob）                   |
| `E2E_HEALER_DENY_GLOBS`        | `.env,.git/**,...`                    | 沙箱禁止访问路径的匹配模式（glob）               |
| `E2E_HEALER_ALLOW_TEMP_HELPER` | `true`                                | 是否允许创建选择器验证器的临时辅助文件           |

> CLI 选项 `--app-url` 会覆盖 `E2E_HEALER_APP_URL`。如需在本地执行选择器验证，必须先安装
> Playwright 所需的浏览器（`npm install && npx playwright install`）。

## 开发

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

请参阅 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 参与贡献

无论规模大小，我们都欢迎各种形式的贡献，包括提交错误报告、完善文档以及编写测试和代码。
请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，再查看标有
[**good first issue**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/good%20first%20issue)
或 [**help wanted**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/help%20wanted)
标签的 Issue。

**🙋 以下任务目前正在招募贡献者：**

- [#3 — 为 Playwright 示例搭建真实的 React + Vite 前端演示环境](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/3)
- [#4 — 添加简体中文（zh-CN）README 译本](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/4) — 欢迎中文开发者参与！

查看 [**v0.3 路线图**](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/9)，了解项目的
整体规划。第一次参与本项目？可以在对应 Issue 下留言认领任务，我们很乐意提供帮助。

## 限制

- 只修复选择器和等待条件，绝不改动断言或控制流。
- v0.1 版的 JSX/TSX diff 分析器采用基于正则表达式的启发式实现（后续计划改用
  tree-sitter）。
- v1 版的选择器验证器只检查打开 `APP_URL` 后的**初始页面状态**。对于必须经过点击或跳转
  才会出现的元素，此阶段无法进行验证；最终仍由测试运行器判定测试是否通过（后续计划支持
  捕获失败时的页面快照）。
- 修复效果取决于 LLM 的能力以及 `git diff` 提供的信息是否清晰。

## 许可证

[MIT](LICENSE)
# AI 驱动的 E2E 测试自愈引擎

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · **简体中文**

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

自动修复损坏的 Playwright E2E 测试。当 UI 变更导致元素重命名或结构变化、测试选择器失效时，引擎会诊断失败原因，修补损坏的选择器/等待条件，**在真实 DOM 上验证新选择器**，然后重新运行测试直到通过（或达到重试上限），并将修复写回文件——可作为本地 **CLI**，或作为在 CI 中自动打开补丁 PR 的 **GitHub Action**。

> **范围护栏：** 引擎**仅**修复**失败的 locator 和等待条件**。它从不修改断言或测试逻辑，每个补丁都保持可人工审查。

![e2e-healer 演示 — 诊断、验证真实 DOM、重新运行、修复完成](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## 工作原理

四个层次驱动 LangGraph 修复循环：

1. **CLI 核心** — 单一入口（`e2e-healer`）；包括 CI 在内的所有调用都通过它。
2. **数据预处理器** — 将原始 Playwright 日志和 `git diff` 抽象为紧凑、抗幻觉的上下文（失败的选择器 + 变更的 DOM 属性）。
3. **LangGraph 智能体** — `Diagnoser → Patch Generator → Selector Verifier → Test Runner`，通过条件 Router 循环直到测试通过或达到 `max_loops`。
4. **Selector Verifier** — 针对真实页面 DOM 检查每个修补后的选择器是否**精确匹配一个元素**（Node/Playwright 辅助脚本）。幻觉（0 匹配）或歧义（>1）选择器会在完整测试运行前被回滚并重新修补。
5. **Test Runner** — 通过子进程运行 `npx playwright test` 验证每次尝试。

完整设计见 [`docs/design.md`](docs/design.md)。

## 安装

需要 Python 3.13+ 和项目中的 Playwright（Node）环境。

```bash
pipx install ai-driven-e2e          # 推荐：一行全局安装
# 或开发/未发布时：uv tool install git+https://github.com/Lee-Dongwook/E2E-Self-Heal.git

cp .env.example .env                # 设置 E2E_HEALER_NVIDIA_API_KEY
```

在 [build.nvidia.com](https://build.nvidia.com/) 获取免费 NVIDIA NIM API 密钥（默认模型 `openai/gpt-oss-120b`）。

## 用法（CLI）

```bash
uv run e2e-healer                              # 修复整个测试套件
uv run e2e-healer tests/example.spec.ts        # 修复单个失败测试
uv run e2e-healer tests/example.spec.ts --dry-run
```

## 用法（CI / GitHub Action）

测试失败时自动修复并打开补丁 PR 供审查：

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.2.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

可运行示例见 [`ci/github-workflow.example.yml`](ci/github-workflow.example.yml) 和 [`examples/README.md`](examples/README.md)。

## 开发

```bash
make install && make check && make test
```

详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 限制

- 仅修复选择器和等待条件 — 不修改断言或控制流。
- JSX/TSX diff 分析器在 v0.1 中为正则启发式（计划升级 tree-sitter）。
- 修复质量取决于 LLM 和 `git diff` 的清晰度。

## 许可证

[MIT](LICENSE)
