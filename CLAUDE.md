# CLAUDE.md · 입시나침반 미니 PJT

- 모든 답변·주석·문서는 한국어로 작성한다.
- 교육 과정(삼성SDS AX, Day 8~10) 미니 PJT. 과제 규약: 산출물 = 코드 + README + RAGAS 평가 리포트, 제출 = 이 GitHub 레포 주소.

## 작업 규칙
- 무엇을 만드는지는 `SERVICE.md`가 기준이다. 애매하면 추측하지 말고 사용자에게 묻는다
- 구현은 `TASKS.md`의 작업을 **한 번에 하나만** 한다. 끝나면 "확인 방법"대로 실행해 보고 체크한다
- 계산·규칙 판정은 LLM이 아니라 파이썬 코드로 한다. LLM 판정은 필요할 때 덧붙인다
- 라이브러리는 교안 패키지(`requirements.txt` 상단)만 쓴다. 승인된 추가분은 `fastapi`·`uvicorn`(과제 API)·`ragas`(평가)뿐이고, 그 밖의 새 프레임워크·라이브러리는 사용자 허락 없이 들이지 않는다
- 레퍼런스 코드는 교안 실습(`sds-ax-practice` 레포의 `day1_practice`~`day7_practice`)이다. 평가는 `day7_practice/run_eval.py`·`llm_judge.py` 구조를 따른다
- API 키·AWS 자격 증명은 `.env`에서 읽는다

## 모델 (Bedrock, us-east-1)
- 사용 가능 목록(강사 제공): `us.`/`global.anthropic.claude-sonnet-4-5-20250929-v1:0`, `us.`/`global.anthropic.claude-sonnet-4-6`, `us.`/`global.anthropic.claude-haiku-4-5-20251001-v1:0`, `us.amazon.nova-pro-v1:0`, `us.`/`global.amazon.nova-2-lite-v1:0`, `us.amazon.nova-lite-v1:0`
- 에이전트 기본: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`(교안과 같은 모델). 호출이 실패하면 `global.anthropic.claude-sonnet-4-5-20250929-v1:0` → `us.anthropic.claude-sonnet-4-6` 순으로 대체해 중단 없이 쓴다 (LangChain 기본 기능으로, 새 라이브러리 없이)
- LLM 심사(judge): 교안처럼 상수로 고정한다(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`, temperature 0). 라운드 간 비교는 항상 같은 judge로 한다
- 임베딩: `amazon.titan-embed-text-v2:0`(교안 2·6일차와 동일, LLM 목록과 별개)
- 목록 밖 모델 ID는 쓰지 않는다

## 프로젝트 요약
고1 학기별·과목별 5등급제 내신 기반 수도권 **4년제·전문대** 지원 가능성 예측 + 상위 대학을 위한 보강 과목 추천(What-if) + 고2·고3 로드맵 Agentic RAG 웹서비스. 상세는 `SERVICE.md`, 화면은 `docs/SCREEN_SPEC.md`.
- 대상 학생: 2010년생, 2026년 고1 → **2029학년도 대입**. 2029 시행계획(2027년 봄 공개) 전까지 2028 자료를 proxy로 쓰고 학년도를 항상 명시
- 대상 성적대: 1등급 최상위가 아닌 2~3등급대
- MVP 데이터: 간호(4년제 17 + 전문대 1)와 컴퓨터공학(4년제 16) 두 계열 (2026-11-04 성균관대·경희대·한국외대·명지대·인천대·인천가톨릭대 추가). 학과 무관 구조를 유지하고 특정 학과 전용 코드를 만들지 않는다

## 과제 필수 규약
- 폴더: `src/`(agent.py · tools.py · retriever.py), `data/`, `evaluation/`(test_queries.csv · round1_report.md · round2_report.md), `SERVICE.md`, `README.md`, `requirements.txt`
- API: `POST /query` body `{"question": ...}` → `{"answer", "contexts": [{"doc_id","text"}], "trace": [{"step","input","output"}]}`
- `test_queries.csv` 7컬럼 스키마(id,category,input,expected_traits,forbidden,expected_tools,note) 고정. RAGAS 정답은 `evaluation/ragas_references.csv`에 id로 조인
- 평가셋은 이 CSV 하나만 쓴다(강사 안내: json 또는 csv). `run_eval.py`가 교안 `run_eval.py`처럼 유형별로 자동 판정한다: guardrail은 고정 거절 문구로 차단 여부(`expected_tools`가 비어 있으면 차단 기대, 있으면 차단 없이 통과 기대 — 28번), `expected_tools`는 호출 도구, note의 `정답:`은 숫자 대조, 데이터 없음 문항은 거절 신호, 나머지는 `llm_judge` 정답 대조. 결과는 교안 DoD처럼 기능·안전 두 줄로도 보여 준다
- 필수 패턴: LCEL+Pydantic, RAG, Observability(트레이스), 평가(RAGAS·LLM-as-Judge). 권장 6개 이상

## 구현 방향
- 설계 원칙: **계산·환산·분류는 코드(`src/grades.py` 순수 함수), LLM은 설명만.** 위험 요청은 `before_model` 규칙으로 모델 호출 전 차단, 숫자 표는 도구 결과로 코드가 렌더링. 모듈 구성·진입점은 `docs/ARCHITECTURE.md`를 따른다
- README는 강사 리뷰 형식(한 줄 요약·제출자 설명·아키텍처·눈에 띄는 점·진입점)을 유지하고, 평가 결과 숫자는 README와 `evaluation/roundN_report.md`를 일치시킨다
- 에이전트는 실습과 같은 `create_agent` + `AgentMiddleware` + `ChatBedrockConverse` + `SqliteSaver` 구성 (`day5_practice` 참고)
- 웹서비스: Python FastAPI 백엔드 — 상담 챗은 `POST /query`(에이전트), 계산·조회 화면은 `/api/*`(도구 직접 호출). 화면은 Claude Design 캔버스 「입시나침반 화면 디자인」(화면 6개) 시안을 `web/`의 정적 HTML+JS로 옮겨 FastAPI가 함께 띄운다(새 프론트엔드 라이브러리 없음). 개발 중 각 작업은 브라우저 화면에서 동작을 확인한다 (`docs/SCREEN_SPEC.md`)
- 스택은 이 폴더 `requirements.txt` 기준: LangChain/LangGraph, AWS Bedrock(langchain-aws), Chroma + BM25(kiwipiepy) 하이브리드 검색, FastAPI
- 출발점 코드: 교안 실습 `day1_practice`~`day7_practice` (평가·트레이스는 `day7_practice`)
- 개발 순서: 평가 셋(`evaluation/test_queries.csv`) 확정 → `TASKS.md` 작업을 Plan 모드로 하나씩 계획 승인 → 1차 도구 3개(SERVICE.md §3-1) → 채점 기준선 → 한 번에 하나씩 고치는 루프

## 파일 구성 (이 목록 밖의 파일은 만들지 않는다)
| 파일 | 역할 |
|---|---|
| `TASKS.md` | 구현 작업 목록과 확인 방법 (작업 끝나면 체크) |
| `requirements.txt` | 교안 패키지 + 승인된 추가분 |
| `src/api.py` | FastAPI 진입점 (`POST /query`, `/api/*`) |
| `src/agent.py` | `create_agent` 조립, 시스템 프롬프트, 미들웨어 순서 |
| `src/tools.py` | 도메인 도구 (`@tool`) |
| `src/retriever.py` | RAG 파이프라인 |
| `src/grades.py` | 평균·환산·판정·What-if 순수 함수 |
| `src/data_store.py` | CSV 로드·조회 |
| `src/middleware.py` | PII 마스킹, 입력 규칙 선처리, 트레이스 |
| `src/guardrails.py` | 출력 가드레일, 결과표 렌더링 |
| `src/replies.py` | 고정 응답 문구 |
| `tests/test_grades.py` | `grades.py` 단위 테스트 |
| `web/*.html`, `web/app.js`, `web/style.css` | 화면 6개(성적 입력·분석·지원 가능 대학·보강 과목·로드맵·상담)와 공통 스크립트·스타일 |
| `evaluation/run_eval.py` | 평가셋 실행·채점·RAGAS |

## 코드 규칙
- 파일 하나에 한 가지 역할만 둔다 (위 표)
- 함수와 도구에는 한국어 docstring을 쓴다. 도구 docstring은 "언제 쓰는지"를 첫 줄에 쓴다
- 비밀 값(AWS 키 등)은 `.env`에서 읽고 코드·로그에 적지 않는다
- 숫자 계산은 `grades.py` 함수만 하고, 도구·프롬프트에서 따로 계산하지 않는다

## 하지 말 것
- 요청하지 않은 파일을 새로 만들지 않는다
- 기존 파일을 통째로 다시 쓰지 않는다. 바뀐 부분만 고친다
- `evaluation/test_queries.csv`를 통과율을 올리려고 고치지 않는다 (문항 수정은 사용자 승인 후에만)
- 평가 루프에서 한 번에 여러 곳을 고치지 않는다

## 데이터 규칙
- CSV는 UTF-8 BOM, pandas `encoding="utf-8-sig"`, 필드 내 다중값은 `;`
- `admission_results.csv`의 컷은 9등급 기준(`grade_scale=9`). 5등급 값과 섞지 않는다. 정시 행은 `cut_metric=평균백분위`라 등급과 비교하지 않는다
- 원본 PDF(`data/raw/`)·자녀 실제 성적·학교 정보(`data/private/`)는 커밋하지 않는다. `data/sample_student_grades.csv`는 가짜 페르소나
- 석차등급이 없는(성취도만) 과목은 평균 계산에서 제외
- 4년제·전문대를 우열로 표현하지 않는다
- 데이터에 없는 수치는 추정해서 채우거나 답하지 않는다
