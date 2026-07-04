# AI 기반 E2E 테스트 자가 치유 엔진

깨진 Playwright E2E 테스트를 자동으로 복구합니다. UI 변경으로 요소가 이름이 바뀌거나
구조가 달라져 테스트의 셀렉터가 깨지면, 엔진이 실패 원인을 진단하고 → 셀렉터/대기 조건을
패치하고 → **실제 DOM에서 새 셀렉터를 검증한 뒤** → 테스트를 다시 실행하는 과정을 테스트가
통과하거나 재시도 한도에 도달할 때까지 반복한 뒤 수정본을 파일에 기록합니다. 로컬 **CLI**로도,
패치 PR을 여는 **CI GitHub Action**으로도 동작합니다.

> **안전장치(가드레일):** 엔진은 **실패한 셀렉터(locator)와 대기 조건만** 수정합니다.
> 단언(assertion)이나 테스트 로직은 절대 건드리지 않으며, 패치는 항상 사람이 검토할 수
> 있는 형태로 남습니다.

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

## 구현 현황

핵심 복구 루프가 처음부터 끝까지 동작하는 상태입니다.

| 계층 / 구성요소                                  | 상태                                                   | 위치                                            |
| ------------------------------------------------ | ------------------------------------------------------ | ----------------------------------------------- |
| CLI 코어(`heal` 명령, 종료 코드 + `--json` 요약) | ✅ 구현 완료                                           | `app/cli.py`                                    |
| 에러 로그 파서                                   | ✅ 구현 완료                                           | `app/preprocess/error_log_parser.py`            |
| Diff-JSX AST 분석기(정규식 휴리스틱)             | ✅ 구현 완료                                           | `app/preprocess/diff_ast_analyzer.py`           |
| LangGraph 상태 / 그래프 조립                     | ✅ 구현 완료                                           | `app/state.py`, `app/graph.py`                  |
| Diagnoser 노드                                   | ✅ 구현 완료                                           | `app/nodes/diagnoser.py`                        |
| Patch Generator 노드(Structured Outputs)         | ✅ 구현 완료                                           | `app/nodes/patch_generator.py`                  |
| **Selector Verifier 노드(실제 DOM 대조 검증)**   | ✅ **구현 완료**                                       | `app/nodes/selector_verifier.py`, `app/verify/` |
| Test Runner 노드 + Router 조건부 엣지            | ✅ 구현 완료                                           | `app/nodes/test_runner.py`, `app/graph.py`      |
| LLM 클라이언트                                   | ✅ **NVIDIA NIM(`openai/gpt-oss-120b`)으로 전환 완료** | `app/llm.py`                                    |
| GitHub Action 래퍼 + 셀프 데모 워크플로          | ✅ 구현 완료                                           | `action.yml`, `ci/`                             |
| 테스트 스위트(pytest)                            | ✅ 대부분 통과(아래 참고)                              | `tests/`                                        |

> **참고 — LLM 제공자:** 초기 OpenAI 구현에서 **NVIDIA NIM의 OpenAI 호환 엔드포인트**로
> 마이그레이션했습니다. OpenAI SDK를 그대로 쓰되 `base_url`만 NVIDIA로 지정합니다. 기본
> 모델 `openai/gpt-oss-120b`는 추론(reasoning) 모델이라 `reasoning_content`가 응답 토큰을
> 잠식하지 않도록 두 호출 모두 `max_tokens`를 명시합니다. 패치 생성기의 Structured Outputs
> 가드레일(`response_format=PatchOutput`)은 그대로 유지되며 실제 호출로 검증했습니다.

### 알려진 이슈 / 남은 작업

- `tests/test_error_log_parser.py`의 라인 번호 단언이 파서 실제 출력과 어긋나 1건 실패
  (기능 결함이 아닌 테스트 기대값 문제).
- `app/nodes/test_runner.py::test_runner` 노드 함수가 이름 규칙상 pytest에 테스트로
  잘못 수집됨(실제 테스트 아님, 수집 경고).
- Diff 분석기는 v0.1에서 정규식 휴리스틱 → 향후 tree-sitter로 고도화 예정.

## 설치

Python 3.13+ 과 저장소 내 Playwright(Node) 프로젝트가 필요합니다.

```bash
uv sync                 # 또는 (배포 후): pipx install ai-driven-e2e
cp .env.example .env    # 이후 E2E_HEALER_NVIDIA_API_KEY 설정
```

## 사용법 (CLI)

```bash
# 실패한 테스트를 치유. --log 없이 실행하면 도구가 직접 테스트를 돌려 실패 로그를 수집.
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

스위트를 실행하고 실패 시 자동 치유한 뒤 검토용 패치 PR을 엽니다. 일반적인 연결 예시:

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/AI_Anything@v0.1.0
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}

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
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml)에 있습니다 —
`.github/workflows/`로 복사하고 `NVIDIA_API_KEY`를 설정하면 동작합니다.

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
