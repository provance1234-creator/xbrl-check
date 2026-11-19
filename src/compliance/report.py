"""
점검 결과 HTML 리포트.

구성: 요약 지표 → 영역별 막대 → [점검항목별 | 주석별] 두 보기 + 등급·연결/별도·검색 필터.
데이터는 JSON으로 페이지에 넣고 브라우저에서 그린다(외부 라이브러리 없음, 파일 하나로 배포 가능).
검사 내부 이름(checks.py)은 그대로 두고, 화면에는 아래 표시 이름을 쓴다.
"""

import json
import html
from datetime import date

from .common import consep, disp_title, disp_basis

AREA = {
    # 실무 흐름 순서: 요소 선택 → 표 구조 → 요소 속성 → 입력값
    "요소 선택": ("A", "요소 선택"),
    "택사노미 버전": ("B", "택사노미 버전"),
    "표별 전용 요소": ("C", "표별 전용 요소"),
    "축·멤버 구조": ("D", "축·멤버 구조"),
    "표 구조 추가 점검": ("E", "표 구조 추가 점검"),
    "확장요소 속성·라벨": ("F", "확장요소 속성·라벨"),
    "작성가이드 규칙": ("H", "작성가이드 규칙"),
    "입력값": ("G", "입력값"),
}

TITLE = {
    "총액 요소를 순액 자리에 사용": "총액 요소를 순액 자리에 사용",
    "초과적립액(과소적립액) 단독 사용": "초과적립액(과소적립액) 단독 사용",
    "재고자산 표의 장부금액 축 구성": "재고자산 표의 장부금액 축 구성",
    "유동·비유동을 축으로 구분": "유동·비유동을 축으로 구분",
    "같은 뜻 ifrs-full 요소 대신 dart 요소 사용": "같은 뜻 ifrs-full 요소 대신 dart 요소 사용",
    "사용중단 표시 요소": "사용중단 표시 요소",
    "IAS 39 대손충당금 변동표": "IAS 39 대손충당금 변동표",
    "금융자산 손상차손 축(만료 예정)": "금융자산 손상차손 축(만료 예정)",
    "IAS 39 대손충당금 요소": "IAS 39 대손충당금 요소",
    "다른 축 소속 멤버 사용": "다른 축 소속 멤버 사용",
    "공시금액 멤버의 위치": "공시금액 멤버의 위치",
    "현금흐름 표에 표준 외 요소": "현금흐름 표에 표준 외 요소",
    "현금흐름 요소의 다른 표 사용": "현금흐름 요소의 다른 표 사용",
    "판관비 표에 표준 외 요소": "판관비 표에 표준 외 요소",
    "판관비 요소의 다른 표 사용": "판관비 요소의 다른 표 사용",
    "특수관계자 표에 표준 외 요소": "특수관계자 표에 표준 외 요소",
    "특수관계자 요소의 다른 표 사용": "특수관계자 요소의 다른 표 사용",
    "공시금액 입력값의 적정성": "공시금액 열 입력값",
    "공시금액 열에 문장 입력": "공시금액 열에 문장 입력",
    "외화 item의 통화 속성": "외화 item의 통화 속성",
    "확장요소 기간 유형": "확장요소 특정기간/특정시점",
    "확장요소 차변/대변": "확장요소 차변/대변",
    "확장요소 영문명": "확장요소 영문 라벨",
}

LEVEL = {"정정위험": "risk", "오류": "fix", "개선": "imp", "검토필요": "check", "권고": "adv", "실행오류": "fix", "참고": "info"}
RANK = {"risk": 0, "fix": 1, "imp": 2, "check": 3, "adv": 4, "info": 5}  # 정정공시 위험 > 수정 필요 > 개선 권장 > 확인 권장 > 참고

CSS = """
:root{--bg:#f3f5f7;--surface:#fff;--sunk:#eef1f4;--line:#dde2e8;--text:#1c2733;--muted:#5f6b78;
--accent:#0f766e;--accent-soft:#ccfbf1;--fix:#c2410c;--fix-soft:#ffedd5;--check:#a16207;--check-soft:#fef3c7;
--pass:#15803d;--info:#475569;--info-soft:#e2e8f0;--row-info:#e2e8f0;--row-fix:#f7aaaa;--risk:#b91c1c;--risk-soft:#fee2e2;--row-risk:#f87171;--row-chk:#fde68a;--imp:#6d28d9;--imp-soft:#ede9fe;--row-imp:#ddd6fe;--adv:#0369a1;--adv-soft:#e0f2fe;--row-adv:#bae6fd;--con:#334155;--con-soft:#e2e8f0;--sep:#0e7490;--sep-soft:#cffafe}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#12171d;--surface:#1b222a;--sunk:#161c23;
--line:#2c3640;--text:#e4e9ee;--muted:#93a1ae;--accent:#2dd4bf;--accent-soft:#134e4a;--fix:#fb923c;--fix-soft:#431407;
--check:#facc15;--check-soft:#422006;--pass:#4ade80;--info:#94a3b8;--info-soft:#1e293b;--row-info:#334155;--row-fix:#7a2c2c;--risk:#f87171;--risk-soft:#450a0a;--row-risk:#991b1b;--row-chk:#5c4708;--imp:#c4b5fd;--imp-soft:#2e1065;--row-imp:#4c1d95;--adv:#7dd3fc;--adv-soft:#082f49;--row-adv:#075985;--con:#cbd5e1;--con-soft:#334155;--sep:#67e8f9;--sep-soft:#164e63}}
:root[data-theme="dark"]{--bg:#12171d;--surface:#1b222a;--sunk:#161c23;--line:#2c3640;--text:#e4e9ee;--muted:#93a1ae;
--accent:#2dd4bf;--accent-soft:#134e4a;--fix:#fb923c;--fix-soft:#431407;--check:#facc15;--check-soft:#422006;
--pass:#4ade80;--info:#94a3b8;--info-soft:#1e293b;--row-info:#334155;--row-fix:#7a2c2c;--risk:#f87171;--risk-soft:#450a0a;--row-risk:#991b1b;--row-chk:#5c4708;--imp:#c4b5fd;--imp-soft:#2e1065;--row-imp:#4c1d95;--adv:#7dd3fc;--adv-soft:#082f49;--row-adv:#075985;--con:#cbd5e1;--con-soft:#334155;--sep:#67e8f9;--sep-soft:#164e63}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 "Pretendard","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px 48px}
header{background:var(--surface);border-bottom:3px solid var(--accent)}
header .wrap{padding-top:20px;padding-bottom:18px;display:flex;flex-wrap:wrap;gap:8px 24px;align-items:flex-end;justify-content:space-between}
.kicker{font-size:12px;letter-spacing:.08em;color:var(--accent);font-weight:700}
h1{font-size:22px;margin:2px 0 0}
.meta{color:var(--muted);font-size:13px;text-align:right}
.note{margin:16px 0 0;padding:10px 14px;border-left:3px solid var(--accent);background:var(--surface);color:var(--muted);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px}
.kpi .n{font-size:30px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
.kpi .l{color:var(--muted);font-size:13px}
.kpi .of{font-size:16px;font-weight:600;color:var(--muted)}.kpi.risk .n{color:var(--risk)}.kpi.fix .n{color:var(--fix)}.kpi.imp .n{color:var(--imp)}.kpi.check .n{color:var(--check)}.kpi.adv .n{color:var(--adv)}.kpi.info .n{color:var(--info)}.kpi.pass .n{color:var(--pass)}
.toolbar{position:sticky;top:0;z-index:2;background:var(--bg);padding:14px 0 10px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:var(--surface)}
.seg button{border:0;background:none;color:var(--text);padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed="true"]{background:var(--accent);color:#fff}
.toolbar input{flex:1 1 200px;min-width:0;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--text);font:inherit;font-size:13px}
.group{background:var(--surface);border:1px solid var(--line);border-radius:6px;margin-bottom:10px}
.ghead{display:flex;gap:10px;align-items:baseline;padding:10px 14px;cursor:pointer;user-select:none}
.ghead .area{font-weight:700;color:var(--accent);min-width:26px;text-align:right;margin-right:4px}
.ghead .t{font-weight:600;flex:1}
.ghead .basis{color:var(--muted);font-size:12px}
.ghead .cnt{font-variant-numeric:tabular-nums;font-size:13px;white-space:nowrap}
.status{font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px;white-space:nowrap}
.status.risk{background:var(--risk-soft);color:var(--risk);font-weight:700}.status.fix{background:var(--fix-soft);color:var(--fix)}.status.imp{background:var(--imp-soft);color:var(--imp)}.status.check{background:var(--check-soft);color:var(--check)}.status.adv{background:var(--adv-soft);color:var(--adv)}.status.info{background:var(--info-soft);color:var(--info)}
.status.pass{color:var(--pass)}.status.wait{color:var(--muted,#888);border:1px dashed currentColor}
.gbody{display:none;border-top:1px solid var(--line)}
.group.open .gbody{display:block}
.item{display:grid;grid-template-columns:4px 64px 1fr;gap:0 12px;padding:9px 14px 9px 0;border-top:1px solid var(--line)}
.item:first-child{border-top:0}
.item .sv{grid-row:1/4}.item.risk .sv{background:var(--risk)}.item.fix .sv{background:var(--fix)}.item.imp .sv{background:var(--imp)}.item.check .sv{background:var(--check)}.item.adv .sv{background:var(--adv)}.item.info .sv{background:var(--info)}
.item .where{grid-row:1/4;font-size:12px}
.cs{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px;font-weight:600}
.cs.c{background:var(--con-soft);color:var(--con)}.cs.s{background:var(--sep-soft);color:var(--sep)}
.rc{display:block;color:var(--muted);font-family:Consolas,monospace;font-size:11px;margin-top:2px;word-break:break-all}
.el{font-weight:600}.en{color:var(--muted);margin-left:6px}.qn{color:var(--muted);font-family:Consolas,monospace;font-size:11px;word-break:break-all}
.ctx{color:var(--muted);font-size:12px}.why{font-size:13px}
.weak{color:var(--muted);font-size:12px;font-style:italic}
.empty{padding:24px;text-align:center;color:var(--muted)}
.hint{color:var(--muted);font-size:12px;margin:2px 0 8px}
.sbtn{margin-top:4px;border:1px solid var(--accent);color:var(--accent);background:none;border-radius:4px;
padding:1px 8px;font:inherit;font-size:12px;cursor:pointer}.sbtn:hover{background:var(--accent-soft)}
.shade{position:fixed;inset:0;background:rgba(10,20,30,.35);z-index:10;display:none}
.drawer{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:98vw;height:96vh;
background:var(--surface);z-index:11;display:none;flex-direction:column;border-radius:8px;overflow:hidden;
box-shadow:0 12px 40px rgba(0,0,0,.28)}
body.open-drawer{overflow:hidden}.open-drawer .shade{display:block}.open-drawer .drawer{display:flex}
.dhead{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:3px solid var(--accent)}
.dhead .t{flex:1;font-weight:700}.dhead .s{color:var(--muted);font-size:12px;font-weight:400}
.dhead button{border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:4px;padding:4px 12px;font:inherit;cursor:pointer}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);padding:6px 16px;border-bottom:1px solid var(--line)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-2px;margin-right:4px}
.dbody{flex:1;overflow:auto;overscroll-behavior:contain;outline:none;cursor:grab}.dbody.dragging{cursor:grabbing;user-select:none}
table.tx{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:13.5px;zoom:.85}
.tx th{position:sticky;top:0;background:var(--sunk);text-align:left;font-weight:600;padding:6px 10px;
border-bottom:1px solid var(--line);white-space:nowrap;z-index:2}
.tx td{padding:6px 12px;border-bottom:1px solid var(--line);white-space:nowrap;max-width:640px;overflow:hidden;text-overflow:ellipsis}
.tx th:first-child{left:0;z-index:3}
.tx td.tree{position:sticky;left:0;background:var(--surface);z-index:1;padding-right:6px;border-right:1px solid var(--line)}
.tx tr.hrisk td,.tx tr.hrisk td.tree{background:var(--row-risk)}.tx tr.hfix td,.tx tr.hfix td.tree{background:var(--row-fix)}
.tx tr.hchk td,.tx tr.hchk td.tree{background:var(--row-chk)}.tx tr.himp td,.tx tr.himp td.tree{background:var(--row-imp)}.tx tr.hadv td,.tx tr.hadv td.tree{background:var(--row-adv)}
.tx tr.hinf td,.tx tr.hinf td.tree{background:var(--row-info)}
.dhead .scope{border:1px solid var(--accent);color:var(--accent);background:none}
.miss{padding:6px 16px;font-size:13px;color:var(--fix);background:var(--fix-soft);border-bottom:1px solid var(--line)}
.tx .k{display:inline-block;min-width:38px;font-size:10.5px;color:var(--muted);margin-right:4px}
.tx tr.ax td.tree,.tx tr.tb td.tree{font-weight:600}
.tx .mono{font-family:Consolas,monospace;font-size:11.5px;color:var(--muted)}
@media (max-width:640px){.item{grid-template-columns:4px 1fr}
.item .where{grid-row:auto;grid-column:2}.meta{text-align:left}}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const state = {view:'check', level:'all', cs:'all', q:''};
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function visible(f){
  if (state.level !== 'all' && f.lv !== state.level) return false;
  if (state.cs !== 'all' && f.cs !== state.cs) return false;
  if (state.q){ const h = (f.el+' '+(f.en||'')+' '+f.qn+' '+f.why+' '+f.note+' '+f.role+' '+f.rt+' '+f.chk).toLowerCase();
    if (!h.includes(state.q)) return false; }
  return true;
}
function itemHTML(f, showCheck){
  const cs = f.cs ? `<span class="cs ${f.cs==='별도'?'s':'c'}">${f.cs}</span>` : '';
  const ctx = [f.rt, f.tb, f.tbl].filter(Boolean).map(esc).join(' › ');
  return `<div class="item ${f.lv}"><i class="sv"></i><div class="where">${cs}<span class="rc">${esc(f.role)}</span></div>
  <div>${showCheck?`<div class="ctx">${esc(f.area)} · ${esc(f.chk)}</div>`:''}
  <div><span class="el">${esc(f.el)}</span>${f.en && f.en!==f.el?` <span class="en">${esc(f.en)}</span>`:''}</div>
  ${f.qn && f.qn!==f.el?`<div class="qn">${esc(f.qn)}</div>`:''}
  ${ctx?`<div class="ctx">${ctx}</div>`:''}<div class="why">${esc(f.why)}</div>
  ${f.note||f.weak?`<div class="weak">${f.weak?'가이드 직접 근거 약함 · ':''}${esc(f.note)}</div>`:''}
  ${f.sr && D.trees[f.sr] ? `<button class="sbtn" data-sr="${esc(f.sr)}" data-q="${esc(f.qn)}" data-tb="${esc(f.tbq||'')}" data-miss="${esc((f.miss||[]).join(', '))}" data-tq="${esc(f.tq||'')}">구조 보기</button>`:''}</div></div>`;
}
function groupHTML(key, title, sub, items, status, area, showCheck, open){
  const n = items.length;
  const st = status==='pass' ? '<span class="status pass">통과</span>' : status==='wait' ? '<span class="status wait">값 입력 후 판정</span>' :
     `<span class="status ${status}">${{risk:'1단계',fix:'2단계',imp:'3단계',check:'4단계',adv:'5단계',info:'6단계'}[status]}</span>`;
  return `<section class="group${open?' open':''}" data-k="${esc(key)}"><div class="ghead" role="button" tabindex="0">
   <span class="area">${esc(area)}</span><span class="t">${esc(title)}<br><span class="basis">${esc(sub)}</span></span>
   <span class="cnt">${n?n+'건 ':''}${st}</span></div>
   <div class="gbody">${items.map(f=>itemHTML(f, showCheck)).join('') || '<div class="empty">해당 없음</div>'}</div></section>`;
}
const openSet = new Set();
function render(){
  const root = document.getElementById('list');
  let out = '';
  if (state.view === 'check'){
    for (const c of D.checks){
      const items = c.items.filter(visible);
      if ((state.q || state.level !== 'all' || state.cs !== 'all') && !items.length) continue;
      if (!c.items.length && !c.nv) continue;   // 검출 없는 항목은 목록에서 뺀다
      const status = c.items.length ? c.status : 'wait';
      const sub = '근거: '+(/^p\d/.test(c.basis) ? '작성가이드 '+c.basis : c.basis) + (c.nv ? ' · 틀 단계: 값이 들어간 곳만 판정' : '');
      out += groupHTML(c.key, c.title, sub, items, status, c.area, false, openSet.has(c.key));
    }
  } else {
    const by = new Map();
    for (const c of D.checks) for (const f of c.items) if (visible(f)){
      const k = f.role || '(요소 정의·fact)'; if (!by.has(k)) by.set(k, []); by.get(k).push(f); }
    const keys = [...by.keys()].sort((a,b)=> (D.order[a]??9e9) - (D.order[b]??9e9) || a.localeCompare(b));
    for (const k of keys){
      const items = by.get(k); const st = items.some(f=>f.lv==='risk') ? 'risk' : items.some(f=>f.lv==='fix') ? 'fix' : items.some(f=>f.lv==='imp') ? 'imp' : items.some(f=>f.lv==='check') ? 'check' : items.some(f=>f.lv==='adv') ? 'adv' : 'info';
      out += groupHTML('r:'+k, items[0].rt || k, k, items, st, '', true, openSet.has('r:'+k));
    }
  }
  root.innerHTML = out || '<div class="empty">조건에 맞는 항목이 없습니다.</div>';
}
const KIND = {table:'[표]', axis:'[축]', member:'[멤버]', abstract:'[개요]', textblock:'[문장]', item:''};
let LAST = null;
function scopeRange(rows, tbq, tq){
  // 각 행의 부모·가장 가까운 문장영역을 구한 뒤, 검출된 표를 감싼 문장영역 세트(없으면 [개요] 세트)를 범위로 잡는다
  const parent = [], tbOf = [], st = [];
  rows.forEach((r, i) => {
    while (st.length && rows[st[st.length-1]][0] >= r[0]) st.pop();
    parent[i] = st.length ? st[st.length-1] : -1;
    let p = parent[i]; while (p >= 0 && rows[p][2] !== 'textblock') p = parent[p];
    tbOf[i] = p; st.push(i);
  });
  let root = -1;
  if (tq){
    const ai = rows.findIndex((r, i) => r[1] === tq && (!tbq || (tbOf[i] >= 0 && rows[tbOf[i]][1] === tbq)));
    if (ai >= 0){ const t = tbOf[ai]; root = (t >= 0 && rows[t][0] > 0) ? t : (parent[ai] >= 0 ? parent[ai] : ai); }
  } else if (tbq){
    const ti = rows.findIndex(r => r[1] === tbq && r[2] === 'textblock');
    if (ti >= 0 && rows[ti][0] > 0) root = ti;
  }
  if (root < 0) return null;
  let end = root + 1; while (end < rows.length && rows[end][0] > rows[root][0]) end++;
  return [root, end];
}
function openTree(rc, q, tbq, miss, tq, full){
  const T = D.trees[rc]; if (!T) return;
  LAST = [rc, q, tbq, miss, tq];
  const hits = D.hits[rc] || {};
  const range = full ? null : scopeRange(T.rows, tbq, tq);
  const [from, to] = range || [0, T.rows.length];
  const base = T.rows[from][0];
  const stack = [];
  let selDone = false, html = '';
  T.rows.forEach((r, i) => {
    const [d, id, kind, dko, den, bko, ben] = r;
    while (stack.length && stack[stack.length-1][0] >= d) stack.pop();
    const tb = stack.length ? stack[stack.length-1][1] : '';
    if (kind === 'textblock') stack.push([d, id]);
    if (i < from || i >= to) return;
    const hit = hits[tb+'|'+id] || hits['|'+id];
    const sel = id === q && (!tbq || tb === tbq || range) && !selDone; if (sel) selDone = true;
    const cls = [hit==='risk'?'hrisk':hit==='fix'?'hfix':hit==='imp'?'himp':hit==='adv'?'hadv':hit==='info'?'hinf':(hit?'hchk':''), kind==='axis'?'ax':'', kind==='table'?'tb':''].join(' ');
    const dd = d - base;
    html += `<tr class="${cls}" ${sel?'id="selrow"':''}><td class="tree" style="padding-left:${10+dd*16}px" title="${esc(dko)}">${dd?'└ ':''}<span class="k">${KIND[kind]||''}</span></td><td title="${esc(dko)}">${esc(dko)}</td>
     <td class="mono">${esc(id.split(':')[0])}</td><td title="${esc(den)}">${esc(den)}</td><td class="mono" title="${esc(id)}">${esc(id.replace(':','_'))}</td>
     <td title="${esc(bko)}">${esc(bko)}</td><td title="${esc(ben)}">${esc(ben)}</td></tr>`;
  });
  document.getElementById('dt').textContent = T.title;
  document.getElementById('ds').textContent = rc + (range ? ` · 해당 표 ${to-from}행 (주석 전체 ${T.rows.length}행)` : ` · 주석 전체 ${T.rows.length}행`);
  const sw = document.getElementById('dsw');
  sw.style.display = (range || !full) && scopeRange(T.rows, tbq, tq) ? '' : 'none';
  sw.textContent = range ? '주석 전체 보기' : '해당 표만 보기';
  sw.onclick = () => openTree(LAST[0], LAST[1], LAST[2], LAST[3], LAST[4], !!range);
  const mz = document.getElementById('dm');
  mz.textContent = miss ? '구조에 없는(누락된) 요소: ' + miss : ''; mz.style.display = miss ? '' : 'none';
  document.getElementById('db').innerHTML = `<table class="tx"><thead><tr><th></th><th>표시 한글명</th><th>접두어</th><th>표시 영문명</th><th>요소 ID</th><th>기본 한글명</th><th>기본 영문명</th></tr></thead><tbody>${html}</tbody></table>`;
  document.body.classList.add('open-drawer');
  const db = document.getElementById('db'); db.focus({preventScroll:true}); db.scrollTo(0, 0);
  const el = document.getElementById('selrow'); if (el) el.scrollIntoView({block:'center'});
}
function closeTree(){ document.body.classList.remove('open-drawer'); }
// 구조 표: 빈 곳을 잡고 끌면 상하좌우로 이동
(() => {
  const db = document.getElementById('db'); let drag = null;
  db.addEventListener('mousedown', e => { if (e.button !== 0) return;
    drag = {x:e.clientX, y:e.clientY, l:db.scrollLeft, t:db.scrollTop, moved:false}; });
  window.addEventListener('mousemove', e => { if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    drag.moved = true; db.classList.add('dragging');
    db.scrollLeft = drag.l - dx; db.scrollTop = drag.t - dy; e.preventDefault(); });
  window.addEventListener('mouseup', () => { drag = null; db.classList.remove('dragging'); });
})();
document.getElementById('dc').addEventListener('click', closeTree);
document.querySelector('.shade').addEventListener('click', closeTree);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTree(); });
document.getElementById('list').addEventListener('click', e => {
  const sb = e.target.closest('.sbtn');
  if (sb){ openTree(sb.dataset.sr, sb.dataset.q, sb.dataset.tb, sb.dataset.miss, sb.dataset.tq, false); return; }
  const h = e.target.closest('.ghead'); if (!h) return;
  const g = h.parentElement; g.classList.toggle('open');
  g.classList.contains('open') ? openSet.add(g.dataset.k) : openSet.delete(g.dataset.k);
});
document.getElementById('list').addEventListener('keydown', e => {
  if ((e.key==='Enter'||e.key===' ') && e.target.classList.contains('ghead')){ e.preventDefault(); e.target.click(); }
});
document.querySelectorAll('.seg').forEach(seg => seg.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x===b));
  state[seg.dataset.f] = b.dataset.v; render();
}));
document.getElementById('q').addEventListener('input', e => { state.q = e.target.value.trim().toLowerCase(); render(); });
render();
"""


def disp_label(r, std, q, lang, role):
    """표시명: 회사 라벨(지정 역할) → 표준 라벨(지정 역할) → 기본 라벨."""
    for l, rl, t in r.all_labels.get(q, []):
        if l == lang and rl == role:
            return t
    t = std.labels_all.get((q, lang, role)) if std else None
    if t:
        return t
    if role != "label":
        return disp_label(r, std, q, lang, "label")
    return ""


def build_tree(r, rc):
    """role 코드의 표시(pre) 구조 → [깊이, 요소, 종류, 표시 한글명, 표시 영문명, 기본 한글명, 기본 영문명]"""
    std = r.std
    rows = []
    for u in [u for u in r.uris() if r.role_code(u) == rc]:
        tree = r.pre[u]
        kids = {ch for lst in tree.values() for _, ch, _ in lst}

        def walk(q, d, pl, seen):
            role = (pl or "").rsplit("/", 1)[-1] or "label"
            rows.append([d, q, r.kind(q), disp_label(r, std, q, "ko", role), disp_label(r, std, q, "en", role),
                         disp_label(r, std, q, "ko", "label"), disp_label(r, std, q, "en", "label")])
            if q in seen:
                return
            for _, ch, cpl in tree.get(q, []):
                walk(ch, d + 1, cpl, seen | {q})

        for root in [p for p in tree if p not in kids]:
            walk(root, 0, None, frozenset())
    return rows


def build_data(r, results):
    checks, order = [], {}
    for rc, title in r.note_title.items():
        import re
        m = re.match(r"\s*(\d+)(?:-(\d+))?", title or "")
        if m:
            order[rc] = int(m.group(1)) * 100 + int(m.group(2) or 0) + (0.5 if rc.endswith("5") else 0)
    for no, res in enumerate(results, 1):   # 점검항목 번호 1~N (표시 순서)
        _, area_name = AREA.get(res["section"], ("", res["section"]))
        code = str(no)
        items = []
        for x in sorted(res["findings"], key=lambda f: (f.role_code, f.table, f.element)):
            is_role = x.role_code[:1] in "DU"
            lv = LEVEL.get(x.level, "check")
            items.append(dict(
                lv=lv, cs=consep(x.role_code) if is_role else "", role=x.role_code if is_role else "",
                rt=r.role_label(x.role_code) if is_role else "",
                tb=r.label(x.parent) if x.parent else "",
                tbl=r.label(x.table) if x.table and not x.table.startswith("(") else "",
                el=r.label(x.element), qn=x.element,
                en=(r.std.label_en.get(x.element) if r.std else None) or r.label_en.get(x.element, ""), why=x.detail, note=x.extra.get("note", ""),
                weak=x.extra.get("basis") == "약함",
                area=code, chk=disp_title(TITLE.get(res["card"], res["card"])),
                sr=x.role_code if is_role else "", tbq=x.parent or "",
                tq=x.table if x.table and not x.table.startswith("(") else "",
                marks=x.extra.get("marks", []), miss=x.extra.get("missing", [])))
        status = min((i["lv"] for i in items), key=lambda v: RANK.get(v, 9), default="info")
        checks.append(dict(key=disp_title(res["card"]), title=disp_title(TITLE.get(res["card"], res["card"])), area=code,
                           area_name=area_name, basis=disp_basis(res["guide"]), status=status, items=items,
                           grade=LEVEL.get(res.get("grade"), status),
                           nv=bool(res.get("needs_values"))))
    # 구조 보기: 검출이 있는 role만 싣는다. fact 단위 검출은 그 요소가 처음 나오는 role로 연결
    elem_role = {}
    for u in r.uris():
        rc = r.role_code(u)
        for par, lst in r.pre[u].items():
            for _, ch, _ in lst:
                elem_role.setdefault(ch, rc)
    trees, hits = {}, {}
    for c in checks:
        for i in c["items"]:
            if not i["sr"]:
                i["sr"] = elem_role.get(i["qn"], "")
            rc = i["sr"]
            if not rc:
                continue
            if rc not in trees:
                rows = build_tree(r, rc)
                if not rows:
                    i["sr"] = ""
                    continue
                trees[rc] = dict(title=r.role_label(rc), rows=rows)
            h = hits.setdefault(rc, {})
            # 검출된 문장영역 안에서만 강조(문장영역 정보가 없으면 role 전체).
            # 표 단위 검출은 표 안에서 잘못 쓴 요소(marks)를 칠하고, 표 행은 위치 표시(ctx)만 한다
            for q in i["marks"] or [i["qn"]]:
                k = f"{i['tbq']}|{q}"
                if k not in h or RANK.get(i["lv"], 9) < RANK.get(h[k], 9):
                    h[k] = i["lv"]
    return dict(checks=checks, order=order, trees=trees, hits=hits)


def write_html(r, results, path, tool_name="XBRL 작성기준 점검"):
    e = html.escape
    data = build_data(r, results)
    # 요약: 칸마다 "검출된 점검항목 수 / 그 단계 점검항목 총수"(검출 없어도 표시)와 그 단계로 표시된 검출 건수.
    #  항목의 단계는 기본 단계(card grade), 검출은 건별 표시 단계(item lv) 기준 — 목록의 단계 필터와 같은 건수
    G = (("risk", "1단계"), ("fix", "2단계"), ("imp", "3단계"), ("check", "4단계"), ("adv", "5단계"), ("info", "6단계"))
    kp = {}
    for k, _ in G:
        cards = [c for c in data["checks"] if any(i["lv"] == k for i in c["items"])]
        kp[k] = (len(cards), sum(1 for c in data["checks"] if c["grade"] == k),
                 sum(1 for c in cards for i in c["items"] if i["lv"] == k))
    kpi_html = "".join(f'<div class="kpi {k}"><div class="n">{kp[k][0]}<span class="of"> / {kp[k][1]}</span></div>'
                       f'<div class="l">{nm} 항목 · {f"{kp[k][2]}건 검출" if kp[k][2] else "검출 없음"}</div></div>' for k, nm in G)
    n_wait = sum(1 for c in data["checks"] if not c["items"] and c["nv"])   # 틀 검사: 값이 없어 판정 못 한 항목
    n_pass = sum(1 for c in data["checks"] if not c["items"]) - n_wait
    n_all = len(data["checks"])
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XBRL 작성기준 점검</title><style>{CSS}</style></head><body>
<header><div class="wrap"><div><div class="kicker">{e(tool_name)}</div>
<h1>{e(r.corp_name)} · {e(r.period)}</h1></div>
<div class="meta">점검일 {date.today().isoformat()}<br>근거: 금융감독원 DART XBRL 작성 가이드(2026), DART 택사노미</div></div></header>
<div class="wrap">
<p class="note">자동 점검 결과이며 <b>금융감독원의 공식 판단이 아닙니다.</b> 단계는 먼저 볼 순서를 정하려고 작성 가이드와 금감원 설명회 교재에 적힌 내용을 근거로 나눈 것입니다. <b>1단계</b> 설명회 교재 등에 정정공시 사례·요구로 나온 유형 · <b>2단계</b> 가이드에 의무(~하여야 함)로 적혀 있고 설명회에서도 다룬 것 · <b>3단계</b> 가이드에 의무로 적혀 있으나 설명회 교재에는 따로 언급이 없는 것 · <b>4단계</b> 추정으로 걸렸거나 공시 내용에 따라 판단이 달라 사람이 확인해야 하는 것 · <b>5단계</b> 가이드가 권고(적절·권장·할 수 있음)로 적은 것 · <b>6단계</b> 참고 안내.
근거의 쪽수(p)는 금융감독원 「DART XBRL 재무제표 본문·주석 작성 가이드(2026)」의 인쇄 쪽 번호입니다.</p>
<div class="kpis">
{kpi_html}</div>
<p class="hint">칸의 숫자는 "검출된 점검항목 수 / 그 단계 점검항목 수"이고, 아래는 그 항목들에서 그 단계로 표시된 검출 건수(문제가 된 요소·위치를 하나씩 센 수)입니다. 한 항목에서 여러 건이 걸릴 수 있고, 같은 항목이라도 건마다 단계가 다를 수 있습니다(예: 추정으로 걸린 건은 4단계, 권고에 해당하는 건은 5단계). 전체 점검항목 {n_all}개.{f" 값 입력 후 판정 {n_wait}." if n_wait else ""}</p>
{f'<p class="note"><b>틀 검사 모드</b> — 숫자를 넣기 전 표 구성(표·축·멤버·행)을 점검한 결과입니다. 입력값을 보고 판정하는 점검 {sum(1 for c in data["checks"] if c["nv"])}개는 값이 들어간 곳만 판정했고, 그중 {n_wait}개는 아직 판정할 값이 없어 "값 입력 후 판정"으로 표시했습니다. 숫자 입력 후 일반 점검으로 다시 확인하세요.</p>' if any(c["nv"] for c in data["checks"]) else ''}
<div class="toolbar">
<div class="seg" data-f="view"><button data-v="check" aria-pressed="true">점검항목별</button><button data-v="role" aria-pressed="false">주석별</button></div>
<div class="seg" data-f="level"><button data-v="all" aria-pressed="true">전체</button><button data-v="risk" aria-pressed="false">1단계</button><button data-v="fix" aria-pressed="false">2단계</button><button data-v="imp" aria-pressed="false">3단계</button><button data-v="check" aria-pressed="false">4단계</button><button data-v="adv" aria-pressed="false">5단계</button><button data-v="info" aria-pressed="false">6단계</button></div>
<div class="seg" data-f="cs"><button data-v="all" aria-pressed="true">연결+별도</button><button data-v="연결" aria-pressed="false">연결</button><button data-v="별도" aria-pressed="false">별도</button></div>
<input id="q" type="search" placeholder="요소명·주석·내용 검색">
</div>
<p class="hint">항목을 누르면 상세가 펼쳐지고, <b>구조 보기</b>를 누르면 해당 표의 택사노미 표시 구조가 열립니다(진한 빨강 = 1단계, 빨강 = 2단계, 보라 = 3단계, 노랑 = 4단계, 파랑 = 5단계).</p>
<div id="list"></div></div>
<div class="shade"></div>
<aside class="drawer" aria-label="택사노미 구조"><div class="dhead"><span class="t"><span id="dt"></span><br><span class="s" id="ds"></span></span>
<span class="s">Shift + 마우스 휠: 좌우 스크롤 · 마우스로 끌기: 이동</span>
<button id="dsw" class="scope"></button>
<button id="dc">닫기 (Esc)</button></div>
<div class="legend"><span><i style="background:var(--row-risk)"></i>1단계</span><span><i style="background:var(--row-fix)"></i>2단계</span><span><i style="background:var(--row-imp)"></i>3단계</span><span><i style="background:var(--row-chk)"></i>4단계</span><span><i style="background:var(--row-adv)"></i>5단계</span><span><i style="background:var(--row-info)"></i>6단계</span></div>
<div class="miss" id="dm" style="display:none"></div>
<div class="dbody" id="db" tabindex="-1"></div></aside>
<script type="application/json" id="data">{json.dumps(data, ensure_ascii=False).replace('</', '<\\/')}</script>
<script>{JS}</script></body></html>"""
    path.write_text(page, encoding="utf-8")
