"""api.py - FastAPI 진입점

실행: uvicorn src.api:app --reload  →  화면 http://localhost:8000 · API 문서 http://localhost:8000/docs
- 계산·조회 화면용 /api/* 는 도구를 직접 호출한다 (LLM 없음)
- 상담 챗 POST /query 는 에이전트를 쓴다 → {answer, contexts, trace} (과제 표준 규약) + thread_id
- web/ 의 화면 파일을 같은 서버에서 띄운다
"""
import json
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from src import data_store as ds
from src.agent import ask, build_agent, open_checkpointer, resume
from src.guardrails import finalize
from src.middleware import mask_pii
from src.tools import (GpaInput, SimulateInput, SubjectGrade, save_student_profile, calc_school_gpa, classify_chances, get_recommended_courses,
                       simulate_improvement)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="입시나침반 API", description="5등급제 내신 기반 지원 가능권 예측 · 계산은 코드, LLM은 설명만")


class PredictRequest(BaseModel):
    """지원 가능 대학 판정 요청"""
    avg5: float = Field(ge=1, le=5, description="5등급제 평균 (1~5)")
    keyword: str = Field(min_length=1, description="희망 학과 키워드. 예: 간호, 컴퓨터")
    univ_type: Literal["전체", "4년제", "전문대"] = "전체"
    year: int = Field(default=2026, description="비교할 입결 학년도")


class ResumeRequest(BaseModel):
    """승인 대기 중인 도구 호출(프로필 저장)에 대한 사용자 결정"""
    thread_id: str
    approve: bool


class ProfileRequest(BaseModel):
    """화면 1 '프로필 저장' (확인 창에서 사용자가 승인한 뒤 호출)"""
    alias: str = Field(min_length=1, max_length=20, description="학생 이름 대신 쓸 별칭")
    major: str = ""
    field: str = ""
    subjects: list[SubjectGrade] = Field(default_factory=list)


class QueryRequest(BaseModel):
    """상담 질문 (과제 규약: {"question": ...}). thread_id를 같이 보내면 앞 대화를 이어 간다."""
    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, description="대화 id. 비우면 새로 만든다")


@lru_cache(maxsize=1)
def _agent():
    """에이전트는 처음 질문이 올 때 한 번만 만든다 (서버 시작을 느리게 하지 않도록)."""
    return build_agent(open_checkpointer())


def _load(content) -> dict | None:
    """도구 결과(ToolMessage.content, JSON 문자열)를 dict로 읽는다."""
    try:
        return json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None


def _tool_contexts(name: str, result: dict) -> list[dict]:
    """도구 결과를 화면 '근거 보기'와 RAGAS contexts에 쓸 문장으로 바꾼다 (출처 문서별로 묶음)."""
    if name == "calc_school_gpa":
        sems = " / ".join(f"{s} 전과목 {v['전과목']['avg']} ({v['전과목']['sum']}÷{v['전과목']['credits']})"
                          for s, v in result["semesters"].items())
        cum = result["cumulative"]["전과목"]
        g9 = cum["grade9"]["grade9"] if cum.get("grade9") else None
        return [{"doc_id": "계산: calc_school_gpa (grades.py 학점 가중 평균)",
                 "text": f"{sems} / 누적 전과목 {cum['avg']} ({cum['formula']}) → 9등급 환산 {g9}"}]
    if name == "retrieve_admission_docs":
        # 검색한 문서 청크를 그대로 근거로 쓴다 (RAGAS contexts)
        if not result.get("found"):
            return [{"doc_id": "검색: retrieve_admission_docs", "text": result.get("message", "")}] if result.get("message") else []
        return [{"doc_id": f"{r['source']} p.{r['page']}", "text": r["text"]} for r in result["results"]]
    if name == "convert_grade_5to9":
        return [{"doc_id": "계산: convert_grade_5to9 (grades.py 5→9 환산)",
                 "text": f"5등급 {result['avg5']} → 누적 백분위 {result['percentile']}% → 9등급 {result['grade9']} ({result['method']})"}]
    if name == "search_programs":
        return [{"doc_id": "조회: search_programs (programs.csv)",
                 "text": "\n".join(f"{u['univ_name']}({u['univ_type']}, {u['region']}): {', '.join(u['units'])}" for u in result["universities"])
                         or "; ".join(result["notes"])}]
    if name == "save_student_profile":
        return [{"doc_id": "저장: save_student_profile", "text": result.get("message", "")}]
    if name == "simulate_improvement" and not result.get("error"):
        return [{"doc_id": "계산: simulate_improvement (grades.py 역환산·필요 평균)",
                 "text": "\n".join(f"{t['univ_name']} {t['track_name']} 기준선 {t['cut9']}(9등급) → 5등급 {t['target_avg5']} → "
                                   f"남은 학기 필요 평균 {t['required_future_avg']}" for t in result["targets"])
                         + "\n교과군: " + ", ".join(f"{g['group']} 비중 {g['share']} 평균 {g['avg']}" for g in result["group_effects"])}]
    if name == "get_recommended_courses":
        return [{"doc_id": f"{x['source_doc']} p.{x['source_page']}",
                 "text": f"{x['univ_name']} {x['field_or_dept']}: 핵심 {', '.join(x['core_courses']) or '없음'} / "
                         f"권장 {', '.join(x['recommended_courses']) or '없음'} ({x['note']})"} for x in result["results"]]
    rows = []
    if name == "classify_chances":
        conv = result["conversion"]
        rows = [(x["source_doc"], f"{x['univ_name']} {x['unit_name']} {x['track_name']} {x['year']}학년도 "
                 + (f"{x['basis']} {x['cut']} → {x['label']}({x['gap']:+.2f})" if x["judged"]
                    else f"{x['label']}" + (f" {x['basis']} {x['cut']}" if x["cut"] is not None else "")))
                for x in result["results"]]
        head = [{"doc_id": "계산: classify_chances (grades.py 5→9 환산·판정 규칙 v1)",
                 "text": f"5등급 {conv['avg5']} → 누적 백분위 {conv['percentile']}% → 9등급 {conv['grade9']}"}]
    elif name == "lookup_admission_results":
        if not result.get("found"):
            return [{"doc_id": "조회: lookup_admission_results", "text": result.get("message", "")}]
        rows = [(r["source_doc"], f"{result['univ_name']} {r['unit_name']} {r['track_name']} {r['year']}학년도 "
                 f"모집 {r['quota']} 경쟁률 {r['competition_rate']} 충원 {r['added_pass']} "
                 f"50% {r['cut_50']} 70% {r['cut_70']} 평균 {r['cut_avg']} 최저 {r['cut_min']}")
                for r in result["results"]]
        head = []
    else:
        return [{"doc_id": f"도구: {name}", "text": json.dumps(result, ensure_ascii=False)[:2000]}]
    by_doc: dict[str, list[str]] = {}
    for doc, line in rows:
        by_doc.setdefault(doc, []).append(line)
    return head + [{"doc_id": doc, "text": "\n".join(lines)} for doc, lines in by_doc.items()]


def _contexts_and_trace(question: str, messages: list, answer: str) -> tuple[list[dict], list[dict]]:
    """이번 턴 메시지에서 근거(contexts)와 단계 기록(trace)을 만든다. trace에는 개인정보를 가린 질문만 남긴다."""
    masked_q, pii_kinds = mask_pii(question)
    contexts, trace = [], [{"step": "question", "input": masked_q, "output": ""}]
    if pii_kinds:
        trace.append({"step": "pii_mask", "input": pii_kinds, "output": "개인정보를 가린 뒤 모델에 전달"})
    calls = {}
    for m in messages:
        if isinstance(m, AIMessage):
            if m.name != "input_guard":
                usage = m.usage_metadata or {}
                decision = [c["name"] for c in m.tool_calls] if m.tool_calls else "최종 답변"
                trace.append({"step": "model_call", "input": {"model": (m.response_metadata or {}).get("model_name"),
                                                              "tokens": usage.get("total_tokens")},
                              "output": decision})
            for c in m.tool_calls or []:
                calls[c["id"]] = c
                trace.append({"step": f"tool_call:{c['name']}", "input": c["args"], "output": ""})
        elif isinstance(m, ToolMessage):
            name = calls.get(m.tool_call_id, {}).get("name", m.name or "tool")
            result = _load(m.content)
            if result is not None:
                contexts += _tool_contexts(name, result)
            trace.append({"step": f"tool_result:{name}", "input": calls.get(m.tool_call_id, {}).get("args", {}),
                          "output": str(m.content)[:500]})
    final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    meta = (final.response_metadata or {}) if final else {}
    if final is not None and final.name == "input_guard":
        trace.append({"step": "input_guard", "input": {"rule": meta.get("guard")}, "output": "차단: 모델 호출 없이 고정 문구로 응답"})
    trace.append({"step": "answer", "input": {"model": meta.get("model_name")}, "output": answer[:500]})
    return contexts, trace


def _tool_results(messages: list) -> list[tuple[str, dict]]:
    """이번 턴의 (도구 이름, 결과 dict) 목록"""
    names = {c["id"]: c["name"] for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            result = _load(m.content)
            if result is not None:
                out.append((names.get(m.tool_call_id, m.name or ""), result))
    return out


def _call(tool, args: dict) -> dict:
    """도구를 호출하고, 입력 오류는 400으로 돌려준다."""
    try:
        return tool.invoke(args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/health")
def health() -> dict:
    """서버 상태 확인"""
    return {"status": "ok"}


@app.get("/api/sample")
def sample_student() -> dict:
    """화면 '예시 학생으로 체험'용 가짜 페르소나 성적 (data/sample_student_grades.csv)"""
    rows = ds.load("sample_student_grades")
    return {"subjects": [{"semester": r["semester"], "subject": r["subject"], "group": r["subject_group"],
                          "credits": int(r["credits"]), "grade": int(r["grade_5"]) if r["grade_5"] else None,
                          "achievement": r["achievement"]} for r in rows]}


@app.post("/api/grades/analyze")
def analyze_grades(req: GpaInput) -> dict:
    """학기별·누적 평균(전과목·국영수사·국영수과), 추이, 9등급 환산 → calc_school_gpa"""
    return _call(calc_school_gpa, req.model_dump())


@app.post("/api/predict")
def predict(req: PredictRequest) -> dict:
    """지원 가능 대학 판정 보드 (수도권·비수도권 함께, 지역 표시) → classify_chances"""
    return _call(classify_chances, req.model_dump())


@app.post("/api/simulate")
def simulate(req: SimulateInput) -> dict:
    """보강 과목 What-if: 목표별 남은 학기 필요 평균, 교과군 효과, 예상 평균 가정 → simulate_improvement"""
    return _call(simulate_improvement, req.model_dump())


@app.get("/api/courses")
def courses(keyword: str, univ_name: str | None = None) -> dict:
    """학과 키워드별 대학 권장·핵심 과목 → get_recommended_courses"""
    return _call(get_recommended_courses, {"keyword": keyword, "univ_name": univ_name})


def _respond(question: str, thread_id: str, out: dict, start: float) -> dict:
    """에이전트 결과 → {answer, contexts, trace, thread_id, pending_approval}"""
    messages = out["messages"]
    blocked = any(isinstance(m, AIMessage) and m.name == "input_guard" for m in messages)
    guard = None
    answer = out["answer"]
    if not blocked and not out.get("pending_approval"):
        # LLM 요약 + 코드 결과표 + 고지. 요약에 위반이 있으면 결과표만 보여 준다 (guardrails.finalize)
        allowed = mask_pii(question)[0] + "\n" + "\n".join(str(m.content) for m in messages if isinstance(m, ToolMessage))
        answer, guard = finalize(out["answer"], _tool_results(messages), allowed)
    contexts, trace = _contexts_and_trace(question, messages, answer)
    if guard is not None:
        trace.insert(len(trace) - 1, {"step": "output_guard", "input": {"tables": guard["tables"]},
                                      "output": "위반 없음" if not guard["violations"]
                                      else f"위반 {guard['violations']} → 요약을 빼고 결과표만 표시"})
    if out.get("pending_approval"):
        trace.append({"step": "human_approval", "input": out["pending_approval"], "output": "사용자 승인 대기"})
    trace.append({"step": "total", "input": {"thread_id": thread_id}, "output": f"{time.time() - start:.1f}초"})
    return {"answer": answer, "contexts": contexts, "trace": trace, "thread_id": thread_id,
            "pending_approval": out.get("pending_approval")}


@app.post("/query")
def query(req: QueryRequest) -> dict:
    """상담 챗 (과제 표준 규약) → {"answer", "contexts": [{"doc_id","text"}], "trace": [{"step","input","output"}]}
    + thread_id, pending_approval(프로필 저장 승인 대기일 때)"""
    thread_id = req.thread_id or uuid.uuid4().hex
    start = time.time()
    return _respond(req.question, thread_id, ask(_agent(), req.question, thread_id), start)


@app.post("/query/resume")
def query_resume(req: ResumeRequest) -> dict:
    """승인 대기 중인 프로필 저장을 승인/거절하고 이어서 답을 받는다 (HITL)"""
    start = time.time()
    return _respond("(승인 결정) " + ("승인" if req.approve else "거절"), req.thread_id, resume(_agent(), req.thread_id, req.approve), start)


@app.post("/api/profile")
def save_profile(req: ProfileRequest) -> dict:
    """화면 1 프로필 저장: 사용자가 확인 창에서 승인한 뒤에만 호출된다 → save_student_profile"""
    return _call(save_student_profile, {"alias": req.alias, "major": req.major, "field": req.field or None,
                                        "subjects": [s.model_dump() for s in req.subjects]})


@app.get("/api/profile/{alias}")
def get_profile(alias: str) -> dict:
    """별칭으로 저장한 프로필 불러오기"""
    p = ds.load_profile(alias)
    if p is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    return {"alias": alias, **p}


# 화면 파일은 API 경로 뒤에 붙인다. web/ 이 아직 없으면 안내만 돌려준다
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/")
    def no_web() -> dict:
        """web/ 이 없을 때의 안내"""
        return {"message": "화면(web/)이 아직 없습니다. API 문서는 /docs 에서 볼 수 있습니다."}
