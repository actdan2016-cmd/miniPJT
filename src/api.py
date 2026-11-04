"""api.py - FastAPI 진입점

실행: uvicorn src.api:app --reload  →  화면 http://localhost:8000 · API 문서 http://localhost:8000/docs
- 계산·조회 화면용 /api/* 는 도구를 직접 호출한다 (LLM 없음)
- 상담 챗 POST /query 는 에이전트를 쓴다 → {answer, contexts, trace} (과제 표준 규약) + thread_id
- web/ 의 화면 파일을 같은 서버에서 띄운다
"""
import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from src import data_store as ds
from src.agent import ask, build_agent, open_checkpointer
from src.tools import GpaInput, calc_school_gpa, classify_chances

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="입시나침반 API", description="5등급제 내신 기반 지원 가능권 예측 · 계산은 코드, LLM은 설명만")


class PredictRequest(BaseModel):
    """지원 가능 대학 판정 요청"""
    avg5: float = Field(ge=1, le=5, description="5등급제 평균 (1~5)")
    keyword: str = Field(min_length=1, description="희망 학과 키워드. 예: 간호, 컴퓨터")
    univ_type: Literal["전체", "4년제", "전문대"] = "전체"
    year: int = Field(default=2026, description="비교할 입결 학년도")


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
    rows = []
    if name == "classify_chances":
        conv = result["conversion"]
        rows = [(x["source_doc"], f"{x['univ_name']} {x['unit_name']} {x['track_name']} {x['year']}학년도 "
                 + (f"{x['basis']} {x['cut']} → {x['label']}({x['gap']:+.2f})" if x["judged"]
                    else f"{x['label']}" + (f" {x['basis']} {x['cut']}" if x["cut"] is not None else "")))
                for x in result["metro"] + result["non_metro_reference"]]
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
    """이번 턴 메시지에서 근거(contexts)와 단계 기록(trace)을 만든다."""
    contexts, trace = [], [{"step": "question", "input": question, "output": ""}]
    calls = {}
    for m in messages:
        if isinstance(m, AIMessage):
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
    """지원 가능 대학 판정 보드 (수도권 / 수도권 외 참고) → classify_chances"""
    return _call(classify_chances, req.model_dump())


@app.post("/query")
def query(req: QueryRequest) -> dict:
    """상담 챗 (과제 표준 규약) → {"answer", "contexts": [{"doc_id","text"}], "trace": [{"step","input","output"}]}"""
    thread_id = req.thread_id or uuid.uuid4().hex
    out = ask(_agent(), req.question, thread_id)
    contexts, trace = _contexts_and_trace(req.question, out["messages"], out["answer"])
    return {"answer": out["answer"], "contexts": contexts, "trace": trace, "thread_id": thread_id}


# 화면 파일은 API 경로 뒤에 붙인다. web/ 이 아직 없으면 안내만 돌려준다
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/")
    def no_web() -> dict:
        """web/ 이 없을 때의 안내"""
        return {"message": "화면(web/)이 아직 없습니다. API 문서는 /docs 에서 볼 수 있습니다."}
