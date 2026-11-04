"""grades.py - 내신 평균·5→9등급 환산·지원 가능성 판정 순수 함수

계산과 판정은 LLM이 아니라 이 파일의 함수만 한다 (CLAUDE.md 코드 규칙).
파일·네트워크를 읽지 않고 입력만 받아 값을 돌려준다.
"""

# 등급별 중간 백분위 (data/grade_conversion.csv의 mid_pct와 같아야 한다)
MID_PCT_5 = [5, 22, 50, 78, 95]                          # 5등급: 누적 10/34/66/90/100%
MID_PCT_9 = [2, 7.5, 17, 31.5, 50, 68.5, 83, 92.5, 98]   # 9등급: 누적 4/11/23/40/60/77/89/96/100%

# 평균 범위별 교과군 (한국사는 국영수사·국영수과에 넣지 않는다)
SCOPES = {
    "전과목": None,
    "국영수사": {"국어", "수학", "영어", "사회"},
    "국영수과": {"국어", "수학", "영어", "과학"},
}

# 판정 구간: 환산 등급 - 기준선(d)이 이 값 이하이면 해당 판정 (SERVICE.md §3-2 v1)
CLASSIFY_BANDS = [(-0.5, "안정"), (0.0, "적정"), (0.5, "소신"), (1.0, "상향")]
GAP_LABEL = "격차 큼"


def weighted_average(rows: list[dict], groups: set[str] | None = None) -> dict:
    """학점 가중 평균을 구한다. 석차등급이 없는(성취도만 있는) 과목은 뺀다.

    rows: [{"group": "국어", "credits": 4, "grade": 2}, ...]  grade가 None이면 제외
    groups: 포함할 교과군 집합. None이면 전과목
    반환: {"sum": 등급×학점 합, "credits": 학점 합, "avg": 소수 둘째 자리 평균 또는 None}
    """
    total, credits = 0, 0
    for r in rows:
        if r.get("grade") is None:
            continue
        if groups is not None and r["group"] not in groups:
            continue
        total += r["grade"] * r["credits"]
        credits += r["credits"]
    avg = round(total / credits, 2) if credits else None
    return {"sum": total, "credits": credits, "avg": avg}


def gpa_summary(rows: list[dict]) -> dict:
    """학기별·누적 평균을 전과목/국영수사/국영수과로 묶어 돌려준다.

    rows: [{"semester": "1-1", "subject": "공통국어1", "group": "국어", "credits": 4, "grade": 2}, ...]
    반환: {"semesters": {"1-1": {"전과목": {...}, ...}, ...}, "cumulative": {"전과목": {...}, ...}}
    """
    semesters = sorted({r["semester"] for r in rows})
    by_sem = {
        s: {name: weighted_average([r for r in rows if r["semester"] == s], g) for name, g in SCOPES.items()}
        for s in semesters
    }
    cumulative = {name: weighted_average(rows, g) for name, g in SCOPES.items()}
    return {"semesters": by_sem, "cumulative": cumulative}


def to_percentile(avg5: float) -> float:
    """5등급 평균을 누적 백분위(%)로 바꾼다. 등급 중간 백분위 사이를 선형 보간한다."""
    if not 1 <= avg5 <= 5:
        raise ValueError(f"5등급 평균은 1~5 사이여야 합니다: {avg5}")
    i = min(int(avg5), 4) - 1
    return MID_PCT_5[i] + (avg5 - (i + 1)) * (MID_PCT_5[i + 1] - MID_PCT_5[i])


def to_grade9(pct: float) -> float:
    """누적 백분위(%)를 9등급으로 역보간한다 (점 추정)."""
    for j in range(len(MID_PCT_9) - 1):
        lo, hi = MID_PCT_9[j], MID_PCT_9[j + 1]
        if lo <= pct <= hi:
            return (j + 1) + (pct - lo) / (hi - lo)
    raise ValueError(f"백분위가 환산 범위({MID_PCT_9[0]}~{MID_PCT_9[-1]}) 밖입니다: {pct}")


def convert_5to9(avg5: float) -> dict:
    """5등급 평균을 9등급 환산값으로 바꾼다. 근사치이며 과거 입결 비교용이다.

    반환: {"avg5": 입력, "percentile": 누적 백분위(소수 첫째), "grade9": 9등급(소수 둘째)}
    """
    pct = to_percentile(avg5)
    return {"avg5": avg5, "percentile": round(pct, 1), "grade9": round(to_grade9(pct), 2)}


def classify(g: float, c: float) -> dict:
    """환산 9등급(g)을 기준선 컷(c)과 비교해 안정·적정·소신·상향·격차 큼으로 나눈다.

    두 값의 차이(d = g - c)를 소수 둘째 자리로 맞춘 뒤 구간에 넣는다 (화면에 보이는 숫자와 판정이 같도록).
    반환: {"label": 판정, "gap": d}
    """
    d = round(g - c, 2)
    for upper, label in CLASSIFY_BANDS:
        if d <= upper:
            return {"label": label, "gap": d}
    return {"label": GAP_LABEL, "gap": d}
