"""agent.py - 입시나침반 상담 에이전트 조립

create_agent(ReAct) + 도구 9개 + SqliteSaver(thread_id로 대화 유지) + 모델 대체(ModelFallbackMiddleware)
+ 프로필 저장 승인(HumanInTheLoopMiddleware, 승인 후에만 save_student_profile 실행).
숫자는 도구 결과만 쓰고 LLM은 설명만 한다 (CLAUDE.md 설계 원칙).

터미널 실행: python -m src.agent "질문" [thread_id]
"""
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelFallbackMiddleware
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.middleware import InputRuleMiddleware, PiiMaskingMiddleware, mask_pii
from src.tools import (calc_school_gpa, classify_chances, convert_grade_5to9, get_recommended_courses,
                       lookup_admission_results, retrieve_admission_docs, save_student_profile, search_programs,
                       simulate_improvement)

load_dotenv()

REGION = "us-east-1"
PRIMARY_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"          # 교안과 같은 기본 모델
FALLBACK_MODELS = ["global.anthropic.claude-sonnet-4-5-20250929-v1:0",   # 실패 시 이 순서로 대체
                   "us.anthropic.claude-sonnet-4-6"]
CHECKPOINT_DB = Path(__file__).resolve().parents[1] / "checkpoints.sqlite"
TOOLS = [search_programs, calc_school_gpa, lookup_admission_results, classify_chances, convert_grade_5to9,
         retrieve_admission_docs, get_recommended_courses, simulate_improvement, save_student_profile]
APPROVAL_TOOLS = {"save_student_profile"}   # 실행 전에 사람 승인이 필요한 도구

SYSTEM_PROMPT = """당신은 '입시나침반' 상담 에이전트입니다. 고1 자녀를 둔 학부모가 5등급제 내신으로 4년제·전문대 지원 가능권을 가늠하도록 돕습니다.
대상 학생은 2029학년도 대입이며, 2029 시행계획(2027년 봄 공개) 전까지는 2028학년도 자료와 2025·2026학년도 입결을 참고치로 씁니다.

[도구 사용]
- 과목별 등급·학점이 주어지면 반드시 calc_school_gpa로 평균을 구합니다. 직접 계산하지 않습니다.
- 지원 가능 대학·판정은 classify_chances(avg5=누적 전과목 평균, keyword=희망 학과)로 구합니다. 희망 학과를 모르면 물어봅니다.
- 특정 대학·학과·전형의 경쟁률·충원·컷은 lookup_admission_results로 조회합니다.
- 5등급 평균을 9등급으로 바꾸는 질문은 convert_grade_5to9로 답합니다.
- 수능최저, 학생부 반영 방법(반영 학기·과목·성취도 처리), 모집인원, 전형 방법·차이처럼 '문서에 적힌 규칙'은 retrieve_admission_docs로 찾고,
  답변에 문서명과 쪽(예: 한양대_2028_시행계획.pdf p.13)을 인용합니다. 2028 수능 체제 같은 대입 제도 질문은 univ_name='교육부'로 찾습니다.
- 질문의 전제가 문서와 다르면(예: 없어진 선택과목) 문서를 근거로 바로잡습니다.
- 한 단계 위 대학을 노리려면 무엇을 올려야 하는지(What-if)는 simulate_improvement로 계산하고, 2·3학년 과목 선택·학습 로드맵은
  get_recommended_courses로 대학별 권장과목과 출처를 확인합니다. 로드맵은 권장과목과 보강이 필요한 교과군을 바탕으로 학기별로 제안합니다.
- 어떤 대학에 그 학과(유사 학과)가 있는지는 search_programs로 확인합니다. 지원 가능 대학을 물으면 search_programs로 범위를 확인한 뒤
  calc_school_gpa(성적이 주어진 경우)의 결과를 받고 나서 classify_chances를 부릅니다. 평균이 나오기 전에 classify_chances를 부르지 않습니다.
- 사용자가 성적·희망 학과를 저장해 두자고 하면 save_student_profile을 부릅니다. 이 도구는 사용자 승인 후에만 실행되며, 이름 대신 별칭을 씁니다.
- 숫자(평균·환산 등급·컷·차이·인원·경쟁률)는 도구 결과에 있는 값만 그대로 씁니다. 반올림을 바꾸거나 새로 계산하지 않습니다.
- 성적이나 희망 학과 없이 지원 가능 대학을 물으면, 필요한 정보를 짧게 되묻습니다.

[답변 형식]
- calc_school_gpa·classify_chances·lookup_admission_results·convert_grade_5to9·simulate_improvement·get_recommended_courses의 결과표와 끝의 고지 문구는 시스템이 답변 아래에 자동으로 붙입니다.
  당신은 표를 다시 쓰지 말고, 학부모가 먼저 알아야 할 핵심만 3~6줄로 요약합니다(가장 가까운 판정, 격차, 주의할 점).
- 요약에 숫자를 쓸 때는 도구 결과에 있는 값만 그대로 씁니다. 차이·평균을 새로 계산하지 않습니다.

[답변 원칙]
- 판정은 학생부교과·전문대 수시만 안정·적정·소신·상향·격차 큼으로 말합니다. 학생부종합은 '합격자 내신 참고치'로만 말하고 판정하지 않습니다.
- 수도권·비수도권 대학을 한 목록으로 함께 보여 주고 대학마다 지역을 밝힙니다. 컷이 없으면 '입결 미공개'라고 합니다.
- '격차 큼'도 숨기지 않습니다.
- 도구 결과의 notes(2028 전형 변경, 수집 데이터에 없음 등)는 요약에서도 한 줄로 짚어 줍니다.
- 수집 데이터는 간호·컴퓨터공학 계열 일부 대학뿐입니다. 데이터에 없으면 '확인할 수 없다'고 말하고 어디가(adiga.kr)·대학 입학처를 안내합니다.
- 도구를 쓰지 않은 답변(되묻기, 데이터 없음 안내 등)은 표 없이 짧게 답합니다.
- '무조건 합격', '확실히 붙는다' 같은 보장 표현을 쓰지 않습니다. 4년제와 전문대를 우열로 표현하지 않습니다.
- 도구 결과에 없는 목표 등급·필요 평균·합격 확률 같은 숫자를 새로 만들지 않습니다. 얼마나 올려야 하는지는 '보강 과목(What-if)' 화면에서 계산해 볼 수 있다고 안내합니다.
- 도구 결과나 이 지침에 없는 입시 제도 설명(전형 요소, 서류 등)을 일반 지식으로 덧붙이지 않습니다.
- 앞 대화에서 받은 성적·희망 학과는 다시 묻지 않고 그대로 씁니다.
- 학부모가 읽기 쉽게 한국어로, 표나 짧은 목록을 활용해 간결하게 답합니다. 이모지는 쓰지 않습니다."""


def get_text(message) -> str:
    """ChatBedrockConverse는 content를 블록 리스트로 주기도 하므로 텍스트만 모아 반환한다 (교안과 같은 도우미)."""
    content = message.content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return content


def build_llm(model_id: str) -> ChatBedrockConverse:
    """Bedrock 채팅 모델을 만든다 (temperature 0, us-east-1)."""
    return ChatBedrockConverse(model=model_id, region_name=REGION, temperature=0, max_tokens=4096)


def open_checkpointer(path: Path = CHECKPOINT_DB) -> SqliteSaver:
    """대화 기록을 저장할 SQLite 체크포인터. FastAPI 여러 스레드에서 쓰도록 check_same_thread=False."""
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def build_agent(checkpointer=None):
    """입시나침반 에이전트를 조립한다.

    미들웨어 순서: 개인정보 마스킹(before_model, 기록에도 원문을 남기지 않음) → 입력 규칙 차단 → 모델 대체.
    차단되면 모델(대체 모델 포함)을 아예 부르지 않는다.
    """
    return create_agent(
        model=build_llm(PRIMARY_MODEL),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=[PiiMaskingMiddleware(), InputRuleMiddleware(),
                    ModelFallbackMiddleware(*[build_llm(m) for m in FALLBACK_MODELS]),
                    HumanInTheLoopMiddleware(interrupt_on={t: {"allowed_decisions": ["approve", "reject"]} for t in APPROVAL_TOOLS})],
    )


def ask(agent, question: str, thread_id: str) -> dict:
    """질문 하나를 보내고 최종 답변과 이번 턴의 메시지들을 돌려준다.

    개인정보는 그래프에 넣기 전에 먼저 가린다. 체크포인터는 첫 입력을 기록 이력에 그대로 저장하므로,
    미들웨어(PiiMaskingMiddleware)만으로는 이력에 원문이 남는다. 미들웨어는 다른 경로로 들어온 입력에 대한 2차 방어다.
    """
    config = {"configurable": {"thread_id": thread_id}}
    before = len(agent.get_state(config).values.get("messages", [])) if agent.checkpointer else 0
    masked, _ = mask_pii(question)
    result = agent.invoke({"messages": [HumanMessage(masked)]}, config=config)
    return _turn(result, before)


def _turn(result: dict, before: int) -> dict:
    """invoke 결과에서 이번 턴 메시지·답변과, 승인 대기(interrupt)가 있으면 그 내용을 꺼낸다."""
    messages = result["messages"][before:]
    pending = None
    for it in result.get("__interrupt__") or []:
        reqs = (it.value or {}).get("action_requests", []) if isinstance(it.value, dict) else []
        pending = [{"tool": r.get("name"), "args": r.get("args"), "description": r.get("description")} for r in reqs]
    answer = get_text(result["messages"][-1]) if not pending else "저장하기 전에 확인이 필요합니다. 아래 내용을 저장할지 승인해 주세요."
    return {"answer": answer, "messages": messages, "pending_approval": pending}


def resume(agent, thread_id: str, approve: bool) -> dict:
    """승인 대기 중인 도구 호출을 승인/거절하고 이어서 답을 받는다 (HumanInTheLoopMiddleware 재개)."""
    config = {"configurable": {"thread_id": thread_id}}
    before = len(agent.get_state(config).values.get("messages", []))
    decision = {"type": "approve"} if approve else {"type": "reject", "message": "사용자가 저장을 거절했습니다. 저장하지 않았다고 알려 주세요."}
    result = agent.invoke(Command(resume={"decisions": [decision]}), config=config)
    return _turn(result, before)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print('사용법: python -m src.agent "질문" [thread_id]')
        sys.exit(1)
    agent = build_agent(open_checkpointer())
    out = ask(agent, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "cli")
    for m in out["messages"]:
        for call in getattr(m, "tool_calls", None) or []:
            print(f"[도구 호출] {call['name']}({call['args']})")
    print("\n" + out["answer"])
