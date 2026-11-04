"""agent.py - 입시나침반 상담 에이전트 조립

create_agent(ReAct) + 1차 도구 3개 + SqliteSaver(thread_id로 대화 유지) + 모델 대체(ModelFallbackMiddleware).
숫자는 도구 결과만 쓰고 LLM은 설명만 한다 (CLAUDE.md 설계 원칙).

터미널 실행: python -m src.agent "질문" [thread_id]
"""
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from src.middleware import InputRuleMiddleware
from src.tools import calc_school_gpa, classify_chances, lookup_admission_results

load_dotenv()

REGION = "us-east-1"
PRIMARY_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"          # 교안과 같은 기본 모델
FALLBACK_MODELS = ["global.anthropic.claude-sonnet-4-5-20250929-v1:0",   # 실패 시 이 순서로 대체
                   "us.anthropic.claude-sonnet-4-6"]
CHECKPOINT_DB = Path(__file__).resolve().parents[1] / "checkpoints.sqlite"
TOOLS = [calc_school_gpa, lookup_admission_results, classify_chances]

SYSTEM_PROMPT = """당신은 '입시나침반' 상담 에이전트입니다. 고1 자녀를 둔 학부모가 5등급제 내신으로 4년제·전문대 지원 가능권을 가늠하도록 돕습니다.
대상 학생은 2029학년도 대입이며, 2029 시행계획(2027년 봄 공개) 전까지는 2028학년도 자료와 2025·2026학년도 입결을 참고치로 씁니다.

[도구 사용]
- 과목별 등급·학점이 주어지면 반드시 calc_school_gpa로 평균을 구합니다. 직접 계산하지 않습니다.
- 지원 가능 대학·판정은 classify_chances(avg5=누적 전과목 평균, keyword=희망 학과)로 구합니다. 희망 학과를 모르면 물어봅니다.
- 특정 대학·학과·전형의 경쟁률·충원·컷은 lookup_admission_results로 조회합니다.
- 숫자(평균·환산 등급·컷·차이·인원·경쟁률)는 도구 결과에 있는 값만 그대로 씁니다. 반올림을 바꾸거나 새로 계산하지 않습니다.
- 성적이나 희망 학과 없이 지원 가능 대학을 물으면, 필요한 정보를 짧게 되묻습니다.

[답변 원칙]
- 판정은 학생부교과·전문대 수시만 안정·적정·소신·상향·격차 큼으로 말합니다. 학생부종합은 '합격자 내신 참고치'로만 말하고 판정하지 않습니다.
- 기본 목록은 수도권이고, 비수도권은 '수도권 외 참고'로 따로 묶습니다. 컷이 없으면 '입결 미공개'라고 합니다.
- '격차 큼'도 숨기지 않고 컷까지의 차이를 보여 줍니다.
- 컷·인원은 대학명·전형명·학년도·출처를 함께 밝힙니다. 2025학년도 결과에 없는 모집인원·경쟁률을 지어내지 않습니다.
- 도구 결과의 notes(2028 전형 변경, 수집 데이터에 없음 등)는 빠뜨리지 않고 전합니다.
- 수집 데이터는 간호·컴퓨터공학 계열 일부 대학뿐입니다. 데이터에 없으면 '확인할 수 없다'고 말하고 어디가(adiga.kr)·대학 입학처를 안내합니다.
- 답변 끝에 한 줄로 고지합니다: 과거 입결 기반 참고치이며 합격을 보장하지 않고, 5→9등급 환산은 근사, 고1 성적 기반 예측은 현재 추세 유지 가정입니다.
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

    미들웨어 순서(바깥 → 안): 입력 규칙 차단 → 모델 대체. 차단되면 모델(대체 모델 포함)을 아예 부르지 않는다.
    """
    return create_agent(
        model=build_llm(PRIMARY_MODEL),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=[InputRuleMiddleware(), ModelFallbackMiddleware(*[build_llm(m) for m in FALLBACK_MODELS])],
    )


def ask(agent, question: str, thread_id: str) -> dict:
    """질문 하나를 보내고 최종 답변과 이번 턴의 메시지들을 돌려준다."""
    config = {"configurable": {"thread_id": thread_id}}
    before = len(agent.get_state(config).values.get("messages", [])) if agent.checkpointer else 0
    result = agent.invoke({"messages": [HumanMessage(question)]}, config=config)
    messages = result["messages"][before:]
    return {"answer": get_text(result["messages"][-1]), "messages": messages}


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
