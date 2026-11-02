# CLAUDE.md · 입시나침반 미니 PJT

- 모든 답변·주석·문서는 한국어로 작성한다.
- 교육 과정(삼성SDS AX, Day 8~10) 미니 PJT. 과제 규약: 산출물 = 코드 + README + RAGAS 평가 리포트, 제출 = 이 GitHub 레포 주소.

## 프로젝트 요약
고1 학기별·과목별 5등급제 내신 기반 수도권 **4년제·전문대** 지원 가능성 예측 + 상위 대학을 위한 보강 과목 추천(What-if) + 고2·고3 로드맵 Agentic RAG 웹서비스. 상세는 `SERVICE.md`, 화면은 `docs/SCREEN_SPEC.md`.
- 대상 학생: 2010년생, 2026년 고1 → **2029학년도 대입**. 2029 시행계획(2027년 봄 공개) 전까지 2028 자료를 proxy로 쓰고 학년도를 항상 명시
- 대상 성적대: 1등급 최상위가 아닌 2~3등급대

## 과제 필수 규약
- 폴더: `src/`(agent.py · tools.py · retriever.py), `data/`, `evaluation/`(test_queries.csv · round1_report.md · round2_report.md), `SERVICE.md`, `README.md`, `requirements.txt`
- API: `POST /query` body `{"question": ...}` → `{"answer", "contexts": [{"doc_id","text"}], "trace": [{"step","input","output"}]}`
- `test_queries.csv` 7컬럼 스키마(id,category,input,expected_traits,forbidden,expected_tools,note) 고정. RAGAS 정답은 `evaluation/ragas_references.csv`에 id로 조인
- 필수 패턴: LCEL+Pydantic, RAG, Observability(트레이스), 평가(RAGAS·LLM-as-Judge). 권장 6개 이상

## 구현 방향
- 설계 원칙: **계산·환산·분류는 코드(`src/grades.py` 순수 함수), LLM은 설명만.** 위험 요청은 `before_model` 규칙으로 모델 호출 전 차단, 숫자 표는 도구 결과로 코드가 렌더링. 모듈 구성·진입점은 `docs/ARCHITECTURE.md`를 따른다
- README는 강사 리뷰 형식(한 줄 요약·제출자 설명·아키텍처·눈에 띄는 점·진입점)을 유지하고, 평가 결과 숫자는 README와 `evaluation/roundN_report.md`를 일치시킨다
- 에이전트는 실습과 같은 `create_agent` + `AgentMiddleware` + `ChatBedrockConverse` + `SqliteSaver` 구성 (`day5_practice` 참고)
- 웹서비스: Python FastAPI 백엔드 — 상담 챗은 `POST /query`(에이전트), 계산·조회 화면은 `/api/*`(도구 직접 호출). 화면은 Claude Design으로 별도 개발 (`docs/SCREEN_SPEC.md`)
- 스택은 상위 폴더 `requirements.txt` 기준: LangChain/LangGraph, AWS Bedrock(langchain-aws), Chroma + BM25(kiwipiepy) 하이브리드 검색
- 출발점 코드: 상위 폴더 `day3_practice`~`day6_practice`

## 데이터 규칙
- CSV는 UTF-8 BOM, pandas `encoding="utf-8-sig"`, 필드 내 다중값은 `;`
- `admission_results.csv`의 컷은 9등급 기준(`grade_scale=9`). 5등급 값과 섞지 않는다. 정시 행은 `cut_metric=평균백분위`라 등급과 비교하지 않는다
- 원본 PDF(`data/raw/`)·자녀 실제 성적·학교 정보(`data/private/`)는 커밋하지 않는다. `data/sample_student_grades.csv`는 가짜 페르소나
- 석차등급이 없는(성취도만) 과목은 평균 계산에서 제외
- 4년제·전문대를 우열로 표현하지 않는다
- 데이터에 없는 수치는 추정해서 채우거나 답하지 않는다
