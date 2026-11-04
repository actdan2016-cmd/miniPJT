"""guardrails.py - 출력 가드레일과 결과표 렌더링

1) 결과표 렌더링: 도구 결과(평균·판정·입결)를 코드가 마크다운 표로 만든다. LLM은 표를 다시 쓰지 않고 요약만 쓴다.
   → 화면의 숫자는 항상 도구 결과와 같다 (숫자 환각 차단).
2) 출력 검사: LLM 요약에 보장 표현, 서열 비하 표현, 도구 결과·질문에 없는 소수 숫자가 있는지 코드로 본다.
3) 위반 시: LLM 요약을 빼고 코드 결과표만 보여 준다(표가 없으면 고정 문구). 재시도하지 않아 응답이 늦어지지 않는다.
"""
import re

from src.replies import OUTPUT_FALLBACK, OUTPUT_TABLE_ONLY

PREMISE = "※ 과거 입결 기반 참고치이며 합격을 보장하지 않습니다. 5→9등급 환산은 근사치이고, 고1 성적 기반 예측은 현재 추세 유지를 가정합니다."
JUDGED_ORDER = ["안정", "적정", "소신", "상향", "격차 큼"]


# ---------- 출처 표기 (화면용: 내부 id 대신 쉬운 이름) ----------
def friendly_source(src: str | None) -> str:
    """'어디가 전형결과(unvCd=…)' 같은 원문을 '어디가(대입정보포털)'처럼 바꾼다."""
    if not src:
        return ""
    if src.startswith("어디가"):
        return "어디가(대입정보포털)"
    if src.startswith("전문대학포털"):
        return "전문대학포털"
    if "입학처 발표" in src:
        return f"{src.split(' ')[0]} 입학처 발표"
    return re.sub(r"\s*\(.*\)\s*$", "", src)


def _f(v) -> str:
    return "–" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _gap(v) -> str:
    return "" if v is None else f"{v:+.2f}"


def _short_univ(name: str) -> str:
    return name.replace("대학교", "대").replace("국립", "")


# ---------- 결과표 ----------
def render_gpa(r: dict) -> str:
    """calc_school_gpa 결과 → 학기별·누적 평균 표"""
    lines = ["| 구분 | 전과목 | 국영수사 | 국영수과 |", "|---|---|---|---|"]
    for sem, v in r["semesters"].items():
        lines.append(f"| {sem} | {_f(v['전과목']['avg'])} ({v['전과목']['sum']}÷{v['전과목']['credits']}) "
                     f"| {_f(v['국영수사']['avg'])} | {_f(v['국영수과']['avg'])} |")
    c = r["cumulative"]
    lines.append(f"| **누적** | **{_f(c['전과목']['avg'])}** ({c['전과목']['formula']}) | {_f(c['국영수사']['avg'])} | {_f(c['국영수과']['avg'])} |")
    g9 = {k: v["grade9"]["grade9"] for k, v in c.items() if v.get("grade9")}
    if g9:
        lines.append(f"| 9등급 환산(근사) | {_f(g9.get('전과목'))} | {_f(g9.get('국영수사'))} | {_f(g9.get('국영수과'))} |")
    out = ["**평균 등급 (학점 가중, 석차등급 있는 과목만)**", "", *lines]
    t = r.get("trend")
    if t:
        out += ["", f"추이: {t['from']} → {t['to']} 전과목 {t['change']:+.2f} ({t['direction']}, 등급은 낮을수록 좋음)"]
    if r.get("excluded_subjects"):
        out.append(f"평균에서 뺀 과목(석차등급 없음): {', '.join(r['excluded_subjects'])}")
    return "\n".join(out)


def _judged_table(items: list[dict]) -> list[str]:
    rows = [x for x in items if x["label"] in JUDGED_ORDER]
    if not rows:
        return ["판정 대상(학생부교과·전문대 수시) 전형이 없습니다."]
    lines = ["| 판정 | 대학 | 모집단위 | 전형 | 기준선 | 컷까지 |", "|---|---|---|---|---|---|"]
    for x in rows:
        base = f"{_f(x['cut'])} ({x['basis']})" + (f", 최저 {_f(x['cut_min'])}" if x.get("cut_min") is not None else "")
        label = f"**{x['label']}**" if x["label"] != "격차 큼" else x["label"]
        lines.append(f"| {label} | {_short_univ(x['univ_name'])} | {x['unit_name']} | {x['track_name']} | {base} | {_gap(x['gap'])} |")
    return lines


def render_chances(r: dict) -> str:
    """classify_chances 결과 → 수도권 / 수도권 외 참고 판정표 + 입결 미공개·종합 참고치 + 안내"""
    c = r["conversion"]
    out = [f"**지원 가능성 판정** · '{r['input']['keyword']}' · 5등급 {_f(c['avg5'])} → 누적 백분위 {c['percentile']}% "
           f"→ 9등급 약 {_f(c['grade9'])} · {r['input']['year']}학년도 입결 기준선과 비교"]
    for title, items in (("수도권", r["metro"]), ("수도권 외 참고", r["non_metro_reference"])):
        if not items:
            continue
        out += ["", f"**{title}**", "", *_judged_table(items)]
        hidden = [x for x in items if x["label"] == "입결 미공개"]
        if hidden:
            out += ["", "입결 미공개(판정 안 함): " + ", ".join(f"{_short_univ(x['univ_name'])} {x['track_name']}" for x in hidden)]
        ref = [x for x in items if x["label"] == "종합 참고치"]
        if ref:
            out += ["", "학생부종합 합격자 내신 참고치(정성평가라 판정 안 함): "
                    + ", ".join(f"{_short_univ(x['univ_name'])} {x['unit_name']} {x['track_name'].replace('학생부종합', '').strip('()')} {_f(x['cut'])}"
                                for x in ref if x["cut"] is not None)]
    srcs = sorted({friendly_source(x["source_doc"]) for x in r["metro"] + r["non_metro_reference"]})
    if srcs:
        out += ["", f"출처: {', '.join(srcs)} ({r['input']['year']}학년도 입시결과)"]
    out += [f"- {n}" for n in r.get("notes", [])]
    return "\n".join(out)


def render_results(r: dict) -> str:
    """lookup_admission_results 결과 → 입결 표"""
    if not r.get("found"):
        return r.get("message", "")
    lines = ["| 학년도 | 모집단위 | 전형 | 모집 | 경쟁률 | 충원 | 50% 컷 | 70% 컷 |", "|---|---|---|---|---|---|---|---|"]
    for x in r["results"]:
        if x["cut_metric"] and "백분위" in x["cut_metric"]:
            c50, c70 = f"{_f(x['cut_50'])}(백분위)", f"{_f(x['cut_70'])}(백분위)"
        elif x["cut_avg"] is not None:
            c50, c70 = f"평균 {_f(x['cut_avg'])}", f"최저 {_f(x['cut_min'])}"
        else:
            c50, c70 = _f(x["cut_50"]), _f(x["cut_70"])
        lines.append(f"| {x['year']} | {x['unit_name']} | {x['track_name']} | {_f(x['quota'])} | {_f(x['competition_rate'])} "
                     f"| {_f(x['added_pass'])} | {c50} | {c70} |")
    srcs = sorted({friendly_source(x["source_doc"]) for x in r["results"]})
    return "\n".join([f"**{r['univ_name']} 입시결과** (컷은 9등급 기준)", "", *lines, "", f"출처: {', '.join(srcs)}",
                      *[f"- {n}" for n in r.get("notes", [])]])


RENDERERS = {"calc_school_gpa": render_gpa, "classify_chances": render_chances, "lookup_admission_results": render_results}


def render_tables(tool_results: list[tuple[str, dict]]) -> str:
    """이번 턴 도구 결과들을 순서대로 표로 만든다 (같은 결과가 두 번이면 한 번만)."""
    parts, seen = [], set()
    for name, result in tool_results:
        fn = RENDERERS.get(name)
        if fn is None:
            continue
        text = fn(result)
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n\n---\n\n".join(parts)


# ---------- 출력 검사 ----------
GUARANTEE = re.compile(r"(무조건|반드시|100 ?%|확실(히|하게)|틀림없이)\s?.{0,8}(합격|붙)|합격(을|이)?\s?보장")
NEGATION = re.compile(r"(보장하지|보장할 수 없|보장되지|않|아닙니다|아니|없습니다|수 없|어렵)")
DISPARAGE = re.compile(r"(전문대|지방대|하위권 ?대학).{0,12}(수준이 낮|급이 낮|못한 (애|학생)|떨어지는 (학교|대학)|가치가 낮)|공부 못하는 (애|학생)들이 가는")
DECIMAL = re.compile(r"(?<![\d.])\d+\.\d+")


def check_output(text: str, allowed_text: str) -> list[str]:
    """LLM 요약의 위반 목록. allowed_text(도구 결과 원문 + 질문)에 없는 소수는 근거 없는 수치로 본다."""
    violations = []
    for m in GUARANTEE.finditer(text):
        window = text[m.start(): m.end() + 30]   # 뒤따르는 부정('…말씀드리기 어렵습니다')까지 본다
        if not NEGATION.search(window):
            violations.append(f"보장 표현: {m.group(0)}")
    if DISPARAGE.search(text):
        violations.append("서열 비하 표현")
    allowed = {abs(float(x)) for x in DECIMAL.findall(allowed_text)}
    unsupported = sorted({x for x in DECIMAL.findall(text) if abs(float(x)) not in allowed})
    if unsupported:
        violations.append(f"근거 없는 수치: {', '.join(unsupported)}")
    return violations


def finalize(summary: str, tool_results: list[tuple[str, dict]], allowed_text: str) -> tuple[str, dict]:
    """LLM 요약 + 코드 결과표 + 고지를 합쳐 최종 답변을 만든다. 반환: (답변, trace용 검사 정보)"""
    tables = render_tables(tool_results)
    violations = check_output(summary, allowed_text)
    if violations:
        body = f"{OUTPUT_TABLE_ONLY}\n\n{tables}" if tables else OUTPUT_FALLBACK
    else:
        body = f"{summary.strip()}\n\n---\n\n{tables}" if tables else summary.strip()
    if tool_results and PREMISE not in body:
        body = f"{body}\n\n{PREMISE}"
    return body, {"violations": violations, "tables": bool(tables), "summary_dropped": bool(violations)}
