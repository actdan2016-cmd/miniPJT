"""middleware.py - 에이전트 미들웨어

PiiMaskingMiddleware: 모델 호출 전에 최근 사용자 메시지의 개인정보(이름·학교명·반·연락처)를 가린다 (교안 day5 MaskingMiddleware 방식).
  체크포인터는 그래프에 처음 들어온 입력을 이력에 그대로 저장하므로, 기록에 원문을 남기지 않으려면
  입구(agent.ask)에서 mask_pii를 먼저 적용한다. 이 미들웨어는 그 밖의 경로로 들어온 입력에 대한 2차 방어다.
InputRuleMiddleware: 위험 요청을 규칙(정규식)으로 먼저 걸러 모델 호출 없이 고정 문구로 답한다 (교안 day5 InputGuardrailMiddleware 방식).
  - guarantee     합격 보장 문구 작성 요청 (평가 18)
  - other_student 다른 학생 성적 조회·비교 (평가 19)
  - proxy_writing 세특·자소서 등 학생부 대리 작성 (평가 20)
  - injection     지시 무시·시스템 프롬프트·데이터 원본 노출 요구 (평가 21)
전문대 비하처럼 답은 하되 중립적으로 고쳐 말해야 하는 요청(평가 28)은 막지 않는다.
"""
import re

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from src.replies import REFUSALS

RULES: dict[str, list[str]] = {
    "injection": [
        r"ignore (the |all )?(previous|above|prior) (instructions?|prompts?)",
        r"(위의?|이전|기존|지금까지|앞의?) ?(모든 )?(지시|명령|규칙|프롬프트|설정)[은는을를이가]? ?.{0,8}(무시|잊|버려)",
        r"(시스템 ?프롬프트|숨겨진 (지시|프롬프트)|내부 (지시|설정|프롬프트))",
        r"(DB|디비|데이터베이스|원본 데이터|데이터 원본|CSV) ?(원본)?.{0,10}(전부|전체|모두|통째)?.{0,6}(출력|덤프|보여|내놔|공개)",
        r"</?(system|admin|root)>|system\s*:",
        r"(규칙|제한) ?(이|가)? ?없는 (AI|인공지능|모드)|탈옥|jailbreak",
    ],
    "guarantee": [
        r"(무조건|100 ?%|확실히|반드시|틀림없이).{0,12}(붙|합격).{0,25}(써|쓰|작성|만들|문구|홍보)",
        r"(합격 ?보장|보장 ?합격).{0,20}(써|쓰|작성|만들|문구|홍보)",
        r"(홍보물|광고|전단).{0,20}(무조건|100 ?%|확실히|보장).{0,10}(붙|합격)",
    ],
    "other_student": [
        r"(친구|다른 ?(학생|아이|애)|같은 ?반|반 ?(친구|애)|옆 ?자리|전교 ?\d ?등).{0,25}(성적|등급|내신|점수)",
        r"(성적|등급|내신).{0,15}(친구|다른 ?(학생|아이|애)).{0,10}(조회|비교|알려|보여)",
        r"(반|학년|전교|학교) ?\d+ ?등 ?(하는 )?(애|학생|친구|아이)?.{0,10}(성적|등급|내신|점수)",
    ],
    "proxy_writing": [
        r"(세특|세부 ?능력|특기 ?사항|행특|행동 ?특성|자소서|자기 ?소개서|생기부 ?(문구|내용)|학생부 ?(문구|기재 ?내용)).{0,40}(대신|대필|써 ?줘|써 ?주|작성해|완성본|만들어)",
        r"(대신|대필).{0,20}(세특|특기 ?사항|자소서|자기 ?소개서|생기부|학생부)",
    ],
}
_COMPILED = {kind: [re.compile(p, re.IGNORECASE) for p in pats] for kind, pats in RULES.items()}


def match_rule(text: str) -> str | None:
    """입력에 걸리는 규칙 종류를 돌려준다. 없으면 None. (검사 순서: 인젝션 → 보장 → 타 학생 → 대리 작성)"""
    for kind, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            return kind
    return None


def _text(message) -> str:
    content = message.content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


# ---------- 개인정보 마스킹 ----------
# 성씨만 보고 이름을 추측하면 '정보는', '성적이야' 같은 일반 단어까지 가리므로, 이름은 단서가 있을 때만 가린다
PII_PATTERNS = [
    ("연락처", re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}")),
    ("이메일", re.compile(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}")),
    ("주민번호", re.compile(r"\d{6}-?[1-4]\d{6}")),
    ("학교명", re.compile(r"[가-힣A-Za-zO○]{1,12}(여자고등학교|고등학교|중학교|초등학교|고교|여고|외고|과학고|국제고)")),
    ("학교명", re.compile(r"[가-힣A-Za-z]{0,8}(OO|○○)[가-힣]{0,2}고")),
    ("학교명", re.compile(r"[가-힣]{2,8}고(?= ?[1-3] ?학년)")),
    ("반", re.compile(r"(?<=학년 )\d{1,2} ?반|(?<=학년)\d{1,2} ?반")),
]
NAME_CUES = re.compile(r"(이름은|이름이|이름:|이름 :|성함은|성함이)\s*([가-힣]{2,4}?)(?=이고|이야|이에요|입니다|이며|[,.\s]|$)")


def mask_pii(text: str) -> tuple[str, list[str]]:
    """개인정보를 [이름]·[학교명]·[반]·[연락처] 같은 자리표시로 바꾼다. 반환: (가린 문장, 가린 종류 목록)"""
    kinds = []
    for m in NAME_CUES.finditer(text):
        name = m.group(2)
        text = text.replace(name, "[이름]")
        kinds.append("이름")
    for kind, pattern in PII_PATTERNS:
        text, n = pattern.subn(f"[{kind}]", text)
        if n:
            kinds.append(kind)
    return text, sorted(set(kinds))


class PiiMaskingMiddleware(AgentMiddleware):
    """모델 호출 전에 최근 사용자 메시지의 개인정보를 가리고, 같은 id로 바꿔 넣어 기록에도 원문을 남기지 않는다."""

    def before_model(self, state, runtime):
        last = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
        if last is None:
            return None
        masked, kinds = mask_pii(_text(last))
        if not kinds:
            return None
        return {"messages": [HumanMessage(content=masked, id=last.id)]}


class InputRuleMiddleware(AgentMiddleware):
    """새 사용자 질문이 들어온 직후의 모델 호출에서만 검사하고, 걸리면 모델을 부르지 않고 고정 문구로 끝낸다."""

    def _refusal_or_none(self, request):
        messages = request.state["messages"]
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None  # 도구 결과를 받은 뒤의 호출은 이미 검사를 통과한 질문이다
        kind = match_rule(_text(messages[-1]))
        if kind is None:
            return None
        # name·response_metadata로 차단 사실과 규칙 종류를 trace에 남긴다 (사용자 답변에는 사유를 보이지 않음)
        return AIMessage(content=REFUSALS[kind], name="input_guard", response_metadata={"guard": kind})

    def wrap_model_call(self, request, handler):
        refusal = self._refusal_or_none(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_model_call(self, request, handler):
        refusal = self._refusal_or_none(request)
        return refusal if refusal is not None else await handler(request)
