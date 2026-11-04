"""grades.py 단위 테스트 - 정답은 evaluation/test_queries.csv의 note(`정답:`) 기준"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.grades import (  # noqa: E402
    MID_PCT_5, MID_PCT_9, baseline_cut, classify, convert_5to9, gpa_summary, semester_trend, to_grade9,
    to_percentile, weighted_average,
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


# ---------- 추이·기준선 ----------
def test_q22_trend():
    assert semester_trend(gpa_summary(load_sample())) == {"from": "1-1", "to": "1-2", "change": -0.13, "direction": "상승"}
    assert semester_trend(gpa_summary(Q1)) is None


@pytest.mark.parametrize("ttype, c70, c50, cavg, expected", [
    ("학생부교과", 3.43, 3.25, None, {"cut": 3.43, "basis": "70% 컷"}),
    ("학생부교과", None, 2.4, None, {"cut": 2.4, "basis": "50% 컷"}),
    ("학생부교과", None, None, None, {"cut": None, "basis": None}),       # 가천대 교과 = 입결 미공개
    ("전문대수시", None, None, 2.05, {"cut": 2.05, "basis": "합격자 평균"}),  # 동남보건대
])
def test_baseline_cut(ttype, c70, c50, cavg, expected):
    assert baseline_cut(ttype, c70, c50, cavg) == expected


# ---------- What-if (C3) ----------
from src.grades import grade9_to_avg5, group_effects, projected_avg, required_future_avg  # noqa: E402


@pytest.mark.parametrize("avg5", [1.5, 2.2, 2.28, 2.4, 3.5])
def test_grade9_to_avg5_is_inverse(avg5):
    g9 = convert_5to9(avg5)["grade9"]
    assert abs(grade9_to_avg5(g9) - avg5) <= 0.01


def test_q23_target_suwon():
    # 23번: 수원대 간호 교과우수 c=2.65 → 5등급 약 1.51, 22번 성적(105/46)에 남은 3학기(69학점)로는 1등급 미만이 필요
    assert grade9_to_avg5(2.65) == 1.51
    assert required_future_avg(105, 46, 69, 1.51) < 1.0
    # 수원대 고교추천 c=3.0 → 5등급 약 1.71, 남은 학기 평균 약 1.33이면 적정 경계 ((1.71×115−105)÷69 = 1.328)
    assert grade9_to_avg5(3.0) == 1.71
    assert required_future_avg(105, 46, 69, 1.71) == 1.33


def test_projected_avg():
    assert projected_avg(105, 46, 69, 2.0) == 2.11


def test_group_effects_order():
    effects = group_effects(load_sample())
    assert effects[0]["group"] in ("수학", "과학", "영어")   # 학점 비중이 같으면 지금 등급이 낮은(숫자 큰) 교과가 앞
    sci = next(e for e in effects if e["group"] == "과학")
    assert sci["avg"] == 3.0 and sci["credits"] == 8
