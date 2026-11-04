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


def semester_trend(summary: dict, scope: str = "전과목") -> dict | None:
    """gpa_summary 결과에서 직전 학기 대비 변화를 구한다. 등급은 작을수록 좋으므로 change<0이면 상승.

    반환: {"from": "1-1", "to": "1-2", "change": -0.13, "direction": "상승"} 또는 학기가 하나면 None
    """
    sems = [s for s, v in summary["semesters"].items() if v[scope]["avg"] is not None]
    if len(sems) < 2:
        return None
    a, b = summary["semesters"][sems[-2]][scope]["avg"], summary["semesters"][sems[-1]][scope]["avg"]
    change = round(b - a, 2)
    direction = "상승" if change < 0 else "하락" if change > 0 else "유지"
    return {"from": sems[-2], "to": sems[-1], "change": change, "direction": direction}


def baseline_cut(track_type: str, cut_70: float | None, cut_50: float | None, cut_avg: float | None) -> dict:
    """판정 기준선(c)을 고른다 (SERVICE.md §3-2 v1).

    4년제 학생부교과: 70% 컷, 없으면 50% 컷 / 전문대 수시: 합격자 평균
    반환: {"cut": 값 또는 None, "basis": "70% 컷"·"50% 컷"·"합격자 평균"·None}
    """
    if track_type == "전문대수시":
        return {"cut": cut_avg, "basis": "합격자 평균" if cut_avg is not None else None}
    if cut_70 is not None:
        return {"cut": cut_70, "basis": "70% 컷"}
    if cut_50 is not None:
        return {"cut": cut_50, "basis": "50% 컷"}
    return {"cut": None, "basis": None}


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


def grade9_to_avg5(g9: float) -> float:
    """9등급 값(예: 컷)을 같은 누적 백분위의 5등급 평균으로 거꾸로 바꾼다 (convert_5to9의 역함수, 근사).

    1등급보다 좋은 값이 필요하면 1.0 미만이 나온다(= 5등급 평균으로는 도달하기 어려움).
    """
    j = min(max(int(g9), 1), 8) - 1
    pct = MID_PCT_9[j] + (g9 - (j + 1)) * (MID_PCT_9[j + 1] - MID_PCT_9[j])
    if pct < MID_PCT_5[0]:
        return round(1 - (MID_PCT_5[0] - pct) / (MID_PCT_5[1] - MID_PCT_5[0]), 2)
    for i in range(len(MID_PCT_5) - 1):
        lo, hi = MID_PCT_5[i], MID_PCT_5[i + 1]
        if lo <= pct <= hi:
            return round((i + 1) + (pct - lo) / (hi - lo), 2)
    return 5.0


def required_future_avg(total: float, credits: int, future_credits: int, target_avg5: float) -> float:
    """지금까지 (등급×학점 합 total, 학점 credits)에서 남은 학점 future_credits로 누적 평균 target_avg5에 닿으려면
    남은 학기 평균이 몇이어야 하는지. 1.0보다 작으면 남은 학기를 모두 1등급으로 받아도 닿지 않는다."""
    return round((target_avg5 * (credits + future_credits) - total) / future_credits, 2)


def projected_avg(total: float, credits: int, future_credits: int, future_avg: float) -> float:
    """남은 학기를 평균 future_avg로 받을 때의 누적 평균"""
    return round((total + future_avg * future_credits) / (credits + future_credits), 2)


def group_effects(rows: list[dict]) -> list[dict]:
    """교과군별 학점 비중과 '앞으로 그 교과군을 1등급 올릴 때 누적 평균이 내려가는 정도'(학점 비중 기준).

    반환: 교과군별 {group, credits, share, avg, room}을 효과(share×여유) 큰 순으로. room = 지금 평균에서 1등급까지 남은 폭
    """
    graded = [r for r in rows if r.get("grade") is not None]
    total = sum(r["credits"] for r in graded)
    out = []
    for g in sorted({r["group"] for r in graded}):
        rs = [r for r in graded if r["group"] == g]
        c = sum(r["credits"] for r in rs)
        avg = sum(r["grade"] * r["credits"] for r in rs) / c
        out.append({"group": g, "credits": c, "share": round(c / total, 3), "avg": round(avg, 2), "room": round(avg - 1, 2)})
    return sorted(out, key=lambda x: (x["share"] * x["room"], x["share"]), reverse=True)


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
