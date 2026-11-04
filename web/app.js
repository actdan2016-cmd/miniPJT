// app.js - 화면 공통: 머리말·메뉴, 브라우저 저장, API 호출, DOM 도우미
// 숫자 계산은 하지 않는다. 평균·환산·판정은 서버(/api/*)가 grades.py로 계산한 값을 그대로 보여 준다.

const PAGES = [
  { href: 'index.html', label: '성적 입력', ready: true },
  { href: 'analysis.html', label: '성적 분석', ready: true },
  { href: 'universities.html', label: '지원 가능 대학', ready: true },
  { href: 'improve.html', label: '보강 과목', ready: true },
  { href: 'roadmap.html', label: '학습 로드맵', ready: true },
  { href: 'chat.html', label: '상담', ready: true },
];
const SEMESTERS = ['1-1', '1-2', '2-1', '2-2', '3-1'];
const GROUPS = ['국어', '수학', '영어', '사회', '과학', '한국사', '기타'];
const ACHIEVEMENTS = ['', 'A', 'B', 'C', 'D', 'E'];
const DISCLAIMER = '과거 입결 기반 참고치 · 5→9등급 환산은 근사 · 고1 성적 기반 예측은 현재 추세 유지 가정입니다.';

/** 요소를 만든다. attrs의 on* 은 이벤트, 나머지는 속성. 자식은 문자열(텍스트) 또는 요소. */
function el(tag, attrs = {}, ...children) {
  const node = tag === 'svg' || attrs.svg ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'svg' || v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') node.setAttribute('class', v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** SVG 요소를 만든다. */
function svgEl(tag, attrs = {}, ...children) { return el(tag, { ...attrs, svg: true }, ...children); }

function semLabel(s) { return s.replace('-', '학년 ') + '학기'; }
function fmt(v) { return v === null || v === undefined ? '–' : Number(v).toFixed(2); }

/** 머리말(로고·메뉴)과 고지 띠를 #site-top 에 그린다. current: 현재 화면 파일명 */
function renderHeader(current) {
  const logo = svgEl('svg', { width: 28, height: 28, viewBox: '0 0 24 24', fill: 'none', stroke: '#2650C0', 'stroke-width': 2,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' },
    svgEl('circle', { cx: 12, cy: 12, r: 10 }), svgEl('polygon', { points: '16.2 7.8 14.1 14.1 7.8 16.2 9.9 9.9 16.2 7.8' }));
  const nav = el('nav', { class: 'nav', 'aria-label': '주요 메뉴' },
    PAGES.map((p) => p.ready
      ? el('a', { href: p.href, 'aria-current': p.href === current ? 'page' : null }, p.label)
      : el('span', { title: '아직 준비 중인 화면입니다', 'aria-disabled': 'true' }, p.label)));
  document.getElementById('site-top').replaceChildren(
    el('header', { class: 'site-header' }, el('div', { class: 'wrap' }, el('a', { class: 'brand', href: 'index.html' }, logo, '입시나침반'), nav)),
    el('div', { class: 'disclaimer' }, el('div', { class: 'wrap' }, DISCLAIMER)));
}

/** 출처 원문(내부 검증용: unvCd 등 포함)을 화면용 쉬운 문구로 바꾼다. 원문은 title 속성으로만 남긴다. */
function friendlySource(src, year) {
  if (!src) return '';
  const y = year ? ` · ${year}학년도 입시결과` : '';
  if (src.startsWith('어디가')) return `어디가(대입정보포털)${y}`;
  if (src.startsWith('전문대학포털')) return `전문대학포털${y}`;
  if (src.includes('입학처 발표')) return `${src.split(' ')[0]} 입학처 발표${y}`;
  return src.replace(/\s*\(.*\)\s*$/, '');
}

/** 공통 로딩 표시(스피너). elapsed=true면 경과 초를 함께 보여 준다. 화면에서 빠지면 타이머도 멈춘다. */
function spinner(text, { elapsed = false } = {}) {
  const sec = el('span', { class: 'loading-sec tabular' });
  const node = el('div', { class: 'loading', role: 'status', 'aria-live': 'polite' },
    el('span', { class: 'spinner', 'aria-hidden': 'true' }), el('span', {}, text), elapsed ? sec : null);
  if (elapsed) {
    const start = Date.now();
    const tick = () => {
      if (!node.isConnected && Date.now() - start > 1000) return;  // 화면에서 빠지면 멈춤
      sec.textContent = `${Math.floor((Date.now() - start) / 1000)}초`;
      setTimeout(tick, 1000);
    };
    tick();
  }
  return node;
}

/** 이 브라우저에만 저장하는 학생 프로필(성적·희망 학과). 서버로 보내지 않으며, 저장이 막혀도 화면은 동작한다. */
const Store = {
  key: 'ipsi.profile.v1',
  load() {
    try { return JSON.parse(localStorage.getItem(this.key)) || null; } catch { return null; }
  },
  save(profile) {
    try { localStorage.setItem(this.key, JSON.stringify(profile)); } catch { /* 저장 불가 환경 */ }
  },
};

function emptyProfile() { return { subjects: [], major: '', field: '' }; }

/** 서버 API 호출. 오류면 사용자에게 보여 줄 한국어 메시지를 담은 Error를 던진다. */
async function api(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let res;
  try { res = await fetch(path, opts); } catch { throw new Error('서버에 연결하지 못했습니다. 서버가 켜져 있는지 확인해 주세요.'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? '입력값을 확인해 주세요: ' + d.map((x) => x.msg).join(', ') : (d || `요청 실패 (${res.status})`));
  }
  return data;
}

/** 평균 계산에 보낼 수 있는 과목만 고른다 (학점이 1 이상인 행). */
function validSubjects(subjects) {
  return subjects.filter((s) => Number.isInteger(s.credits) && s.credits >= 1)
    .map((s) => ({ semester: s.semester, subject: s.subject || '(이름 없음)', group: s.group, credits: s.credits,
      grade: s.grade ?? null, achievement: s.achievement || null }));
}
