"""tools.py - 에이전트 도메인 도구 (@tool)

도구는 입력을 검증하고 grades.py(계산·판정)와 data_store.py(조회)를 불러 결과를 dict로 돌려준다.
도구 안에서 숫자를 따로 계산하지 않는다. 화면용 API(/api/*)도 같은 도구를 직접 호출한다.
"""
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import data_store as ds
from src import grades

PREMISE = "과거 입결 기반 참고치이며 합격을 보장하지 않습니다. 5→9등급 환산은 근사치이고, 고1 성적 기반 예측은 현재 추세 유지를 가정합니다."
LABEL_ORDER = ["안정", "적정", "소신", "상향", "격차 큼", "입결 미공개", "종합 참고치"]


# ---------- calc_school_gpa ----------
class SubjectGrade(BaseModel):
    """성적표 한 과목"""
    semester: str = Field(description="학기. 예: '1-1'(1학년 1학기), '1-2'")
    subject: str = Field(description="과목명. 예: '공통국어1'")
    group: str = Field(description="교과군: 국어·수학·영어·사회·과학·한국사·기타 중 하나 (통합사회→사회, 통합과학→과학)")
    credits: int = Field(ge=1, description="학점(단위 수)")
    grade: int | None = Field(default=None, ge=1, le=5, description="석차등급(5등급제 1~5). 성취도만 있는 과목은 비움")
    achievement: str | None = Field(default=None, description="성취도 A~E (선택)")


class GpaInput(BaseModel):
    subjects: list[SubjectGrade] = Field(description="학기별 과목 성적 목록")


@tool("calc_school_gpa", args_schema=GpaInput)
def calc_school_gpa(subjects: list) -> dict:
    """학생이 학기별·과목별 성적(등급·학점)을 알려 주고 평균 등급을 물을 때 쓴다.

    학점 가중 평균을 학기별·누적으로, 전과목·국영수사·국영수과 세 범위로 계산하고 직전 학기 대비 추이와
    누적 평균의 9등급 환산값을 함께 돌려준다. 석차등급이 없는(성취도만 있는) 과목은 평균에서 뺀다.
    """
    rows = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in subjects]
    summary = grades.gpa_summary(rows)
    cumulative = {}
    for scope, v in summary["cumulative"].items():
        g9 = grades.convert_5to9(v["avg"]) if v["avg"] is not None else None
        cumulative[scope] = {**v, "formula": f"{v['sum']} ÷ {v['credits']}학점", "grade9": g9}
    return {
        "semesters": summary["semesters"],
        "cumulative": cumulative,
        "trend": grades.semester_trend(summary),
        "excluded_subjects": [f"{r['semester']} {r['subject']}" for r in rows if r.get("grade") is None],
        "notes": ["석차등급이 없는 과목은 평균에서 제외했습니다.",
                  "국영수사·국영수과에는 한국사를 넣지 않았습니다(대학마다 다름)."],
    }


# ---------- lookup_admission_results ----------
RESULT_FIELDS = ["univ_name", "unit_name", "track_name", "track_type", "year", "cut_metric", "quota",
                 "competition_rate", "added_pass", "cut_50", "cut_70", "cut_avg", "cut_min", "source_doc", "note"]


@tool("lookup_admission_results")
def lookup_admission_results(univ_name: str, keyword: str | None = None, track: str | None = None,
                             year: int | None = None) -> dict:
    """특정 대학·학과·전형의 과거 입시결과(경쟁률·충원·50%/70% 컷)를 물을 때 쓴다.

    univ_name: 대학명('인하대'), keyword: 학과 키워드('간호', '컴퓨터'), track: 전형명 일부('지역균형', '교과'),
    year: 학년도(2025 또는 2026, 비우면 둘 다). 컷은 9등급 환산 기준이고, 수능 행은 평균백분위다.
    """
    univ = ds.find_university(univ_name)
    if univ is None:
        return {"found": False, "message": f"'{univ_name}'은(는) 수집한 데이터(간호·컴퓨터공학 계열 대학)에 없습니다. "
                                           "어디가(adiga.kr)나 대학 입학처에서 확인해 주세요."}
    rows = ds.admission_results(univ["univ_id"], keyword=keyword, track=track, year=year)
    rows = sorted(rows, key=lambda r: (r["unit_name"], r["track_name"], -r["year"]))
    notes = ["4년제 컷은 최종등록자 기준 9등급 환산값입니다. 5등급 값과 직접 비교하지 마세요."]
    if any(r["year"] == 2025 for r in rows):
        notes.append("2025학년도 결과는 공개 자료에 모집인원·경쟁률이 없습니다.")
    if any(r["track_type"] in ("수능", "전문대정시") for r in rows):
        notes.append("수능(정시) 행의 컷은 등급이 아니라 평균 백분위입니다.")
    return {"found": True, "univ_name": univ["univ_name"], "region": univ["region"], "is_metro": univ["is_metro"],
            "results": [{k: r[k] for k in RESULT_FIELDS} for r in rows], "notes": notes}


# ---------- classify_chances ----------
def _tracks_2028(univ_id: str, unit_name: str, keyword: str) -> list[str]:
    """같은 대학·학과의 2028학년도 정량(교과·전문대 수시) 전형명. 2026 입결 전형과 이름이 다르면 답변에서 알린다."""
    plans = [p for p in ds.admission_plans(univ_id, year=2028) if p["track_type"] in ("학생부교과", "전문대수시")]
    same_unit = [p for p in plans if p["unit_name"] in unit_name or unit_name in p["unit_name"]]
    pick = same_unit or [p for p in plans if keyword in p["unit_name"]]
    return sorted({p["track_name"] for p in pick})


def _track_key(name: str) -> str:
    """전형명 비교용 정규화: 공통 단어·괄호·공백을 뺀다. 예: '학생부교과(일반학생 전형)' → '일반학생'."""
    for w in ("학생부교과", "정원내", "특별", "전형", "(", ")", " "):
        name = name.replace(w, "")
    return name


def _renamed_in_2028(track_name: str, tracks_2028: list[str]) -> bool:
    """2026 입결 전형명이 2028 전형 목록에서 같은 이름으로 보이지 않으면 True (예: 한서대 교과전형1·2 → 통합)."""
    if not tracks_2028:
        return False
    k = _track_key(track_name)
    keys = [_track_key(t) for t in tracks_2028]
    return not any(k and t and (k in t or t in k) for t in keys)


def _judge_row(r: dict, g9: float, keyword: str) -> dict:
    """입결 한 행을 판정한다: 교과·전문대 수시는 4단계+격차 큼, 종합은 참고치만, 컷이 없으면 미공개."""
    item = {k: r[k] for k in ("univ_name", "univ_type", "region", "unit_name", "track_name", "track_type",
                              "year", "cut_min", "source_doc")}
    if r["track_type"] == "학생부종합":
        ref = r["cut_70"] if r["cut_70"] is not None else r["cut_50"]
        item.update(label="종합 참고치", judged=False, cut=ref,
                    basis=("70% 컷" if r["cut_70"] is not None else "50% 컷") if ref is not None else None, gap=None,
                    reason="학생부종합은 정성평가라 등급으로 판정하지 않고 합격자 내신만 참고로 보여 줍니다.")
        return item
    base = grades.baseline_cut(r["track_type"], r["cut_70"], r["cut_50"], r["cut_avg"])
    if base["cut"] is None:
        item.update(label="입결 미공개", judged=False, cut=None, basis=None, gap=None,
                    reason=r["note"] or "공개된 컷이 없어 판정하지 않습니다.")
    else:
        c = grades.classify(g9, base["cut"])
        item.update(label=c["label"], judged=True, cut=base["cut"], basis=base["basis"], gap=c["gap"],
                    reason=f"환산 {g9} − 기준선 {base['cut']}({base['basis']}) = {c['gap']:+.2f}")
    item["tracks_2028"] = _tracks_2028(r["univ_id"], r["unit_name"], keyword)
    item["renamed_in_2028"] = _renamed_in_2028(r["track_name"], item["tracks_2028"])
    return item


@tool("classify_chances")
def classify_chances(avg5: float, keyword: str, univ_type: Literal["전체", "4년제", "전문대"] = "전체",
                     year: int = 2026) -> dict:
    """5등급 내신 평균으로 희망 학과(키워드)를 둔 대학들의 지원 가능성(안정·적정·소신·상향·격차 큼)을 볼 때 쓴다.

    avg5: 5등급제 평균(1~5), keyword: 학과 키워드('간호', '컴퓨터'), univ_type: 4년제·전문대 거르기,
    year: 비교할 입결 학년도(기본 2026). 9등급 환산 후 학생부교과·전문대 수시 컷과 비교한다.
    학생부종합은 판정 없이 참고치, 수능(정시)은 단위가 달라 제외한다. 수도권과 수도권 외 참고를 나눠 돌려준다.
    """
    conv = grades.convert_5to9(avg5)
    rows = [r for r in ds.admission_results(keyword=keyword, year=year)
            if univ_type == "전체" or r["univ_type"] == univ_type]
    judged_rows = [r for r in rows if r["track_type"] in ("학생부교과", "학생부종합", "전문대수시")]
    items = [_judge_row(r, conv["grade9"], keyword) for r in judged_rows]
    items.sort(key=lambda x: (LABEL_ORDER.index(x["label"]), x["gap"] if x["gap"] is not None else 0, x["univ_name"]))

    notes = []
    programs = ds.find_programs(keyword)
    for t in ("4년제", "전문대"):
        if univ_type in ("전체", t) and not any(p["univ_type"] == t for p in programs):
            notes.append(f"{t} '{keyword}' 계열은 수집 데이터에 없습니다.")
    if not programs:
        notes.append(f"'{keyword}' 학과는 수집 데이터(간호·컴퓨터공학 계열)에 없습니다.")
    excluded = sum(1 for r in rows if r["track_type"] in ("수능", "전문대정시"))
    if excluded:
        notes.append(f"수능(정시) {excluded}건은 컷 단위가 평균 백분위라 비교에서 뺐습니다.")
    renamed = [f"{x['univ_name']} {x['unit_name']} {x['track_name']} → 2028: {', '.join(x['tracks_2028'])}"
               for x in items if x.get("renamed_in_2028")]
    if renamed:
        notes.append("다음 전형은 2028학년도에 이름이나 구성이 바뀌어 판정이 2026 전형 기준 참고치입니다: " + " / ".join(renamed))

    return {
        "input": {"avg5": avg5, "keyword": keyword, "univ_type": univ_type, "year": year},
        "conversion": conv,
        "metro": [x for x in items if x["region"] in ds.METRO_REGIONS],
        "non_metro_reference": [x for x in items if x["region"] not in ds.METRO_REGIONS],
        "premise": PREMISE,
        "notes": notes,
    }
