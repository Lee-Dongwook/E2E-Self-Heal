# AI 기반 E2E 테스트 자가 치유 엔진

[English](README.md) · **한국어** · [简体中文](README.zh-CN.md)

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

깨진 Playwright E2E 테스트를 자동으로 복구합니다. UI 변경으로 요소가 이름이 바뀌거나
구조가 달라져 테스트의 셀렉터가 깨지면, 엔진이 실패 원인을 진단하고 → 셀렉터/대기 조건을
패치하고 → **실제 DOM에서 새 셀렉터를 검증한 뒤** → 테스트를 다시 실행하는 과정을 테스트가
통과하거나 재시도 한도에 도달할 때까지 반복한 뒤 수정본을 파일에 기록합니다. 로컬 **CLI**로도,
패치 PR을 여는 **CI GitHub Action**으로도 동작합니다.

> **안전장치(가드레일):** 엔진은 **실패한 셀렉터(locator)와 대기 조건만** 수정합니다.
> 단언(assertion)이나 테스트 로직은 절대 건드리지 않으며, 패치는 항상 사람이 검토할 수
> 있는 형태로 남습니다.

![e2e-healer 데모 — 진단, 실제 DOM 검증, 재실행, 치유 완료](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## 동작 방식

네 개의 계층이 LangGraph 복구 루프를 구동합니다.

1. **CLI 코어** — 단일 진입점(`e2e-healer`). CI를 포함한 모든 호출이 이곳으로 들어옵니다.
2. **데이터 전처리기(Preprocessor)** — 원본 Playwright 로그와 `git diff`를 LLM이 이해하기
   쉬운 압축된, 환각(hallucination)에 강한 컨텍스트로 추상화합니다(실패한 셀렉터 + 변경된
   DOM 속성만 추출).
3. **LangGraph 에이전트** — `Diagnoser → Patch Generator → Selector Verifier → Test Runner`
   노드가 조건부 Router를 통해 테스트가 통과하거나 `max_loops`에 도달할 때까지 순환합니다.
4. **Selector Verifier** — 패치된 셀렉터를 실제 페이지 DOM에 대조해 **정확히 1개** 매칭되는지
   확인합니다(Node/Playwright 보조 스크립트). 환각(0개)·모호(2개 이상) 셀렉터는 무거운
   테스트 실행 전에 되돌리고 재패치합니다.
5. **Test Runner** — 각 시도를 검증하기 위해 `npx playwright test`를 서브프로세스로 실행합니다.

```
   ┌──────────┐    ┌─────────────────┐    ┌───────────────────┐    ┌─────────────┐
──▶│ Diagnoser│──▶ │ Patch Generator │──▶ │ Selector Verifier │─┬─▶│ Test Runner │──┐
   └──────────┘    └─────────────────┘    └───────────────────┘ │  └─────────────┘  │
        ▲                   ▲     검증 실패(0/2+ 매칭) → 재패치 ──┘                    │
        │                   └──────────────────────────────────────────────────────┘ │
        │                          실패 & loop_count < max                            │
        └───────────────────────────────  Router  ◀───────────────────────────────────┘
                                            │ 통과 또는 루프 한도 도달
                                            ▼
                                          [End]
```

> Selector Verifier는 `E2E_HEALER_APP_URL`이 비어 있거나 페이지에 접근할 수 없으면(예: Node/
> Playwright 미설치) 검증을 **건너뛰고** 루프를 그대로 진행합니다 — 도구 문제로 치유가 막히지
> 않도록 항상 우아하게 저하(graceful degrade)합니다.

전체 설계는 [`docs/design.md`](docs/design.md)를 참고하세요.

## 데모 (엔드투엔드 검증됨)

[`examples/`](examples/) 프로젝트는 실제 브레이크를 재현합니다. 버튼 id가
`submit-btn` → `submit`으로 바뀌어 `example.spec.ts`가 타임아웃납니다. 실제 NVIDIA 키로
치유기를 돌리면:

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
+ await page.click("#submit");        # "Thanks!" 단언은 그대로 유지
```

직접 재현: [`examples/README.md`](examples/README.md) 참고.

## 실무 활용 사례

랜딩 페이지 CTA 테스트를 대상으로 한 실제 실행 결과입니다. UI 리팩터로 버튼 id가
`#enter-demo-btn` → `#demo-cta-btn`으로 바뀌면서 `demo-cta.spec.ts`가 `locator.click`에서
실패하기 시작했습니다. 파일을 지정하자 엔진이 `git diff`에 대조해 원인을 진단하고,
**깨진 셀렉터만** 패치한 뒤(`toHaveURL` 단언은 그대로 유지) 스위트를 다시 실행해 첫 시도에
통과했습니다 — NVIDIA NIM(`integrate.api.nvidia.com`, `openai/gpt-oss-120b`)에서 엔드투엔드로:

![실무 실행: 이름이 바뀐 CTA 셀렉터 진단 → 패치 → 재실행 → 0루프 만에 치유](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/usecase-demo-cta.png)

```text
playwright_run_finished     passed=False                    # 원래 셀렉터가 타임아웃
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
    await expect(page).toHaveURL(/\/w\//)   // 단언은 그대로 유지
  })
```

## 설치

Python 3.13+ 과 저장소 내 Playwright(Node) 프로젝트가 필요합니다.

```bash
uv sync                 # 또는 (배포 후): pipx install ai-driven-e2e
cp .env.example .env    # 이후 E2E_HEALER_NVIDIA_API_KEY 설정
```

무료 NVIDIA NIM API 키는 [build.nvidia.com](https://build.nvidia.com/)에서 발급받을 수
있습니다(기본 모델 `openai/gpt-oss-120b`).

### 내 실무 프로젝트에서 돌리기 (전역 CLI)

PyPI 배포 전에도, 이 저장소에서 CLI를 **전역 설치**하면 어떤 실무 Playwright
프로젝트 디렉토리에서든 `e2e-healer`를 바로 실행할 수 있습니다:

```bash
uv tool install /path/to/this/repo     # 전역 `e2e-healer` 설치(격리 환경)

cd ~/work/your-real-web-app             # 실제 Playwright 스위트가 있는 곳
export E2E_HEALER_NVIDIA_API_KEY=nvapi-...
e2e-healer                              # 스위트 전체를 그 자리에서 치유
e2e-healer tests/login.spec.ts --dry-run   # 또는 단일 스펙 미리보기(파일에 안 씀)
```

CLI는 `E2E_HEALER_*` 환경변수(또는 프로젝트 폴더의 `.env`)에서 설정을 읽고, 현재
디렉토리에서 `npx playwright test`를 실행하며, 모두 치유되면 종료 코드 `0`을
반환합니다 — 즉 프로젝트별 추가 세팅 없이 실제 저장소에 바로 얹힙니다. 변경 사항을
반영하려면 `uv tool install --force /path/to/this/repo`로 다시 설치하세요.

## 사용법 (CLI)

```bash
# 스위트 전체 치유 — 모든 테스트를 돌린 뒤 실패한 파일마다 복구(통합 요약):
uv run e2e-healer

# 단일 실패 테스트 치유(--log 없이 실행하면 도구가 직접 테스트를 돌려 실패 로그 수집):
uv run e2e-healer tests/example.spec.ts

# 미리보기 전용 — 루프는 돌리되 파일에는 아무것도 쓰지 않음:
uv run e2e-healer tests/example.spec.ts --dry-run

# 미리 캡처한 로그와 PR 범위 diff를 넘기는 방식(CI 경로):
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json

# 셀렉터 검증을 켜고 실행 — 패치한 셀렉터를 실제 페이지에서 대조:
uv run e2e-healer tests/example.spec.ts --app-url http://localhost:4173
```

테스트가 치유되면 종료 코드 `0`, 아니면 0이 아닌 값입니다. `--json`은 기계가 읽을 수 있는
`RepairSummary`를 stdout으로 출력하며(사람용 출력은 stderr로 분리), CI가 이 값으로 분기할
수 있습니다.

## 사용법 (CI / GitHub Action)

스위트를 실행하고 실패 시 자동 치유한 뒤 검토용 패치 PR을 엽니다.

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.2.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173 # 선택: 실제 DOM 셀렉터 검증 활성화

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

액션의 `outcome` 출력값은 `passed` \| `healed` \| `unhealed` 중 하나입니다. Playwright
스위트가 하위 디렉터리에 있으면 `working-directory:`를 넘기세요. 이 저장소의 `examples/`
프로젝트를 스스로 치유하는 **실행 가능한 셀프 데모**가
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml)에 있습니다.

## 설정

모든 설정은 `E2E_HEALER_` 접두사를 사용합니다([`.env.example`](.env.example) 참고).

| 변수                           | 기본값                                | 용도                                                |
| ------------------------------ | ------------------------------------- | --------------------------------------------------- |
| `E2E_HEALER_NVIDIA_API_KEY`    | —                                     | NVIDIA NIM API 키                                   |
| `E2E_HEALER_NVIDIA_BASE_URL`   | `https://integrate.api.nvidia.com/v1` | OpenAI 호환 엔드포인트                              |
| `E2E_HEALER_NVIDIA_MODEL`      | `openai/gpt-oss-120b`                 | Structured Outputs 지원 모델                        |
| `E2E_HEALER_NVIDIA_MAX_TOKENS` | `4096`                                | 응답 토큰 한도(추론 모델 여유분)                    |
| `E2E_HEALER_MAX_LOOPS`         | `3`                                   | 복구 루프 한도                                      |
| `E2E_HEALER_PLAYWRIGHT_CMD`    | `npx playwright test`                 | Playwright 실행 명령                                |
| `E2E_HEALER_VERIFY_SELECTORS`  | `true`                                | 패치 셀렉터의 실제 DOM 검증 on/off                  |
| `E2E_HEALER_APP_URL`           | —                                     | Selector Verifier가 로드할 앱 URL(비면 검증 건너뜀) |
| `E2E_HEALER_NODE_CMD`          | `node`                                | 검증기 실행에 쓰는 Node 실행 파일                   |
| `E2E_HEALER_SANDBOX_MODE`      | `relaxed`                             | `strict`, `relaxed`, `off` 중 하나                  |
| `E2E_HEALER_WORKSPACE_ROOT`    | `.`                                   | strict 모드의 경로 검사 루트                        |
| `E2E_HEALER_WRITE_GLOBS`       | `*.spec.js,...`                       | 쓸 수 있는 테스트 파일 glob                         |
| `E2E_HEALER_DENY_GLOBS`        | `.env,.git/**,...`                    | 샌드박스가 차단하는 경로                            |
| `E2E_HEALER_ALLOW_TEMP_HELPER` | `true`                                | 셀렉터 검증 임시 helper 파일 허용                   |

> `--app-url` CLI 플래그로 `E2E_HEALER_APP_URL`을 덮어쓸 수 있습니다. 로컬에서 셀렉터 검증을
> 실제로 돌리려면 Playwright 프로젝트에 브라우저가 설치돼 있어야 합니다
> (`npm install && npx playwright install`).

## 개발

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

자세한 내용은 [`CONTRIBUTING.md`](CONTRIBUTING.md)를 참고하세요.

## 한계

- 셀렉터와 대기 조건만 수정 — 단언이나 제어 흐름은 절대 건드리지 않음.
- JSX/TSX diff 분석기는 v0.1 기준 정규식 휴리스틱(tree-sitter 업그레이드 예정).
- Selector Verifier는 v1에서 `APP_URL`의 **진입 페이지 상태**를 기준으로 검증합니다. 클릭·이동
  이후의 깊은 상태에서만 나타나는 요소는 이 단계에서 검증되지 않고, 최종 판정은 Test Runner가
  맡습니다(추후 실패 시점 스냅샷 캡처로 고도화 예정).
- 치유 품질은 LLM과 `git diff`의 명확성에 좌우됨.

## 라이선스

[MIT](LICENSE)
# AI 기반 E2E 테스트 자가 치유 엔진

[English](README.md) · **한국어** · [日本語](README.ja.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

깨진 Playwright E2E 테스트를 자동으로 복구합니다. UI 변경으로 요소가 이름이 바뀌거나
구조가 달라져 테스트의 셀렉터가 깨지면, 엔진이 실패 원인을 진단하고 → 셀렉터/대기 조건을
패치하고 → **실제 DOM에서 새 셀렉터를 검증한 뒤** → 테스트를 다시 실행하는 과정을 테스트가
통과하거나 재시도 한도에 도달할 때까지 반복한 뒤 수정본을 파일에 기록합니다. 로컬 **CLI**로도,
패치 PR을 여는 **CI GitHub Action**으로도 동작합니다.

> **안전장치(가드레일):** 엔진은 **실패한 셀렉터(locator)와 대기 조건만** 수정합니다.
> 단언(assertion)이나 테스트 로직은 절대 건드리지 않으며, 패치는 항상 사람이 검토할 수
> 있는 형태로 남습니다.

![e2e-healer 데모 — 진단, 실제 DOM 검증, 재실행, 치유 완료](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## 동작 방식

네 개의 계층이 LangGraph 복구 루프를 구동합니다.

1. **CLI 코어** — 단일 진입점(`e2e-healer`). CI를 포함한 모든 호출이 이곳으로 들어옵니다.
2. **데이터 전처리기(Preprocessor)** — 원본 Playwright 로그와 `git diff`를 LLM이 이해하기
   쉬운 압축된, 환각(hallucination)에 강한 컨텍스트로 추상화합니다(실패한 셀렉터 + 변경된
   DOM 속성만 추출).
3. **LangGraph 에이전트** — `Diagnoser → Patch Generator → Selector Verifier → Test Runner`
   노드가 조건부 Router를 통해 테스트가 통과하거나 `max_loops`에 도달할 때까지 순환합니다.
4. **Selector Verifier** — 패치된 셀렉터를 실제 페이지 DOM에 대조해 **정확히 1개** 매칭되는지
   확인합니다(Node/Playwright 보조 스크립트). 환각(0개)·모호(2개 이상) 셀렉터는 무거운
   테스트 실행 전에 되돌리고 재패치합니다.
5. **Test Runner** — 각 시도를 검증하기 위해 `npx playwright test`를 서브프로세스로 실행합니다.

```
   ┌──────────┐    ┌─────────────────┐    ┌───────────────────┐    ┌─────────────┐
──▶│ Diagnoser│──▶ │ Patch Generator │──▶ │ Selector Verifier │─┬─▶│ Test Runner │──┐
   └──────────┘    └─────────────────┘    └───────────────────┘ │  └─────────────┘  │
        ▲                   ▲     검증 실패(0/2+ 매칭) → 재패치 ──┘                    │
        │                   └──────────────────────────────────────────────────────┘ │
        │                          실패 & loop_count < max                            │
        └───────────────────────────────  Router  ◀───────────────────────────────────┘
                                            │ 통과 또는 루프 한도 도달
                                            ▼
                                          [End]
```

> Selector Verifier는 `E2E_HEALER_APP_URL`이 비어 있거나 페이지에 접근할 수 없으면(예: Node/
> Playwright 미설치) 검증을 **건너뛰고** 루프를 그대로 진행합니다 — 도구 문제로 치유가 막히지
> 않도록 항상 우아하게 저하(graceful degrade)합니다.

전체 설계는 [`docs/design.md`](docs/design.md)를 참고하세요.

## 데모 (엔드투엔드 검증됨)

[`examples/`](examples/) 프로젝트는 실제 브레이크를 재현합니다. 버튼 id가
`submit-btn` → `submit`으로 바뀌어 `example.spec.ts`가 타임아웃납니다. 실제 NVIDIA 키로
치유기를 돌리면:

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
+ await page.click("#submit");        # "Thanks!" 단언은 그대로 유지
```

직접 재현: [`examples/README.md`](examples/README.md) 참고.

## 실무 활용 사례

랜딩 페이지 CTA 테스트를 대상으로 한 실제 실행 결과입니다. UI 리팩터로 버튼 id가
`#enter-demo-btn` → `#demo-cta-btn`으로 바뀌면서 `demo-cta.spec.ts`가 `locator.click`에서
실패하기 시작했습니다. 파일을 지정하자 엔진이 `git diff`에 대조해 원인을 진단하고,
**깨진 셀렉터만** 패치한 뒤(`toHaveURL` 단언은 그대로 유지) 스위트를 다시 실행해 첫 시도에
통과했습니다 — NVIDIA NIM(`integrate.api.nvidia.com`, `openai/gpt-oss-120b`)에서 엔드투엔드로:

![실무 실행: 이름이 바뀐 CTA 셀렉터 진단 → 패치 → 재실행 → 0루프 만에 치유](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/usecase-demo-cta.png)

```text
playwright_run_finished     passed=False                    # 원래 셀렉터가 타임아웃
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
    await expect(page).toHaveURL(/\/w\//)   // 단언은 그대로 유지
  })
```

## 설치

Python 3.13+ 과 저장소 내 Playwright(Node) 프로젝트가 필요합니다.

```bash
uv sync                 # 또는 (배포 후): pipx install ai-driven-e2e
cp .env.example .env    # 이후 E2E_HEALER_NVIDIA_API_KEY 설정
```

무료 NVIDIA NIM API 키는 [build.nvidia.com](https://build.nvidia.com/)에서 발급받을 수
있습니다(기본 모델 `openai/gpt-oss-120b`).

### 내 실무 프로젝트에서 돌리기 (전역 CLI)

PyPI 배포 전에도, 이 저장소에서 CLI를 **전역 설치**하면 어떤 실무 Playwright
프로젝트 디렉토리에서든 `e2e-healer`를 바로 실행할 수 있습니다:

```bash
uv tool install /path/to/this/repo     # 전역 `e2e-healer` 설치(격리 환경)

cd ~/work/your-real-web-app             # 실제 Playwright 스위트가 있는 곳
export E2E_HEALER_NVIDIA_API_KEY=nvapi-...
e2e-healer                              # 스위트 전체를 그 자리에서 치유
e2e-healer tests/login.spec.ts --dry-run   # 또는 단일 스펙 미리보기(파일에 안 씀)
```

CLI는 `E2E_HEALER_*` 환경변수(또는 프로젝트 폴더의 `.env`)에서 설정을 읽고, 현재
디렉토리에서 `npx playwright test`를 실행하며, 모두 치유되면 종료 코드 `0`을
반환합니다 — 즉 프로젝트별 추가 세팅 없이 실제 저장소에 바로 얹힙니다. 변경 사항을
반영하려면 `uv tool install --force /path/to/this/repo`로 다시 설치하세요.

## 사용법 (CLI)

```bash
# 스위트 전체 치유 — 모든 테스트를 돌린 뒤 실패한 파일마다 복구(통합 요약):
uv run e2e-healer

# 단일 실패 테스트 치유(--log 없이 실행하면 도구가 직접 테스트를 돌려 실패 로그 수집):
uv run e2e-healer tests/example.spec.ts

# 미리보기 전용 — 루프는 돌리되 파일에는 아무것도 쓰지 않음:
uv run e2e-healer tests/example.spec.ts --dry-run

# 미리 캡처한 로그와 PR 범위 diff를 넘기는 방식(CI 경로):
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json

# 셀렉터 검증을 켜고 실행 — 패치한 셀렉터를 실제 페이지에서 대조:
uv run e2e-healer tests/example.spec.ts --app-url http://localhost:4173
```

테스트가 치유되면 종료 코드 `0`, 아니면 0이 아닌 값입니다. `--json`은 기계가 읽을 수 있는
`RepairSummary`를 stdout으로 출력하며(사람용 출력은 stderr로 분리), CI가 이 값으로 분기할
수 있습니다.

## 사용법 (CI / GitHub Action)

스위트를 실행하고 실패 시 자동 치유한 뒤 검토용 패치 PR을 엽니다.

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.2.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173 # 선택: 실제 DOM 셀렉터 검증 활성화

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

액션의 `outcome` 출력값은 `passed` \| `healed` \| `unhealed` 중 하나입니다. Playwright
스위트가 하위 디렉터리에 있으면 `working-directory:`를 넘기세요. 이 저장소의 `examples/`
프로젝트를 스스로 치유하는 **실행 가능한 셀프 데모**가
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml)에 있습니다.

## 설정

모든 설정은 `E2E_HEALER_` 접두사를 사용합니다([`.env.example`](.env.example) 참고).

| 변수                           | 기본값                                | 용도                                                |
| ------------------------------ | ------------------------------------- | --------------------------------------------------- |
| `E2E_HEALER_NVIDIA_API_KEY`    | —                                     | NVIDIA NIM API 키                                   |
| `E2E_HEALER_NVIDIA_BASE_URL`   | `https://integrate.api.nvidia.com/v1` | OpenAI 호환 엔드포인트                              |
| `E2E_HEALER_NVIDIA_MODEL`      | `openai/gpt-oss-120b`                 | Structured Outputs 지원 모델                        |
| `E2E_HEALER_NVIDIA_MAX_TOKENS` | `4096`                                | 응답 토큰 한도(추론 모델 여유분)                    |
| `E2E_HEALER_MAX_LOOPS`         | `3`                                   | 복구 루프 한도                                      |
| `E2E_HEALER_PLAYWRIGHT_CMD`    | `npx playwright test`                 | Playwright 실행 명령                                |
| `E2E_HEALER_VERIFY_SELECTORS`  | `true`                                | 패치 셀렉터의 실제 DOM 검증 on/off                  |
| `E2E_HEALER_APP_URL`           | —                                     | Selector Verifier가 로드할 앱 URL(비면 검증 건너뜀) |
| `E2E_HEALER_NODE_CMD`          | `node`                                | 검증기 실행에 쓰는 Node 실행 파일                   |
| `E2E_HEALER_SANDBOX_MODE`      | `relaxed`                             | `strict`, `relaxed`, `off` 중 하나                  |
| `E2E_HEALER_WORKSPACE_ROOT`    | `.`                                   | strict 모드의 경로 검사 루트                        |
| `E2E_HEALER_WRITE_GLOBS`       | `*.spec.js,...`                       | 쓸 수 있는 테스트 파일 glob                         |
| `E2E_HEALER_DENY_GLOBS`        | `.env,.git/**,...`                    | 샌드박스가 차단하는 경로                            |
| `E2E_HEALER_ALLOW_TEMP_HELPER` | `true`                                | 셀렉터 검증 임시 helper 파일 허용                   |

> `--app-url` CLI 플래그로 `E2E_HEALER_APP_URL`을 덮어쓸 수 있습니다. 로컬에서 셀렉터 검증을
> 실제로 돌리려면 Playwright 프로젝트에 브라우저가 설치돼 있어야 합니다
> (`npm install && npx playwright install`).

## 개발

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

자세한 내용은 [`CONTRIBUTING.md`](CONTRIBUTING.md)를 참고하세요.

## 한계

- 셀렉터와 대기 조건만 수정 — 단언이나 제어 흐름은 절대 건드리지 않음.
- JSX/TSX diff 분석기는 v0.1 기준 정규식 휴리스틱(tree-sitter 업그레이드 예정).
- Selector Verifier는 v1에서 `APP_URL`의 **진입 페이지 상태**를 기준으로 검증합니다. 클릭·이동
  이후의 깊은 상태에서만 나타나는 요소는 이 단계에서 검증되지 않고, 최종 판정은 Test Runner가
  맡습니다(추후 실패 시점 스냅샷 캡처로 고도화 예정).
- 치유 품질은 LLM과 `git diff`의 명확성에 좌우됨.

## 라이선스

[MIT](LICENSE)
