# AI-Driven E2E Test Self-Healing Engine — Design

> Original design draft. Consumer-facing docs live in the top-level `README.md`.

## 1. 시스템 아키텍처 및 데이터 흐름

전체 시스템은 CLI 인터페이스(사용자 실행), AST 분석기(데이터 정제), LangGraph 에이전트(판단 및 루프), Test Runner(검증 도구)의 4가지 레이어로 구성됩니다.

## 2. 세부 컴포넌트 설계

### 2.1. 전처리 모듈 (Data Preprocessor)

LLM의 컨텍스트 윈도우 최적화 및 환각 방지를 위해 입력 데이터를 추상화합니다.

- Error Log Parser: Playwright의 전체 로그 중 Error: 키워드와 실패한 라인 넘버, 스택 트레이스 상단의 핵심 실패 사유(예: locator.click: Timeout 5000ms waiting for selector...)만 추출합니다.

- Diff-JSX AST Analyzer: 프론트엔드 코드의 변경 사항(git diff) 중 JSX/TSX 영역을 파싱하여, 변경 전후의 DOM 노드 트리 상태를 가벼운 JSON 객체로 변환합니다.

```json
{
  "file": "components/SubmitButton.tsx",
  "previous": {
    "tag": "button",
    "attributes": { "id": "old-id", "className": "btn" }
  },
  "current": {
    "tag": "button",
    "attributes": { "id": "new-id", "className": "btn" }
  }
}
```

### 2.2. LangGraph 상태 정의 (State Definition)

에이전트 간 공유되는 State는 불변성을 유지하며 상태 추적이 가능하도록 설계합니다. Python의 TypedDict를 활용합니다.

```python
from typing import TypedDict, List, Dict

class AgentState(TypedDict):
    test_script_path: str         # 수정 대상 테스트 파일 경로
    original_code: str            # 최초 테스트 스크립트 코드
    current_code: str             # 현재 루프에서 수정된 테스트 스크립트 코드
    error_log: str                # Playwright 최신 에러 로그
    dom_diff_context: List[Dict]  # AST 파싱을 거친 DOM 변경점 정보
    analysis_report: str          # Diagnoser가 작성한 실패 원인 리포트
    patch_instructions: Dict      # Patch Generator가 생성한 수정 가이드 (라인, 코드)
    loop_count: int               # 무한 루프 방지용 카운터 (Max: 3)
    is_success: bool              # 테스트 통과 여부
```

### 2.3. 에이전트 노드 및 흐름 제어 (Nodes & Conditional Edges)

LangGraph는 3개의 노드와 1개의 조건부 분기(Conditional Edge)로 제어 흐름을 관리합니다.

#### Node 1: Diagnoser (분석 노드)

- Input: error_log, dom_diff_context, current_code

- Logic: 에러 로그의 셀렉터 실패 지점과 실제 DOM 변경 정보를 매핑하여 깨진 원인을 논리적으로 추론합니다.

- Output: analysis_report 업데이트.

#### Node 2: Patch Generator (수정 노드)

- Input: analysis_report, current_code

- Logic: Structured Outputs(NVIDIA NIM, OpenAI 호환)를 강제하여 타겟 라인과 수정할 코드를 엄격한 JSON 형태로 반환받습니다. 임의의 코드 재작성을 방지합니다.

- Output: current_code 수정 및 patch_instructions 기록.

#### Node 3: Test Runner (실행 노드)

- Input: current_code

- Logic: 수정된 코드를 파일 시스템에 물리적으로 쓰고, subprocess를 통해 npx playwright test <path>를 실행합니다.

- Output: 성공 시 is_success = True, 실패 시 error_log 갱신 및 loop_count += 1.

#### Conditional Edge: Router (분기 로직)

- Condition: is_success == True이거나 loop_count >= 3이면 [End]로 분기합니다.

- Condition: is_success == False이고 loop_count < 3이면 [Diagnoser]로 재진입시킵니다.

## 3. 핵심 한계 및 제어 대책

### 코드 왜곡 가드레일 (Code Integrity):

LLM이 스크립트의 전체 테스트 비즈니스 로직(Assertion 등)을 임의로 바꾸지 못하도록, Patch Generator 노드에는 오직 "실패한 Locator(셀렉터) 수정 및 대기 조건(Wait) 최적화" 임무만 프롬프트와 스키마 수준에서 명확히 제한해야 합니다.

### 비결정적 출력 통제:

LLM의 API 응답 지연 및 예외 처리를 위해 파이썬 백엔드 단에서 try-except 블록을 설계하여, JSON 파싱 실패 시 Patch Generator로 즉시 되먹임(Feedback) 처리를 수행하는 내부 예외 루프를 병행합니다.
