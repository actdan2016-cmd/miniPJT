"""api.py - FastAPI 진입점

실행: uvicorn src.api:app --reload  →  화면 http://localhost:8000 · API 문서 http://localhost:8000/docs
- 계산·조회 화면용 /api/* 는 도구를 직접 호출한다 (LLM 없음)
- 상담 챗 POST /query 는 에이전트를 쓴다 (A9에서 추가)
- web/ 의 화면 파일을 같은 서버에서 띄운다
"""
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import data_store as ds
from src.tools import GpaInput, calc_school_gpa, classify_chances

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="입시나침반 API", description="5등급제 내신 기반 지원 가능권 예측 · 계산은 코드, LLM은 설명만")


class PredictRequest(BaseModel):
    """지원 가능 대학 판정 요청"""
    avg5: float = Field(ge=1, le=5, description="5등급제 평균 (1~5)")
    keyword: str = Field(min_length=1, description="희망 학과 키워드. 예: 간호, 컴퓨터")
    univ_type: Literal["전체", "4년제", "전문대"] = "전체"
    year: int = Field(default=2026, description="비교할 입결 학년도")


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


# 화면 파일은 API 경로 뒤에 붙인다. web/ 이 아직 없으면 안내만 돌려준다
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/")
    def no_web() -> dict:
        """web/ 이 없을 때의 안내"""
        return {"message": "화면(web/)이 아직 없습니다. API 문서는 /docs 에서 볼 수 있습니다."}
