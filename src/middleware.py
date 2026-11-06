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
  같은 미들웨어에서 학과를 밝힌 과목 선택·로드맵 질문은 첫 모델 호출의 tool_choice를 get_recommended_courses로 고정한다
  (Round 5 평가 5·6: 프롬프트 지시만으로는 도구 없이 성적부터 되물음).
"""
import re

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from src import grades
from src.replies import FUTURE_GRADE, REFUSALS, clarify_scale

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


GUIDE_KINDS = {"future_grade", "clarify_scale"}
FUTURE_GRADE_RE = re.compile(r"(등급|성적).{0,10}(몇|어떻게).{0,6}(나올지|나올까|받을지)|(등급|성적).{0,15}예측해")
AVG_RE = re.compile(r"(\d(?:\.\d+)?)\s*등급")
WHERE_RE = re.compile(r"어디|어느 대학|갈 수|지원할|지원 가능")
SCALE_RE = re.compile(r"[59]\s*등급\s*제|[59]등급(제|으로|기준)|(5|9)\s*등급 ?체계")


def match_guide(text: str, history: str) -> tuple[str, str] | None:
    """거절이 아니라 되묻기·대안 안내로 끝낼 질문이면 (종류, 문구)를 돌려준다.

    - future_grade: 앞으로 받을 개별 과목 등급 예측 요청 (평가 25)
    - clarify_scale: '내신 3.5등급인데 어디 가?'처럼 등급 체계 없이 평균만 준 지원 가능 질문 (평가 16).
      앞 대화에서 5·9등급제를 이미 밝혔으면 묻지 않는다.
    """
    if FUTURE_GRADE_RE.search(text):
        return "future_grade", FUTURE_GRADE
    found = AVG_RE.findall(text)
    m = AVG_RE.search(text) if len(found) == 1 else None  # 과목별 등급을 여러 개 준 질문(평가 13)은 모델이 누락 과목을 되묻는다
    if m and WHERE_RE.search(text) and not SCALE_RE.search(history):
        avg = float(m.group(1))
        g9 = grades.convert_5to9(avg)["grade9"] if 1 <= avg <= 5 else None
        return "clarify_scale", clarify_scale(avg, g9)
    return None


COURSES_RE = re.compile(r"로드맵|권장 ?과목|과목 ?선택|선택 ?과목|(어떤|무슨|어느) ?과목.{0,10}(선택|골라|들어야|들을|이수)")
DEPT_RE = re.compile(r"[가-힣]{1,10}(학과|공학|학부)|간호|컴퓨터|소프트웨어|인공지능|\bAI\b")


def match_forced_tool(text: str, history: str) -> str | None:
    """첫 호출에서 반드시 불러야 할 도구. 학과를 밝힌(이번 질문이나 앞 대화) 과목 선택·로드맵 질문이면 get_recommended_courses.

    권장과목은 성적 없이도 학과만으로 조회되므로 되묻기 전에 먼저 보여 준다. 수능 선택과목 질문(평가 12)은 제외한다.
    """
    if COURSES_RE.search(text) and "수능" not in text and DEPT_RE.search(history):
        return "get_recommended_courses"
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
        text = _text(messages[-1])
        kind = match_rule(text)
        if kind is not None:
            content = REFUSALS[kind]
        else:
            history = "\n".join(_text(m) for m in messages if isinstance(m, HumanMessage))
            guide = match_guide(text, history)
            if guide is None:
                return None
            kind, content = guide
        # name·response_metadata로 차단 사실과 규칙 종류를 trace에 남긴다 (사용자 답변에는 사유를 보이지 않음)
        return AIMessage(content=content, name="input_guard", response_metadata={"guard": kind})

    def _forced(self, request):
        """새 질문 직후의 호출이면 강제할 도구를 tool_choice에 넣는다. 도구 결과를 받은 뒤의 호출은 모델에 맡긴다."""
        messages = request.state["messages"]
        if not messages or not isinstance(messages[-1], HumanMessage):
            return request
        history = "\n".join(_text(m) for m in messages if isinstance(m, HumanMessage))
        tool = match_forced_tool(_text(messages[-1]), history)
        return request.override(tool_choice=tool) if tool else request

    def wrap_model_call(self, request, handler):
        refusal = self._refusal_or_none(request)
        return refusal if refusal is not None else handler(self._forced(request))

    async def awrap_model_call(self, request, handler):
        refusal = self._refusal_or_none(request)
        return refusal if refusal is not None else await handler(self._forced(request))
