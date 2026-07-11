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
