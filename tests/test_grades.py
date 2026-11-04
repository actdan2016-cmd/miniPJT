"""grades.py 단위 테스트 - 정답은 evaluation/test_queries.csv의 note(`정답:`) 기준"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.grades import (  # noqa: E402
    MID_PCT_5, MID_PCT_9, classify, convert_5to9, gpa_summary, to_grade9, to_percentile, weighted_average,
)


def row(sem, group, credits, grade, subject=""):
    return {"semester": sem, "subject": subject or group, "group": group, "credits": credits, "grade": grade}


# 평가 1번 입력 (1학년 1학기, 한국사 1등급)
Q1 = [row("1-1", "국어", 4, 2), row("1-1", "수학", 4, 3), row("1-1", "영어", 4, 2),
      row("1-1", "사회", 4, 2), row("1-1", "과학", 4, 3), row("1-1", "한국사", 3, 1)]


def load_sample():
    """시연용 가짜 페르소나(평가 22번과 같은 성적 + 등급 없는 과학탐구실험)"""
    with open(ROOT / "data" / "sample_student_grades.csv", encoding="utf-8-sig") as f:
        return [row(r["semester"], r["subject_group"], int(r["credits"]),
                    int(r["grade_5"]) if r["grade_5"] else None, r["subject"]) for r in csv.DictReader(f)]


# ---------- 평균 ----------
def test_q1_scopes():
    assert weighted_average(Q1) == {"sum": 51, "credits": 23, "avg": 2.22}
    assert weighted_average(Q1, {"국어", "수학", "영어", "사회"}) == {"sum": 36, "credits": 16, "avg": 2.25}
    assert weighted_average(Q1, {"국어", "수학", "영어", "과학"}) == {"sum": 40, "credits": 16, "avg": 2.5}


def test_q22_semesters_and_cumulative():
    s = gpa_summary(load_sample())
    assert s["semesters"]["1-1"]["전과목"] == {"sum": 54, "credits": 23, "avg": 2.35}
    assert s["semesters"]["1-2"]["전과목"] == {"sum": 51, "credits": 23, "avg": 2.22}
    assert s["cumulative"]["전과목"] == {"sum": 105, "credits": 46, "avg": 2.28}
    assert s["cumulative"]["국영수사"] == {"sum": 72, "credits": 32, "avg": 2.25}
    assert s["cumulative"]["국영수과"] == {"sum": 80, "credits": 32, "avg": 2.5}


def test_no_grade_subject_excluded():
    rows = [row("1-1", "국어", 4, 2), row("1-1", "과학", 1, None)]
    assert weighted_average(rows) == {"sum": 8, "credits": 4, "avg": 2.0}


def test_empty_returns_none():
    assert weighted_average([row("1-1", "과학", 1, None)])["avg"] is None


def test_q27_equal_credits():
    rows = [row("1-1", "국어", 4, 2.8), row("1-2", "국어", 4, 2.0)]
    assert weighted_average(rows)["avg"] == 2.4


# ---------- 5→9 환산 ----------
@pytest.mark.parametrize("avg5, pct, g9", [
    (2.2, 27.6, 3.73),   # 2번
    (2.0, 22.0, 3.34),   # 8번
    (2.4, 33.2, 4.09),   # 3·17번
    (3.5, 64.0, 5.76),   # 16번
    (2.25, 29.0, 3.83),  # 화면 2 국영수사
    (2.5, 36.0, 4.24),   # 화면 2 국영수과
])
def test_convert(avg5, pct, g9):
    r = convert_5to9(avg5)
    assert r["percentile"] == pct and r["grade9"] == g9


def test_q22_cumulative_to9():
    assert round(to_grade9(to_percentile(105 / 46)), 2) == 3.89


def test_convert_edges():
    assert convert_5to9(1.0)["grade9"] == 1.55
    assert convert_5to9(5.0)["grade9"] == 8.45  # 95% → 8 + (95-92.5)/(98-92.5)
    with pytest.raises(ValueError):
        to_percentile(0.9)
    with pytest.raises(ValueError):
        to_percentile(5.1)


def test_constants_match_csv():
    with open(ROOT / "data" / "grade_conversion.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [float(r["mid_pct"]) for r in rows if r["scale"] == "5"] == MID_PCT_5
    assert [float(r["mid_pct"]) for r in rows if r["scale"] == "9"] == MID_PCT_9


# ---------- 판정 (SERVICE.md §3-2 v1) ----------
@pytest.mark.parametrize("g, c, label", [
    (4.09, 3.43, "상향"),   # 3번 수원대 교과우수
    (4.09, 3.89, "소신"),   # 3번 수원대 면접위주교과
    (4.09, 4.0, "소신"),    # 3번 수원대 고교추천
    (3.89, 3.0, "상향"),    # 22번 수원대 간호 고교추천
    (3.89, 3.9, "적정"),    # 22번 한서대 교과2
    (3.89, 3.6, "소신"),    # 22번 한서인재
    (3.89, 3.29, "상향"),   # 22번 원광대
    (4.09, 4.8, "안정"),    # 3번 한서대 항공AI 교과1
    (3.89, 2.6, "격차 큼"),  # 22번 한서대 교과1
])
def test_classify_eval_cases(g, c, label):
    assert classify(g, c)["label"] == label


@pytest.mark.parametrize("g, label", [
    (2.5, "안정"), (2.51, "적정"), (3.0, "적정"), (3.01, "소신"),
    (3.5, "소신"), (3.51, "상향"), (4.0, "상향"), (4.01, "격차 큼"),
])
def test_classify_boundaries(g, label):
    assert classify(g, 3.0)["label"] == label


def test_classify_gap_value():
    assert classify(3.89, 2.6) == {"label": "격차 큼", "gap": 1.29}
    assert classify(3.89, 3.9) == {"label": "적정", "gap": -0.01}
