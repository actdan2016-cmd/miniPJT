"""data_store.py - data/*.csv 로드와 조회

CSV를 읽어 숫자 칸은 숫자로, 빈 칸은 None으로 바꿔 돌려준다. 계산·판정은 하지 않는다(grades.py 담당).
모집단위명이 파일마다 조금씩 달라(예: 한양대 입결 "간호학과" / 학과 목록 "간호학부") 학과는 키워드로 찾는다.
"""
import csv
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
METRO_REGIONS = {"서울", "경기", "인천"}

# 칸 이름 → 형 변환 (나머지는 문자열, 빈 칸은 None)
INT_COLS = {"year", "admission_year", "quota", "added_pass", "grade_scale", "degree_years"}
FLOAT_COLS = {"competition_rate", "cut_50", "cut_70", "cut_avg", "cut_min"}


def _convert(row: dict) -> dict:
    """CSV 한 행의 빈 칸을 None으로, 숫자 칸을 int/float로 바꾼다."""
    out = {}
    for k, v in row.items():
        v = v.strip() if isinstance(v, str) else v
        if v == "" or v is None:
            out[k] = None
        elif k in INT_COLS:
            out[k] = int(v)
        elif k in FLOAT_COLS:
            out[k] = float(v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=None)
def load(name: str) -> tuple[dict, ...]:
    """data/{name}.csv를 읽어 행 튜플로 돌려준다 (한 번 읽으면 캐시)."""
    with open(DATA_DIR / f"{name}.csv", encoding="utf-8-sig", newline="") as f:
        return tuple(_convert(r) for r in csv.DictReader(f))


def _norm_univ(name: str) -> str:
    """대학명 비교용 정규화: '국립'·공백 제거, '대학교'→'대'."""
    return name.replace("국립", "").replace("대학교", "대").replace(" ", "")


def universities() -> dict[str, dict]:
    """univ_id → 대학 정보 (is_metro: 서울·경기·인천이면 True)."""
    return {u["univ_id"]: {**u, "is_metro": u["region"] in METRO_REGIONS} for u in load("universities")}


def find_university(query: str) -> dict | None:
    """대학명 일부('인하대', '한양대학교', '군산대')로 대학을 찾는다. 없거나 여럿이면 None."""
    q = _norm_univ(query)
    hits = [u for u in universities().values() if q and (q in _norm_univ(u["univ_name"]) or _norm_univ(u["univ_name"]) in q)]
    return hits[0] if len(hits) == 1 else None


def is_metro(univ_id: str) -> bool:
    """수도권(서울·경기·인천) 대학이면 True."""
    return universities()[univ_id]["is_metro"]


def _program_matches(query: str, p: dict) -> bool:
    """검색어가 모집단위에 맞는지 본다.

    1) 검색어가 학과명·모집단위명·키워드 안에 있으면 맞음 ('컴퓨터' → 컴퓨터공학과)
    2) 검색어 안에 키워드가 들어 있어도 맞음 ('컴퓨터공학' ⊃ 컴퓨터, '간호학과' ⊃ 간호).
       단, '보건'·'의료'처럼 짧고 넓은 단어로 '보건행정'이 간호에 잡히지 않도록 대표 키워드(첫 번째)이거나 3글자 이상일 때만
    """
    q = query.replace(" ", "").lower()
    keywords = [k for k in (p["keywords"] or "").split(";") if k]
    if any(q in w.replace(" ", "").lower() for w in [p["dept_name"], p["unit_name"], *keywords] if w):
        return True
    return any(k.lower() in q for i, k in enumerate(keywords) if i == 0 or len(k) >= 3)


def find_programs(keyword: str, univ_type: str | None = None) -> list[dict]:
    """학과 키워드('간호', '컴퓨터공학', 'AI')로 모집단위를 찾는다. univ_type: '4년제'·'전문대'로 거른다."""
    unis = universities()
    out = []
    for p in load("programs"):
        if not _program_matches(keyword, p):
            continue
        u = unis[p["univ_id"]]
        if univ_type and u["univ_type"] != univ_type:
            continue
        out.append({**p, "univ_name": u["univ_name"], "univ_type": u["univ_type"],
                    "region": u["region"], "is_metro": u["is_metro"]})
    return out


def track_type(track_name: str) -> str:
    """입결 전형명에서 전형 유형을 뽑는다: 학생부교과·학생부종합·수능·전문대수시·전문대정시."""
    for prefix in ("학생부교과", "학생부종합", "수능"):
        if track_name.startswith(prefix):
            return prefix
    if track_name.startswith("수시"):
        return "전문대수시"
    if track_name.startswith("정시"):
        return "전문대정시"
    return "기타"


def _unit_keywords(univ_id: str, keyword: str) -> list[str]:
    """키워드에 맞는 그 대학 모집단위의 검색어(학과명 앞부분)를 돌려준다. 예: 한양대 '간호' → ['간호학부', '간호']."""
    names = [p["dept_name"] for p in find_programs(keyword) if p["univ_id"] == univ_id]
    return names + [keyword]


def admission_results(univ_id: str | None = None, keyword: str | None = None, track: str | None = None,
                      year: int | None = None, unit: str | None = None) -> list[dict]:
    """입결(admission_results.csv)을 조회한다. 각 행에 대학명·유형·지역·수도권 여부·전형 유형을 붙인다.

    keyword: 학과 키워드('간호', '컴퓨터') — 대학별 학과명이 달라도 찾는다
    unit: 모집단위명 일부 / track: 전형명 일부('지역균형', '교과') / year: 학년도(2025, 2026)
    """
    unis = universities()
    out = []
    for r in load("admission_results"):
        if univ_id and r["univ_id"] != univ_id:
            continue
        if year and r["year"] != year:
            continue
        if unit and unit not in r["unit_name"]:
            continue
        if track and track not in r["track_name"]:
            continue
        if keyword and not any(k in r["unit_name"] for k in _unit_keywords(r["univ_id"], keyword)):
            continue
        u = unis[r["univ_id"]]
        out.append({**r, "univ_name": u["univ_name"], "univ_type": u["univ_type"], "region": u["region"],
                    "is_metro": u["is_metro"], "track_type": track_type(r["track_name"])})
    return out


def admission_plans(univ_id: str | None = None, unit: str | None = None, year: int | None = None,
                    track_type_: str | None = None) -> list[dict]:
    """전형 계획(admission_plans.csv, 2028 시행계획 요약)을 조회한다."""
    return [p for p in load("admission_plans")
            if (not univ_id or p["univ_id"] == univ_id)
            and (not unit or unit in p["unit_name"])
            and (not year or p["admission_year"] == year)
            and (not track_type_ or p["track_type"] == track_type_)]


# ---------- 학생 프로필 저장 (LangGraph Store, 승인 후에만 저장) ----------
PROFILE_DB = Path(__file__).resolve().parents[1] / "profiles.sqlite"


@lru_cache(maxsize=1)
def _profile_store():
    """프로필 저장소 (LangGraph SqliteStore). 별칭을 키로, 이름·학교명은 저장하지 않는다."""
    import sqlite3

    from langgraph.store.sqlite import SqliteStore
    store = SqliteStore(sqlite3.connect(PROFILE_DB, check_same_thread=False, isolation_level=None))
    store.setup()
    return store


def save_profile(alias: str, profile: dict) -> None:
    """별칭으로 프로필(희망 학과·계열·과목 성적)을 저장한다."""
    _profile_store().put(("profiles",), alias, profile)


def load_profile(alias: str) -> dict | None:
    """별칭으로 저장한 프로필을 읽는다. 없으면 None."""
    item = _profile_store().get(("profiles",), alias)
    return item.value if item else None
