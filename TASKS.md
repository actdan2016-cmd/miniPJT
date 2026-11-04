# TASKS · 입시나침반 구현 작업 목록

> `SERVICE.md`를 구현 순서로 쪼갠 목록입니다. 작업 하나는 30분~1시간 크기이고, 끝나면 "확인"대로 직접 실행해 본 뒤 `[x]`로 체크합니다.
> 한 번에 한 작업만 합니다. 작업마다 Plan 모드로 "TASKS.md N번 작업 계획 보여줘" → 승인 → 구현 → 실행 확인 → 커밋 순서로 진행합니다.
> 정답 숫자는 `evaluation/test_queries.csv`의 `note`(`정답:`)를 기준으로 합니다.
> **화면 확인**: `uvicorn src.api:app --reload` → http://localhost:8000. 화면이 생긴 뒤의 작업은 브라우저에서 동작을 확인합니다. 화면 시안은 claude.ai 캔버스 「입시나침반 화면 디자인」(화면 6개, v0.1)이고, v0.1에 없는 판정 규칙 v1 요소(격차 큼·수도권 외 참고·종합 참고치·입결 미공개)는 옮길 때 추가합니다.

## 묶음 A · 최소 한 경로 (성적 입력 → 평균 → 환산 → 판정 → 화면·답변)
- [x] **A1. 실행 환경**: `.venv`에 `requirements.txt` 설치, `.env`로 Bedrock 연결 확인
  - 확인: 기본 모델(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`)에 한 줄 질문을 보내 답이 온다
- [x] **A2. `src/grades.py` + `tests/test_grades.py`**: 학점 가중 평균(성취도만 있는 과목 제외), 5→9등급 환산(중간 백분위 선형 보간), `classify(g, c)`(안정·적정·소신·상향·격차 큼)
  - 확인: `pytest tests/test_grades.py` 통과 — 51/23=2.22, 2.2→3.73, 2.0→3.34, 2.4→4.09, 105/46→3.89, 3.5→5.76, 구간 경계값
- [x] **A3. `src/data_store.py`**: CSV 로드와 조회(대학·모집단위·전형·학년도), 수도권/비수도권 구분
  - 확인: 인하대 컴퓨터공학과 지역균형 2026 조회 → 25명·7.1:1·충원 42·2.19/2.28 (7번 정답)
- [x] **A4. `src/tools.py` 1차 도구 3개**: `calc_school_gpa`, `lookup_admission_results`, `classify_chances` (도구 docstring 첫 줄은 "언제 쓰는지")
  - 확인: 도구를 직접 호출해 3번 정답(수원대 교과우수=상향 c=3.43 등)과 22번 정답(누적 2.28 → 3.89)이 나온다
- [ ] **A5. `src/api.py` 계산 API + 화면 서빙**: FastAPI가 `web/`을 띄우고 `POST /api/grades/analyze`, `POST /api/predict`는 도구를 직접 호출(LLM 없음)
  - 확인: http://localhost:8000/docs 에서 22번 성적으로 두 API를 호출해 2.28·3.89와 판정 목록이 온다
- [ ] **A6. 화면 1·2 (성적 입력·성적 분석)**: 시안 Main·Analysis를 `web/`으로 옮기고 A5 API에 연결
  - 확인: 브라우저에서 "예시 학생으로 체험" → 1-1 2.35·1-2 2.22, 분석 화면 누적 2.28·국영수사 2.25·국영수과 2.50·9등급 3.89
- [ ] **A7. 화면 3 (지원 가능 대학)**: 시안 Universities를 옮기고 판정 규칙 v1 요소 추가
  - 확인: 22번 성적·간호로 보면 수도권은 수원대 고교추천=상향 외 "격차 큼", 수도권 외 참고에 한서대 교과2=적정·한서인재=소신, 종합 전형에는 판정 없이 참고치만 보인다
- [ ] **A8. `src/agent.py`**: `create_agent` + 시스템 프롬프트 + `SqliteSaver`, 모델 대체 순서(CLAUDE.md 모델 절)
  - 확인: 터미널에서 22번 질문을 넣으면 3.89와 "수원대 간호 고교추천=상향"이 답에 들어 있다
- [ ] **A9. `POST /query` + 화면 6 (상담)**: `{answer, contexts, trace}` 응답, 상담 화면에서 질문·답·근거 표시, `thread_id`로 대화 유지
  - 확인: 상담 화면에서 22번 질문 → 답과 근거가 보이고, /docs에서 같은 질문의 응답 세 필드가 모두 채워져 있다
- [ ] **A10. `evaluation/run_eval.py` 기준선**: 28건 실행, 교안 `run_eval.py`처럼 유형별 자동 판정(도구 호출·숫자 대조·거절 신호·차단 여부) + 기능/안전 DoD 출력
  - 확인: `evaluation/round1_report.md`가 생기고 통과율이 숫자로 적힌다 (목표 1차 ≥ 60%)

## 묶음 B · 가드레일과 관측
- [ ] **B1. `src/replies.py` + 입력 규칙(`middleware.py` before_model)**: 보장 요청·타 학생·대리 작성·인젝션은 모델 호출 전에 고정 문구로 답한다
  - 확인: 상담 화면에 18·19·20·21번을 넣으면 고정 문구가 바로 나오고 trace에 모델 호출이 없다
- [ ] **B2. PII 마스킹 + 트레이스(`middleware.py`)**: 이름·학교명·연락처 마스킹, 단계별 trace 기록
  - 확인: 19번 trace에 "김민준"·"서울OO고" 원문이 없다
- [ ] **B3. `src/guardrails.py`**: 출력 검사(보장 표현·출처 없는 수치) + 결과표 코드 렌더링 + 진로 중립
  - 확인: guardrail 5건(18·19·20·21·28) 100%, 판정 표의 숫자가 도구 결과와 같다

## 묶음 C · RAG와 2차 도구
- [ ] **C1. `src/retriever.py`**: `data/raw/policies/` 시행계획(PDF·HWP·MD) 로드 → 청크 → Chroma(titan v2) + BM25(kiwipiepy) 하이브리드
  - 확인: "한양대 간호 추천형 수능최저" 검색 상위 3개에 `한양대_2028_시행계획.pdf` p.13이 있다
- [ ] **C2. `retrieve_admission_docs` + `convert_grade_5to9` 도구**
  - 확인: 상담 화면에서 4·10·12·24번 질문 → 답에 문서명·학년도가 있고 근거 패널에 해당 청크가 보인다
- [ ] **C3. `get_recommended_courses` + `simulate_improvement` 도구 + `POST /api/simulate`**
  - 확인: 5·6·23번 통과 (23번은 수원대 간호 교과우수 c=2.65까지의 격차 제시)
- [ ] **C4. 화면 4·5 (보강 과목 What-if · 학습 로드맵)**: 시안 Improve·Roadmap을 옮기고 C3 API에 연결
  - 확인: 보강 화면에서 과목 등급을 바꾸면 평균·9등급·판정이 즉시 바뀌고, 로드맵에 권장과목과 출처가 보인다
- [ ] **C5. 평가 확장**: `llm_judge`(정답 대조, judge 모델 고정) + RAGAS 5건(4·10·12·24·26)
  - 확인: 리포트에 judge 점수와 RAGAS 지표 4개가 숫자로 나온다

## 묶음 D · 3차 도구와 개선 루프
- [ ] **D1. `search_programs` + `save_student_profile`(HITL) + 화면 1 "프로필 저장" 승인 모달**
  - 확인: 15번 통과, 17번 멀티턴에서 성적 재입력을 요구하지 않음, 화면에서 승인하기 전에는 저장되지 않음
- [ ] **D2. 개선 루프 (최대 3회)**: 실패 하나 고르기 → 원인 설명 → 한 곳만 수정 → 28건 재실행 → 커밋
  - 확인: `evaluation/round2_report.md` 통과율 ≥ 85%, guardrail 100%
- [ ] **D3. README 마무리**: 실행 방법(화면 포함), 평가 결과 숫자, 트라이앤에러 회고
  - 확인: README의 숫자가 `round1/2_report.md`와 같고, README대로 따라 하면 화면이 뜬다
