# TASKS · 입시나침반 구현 작업 목록

> `SERVICE.md`를 구현 순서로 쪼갠 목록입니다. 작업 하나는 30분~1시간 크기이고, 끝나면 "확인"대로 직접 실행해 본 뒤 `[x]`로 체크합니다.
> 한 번에 한 작업만 합니다. 작업마다 Plan 모드로 "TASKS.md N번 작업 계획 보여줘" → 승인 → 구현 → 실행 확인 → 커밋 순서로 진행합니다.
> 정답 숫자는 `evaluation/test_queries.csv`의 `note`(`정답:`)를 기준으로 합니다.

## 묶음 A · 최소 한 경로 (성적 입력 → 평균 → 환산 → 판정 → 답변)
- [ ] **A1. 실행 환경**: `requirements.txt` 설치, `.env`로 Bedrock 연결 확인
  - 확인: 기본 모델(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`)에 한 줄 질문을 보내 답이 온다
- [ ] **A2. `src/grades.py` + `tests/test_grades.py`**: 학점 가중 평균(성취도만 있는 과목 제외), 5→9등급 환산(중간 백분위 선형 보간), `classify(g, c)`(안정·적정·소신·상향·격차 큼)
  - 확인: `pytest tests/test_grades.py` 통과 — 51/23=2.22, 2.2→3.73, 2.0→3.34, 2.4→4.09, 105/46→3.89, 3.5→5.76, 구간 경계값
- [ ] **A3. `src/data_store.py`**: CSV 로드와 조회(대학·모집단위·전형·학년도), 수도권/비수도권 구분
  - 확인: 인하대 컴퓨터공학과 지역균형 2026 조회 → 25명·7.1:1·충원 42·2.19/2.28 (7번 정답)
- [ ] **A4. `src/tools.py` 1차 도구 3개**: `calc_school_gpa`, `lookup_admission_results`, `classify_chances` (도구 docstring 첫 줄은 "언제 쓰는지")
  - 확인: 도구를 직접 호출해 3번 정답(수원대 교과우수=상향 c=3.43 등)과 22번 정답(누적 2.28 → 3.89)이 나온다
- [ ] **A5. `src/agent.py`**: `create_agent` + 시스템 프롬프트 + `SqliteSaver`, 모델 대체 순서(CLAUDE.md 모델 절)
  - 확인: 터미널에서 22번 질문을 넣으면 3.89와 "수원대 간호 고교추천=상향"이 답에 들어 있다
- [ ] **A6. `src/api.py`**: FastAPI `POST /query` → `{answer, contexts, trace}`
  - 확인: `uvicorn`으로 띄우고 22번 질문을 보내면 세 필드가 모두 채워진 JSON이 온다
- [ ] **A7. `evaluation/run_eval.py` 기준선**: 28건 실행, 교안 `run_eval.py`처럼 유형별 자동 판정(도구 호출·숫자 대조·거절 신호·차단 여부) + 기능/안전 DoD 출력
  - 확인: `evaluation/round1_report.md`가 생기고 통과율이 숫자로 적힌다 (목표 1차 ≥ 60%)

## 묶음 B · 가드레일과 관측
- [ ] **B1. `src/replies.py` + 입력 규칙(`middleware.py` before_model)**: 보장 요청·타 학생·대리 작성·인젝션은 모델 호출 전에 고정 문구로 답한다
  - 확인: 18·19·20·21번이 고정 문구로 끝나고 trace에 모델 호출이 없다
- [ ] **B2. PII 마스킹 + 트레이스(`middleware.py`)**: 이름·학교명·연락처 마스킹, 단계별 trace 기록
  - 확인: 19번 trace에 "김민준"·"서울OO고" 원문이 없다
- [ ] **B3. `src/guardrails.py`**: 출력 검사(보장 표현·출처 없는 수치) + 결과표 코드 렌더링 + 진로 중립
  - 확인: guardrail 5건(18·19·20·21·28) 100%, 판정 표의 숫자가 도구 결과와 같다

## 묶음 C · RAG와 2차 도구
- [ ] **C1. `src/retriever.py`**: `data/raw/policies/` 시행계획(PDF·HWP·MD) 로드 → 청크 → Chroma(titan v2) + BM25(kiwipiepy) 하이브리드
  - 확인: "한양대 간호 추천형 수능최저" 검색 상위 3개에 `한양대_2028_시행계획.pdf` p.13이 있다
- [ ] **C2. `retrieve_admission_docs` + `convert_grade_5to9` 도구**
  - 확인: 4·10·12·24번 통과, 답에 문서명·학년도가 있다
- [ ] **C3. `get_recommended_courses` + `simulate_improvement` 도구**
  - 확인: 5·6·23번 통과 (23번은 수원대 간호 교과우수 c=2.65까지의 격차 제시)
- [ ] **C4. 평가 확장**: `llm_judge`(정답 대조, judge 모델 고정) + RAGAS 5건(4·10·12·24·26)
  - 확인: 리포트에 judge 점수와 RAGAS 지표 4개가 숫자로 나온다

## 묶음 D · 3차 도구와 개선 루프
- [ ] **D1. `search_programs` + `save_student_profile`(HITL)**
  - 확인: 15번 통과, 17번 멀티턴에서 성적 재입력을 요구하지 않음, 승인 전에는 저장되지 않음
- [ ] **D2. 개선 루프 (최대 3회)**: 실패 하나 고르기 → 원인 설명 → 한 곳만 수정 → 28건 재실행 → 커밋
  - 확인: `evaluation/round2_report.md` 통과율 ≥ 85%, guardrail 100%
- [ ] **D3. README 마무리**: 실행 방법, 평가 결과 숫자, 트라이앤에러 회고
  - 확인: README의 숫자가 `round1/2_report.md`와 같다
