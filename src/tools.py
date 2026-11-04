"""tools.py - 에이전트 도메인 도구 (@tool)

도구는 입력을 검증하고 grades.py(계산·판정)와 data_store.py(조회)를 불러 결과를 dict로 돌려준다.
도구 안에서 숫자를 따로 계산하지 않는다. 화면용 API(/api/*)도 같은 도구를 직접 호출한다.
"""
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src import data_store as ds
from src import grades
from src import retriever

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


# ---------- convert_grade_5to9 (2차) ----------
@tool("convert_grade_5to9")
def convert_grade_5to9(avg5: float) -> dict:
    """5등급제 평균을 9등급으로 바꾸면 몇 등급인지(과거 입결과 비교하는 기준) 물을 때 쓴다.

    각 등급의 중간 누적 백분위(5등급 5/22/50/78/95%, 9등급 2/7.5/17/31.5/50/68.5/83/92.5/98%)로 선형 보간한 근사치다.
    """
    r = grades.convert_5to9(avg5)
    return {**r, "method": "5등급 평균 → 등급 중간 누적 백분위 선형 보간 → 9등급 역보간 (근사치)",
            "notes": ["평균 등급은 백분위가 아니라서 정보 손실이 있습니다. 학생부종합처럼 정성평가 전형은 환산만으로 판단하기 어렵습니다."]}


# ---------- retrieve_admission_docs (2차) ----------
@tool("retrieve_admission_docs")
def retrieve_admission_docs(query: str, univ_name: str | None = None) -> dict:
    """대학 시행계획·모집요강·교육부 대입개편 문서에서 근거 문장을 찾을 때 쓴다.

    수능최저학력기준, 학생부 반영 방법(반영 학기·과목·성취도), 모집인원, 전형 방법·차이, 2028 수능 체제 같은 '문서에 적힌 규칙'을 물을 때 쓴다.
    univ_name: 대학명('한양대', '동남보건대'). 대입 제도 전반(2028 수능 등)은 univ_name='교육부'. 비우면 전체 문서에서 찾는다.
    결과마다 문서명·학년도·쪽이 붙으므로 답변에 그대로 인용한다.
    """
    univ = retriever.match_univ(univ_name) if univ_name else None
    if univ_name and univ is None:
        return {"found": False, "message": f"'{univ_name}'의 시행계획 문서는 수집하지 않았습니다. 대학 입학처나 어디가(adiga.kr)에서 확인해 주세요.",
                "results": []}
    docs = retriever.search(query, univ=univ, k=5)
    return {"found": bool(docs), "univ": univ,
            "results": [{"source": d.metadata["source"], "year": d.metadata["year"], "page": d.metadata["page"],
                         "text": d.page_content} for d in docs],
            "notes": ["2029학년도 시행계획은 2027년 4~5월 공개 예정이라, 2028학년도 문서를 참고치로 씁니다."]}


# ---------- get_recommended_courses (2차) ----------
@tool("get_recommended_courses")
def get_recommended_courses(keyword: str, univ_name: str | None = None) -> dict:
    """희망 학과의 대학별 핵심·권장 과목(2·3학년 과목 선택, 학습 로드맵)을 물을 때 쓴다.

    keyword: 학과 키워드('간호', '컴퓨터'), univ_name: 특정 대학만 볼 때. 대학마다 다르고 지정하지 않은 대학도 있다.
    """
    progs = ds.find_programs(keyword)
    univ = ds.find_university(univ_name) if univ_name else None
    unis = ds.universities()
    out = []
    for r in ds.load("recommended_courses"):
        if univ and r["univ_id"] != univ["univ_id"]:
            continue
        if not any(p["univ_id"] == r["univ_id"] and p["dept_name"][:2] == r["field_or_dept"][:2] for p in progs):
            continue
        core = [c for c in (r["core_courses"] or "").split(";") if c]
        rec = [c for c in (r["recommended_courses"] or "").split(";") if c]
        out.append({"univ_name": unis[r["univ_id"]]["univ_name"], "field_or_dept": r["field_or_dept"], "core_courses": core,
                    "recommended_courses": rec, "designated": bool(core or rec), "note": r["note"],
                    "source_doc": r["source_doc"], "source_page": r["source_page"], "year": r["year"]})
    return {"keyword": keyword, "results": out,
            "notes": ["권장과목은 대학마다 다르고, 지정하지 않은 대학도 있습니다(designated=false).",
                      "재학 중인 학교에 그 과목이 개설되는지 학교 교육과정 편성표로 확인해 주세요.",
                      "권장과목 자료를 모으지 않은 대학은 목록에 없습니다."]}


# ---------- simulate_improvement (2차) ----------
TOTAL_SEMESTERS = 5   # 졸업예정자는 3학년 1학기까지 반영


class SimulateInput(BaseModel):
    subjects: list[SubjectGrade] = Field(description="지금까지의 학기별 과목 성적")
    keyword: str = Field(description="희망 학과 키워드. 예: 간호, 컴퓨터")
    target_univ: str | None = Field(default=None, description="목표 대학(선택). 비우면 가장 가까운 상위 목표를 고른다")
    target_track: str | None = Field(default=None, description="목표 전형명 일부(선택). 예: 교과우수")
    future_avg: float | None = Field(default=None, ge=1, le=5, description="남은 학기 예상 평균(선택). 넣으면 그때의 누적 평균과 판정을 계산")


@tool("simulate_improvement", args_schema=SimulateInput)
def simulate_improvement(subjects: list, keyword: str, target_univ: str | None = None, target_track: str | None = None,
                         future_avg: float | None = None) -> dict:
    """한 단계 위 대학을 노리려면 남은 학기에 평균 몇 등급이 필요한지, 어떤 교과군을 먼저 올려야 효과가 큰지(What-if) 물을 때 쓴다.

    지금까지의 성적과 희망 학과로, 목표 전형의 컷(9등급)을 5등급 평균으로 거꾸로 환산해 남은 학기(3학년 1학기까지)에 필요한 평균을 계산한다.
    교과군별로 학점 비중과 지금 평균을 보여 주고, 같은 1등급을 올릴 때 누적 평균이 더 많이 내려가는 순서로 정렬한다.
    """
    rows = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in subjects]
    summary = grades.gpa_summary(rows)
    cum = summary["cumulative"]["전과목"]
    if cum["avg"] is None:
        return {"error": "석차등급이 있는 과목이 없어 계산할 수 없습니다."}
    done = [s for s, v in summary["semesters"].items() if v["전과목"]["avg"] is not None]
    remaining = max(TOTAL_SEMESTERS - len(done), 0)
    future_credits = round(cum["credits"] / len(done) * remaining)
    board = classify_chances.invoke({"avg5": cum["avg"], "keyword": keyword})
    judged = [x for x in board["metro"] + board["non_metro_reference"] if x.get("judged")]
    if target_univ:
        u = ds.find_university(target_univ)
        picks = [x for x in judged if u and x["univ_name"] == u["univ_name"] and (not target_track or target_track in x["track_name"])]
    else:
        metro = [x for x in board["metro"] if x.get("judged")]
        gap_big = sorted([x for x in metro if x["label"] == "격차 큼"], key=lambda x: x["gap"])[:2]
        upward = sorted([x for x in metro if x["label"] == "상향"], key=lambda x: x["gap"])[:2]
        picks = upward + gap_big
    targets = []
    for x in picks:
        need_avg5 = grades.grade9_to_avg5(x["cut"])                 # 적정선: 환산 9등급 ≤ 컷
        need_future = grades.required_future_avg(cum["sum"], cum["credits"], future_credits, need_avg5) if future_credits else None
        sosin_avg5 = grades.grade9_to_avg5(x["cut"] + 0.5)          # 소신선: 환산 9등급 ≤ 컷 + 0.5
        sosin_future = grades.required_future_avg(cum["sum"], cum["credits"], future_credits, sosin_avg5) if future_credits else None
        targets.append({"univ_name": x["univ_name"], "unit_name": x["unit_name"], "track_name": x["track_name"],
                        "current_label": x["label"], "cut9": x["cut"], "basis": x["basis"], "gap": x["gap"],
                        "target_avg5": need_avg5, "required_future_avg": need_future,
                        "reachable": need_future is not None and need_future >= 1.0,
                        "sosin_avg5": sosin_avg5, "required_future_avg_sosin": sosin_future,
                        "sosin_reachable": sosin_future is not None and sosin_future >= 1.0, "source_doc": x["source_doc"]})
    projection = None
    if future_avg is not None and future_credits:
        p5 = grades.projected_avg(cum["sum"], cum["credits"], future_credits, future_avg)
        g9 = grades.convert_5to9(p5)["grade9"]
        projection = {"future_avg": future_avg, "projected_avg5": p5, "projected_grade9": g9,
                      "targets": [{"univ_name": t["univ_name"], "track_name": t["track_name"],
                                   "label": grades.classify(g9, t["cut9"])["label"]} for t in targets]}
    return {"current": {"avg5": cum["avg"], "sum": cum["sum"], "credits": cum["credits"],
                        "grade9": grades.convert_5to9(cum["avg"])["grade9"], "semesters_done": done},
            "remaining_semesters": remaining, "future_credits_assumed": future_credits,
            "targets": targets, "group_effects": grades.group_effects(rows), "projection": projection,
            "notes": ["남은 학기 학점은 지금까지의 학기당 평균 학점으로 가정했습니다.",
                      "목표 컷은 2026학년도 입결(9등급)을 5등급 평균으로 거꾸로 환산한 근사치입니다.",
                      "대학마다 반영 교과·학기가 달라 실제 반영 평균과 다를 수 있습니다.",
                      "required_future_avg가 1.0보다 작으면 남은 학기를 모두 1등급으로 받아도 닿기 어렵다는 뜻입니다."]}


# ---------- search_programs (3차) ----------
@tool("search_programs")
def search_programs(keyword: str, univ_type: Literal["전체", "4년제", "전문대"] = "전체") -> dict:
    """학과 키워드로 그 학과(유사 학과 포함)를 개설한 대학·모집단위를 찾을 때 쓴다. 지원 가능성을 보기 전에 범위를 확인하는 용도다.

    keyword: '간호', '컴퓨터공학', 'AI' 같은 학과 키워드. 대학마다 학과명이 달라도(소프트웨어학과·컴퓨터공학부 등) 함께 찾는다.
    """
    progs = ds.find_programs(keyword, None if univ_type == "전체" else univ_type)
    by_univ = {}
    for p in progs:
        u = by_univ.setdefault(p["univ_name"], {"univ_name": p["univ_name"], "univ_type": p["univ_type"],
                                                 "region": p["region"], "is_metro": p["is_metro"], "units": []})
        u["units"].append(p["unit_name"])
    univs = sorted(by_univ.values(), key=lambda u: (not u["is_metro"], u["univ_name"]))
    notes = ["수집 데이터는 간호·컴퓨터공학 계열 일부 대학입니다. 목록에 없는 대학·학과는 어디가(adiga.kr)에서 확인해 주세요."]
    if not progs:
        notes.insert(0, f"'{keyword}' 학과는 수집 데이터에 없습니다.")
    return {"keyword": keyword, "count_units": len(progs), "count_univs": len(univs),
            "unit_names": sorted({p["unit_name"] for p in progs}), "universities": univs, "notes": notes}


# ---------- save_student_profile (3차, 사람 승인 후 실행) ----------
@tool("save_student_profile")
def save_student_profile(alias: str, major: str, subjects: list[SubjectGrade] | None = None, field: str | None = None) -> dict:
    """사용자가 자녀의 성적·희망 학과를 저장해 두자고 할 때 쓴다. 실행 전에 사용자의 승인을 받는다(HITL).

    alias: 학생 이름 대신 쓸 별칭(예: '첫째'). 이름·학교명은 저장하지 않는다. major: 희망 학과. subjects: 과목 성적(선택).
    """
    from src.middleware import mask_pii
    safe_alias, kinds = mask_pii(alias)
    if kinds:
        return {"saved": False, "message": "별칭에 이름·학교명 같은 개인정보가 있어 저장하지 않았습니다. '첫째'처럼 별칭으로 알려 주세요."}
    rows = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in (subjects or [])]
    ds.save_profile(safe_alias, {"major": major, "field": field, "subjects": rows})
    return {"saved": True, "alias": safe_alias, "major": major, "subject_count": len(rows),
            "message": f"'{safe_alias}' 프로필을 저장했습니다. 다음 상담에서 별칭으로 불러올 수 있습니다."}
