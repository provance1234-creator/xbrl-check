"""
XBRL 작성 적정성 검사 본체.

검사 1건 = 함수 1개. 모두 (r: Report, std: Std) -> [Finding].
CHECKS 목록의 순서가 리포트의 섹션·카드 순서다.
    basis : 가이드 근거 강도 — "명확" | "약함" | "택사노미"
    pages : 가이드 인쇄 쪽(=PDF쪽-28), rules.csv 규칙 ID
    마지막 값 : 내부 호환용(사용하지 않음)
섹션: 요소 선택·택사노미 버전·축·멤버 구조·표별 전용 요소·입력값(기본 점검), 표 구조 추가 점검, 확장요소 속성·라벨(정정공시 사례 대응), 작성가이드 규칙.
"""

import re
from collections import defaultdict

from .common import (Finding, CARRYING_AXIS, REPORTED_MEMBER, CONSEP_AXIS, CONSEP_TABLE,
                     name_kind)
from .guide_checks import NI_RE, DEDUCT_MEMBERS, _dims as _dims_wo_consep

S1, S2, S3, S4, S5, S6, S7 = ("요소 선택", "택사노미 버전", "축·멤버 구조",
                              "표별 전용 요소", "입력값", "표 구조 추가 점검",
                              "확장요소 속성·라벨")
ERR, REV, INFO = "오류", "검토필요", "참고"
# 4단계 = 정정공시 위험 > 수정 필요 > 확인 권장 > 참고. '정정공시 위험'은 실제 정정공시로 이어진 유형만
#  — 유동/비유동을 열(축)로 설계, 외화속성 미사용. 정정 요청이 없던 유형은 수정 필요 이하.
RISK = "정정위험"
RISK_CARDS = {"유동·비유동을 축으로 구분"}  # 카드의 '수정 필요' 건 전부를 정정공시 위험으로. 카드 일부만이면 Finding.extra["risk"]=True
# 2026 하반기 금감원 설명회 교재가 정정공시·수정 공시를 요구한 유형 중 일부를 정정공시 위험(1단계)으로 둔다.
RISK_CARDS |= {"재고자산 표의 장부금액 축 구성", "IAS 39 대손충당금 변동표", "IAS 39 대손충당금 요소"}
RISK_IDS = {"G5C-001", "G5C-010", "G5B-061", "G5C-050", "G5-002", "G4-021", "G5B-059", "G5B-008"}   # G5-029는 수정 필요(교재에 직접 언급 없음, 가이드 p115 의무)
# 5단계 = 정정공시 위험 > 수정 필요 > 개선 권장 > 확인 권장 > 참고
#  기준 = 금감원 강조 강도. 정정공시 위험: 교재 ① 정정공시 언급 / 수정 필요: 교재 ② 오류 사례 또는 교재 ③ + 가이드 의무 문구
#  개선 권장: 가이드 의무 문구(모범사례 포함)이나 교재 언급 없음 / 확인 권장: 가이드 권고 문구 또는 판정 불확실
# 중요계정 확장은 2단계로(교재 54~55쪽의 정정 대상은 표준계정 준수율·영문 계정과목 오류이지 개별 계정 확장이 아님).
#  유동/비유동 축·외화속성 미지정은 교재에 정정 문구는 없으나 실제 정정공시 사례가 근거 → 1단계 유지.
#  총액요소 오용(교재 33쪽 정정 대상 메일 3번)·확장요소 영문명(55쪽)은 2단계 유지.
RISK_IDS |= {"G5C-018", "G5B-056"}
IMP = "개선"
IMPROVE_CARDS = {"같은 축 아래 동일 멤버 중복", "공시금액 멤버의 위치",
                 "같은 뜻 ifrs-full 요소 대신 dart 요소 사용"}   # 가이드 p79 '우선 적용함'(의무 문구), 위반이 아닌 권장 성격
IMPROVE_IDS = {
    "G4-001", "G4-010", "G4-011", "G4-013", "G4-015", "G4-019", "G4-027", "G4-029", "G4-030", "G4-032",
    "G5-019", "G5-024", "G5-031", "G5-033", "G5-039",
    "G5B-003", "G5B-004", "G5B-005", "G5B-006", "G5B-010", "G5B-013", "G5B-017", "G5B-018", "G5B-019", "G5B-021",
    "G5B-033", "G5B-043", "G5B-053", "G5B-054",
    "G5C-002", "G5C-017", "G5C-020", "G5C-024", "G5C-026", "G5C-027", "G5C-028", "G5C-029", "G5C-040", "G5C-047",
    "G5C-051", "G5C-053", "G5C-054", "G5C-055", "G5C-057", "G5C-067", "G5C-073", "G5C-074", "G5C-079", "G5C-080",
    "G5C-081", "G5C-083"}
# 가이드 문구가 권고·검토 수준 → '수정 필요' 건도 확인 권장으로(G5-026: 확장 목차도 쓸 수 있음)
REVIEW_IDS = {"G4-012", "G4-026", "G4-036", "G5-025", "G5-026", "G5C-013", "G5C-070", "G5C-077"}
# 화면 표시는 이름 대신 6단계 숫자. 가이드가 권고로 적은 것(적절·권장·할 수 있음)은 '가이드 권고'(ADV, 5단계)로 따로 —
#  추정으로 걸려 사람 확인이 필요한 건(REV, 4단계)과 받는 사람이 할 일이 다르다.
#  (G5C-061은 권고 문구지만 설명회 교재 PDF 96쪽이 민감도 표 가이드 미준수를 다뤄 2단계, 건별 '약함'도 뺌)
ADV = "권고"
ADVICE_IDS = {"G4-010", "G4-012", "G4-029", "G4-036", "G5-019", "G5-024", "G5-025", "G5-026", "G5C-054", "G5C-070", "G5C-077"}
# 참고 등급: 가이드 권고지만 작성 여건상 피하기 어려운 것 — 결과에는 남기되 수정·확인 대상 아님
REF_CARDS = {"G5B-022"}   # G4-026(본문·주석 단위)은 설명회에서 언급했으나 정정공시 가능성은 거의 없는 권고 → 참고에서 확인 권장(4단계, REVIEW_IDS)


def card_grade(card, level):
    """카드의 기본 등급(리포트 요약 '등급별 카드 수'용): 정정위험/오류/개선/검토필요/참고.
    표준 축-멤버 카드는 기본이 확인 권장이나 대체 표준멤버가 분명한 건은 수정 필요라 수정 필요 카드로 센다(분류안)."""
    cid = card.split(" ", 1)[0]
    if card in RISK_CARDS or cid in RISK_IDS:
        return RISK
    if cid in REF_CARDS or level == INFO:
        return INFO
    if cid in ADVICE_IDS:
        return ADV
    if card in IMPROVE_CARDS or cid in IMPROVE_IDS:
        return IMP
    if card == "다른 축 소속 멤버 사용":
        return ERR
    if cid in REVIEW_IDS or level == REV:
        return REV
    return ERR


# 리포트·CSV에 보이는 구분 이름: 판정처럼 읽히는 이름 대신 근거를 적는다 — 금감원 공식 판단이 아니므로.
#  내부 이름(grade_name: 정정공시 위험·수정 필요·개선 권장·확인 권장·참고)은 내부에서 그대로 쓴다.
#  근거 이름도 오해 소지 → 숫자 단계만 표시. 1 정정 사례 / 2 가이드 의무 + 설명회에서 다룸 / 3 가이드 의무(설명회 언급 없음)
#  / 4 추정으로 걸려 사람 확인 필요 / 5 가이드 권고 / 6 참고
GRADE_LABEL = {"정정공시 위험": "1단계", "수정 필요": "2단계", "개선 권장": "3단계", "확인 권장": "4단계", "가이드 권고": "5단계", "참고": "6단계"}
GRADE_FROM_LABEL = {v: k for k, v in GRADE_LABEL.items()}


def grade_label(level):
    """화면·CSV 표시용 이름."""
    return "실행 오류" if level == "실행오류" else GRADE_LABEL[grade_name(level)]


def grade_name(level):
    return {"정정위험": "정정공시 위험", "오류": "수정 필요", "실행오류": "수정 필요", "개선": "개선 권장", "권고": "가이드 권고",
            "참고": "참고"}.get(level, "확인 권장")

GROSS_MEMBER = "ifrs-full:GrossCarryingAmountMember"
CARRYING_MEMBER = "ifrs-full:CarryingAmountMember"
INV_ALLOWANCE_MEMBER = "dart:AllowanceForInventoryValuationMember"
SURPLUS = "ifrs-full:SurplusDeficitInPlan"
# 퇴직연금운용자산(dart 차감계정)도 사외적립자산으로 본다(G5C-045, p251)
PLAN_ASSETS = {"ifrs-full:PlanAssetsAtFairValue", "dart:FairValueOfPlanAssets",
               "dart:InvestedAssetForPostemploymentBenefitContraAccount"}
DBO = {"ifrs-full:DefinedBenefitObligationAtPresentValue", "dart:PresentValueOfDefinedBenefitObligation"}
CUR_NONCUR_AXIS = "dart:ClassesOfCurrentAndNoncurrentValuesUsedForChangesInAssetAndLiabilityAxis"
IMPAIRMENT_FA_AXIS = "ifrs-full:ImpairmentOfFinancialAssetsAxis"
GROUPS = {"현금흐름": "현금흐름", "판매비와관리비": "판매비", "특수관계자": "특수관계"}


def std_items(std, q):
    # 블록태깅(문장영역) 요소는 검증 대상 아님(ixd에 XBRL로 상세 태깅된 것만 검증)
    return std.is_std(q) and std.kind(q) == "item"


def scope_of(table, role_code):
    return table or f"(role {role_code} 전체)"


# ---------------------------------------------------------------------------
# 요소 선택
# ---------------------------------------------------------------------------

CONTRA_RE = re.compile(r"^(Accumulated|Allowance|PresentValueDiscount|Provision|GovernmentGrant|"
                       r"Impairment|Deferred|Discount|Unamortised)")
CONTRA_LABEL_RE = re.compile(r"(대손충당금|손실충당금|현재가치할인차금|감가상각누계액|상각누계액|손상차손누계액|"
                             r"평가충당금|충당금|정부보조금|국고보조금|할인차금|사채할인발행차금)")


def is_gross(std, q):
    lab = std.label_ko.get(q, "")
    return std.is_std(q) and (q.split(":")[-1].endswith("Gross") or re.search(r"(,\s*|\s)총액$", lab))         and not CONTRA_RE.match(q.split(":")[-1]) and not CONTRA_LABEL_RE.search(lab)


def is_contra(r, q):
    return bool(CONTRA_RE.match(q.split(":")[-1]) or CONTRA_LABEL_RE.search(r.label(q) or ""))


def _net_only_role(rc):
    """총액 요소 단독 사용을 '수정 필요'로 보는 곳: 본문 재무상태표(D2)와 장부금액만 공시하는 표준 표(범주별 금융상품 D82243x).
    그 밖의 주석 표는 총액 공시가 의도일 수 있어 확인 권장(G4-017, p81-82·135)."""
    return bool(re.match(r"D2\d{5}$", rc) or rc.startswith("D82243"))


def check_gross(r, std):
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        scopes = defaultdict(set)
        tb_of = {}
        for n, p, tb, t, pl in r.role_nodes(u):
            if r.kind(n) == "item":
                scopes[t].add(n)
                tb_of[(t, n)] = tb
        tables = r.tables(u)
        # 같은 목차에서 차감계정과 함께 쓰인 총액 요소(확인 권장 문구에 알림)
        with_contra = {q for items in scopes.values() if any(is_contra(r, x) for x in items) for q in items}
        for t, items in scopes.items():
            gross = [q for q in items if is_gross(std, q)]
            if not gross:
                continue
            has_contra = any(is_contra(r, q) for q in items)
            has_axis = t in tables and CARRYING_AXIS in tables[t]["axes"]
            for g in gross:
                net = net_of_gross(std, g)
                # 순액 대응 표준요소가 없으면(예: 장기선수금, 총액) 바꿔 쓸 요소가 없어 확인 권장
                basis = "명확" if net else "약함"
                if has_axis:
                    why = "장부금액 축 아래 공시금액·장부금액 열에는 순액 행을 사용(작성가이드 p135)"
                elif not has_contra:
                    why = "차감계정 없이 총액 요소 단독 사용(순액 공시면 순액 요소 사용)"
                    if not _net_only_role(rc):
                        basis = "약함"
                        if g in with_contra:
                            why += " — 같은 목차의 다른 표에서는 차감계정과 함께 사용"
                else:
                    continue
                out.append(Finding(S1, "총액 요소를 순액 자리에 사용", ERR, rc, g, why + (f" → {net}" if net else " — 순액 대응 표준요소 없음"),
                                   table=scope_of(t, rc), parent=tb_of.get((t, g)) or "",
                                   extra={"basis": basis}))
    return out


def net_of_gross(std, q):
    """총액 요소에 대응하는 순액 표준요소(…Net, 'X, 순액', 같은 이름 'X'). 없으면 None."""
    p, n = q.split(":", 1)
    base = n[:-5] if n.endswith("Gross") else n
    for c in (f"{p}:{base}Net", f"{p}:{base}", f"ifrs-full:{base}", f"dart:{base}Net", f"dart:{base}"):
        if c != q and c in std.concepts and std.kind(c) == "item" and c not in std.deprecated:
            return c
    # 라벨: 'X, 순액' 또는 'X'(공백 무시). 장단기와 유동·비유동은 다른 개념이라 단기매출채권(총액)을 유동 매출채권으로 잇지 않는다
    core = re.sub(r"\s", "", re.sub(r"(,\s*|\s)총액$", "", std.label_ko.get(q, "")))
    want = {core, core + ",순액"}
    for c, l in std.label_ko.items():
        if c != q and re.sub(r"\s", "", l) in want and std.kind(c) == "item" and c not in std.deprecated:
            return c
    return None


def check_surplus(r, std):
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        scopes = defaultdict(set)
        for n, p, tb, t, pl in r.role_nodes(u):
            scopes[t].add(n)
        for t, items in scopes.items():
            if SURPLUS not in items:
                continue
            if rc.startswith("D2"):
                why = "재무상태표에서 순확정급여부채(자산) 대신 초과적립액(과소적립액) 사용"
            elif not (items & PLAN_ASSETS and items & DBO):
                why = "사외적립자산 공정가치·확정급여채무 현재가치 없이 단독 사용"
            else:
                continue
            out.append(Finding(S1, "초과적립액(과소적립액) 단독 사용", REV, rc, SURPLUS, why, table=scope_of(t, rc)))
    return out


FUNC_ALLOC_AXIS = "ifrs-full:AttributionOfExpensesByNatureToTheirFunctionAxis"


def inventory_roles(r, std):
    codes = {rc for rc, t in std.role_title.items() if "재고자산" in t}
    return [u for u in r.uris() if r.role_code(u) in codes or "재고자산" in (r.roles.get(u, ("", ""))[1] or "")]


def check_inventory(r, std):
    out = []
    inv_uris = set(inventory_roles(r, std))
    for u in r.uris():
        rc = r.role_code(u)
        for t, info in r.tables(u).items():
            if t == CONSEP_TABLE:
                continue
            inv_items = [q for q in info["items"] if "Inventor" in q or
                         re.search(r"(RawMaterials|FinishedGoods|WorkInProgress|Merchandise|Supplies)", q)]
            if u not in inv_uris and not any(q.endswith("Gross") for q in inv_items):
                continue
            members = set(info["axes"].get(CARRYING_AXIS, []))
            detail_table = "DetailsOfInventories" in t
            if detail_table:
                # 가이드 p160~161: 행=재고 종류, 열=장부금액 축(총장부금액·평가충당금·장부금액)
                need = (("장부금액 축", CARRYING_AXIS in info["axes"]), ("총장부금액 멤버", GROSS_MEMBER in members),
                        ("평가충당금 멤버", INV_ALLOWANCE_MEMBER in members))
                guide = "작성가이드 p160~161 모범사례"
            else:
                # 가이드 p164~165: 평가손실·비용인식 원가 등은 행으로 두고 장부금액 축 + 공시금액 열
                need = (("장부금액 축", CARRYING_AXIS in info["axes"]), ("공시금액 멤버", REPORTED_MEMBER in members))
                guide = "작성가이드 p164~165 모범사례(평가손실·비용인식 원가는 장부금액 축 + 공시금액 열)"
            missing = [x for x, ok in need if not ok]
            if not missing:
                continue
            wrong = [a for a in info["axes"] if a not in (CARRYING_AXIS, CONSEP_AXIS)]
            marks = [x for a in wrong for x in [a] + list(info["axes"][a])]
            why = "누락: " + ", ".join(missing) + (f" / 대신 사용한 축: {', '.join(r.label(a) for a in wrong)}" if wrong else "")
            basis = "명확"
            # 비용인식·평가손실 표에 택사노미가 그 표에 정의한 기능별 배분 축만 쓴 경우 확인 권장
            if not detail_table and wrong and set(wrong) <= {FUNC_ALLOC_AXIS}:
                basis = "약함"
                why += " — 택사노미가 이 표에 정의한 축이지만 모범사례는 장부금액 축 + 공시금액 열"
            out.append(Finding(S1, "재고자산 표의 장부금액 축 구성", ERR, rc, t, why, table=t, parent=info["parent_tb"] or "",
                               extra={"basis": basis, "note": guide, "marks": marks, "missing": missing}))
    return out


MOVE_RE = re.compile(r"(IncreaseDecrease|Additions|Disposals|Transfers|Reclassification|ChangesIn)")


def is_cur_noncur_axis(r, ax):
    lab = r.label(ax)
    return ax == CUR_NONCUR_AXIS or "CurrentAndNoncurrent" in ax or "CurrentAndNonCurrent" in ax or \
        ("유동" in lab and "비유동" in lab)


def check_cur_noncur(r, std):
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        for t, info in r.tables(u).items():
            axes = [ax for ax in info["axes"] if is_cur_noncur_axis(r, ax)]
            # 확장 유동/비유동 멤버(다른 축 아래)도 같이 본다
            for ax, ms in info["axes"].items():
                for m in ms:
                    lab = r.label(m)
                    if m not in std.concepts and re.fullmatch(r"(비?유동|유동성|비유동성)(부분)?(\s*\[구성요소\])?", lab.strip()):
                        out.append(Finding(S1, "유동·비유동을 축으로 구분", ERR, rc, m,
                                           f"유동/비유동을 확장 멤버로 구성({r.label(ax)} 하위) — 행으로 구분해야 함",
                                           table=t, parent=info["parent_tb"] or ""))
            if not axes:
                continue
            moving = any(pl and ("periodStart" in pl or "periodEnd" in pl) for pl in info["pref_labels"]) or \
                any(MOVE_RE.search(q) for q in info["items"])
            if moving:
                continue
            for ax in axes:
                out.append(Finding(S1, "유동·비유동을 축으로 구분", ERR, rc, ax, "변동내역 없이 유동/비유동 축 사용",
                                   table=t, parent=info["parent_tb"] or "",
                                   extra={"marks": [ax] + list(info["axes"][ax])}))
    return out


# ---------------------------------------------------------------------------
# 택사노미 버전
# ---------------------------------------------------------------------------

def used_elements(r):
    """요소 -> 사용된 role 코드 집합 (pre, def, fact, context 차원 모두)."""
    used = defaultdict(set)
    for u in r.pre:
        rc = r.role_code(u)
        for par, lst in r.pre[u].items():
            used[par].add(rc)
            for _, ch, _ in lst:
                used[ch].add(rc)
    for u, arcs in r.defs.items():
        rc = r.role_code(u)
        for _, p, c in arcs:
            used[p].add(rc)
            used[c].add(rc)
    for f in r.facts:
        used[f["q"]].add("(fact)")
    for c in r.contexts.values():
        for d, m in c["dims"].items():
            used[d].add("(context)")
            if not m.startswith("typed:"):
                used[m].add("(context)")
    used.pop(None, None)
    return used


def _per_role(used, q, mk):
    roles = sorted(x for x in used[q] if not x.startswith("("))
    return [mk(rc) for rc in roles] or [mk("(fact/context)")]


def check_deprecated(r, std):
    used = used_elements(r)
    out = []
    for q in sorted(used):
        if q in std.deprecated:
            # 가이드 글·그림 근거 없이 택사노미 표시만 근거 → 확인 권장
            out += _per_role(used, q, lambda rc: Finding(S2, "사용중단 표시 요소", ERR, rc, q,
                                                         "택사노미 라벨에 (사용중단) 표시 — 현행 요소로 대체",
                                                         extra={"basis": "약함"}))
    return out


# ---------------------------------------------------------------------------
# 같은 뜻의 ifrs-full 요소가 있는데 dart 요소만 사용
# ---------------------------------------------------------------------------
# 가이드 p79 2.(4): "dart"와 "ifrs-full" 요소의 개념이 유사한 경우 "ifrs-full" 요소를 우선 적용
#  (dart 요소는 IFRS 택사노미 업데이트로 유사 개념이 생겨도 과거 데이터 변환 등을 위해 삭제하지 않고 유지). 본문에도 적용된다.
# 쌍 목록은 rules/dart_ifrs_pairs.csv — 영문 정의로 의미가 같은지 판정해 만든 표(라벨 글자 비교가 아님).
#  verdict 같음만 검출(개선 권장). 유사(이익만/손실만 vs 순액, 범위·부호 차이 등)는 개념이 같다고 보기 어려워
#  검출하지 않고 목록에만 남긴다 — 실제 공시(일신방직 3분기) 시험에서 영업이익·이자수익(금융수익)·외화환산손익 조정 등
#  본문 서식 기본 요소가 대거 걸리는 오탐이었다. 확인된 짝은 verdict를 같음으로 올려 검출 대상에 넣는다.
#  장단기(단기·장기)와 유동·비유동은 다른 개념이라 쌍으로 두지 않는다.
# 두 요소를 같은 문서에서 모두 쓰면(본문 dart·주석 ifrs-full 등) 검출하지 않는다.
# 검출은 그 목차(주석)의 표준 서식에 짝 ifrs-full 요소가 있고 dart 요소는 없는 곳에서만 한다.
#  표준 서식이 dart 요소를 주는 곳(예: 기타수익 주석의 수입임대료, 현금흐름표 본문의 관계기업 투자 취득)이나
#  두 요소 모두 서식에 없는 곳은 ifrs-full로 바꾸라고 하면 서식과 어긋나므로 띄우지 않는다.
DART_IFRS_CSV = "dart_ifrs_pairs.csv"


def dart_ifrs_pairs(std):
    """dart 요소 -> [(ifrs-full 요소, verdict, reason)] (std에 캐시). 표준 택사노미에 없는 요소는 건너뛴다."""
    if getattr(std, "_dart_ifrs", None) is not None:
        return std._dart_ifrs
    import csv
    from .common import ROOT
    pairs = defaultdict(list)
    path = ROOT / "rules" / DART_IFRS_CSV
    if path.exists():
        with open(path, encoding="utf-8-sig") as fp:
            for row in csv.DictReader(fp):
                d, t = row["dart"].strip(), row["ifrs_full"].strip()
                if d in std.concepts and t in std.concepts and t not in std.deprecated:
                    pairs[d].append((t, (row.get("verdict") or "").strip() or "유사", (row.get("reason") or "").strip()))
    std._dart_ifrs = dict(pairs)
    return std._dart_ifrs


def check_dart_over_ifrs(r, std):
    used = used_elements(r)
    out = []
    for q, ts in sorted(dart_ifrs_pairs(std).items()):
        same = [(t, w) for t, v, w in ts if v == "같음"]
        if q not in used or not same or any(t in used for t, _, _ in ts):
            continue
        alt = ", ".join(f"{t}({std.label(t)})" for t, _ in same)
        why = next((w for _, w in same if w), "")
        detail = (f"dart 요소 '{std.label(q)}' — 개념이 같은 ifrs-full 요소 {alt} 우선 적용 (가이드 p79)"
                  + (f". {why}" if why else "") + ". 이 목차의 표준 서식에 있는 요소는 ifrs-full")
        for rc in sorted(x for x in used[q] if not x.startswith("(")):
            tmpl = std.role_elems.get(rc) or std.role_elems.get(rc[:-1] + "0") or set()
            if q in tmpl or not any(t in tmpl for t, _ in same):
                continue
            out.append(Finding(S1, "같은 뜻 ifrs-full 요소 대신 dart 요소 사용", ERR, rc, q, detail))
    return out


RECV_LA_HINT = "장부금액 축의 손상차손누계액 [구성요소] + 금융자산(FinancialAssets) 기초·기말 행으로 작성 (작성가이드 p127)"
RECV_LA_TABLE = "dart:DisclosureOfReconciliationOfChangesInLossAllowanceAndExplanationOfChangesInGrossCarryingAmountForReceivablesTable"
FIN_CLASS_AXES = {"ifrs-full:ClassesOfFinancialInstrumentsAxis", "ifrs-full:ClassesOfFinancialAssetsAxis"}


def check_allowance_table(r, std):
    # 가이드 p177은 '추후 사용 만료 예정이므로 위 사례로 공시 권고'(금지 아님) → 확인 권장
    used = used_elements(r)
    return [f for q in sorted(used) if "ReconciliationOfChangesInAllowanceAccountForCreditLosses" in q
            for f in _per_role(used, q, lambda rc: Finding(
                S2, "IAS 39 대손충당금 변동표", ERR, rc, q, "사용 만료 예정 표 — " + RECV_LA_HINT,
                extra={"basis": "약함", "note": "작성가이드 p177"}))]


def check_recv_loss_allowance(r, std):
    """매출채권 손실충당금 변동표(G5B-014): 분류 축(금융상품·금융자산의 종류 [축], p126·177)과
    장부금액 축의 손상차손누계액 [구성요소](p127)가 있어야 함. 기초·기말 행이 금융자산이 아니면 확인 권장(p127·178 그림)."""
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        info = r.tables(u).get(RECV_LA_TABLE)
        if not info:
            continue
        axes = info["axes"]
        miss = []
        if not FIN_CLASS_AXES & set(axes):
            miss.append("분류 축(금융상품의 종류 [축] 또는 금융자산의 종류 [축])")
        if IF_ + "AccumulatedImpairmentMember" not in axes.get(CARRYING_AXIS, []):
            miss.append("장부금액 축의 손상차손누계액 [구성요소]")
        if miss:
            out.append(Finding(S2, "IAS 39 대손충당금 요소", ERR, rc, RECV_LA_TABLE,
                               "매출채권 손실충당금 변동표에 " + "·".join(miss) + " 없음 — " + RECV_LA_HINT,
                               table=RECV_LA_TABLE, extra={"basis": "명확", "note": "작성가이드 p126·127"}))
        ends = {q for q, pl in zip(info["items"], info["pref_labels"])
                if pl and ("periodStart" in pl or "periodEnd" in pl)}
        if ends and IF_ + "FinancialAssets" not in ends:
            out.append(Finding(S2, "IAS 39 대손충당금 요소", REV, rc, sorted(ends)[0],
                               "매출채권 손실충당금 변동표의 기초·기말 행이 금융자산(FinancialAssets)이 아님: "
                               + ", ".join(r.label(q) for q in sorted(ends)),
                               table=RECV_LA_TABLE, extra={"basis": "약함", "note": "작성가이드 p127·178 모범사례"}))
    return out


def check_impairment_axis(r, std):
    used = used_elements(r)
    if IMPAIRMENT_FA_AXIS not in used:
        return []
    # G5-005: 가이드 표현이 '권고'이고 아직 만료 전 → 확인 권장. 택사노미에서 실제 만료되면 수정 필요로 올린다
    return _per_role(used, IMPAIRMENT_FA_AXIS, lambda rc: Finding(
        S2, "금융자산 손상차손 축(만료 예정)", REV, rc, IMPAIRMENT_FA_AXIS,
        "만료 예정 축 — 금융상품의 신용손상 축(CreditImpairmentOfFinancialInstrumentsAxis) 사용 권고(작성가이드 p103·182)",
        extra={"basis": "약함"}))


def check_allowance_elem(r, std):
    used = used_elements(r)
    return [f for q in sorted(used) if "AllowanceAccountForCreditLossesOfFinancialAssets" in q
            and "Reconciliation" not in q
            for f in _per_role(used, q, lambda rc: Finding(
                S2, "IAS 39 대손충당금 요소", ERR, rc, q, "IAS 39 체계 요소 — " + RECV_LA_HINT,
                extra={"basis": "약함", "note": "현행 택사노미에도 남아 있는 요소 — 손실충당금 체계로 공시해야 하는지 확인"}))]


# ---------------------------------------------------------------------------
# 3. 축-멤버 정합성
# ---------------------------------------------------------------------------

def axis_member_pairs(r):
    """(role_code, table, axis, member, 출처) — pre 표, def, context에서 모두 수집."""
    rows = []
    for u in r.uris():
        rc = r.role_code(u)
        for t, info in r.tables(u).items():
            for ax, ms in info["axes"].items():
                for m in ms:
                    rows.append((rc, t, ax, m, "pre"))
    for u, arcs in r.defs.items():
        rc = r.role_code(u)
        kids, doms = defaultdict(list), defaultdict(list)
        for a, p, c in arcs:
            if a in ("dimension-domain", "domain-member"):
                kids[p].append(c)
            if a == "dimension-domain":
                doms[p].append(c)
        for ax, ds in doms.items():
            stack, seen = list(ds), set()
            while stack:
                m = stack.pop()
                if m in seen:
                    continue
                seen.add(m)
                stack += kids[m]
                rows.append((rc, f"(정의 {u.rsplit('/', 1)[-1]})", ax, m, "def"))
    for cid, c in r.contexts.items():
        for d, m in c["dims"].items():
            if not m.startswith("typed:"):
                rows.append(("(context)", "", d, m, "context"))
    return rows


PPE_ROU_AXIS = "ifrs-full:PropertyPlantAndEquipmentIncludingRightofuseAssetsAxis"
PPE_CLASS_AXIS = "ifrs-full:ClassesOfPropertyPlantAndEquipmentAxis"


def _lab_core(t):
    """라벨 핵심: [구성요소]·(사용중단)·끝의 ', 분류/범주/종류'·공백 제거."""
    t = re.sub(r"\[구성요소\]|\(사용중단\)", "", t or "").strip()
    return re.sub(r"\s", "", re.sub(r",\s*(분류|범주|종류)$", "", t))


def _name_core(q):
    return re.sub(r"Category|ClassesOf|Member", "", q.split(":")[-1]).lower()


def substitute_member(std, ax, m):
    """같은 축에서 바꿔 쓸 표준 멤버. (멤버, 근거, 확실 여부) 또는 None."""
    lc, nc = _lab_core(std.label(m)), _name_core(m)
    # ① 가이드 모범사례에서 이 축에 쓴 멤버 중 같은 뜻
    for g, pages in std.guide_pairs.get(ax, {}).items():
        if g != m and (_lab_core(std.label(g)) == lc or _name_core(g) == nc):
            return g, "작성가이드 p" + "·".join(map(str, sorted(pages))) + " 모범사례", True
    pool = [x for x in std.axis_members[ax] | std.pre_axis_members[ax]
            if x != m and x.endswith("Member") and x not in std.deprecated]
    # ② 택사노미 같은 축 멤버 중 이름이 사실상 같음
    for x in pool:
        if _lab_core(std.label(x)) == lc or _name_core(x) == nc:
            return x, "택사노미 같은 축 멤버", True
    # ③ 비슷한 후보(상·하위 개념일 수 있음) — 참고만
    near = [x for x in pool if len(lc) >= 2 and len(_lab_core(std.label(x))) >= 2
            and (lc in _lab_core(std.label(x)) or _lab_core(std.label(x)) in lc)]
    if near:
        return near[:3], "참고", False
    return None


def std_axis_ok(std, ax, m):
    """표준 택사노미의 def(domain-member)와 pre(표시 계층) 양쪽에서 그 축 아래에 있어야 정상.
    예외: 사용권자산을 포함하는 유형자산 축은 유형자산의 분류 축을 대신하므로(작성가이드 p201)
    정의(def)에 있거나 유형자산의 분류 축 멤버이면 정상."""
    if m in std.guide_pairs.get(ax, {}):      # 작성가이드 모범사례 매핑에 있는 조합
        return True
    # 연체상태 축에는 기간 구간 멤버(…개월 초과 …개월 이내 등)를 쓸 수 있음(가이드 p182 모범사례)
    if ax == "ifrs-full:PastDueStatusAxis" and re.match(r"ifrs-full:(NotLaterThan|LaterThan)\w*Member$", m):
        return True
    if ax == PPE_ROU_AXIS and (m in std.axis_members[ax] or m in std.axis_members[PPE_CLASS_AXIS]
                               or m in std.pre_axis_members[PPE_CLASS_AXIS]):
        return True
    return m in std.axis_members[ax] and m in std.pre_axis_members[ax]


IF_ = "ifrs-full:"
CAT_AXES = {IF_ + "CategoriesOfFinancialAssetsAxis", IF_ + "CategoriesOfFinancialLiabilitiesAxis"}
CLS_AXES = {IF_ + a for a in ("ClassesOfAssetsAxis", "ClassesOfLiabilitiesAxis", "ClassesOfFinancialAssetsAxis",
                              "ClassesOfFinancialLiabilitiesAxis", "ClassesOfFinancialInstrumentsAxis",
                              "AssetsAndLiabilitiesAxis")}
MEASURE_AXIS = IF_ + "MeasurementAxis"
RISK_AXIS = IF_ + "TypesOfRisksAxis"
CURRENCY_INFO_AXIS = IF_ + "CurrencyInWhichInformationIsDisplayedAxis"
IP_TYPE_AXIS = IF_ + "TypesOfInvestmentPropertyAxis"
ASSET_CLASS_AXES = {IF_ + "ClassesOfAssetsAxis", PPE_CLASS_AXIS}


def _in_axes(std, axes, m):
    return any(m in std.axis_members[a] or m in std.pre_axis_members[a] for a in axes)


def forbidden_mix(std, ax, m):
    """작성가이드 글·그림이 바로 금지하는 축-멤버 혼용 유형. (설명, 근거) 또는 None.
    대체 멤버가 없어도 수정 필요로 본다."""
    # 범주 멤버: 범주 축 소속이면서 이름이 범주 자체를 뜻하는 것(…Category…, 범주 도메인, …At공정가치/상각후원가 측정 금융자산·부채).
    # 범주 축 아래에는 상장주식·대여금 같은 종류 멤버도 표준으로 있으므로(p322) 소속만으로는 범주 멤버로 보지 않는다.
    # 자산과 부채 축은 표준에서 범주 멤버도 함께 두므로 '종류·분류 축 소속' 판단에서는 뺀다
    if (ax in CLS_AXES and _in_axes(std, CAT_AXES, m)
            and not _in_axes(std, CLS_AXES - {IF_ + "AssetsAndLiabilitiesAxis"}, m)
            and re.search(r"Category|^Categories|^Financial(Assets|Liabilities)At", m.split(":")[-1])):
        return "범주 멤버를 종류·분류 축에 사용 — 범주와 종류·분류는 축을 따로 써야 함", "작성가이드 p321"
    if ax == CARRYING_AXIS and m == IF_ + "AllowanceForCreditLossesMember":
        return "장부금액 축에는 손상차손누계액 [구성요소]를 사용", "작성가이드 p175"
    if ax == CARRYING_AXIS and _in_axes(std, {MEASURE_AXIS}, m) and not _in_axes(std, {CARRYING_AXIS}, m):
        return "측정 축 멤버를 장부금액 축에 사용 — 장부금액 축과 측정 축은 독립", "작성가이드 p123~124·p223"
    if ax == MEASURE_AXIS and _in_axes(std, {CARRYING_AXIS}, m) and not _in_axes(std, {MEASURE_AXIS}, m):
        return "장부금액 축 멤버를 측정 축에 사용 — 장부금액 축과 측정 축은 독립", "작성가이드 p123~124·p223"
    if ax == RISK_AXIS and _in_axes(std, {CURRENCY_INFO_AXIS}, m):
        return "통화 멤버는 위험 유형 축과 별도로 정보를 표시하는 통화 [축] 아래 구성", "작성가이드 p294~295"
    if ax == IP_TYPE_AXIS and _in_axes(std, ASSET_CLASS_AXES, m):
        return "투자부동산 유형 축은 취득완료·건설중 구분용 — 토지·건물 등은 자산의 분류 [축] 사용", "작성가이드 p222"
    return None


def check_axis_member(r, std):
    found = {}
    for rc, t, ax, m, src in axis_member_pairs(r):
        if not std.is_std(m) or name_kind(m) != "member" or m == REPORTED_MEMBER:
            continue
        home = sorted(std.label(a) for a in (std.member_axes[m] | {a for a, ms in std.pre_axis_members.items() if m in ms})
                      if a != ax)
        if std.is_std(ax):
            if std_axis_ok(std, ax, m):
                continue
            in_def, in_pre = m in std.axis_members[ax], m in std.pre_axis_members[ax]
            if in_pre or in_def:
                # 표준 표시 구조·정의 중 한쪽에만 이 축 아래 있음 — 택사노미 허용 범위가 애매해 확인 권장
                why = ("표준 표시 구조에는 이 축 아래 있으나 정의(차원) 관계는 없음" if in_pre
                       else "표준 정의(차원) 관계에는 이 축 아래 있으나 표준 표시 구조에는 없음")
                level, note = REV, "택사노미 허용 여부 확인"
            else:
                why = (f"표준상 이 축 소속이 아님(이 멤버의 본래 축: {', '.join(home[:3])})" if home
                       else "표준상 어느 축에도 소속되지 않은 멤버")
                sub = substitute_member(std, ax, m)
                level, note = REV, ""
                if sub and sub[2]:
                    level, note = ERR, sub[1]
                    why += f" → 이 축에서는 {std.label(sub[0])} 사용"
                elif sub:
                    note = "참고: 이 축의 비슷한 표준 멤버 " + ", ".join(std.label(x) for x in sub[0]) + " (상·하위 개념일 수 있음)"
                fm = forbidden_mix(std, ax, m)
                if fm:
                    level = ERR
                    why += " — " + fm[0]
                    note = fm[1] + (" / " + note if note else "")
                elif ax == CURRENCY_INFO_AXIS and m == "dart:KRWMember":
                    note = "참고: 작성가이드 p228 모범사례의 원화 멤버는 통화별 금융상품 [축] 소속" + (" / " + note if note else "")
        else:
            if not home:
                continue
            why = f"확장 축 아래 다른 표준 축의 멤버 사용(이 멤버의 본래 축: {', '.join(home[:3])})"
            level, note = REV, ""
        key = (src, rc, t, ax, m)
        if key not in found:
            found[key] = Finding(S3, "다른 축 소속 멤버 사용", level, rc, m, f"{r.label(ax)} 하위 — {why}",
                                 table=t, extra={"axis": ax, "source": src, "basis": "명확", "note": note})
    # 표시(pre) 구조 기준으로 센다. 정의(def)·context에만 있는 쌍만 따로 남긴다
    pre_pairs = {(rc, ax, m) for (src, rc, t, ax, m) in found if src == "pre"}
    all_pairs = {(ax, m) for (src, rc, t, ax, m) in found if src == "pre"}
    out = []
    for (src, rc, t, ax, m), f in found.items():
        if src == "pre" or (src == "def" and (rc, ax, m) not in pre_pairs) or                 (src == "context" and (ax, m) not in all_pairs):
            if src != "pre":
                f.detail = f"[{'정의' if src == 'def' else 'context'}에만 있음] " + f.detail
            out.append(f)
    # def만 여러 표별 role에 반복되는 경우 하나로
    seen, res = set(), []
    for f in out:
        k = (f.extra["source"], f.role_code, f.table if f.extra["source"] == "pre" else "", f.extra["axis"], f.element)
        if k not in seen:
            seen.add(k)
            res.append(f)
    for f in res:
        if f.extra["source"] != "pre":
            f.table = ""
        f.parent = f.parent or ""
    return res


def check_reported_use(r, std):
    out = []
    for rc, t, ax, m, src in axis_member_pairs(r):
        if m == REPORTED_MEMBER and ax != CARRYING_AXIS:
            out.append(Finding(S3, "공시금액 멤버의 위치", ERR, rc, m,
                               f"장부금액 축이 아닌 {r.label(ax)} 하위에 공시금액 사용", table=t))
    for u in r.uris():
        rc = r.role_code(u)
        for t, info in r.tables(u).items():
            ms = info["axes"].get(CARRYING_AXIS, [])
            if REPORTED_MEMBER not in ms:
                continue
            others = [m for m in ms if m not in (REPORTED_MEMBER, CARRYING_MEMBER)]
            if others:
                out.append(Finding(S3, "공시금액 멤버의 위치", ERR, rc, REPORTED_MEMBER,
                                   "장부금액 축에서 공시금액을 다른 멤버와 함께 사용: " +
                                   ", ".join(r.label(x) for x in others), table=t, parent=info["parent_tb"] or "",
                                   extra={"marks": [REPORTED_MEMBER] + others}))
            cls_axes = [a for a in info["axes"] if a not in (CARRYING_AXIS, CONSEP_AXIS)]
            if cls_axes:
                out.append(Finding(S3, "공시금액 멤버의 위치", ERR, rc, REPORTED_MEMBER,
                                   "다른 분류 축과 함께 공시금액 사용: " + ", ".join(r.label(a) for a in cls_axes),
                                   table=t, parent=info["parent_tb"] or "",
                                   extra={"basis": "명확", "note": "작성가이드 p132",
                                          "marks": [REPORTED_MEMBER] + cls_axes}))
    seen, res = set(), []
    for f in out:
        k = (f.role_code, f.table, f.detail)
        if k not in seen:
            seen.add(k)
            res.append(f)
    return res


# ---------------------------------------------------------------------------
# 표별 전용 요소
# ---------------------------------------------------------------------------

def group_roles(std, kw, general_only):
    return {rc for rc, t in std.role_title.items() if kw in t and
            (not general_only or (rc.startswith("D") and rc[1].isdigit()))}


def _group_ctx(std):
    if not hasattr(std, "_grp"):
        g = {}
        for name, kw in GROUPS.items():
            all_r, gen_r = group_roles(std, kw, False), group_roles(std, kw, True)
            g[name] = dict(all=all_r, gen=gen_r,
                           U_all=set().union(*(std.role_elems[x] for x in all_r)),
                           U_gen=set().union(*(std.role_elems[x] for x in gen_r)))
        std._grp = g
    return std._grp


def in_group(r, u, rc, kw, codes):
    return rc in codes or (rc.startswith("U") and kw in (r.roles.get(u, ("", ""))[1] or ""))


SHORT = {"판매비와관리비": "판관비"}   # 카드 이름에 쓰는 표 줄임말


def make_other_in(name):
    def check(r, std):
        g = _group_ctx(std)[name]
        out, seen = [], set()
        if name == "현금흐름":
            # 영업활동 조정 행의 손익 요소는 G5C-074·G4-019, 법인세비용차감전순이익 출발은 G4-035 카드가 맡는다(중복 표시 방지)
            from .guide_checks import cf_operating_pl_rows
            seen |= {(x["rc"], x["q"]) for x in cf_operating_pl_rows(r, std)}
        elif name == "판매비와관리비":
            # p265 '~, 판관비' 규칙 건은 G5C-053 카드가 맡는다(중복 표시 방지)
            from .guide_checks import g5c_053
            seen |= {(x.role_code, x.element) for x in g5c_053(r, std)}
        elif name == "특수관계자":
            # 특수관계자 거래·잔액 표의 일반 행은 G5C-075 카드가 맡고, 이름에 RelatedPart가 있는 특수관계자 전용 요소는
            # 원칙에 맞는 행이라 띄우지 않는다(G5C-075, p349)
            from .guide_checks import g5c_075
            seen |= {(x.role_code, x.element) for x in g5c_075(r, std)}
            seen |= {(r.role_code(u), n) for u in r.uris() for n, *_ in r.role_nodes(u) if "RelatedPart" in n}
        for u in r.uris():
            rc = r.role_code(u)
            if not in_group(r, u, rc, GROUPS[name], g["all"]):
                continue
            for n, p, tb, t, pl in r.role_nodes(u):
                if std_items(std, n) and n not in g["U_all"] and (rc, n) not in seen:
                    seen.add((rc, n))
                    out.append(Finding(S4, f"{SHORT.get(name, name)} 표에 표준 외 요소", REV, rc, n,
                                       f"표준 {name} 표에 없는 요소", table=t or "", parent=tb or ""))
        return out
    return check


def make_dedicated_out(name):
    def check(r, std):
        g = _group_ctx(std)[name]
        out, seen = [], set()
        # 그룹 밖에서 쓰인 '그 그룹 전용(그룹 role에만 나오는)' 요소 — 엄격 보강용
        exclusive = {q for q in g["U_all"] if std.elem_roles[q] and std.elem_roles[q] <= g["all"]}
        for u in r.uris():
            rc = r.role_code(u)
            if in_group(r, u, rc, GROUPS[name], g["all"]):
                continue
            for n, p, tb, t, pl in r.role_nodes(u):
                # 실제 전용요소만 대상 — 재무상태표 등 다른 표에도 정의된 공용 요소(예: CashAndCashEquivalents)는 제외
                if not std_items(std, n) or (rc, n) in seen or n not in exclusive:
                    continue
                seen.add((rc, n))
                out.append(Finding(S4, f"{SHORT.get(name, name)} 요소의 다른 표 사용", REV, rc, n,
                                   f"{name} 표에만 정의된 전용요소를 다른 표에서 사용",
                                   table=t or "", parent=tb or "", extra={"basis": "명확"}))
        return out
    return check


CF_WORD_RE = re.compile(r"(지급|수취|유입|유출|상환|납부|수령|차입|발행|취득|처분|현금)")
# 공급자금융약정 관련 IAS 7 요소는 현금흐름 목차에만 정의돼 있지만 금융부채·지급기일 주석에서 쓰는 것이 정상(G4-003 ②)
SUPPLIER_FIN_RE = re.compile(r"SupplierFinanceArrangement|NumberOfDaysBetweenInvoiceDate")
FIN_ROLE_RE = re.compile(r"^D[BISX]")   # 금융업 목차(DB·DI·DS·DX)
PL_NAME_RE = re.compile(r"(Gain|Loss|Profit|Income|Expense|Revenue|Impairment)")


def _cf_named(r, *qs):
    """표·항목 이름이나 라벨에 CashFlow·현금흐름이 있는지 — 부문별 현금흐름 표 등(G4-003 ②)."""
    return any(q and (re.search(r"CashFlow", q.split(":")[-1], re.I) or "현금흐름" in (r.label(q) or "")) for q in qs)


def _cf_replacement(std, q, exclusive):
    """현금흐름 전용요소 대신 쓸 표준요소: AdjustmentsFor 뗀 이름, '조정'을 뗀 라벨, 같은 라벨(전용요소가 아닌 것)."""
    loc = q.split(":")[-1]
    names = []
    if loc.startswith("AdjustmentsFor"):
        names += [f"{p}:{loc[len('AdjustmentsFor'):]}" for p in ("ifrs-full", "dart")]
    lab = re.sub(r"\s", "", std.label_ko.get(q, ""))
    labs = {lab, re.sub(r"(에대한)?조정$", "", lab)}
    for c in names + sorted(c for c, l in std.label_ko.items() if re.sub(r"\s", "", l) in labs):
        if c != q and c in std.concepts and std.kind(c) == "item" and c not in exclusive and c not in std.deprecated:
            return c
    return None


def check_cf_dedicated_out(r, std):
    """현금흐름 전용요소를 다른 표에서 쓴 경우(G4-003 ①~③으로 대상 축소).
    (a) 조정 요소(이름 AdjustmentsFor… 또는 라벨 '…조정')를 손익계산서·손익 주석·부문 손익 표에서 사용,
    (b) 현금흐름 전용요소를 자본변동표에서 사용 → 대체 표준요소가 있으면 수정 필요, 없으면 확인 권장.
    (c) 금융업 현금흐름 목차에만 있는 손익 이름 요소 → 확인 권장.
    공급자금융약정 요소와 부문별 현금흐름 표는 제외."""
    g = _group_ctx(std)["현금흐름"]
    exclusive = {q for q in g["U_all"] if std.elem_roles[q] and std.elem_roles[q] <= g["all"]}
    out, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        if in_group(r, u, rc, "현금흐름", g["all"]):
            continue
        for n, p, tb, t, pl in r.role_nodes(u):
            if not std_items(std, n) or (rc, n) in seen or n not in exclusive or SUPPLIER_FIN_RE.search(n):
                continue
            if _cf_named(r, t, p):
                continue
            loc, lab = n.split(":")[-1], re.sub(r"\s", "", std.label_ko.get(n, ""))
            sce = bool(re.match(r"D6\d{5}$", rc))
            adj = loc.startswith("AdjustmentsFor") or lab.endswith("조정")
            fin_only = all(FIN_ROLE_RE.match(x) for x in std.elem_roles[n])
            if sce or (adj and not re.match(r"D2\d{5}$", rc)):
                rep = _cf_replacement(std, n, exclusive)
                where = "자본변동표" if sce else "손익 표"
                why = (f"현금흐름 전용요소를 {where}에서 사용 — " +
                       (f"표준요소 {rep} 사용" if rep else "현금흐름표 밖에서 쓸 표준요소 확인"))
                basis = "명확" if rep else "약함"
            elif fin_only and PL_NAME_RE.search(loc):
                why, basis = "금융업 현금흐름 목차에만 있는 요소를 손익 표에서 사용 — 일반 손익 요소 확인", "약함"
            else:
                continue
            seen.add((rc, n))
            out.append(Finding(S4, "현금흐름 요소의 다른 표 사용", ERR, rc, n, why,
                               table=t or "", parent=tb or "", extra={"basis": basis}))
    return out


def check_cf_ext_alike(r, std):
    """보강: 현금흐름 전용요소와 같은 개념을 다른 표에서 확장요소로 만든 경우 (전용요소 재사용 권고).
    같은 이름의 일반 표준요소가 있는 건(리스부채의 지급 등)은 '같은 이름 표준요소' 카드(G4-004)가 맡는다(G4-003 ④)."""
    g = _group_ctx(std)["현금흐름"]
    exclusive = [q for q in g["U_all"] if std.elem_roles[q] and std.elem_roles[q] <= g["all"]
                 and std.kind(q) == "item" and not SUPPLIER_FIN_RE.search(q)]
    # 정렬: 후보가 여럿일 때 실행마다 안내 요소가 바뀌지 않게(집합 순회 순서 비결정성)
    lab_index = sorted((std.label(q), q) for q in exclusive)
    exclusive = set(exclusive)
    std_by_label = defaultdict(list)
    for q, l in std.label_ko.items():
        if std.kind(q) == "item" and q not in std.deprecated:
            std_by_label[l].append(q)
    out, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        if in_group(r, u, rc, "현금흐름", g["all"]):
            continue
        for n, p, tb, t, pl in r.role_nodes(u):
            if std.is_std(n) or r.kind(n) != "item" or (rc, n) in seen or _cf_named(r, t, p):
                continue
            lab = (r.label_ko.get(n) or "").strip()
            if len(lab) < 4 or not CF_WORD_RE.search(lab):
                continue
            hit = next((q for sl, q in lab_index if sl == lab or sl.endswith(" " + lab) or sl.endswith(lab)), None)
            if hit:
                seen.add((rc, n))
                if any(q not in exclusive for q in std_by_label.get(lab, [])):
                    continue   # 같은 이름의 일반 표준요소 있음 → G4-004 카드
                out.append(Finding(S4, "현금흐름 요소의 다른 표 사용", REV, rc, n,
                                   f"확장요소 '{lab}' — 현금흐름 전용요소 {hit}({std.label(hit)})와 같은 개념, 표준요소 사용 검토",
                                   table=t or "", parent=tb or "", extra={"basis": "약함"}))
    return out


# ---------------------------------------------------------------------------
# 입력값
# ---------------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_reported_value(r, std):
    out, seen = [], set()
    idx = {}
    for f in r.facts:
        c = r.contexts.get(f["ctx"])
        if c:
            key = (f["q"], c["start"], c["end"], c["instant"], frozenset(c["dims"].items()))
            idx[key] = f
    for f in r.facts:
        c = r.contexts.get(f["ctx"])
        if not c or c["dims"].get(CARRYING_AXIS) != REPORTED_MEMBER:
            continue
        if f["unit"] is None:
            # 문장·날짜 fact: 가이드 p151 "(8) 공시금액 구성요소를 사용한 경우 Fact는 공시금액 열에 입력" → 점검 안 함(103번 삭제)
            continue
        rest = {d: m for d, m in c["dims"].items() if d != CARRYING_AXIS}
        tot = idx.get((f["q"], c["start"], c["end"], c["instant"], frozenset(rest.items())))
        a, b = _num(f["value"]), _num(tot["value"]) if tot else None
        if tot and a is not None and b is not None and abs(a - b) > _tol(f, tot):
            k = (f["q"], c["instant"] or c["end"], a, b)
            if k in seen:
                continue
            seen.add(k)
            out.append(Finding(S5, "공시금액 입력값의 적정성", REV, _fact_role(r, f["q"], c), f["q"],
                               f"공시금액 {a:,.0f} ≠ 합계(기본 context) {b:,.0f} ({c['instant'] or c['end']})",
                               extra={"ctx": f["ctx"],
                                      "note": "표마다 포함 범위가 달라 생긴 차이(예: 범주별 표에 종속기업 배당 미포함)라면 정상 — 입력 실수인지만 확인"}))
    return out


def _fact_role(r, q, c):
    """값이 들어간 주석(role) — 요소를 쓰는 role 중 context의 연결/별도와 맞는 것."""
    if not hasattr(r, "_elem_roles"):
        m = defaultdict(list)
        for u, tree in r.pre.items():
            rc = r.role_code(u)
            for lst in tree.values():
                for _, ch, _ in lst:
                    if rc not in m[ch]:
                        m[ch].append(rc)
        r._elem_roles = m
        r._role_nodes_all = defaultdict(set)
        for u, tree in r.pre.items():
            for lst in tree.values():
                for _, ch, _ in lst:
                    r._role_nodes_all[r.role_code(u)].add(ch)
    sep = c["dims"].get(CONSEP_AXIS) == "ifrs-full:SeparateMember"
    rcs = [x for x in r._elem_roles.get(q, []) if x[:1] in "DU"]
    hit = [x for x in rcs if (x[-1] == "5") == sep] or rcs
    # context의 멤버(공시금액 등)를 모두 가진 주석을 우선한다
    ms = {m for d, m in c["dims"].items() if d != CONSEP_AXIS}
    best = [x for x in hit if ms <= r._role_nodes_all.get(x, set())]
    return (best or hit or ["(fact)"])[0]


def check_reported_amount(r, std):
    return check_reported_value(r, std)


def _tol(*facts):
    """decimals가 다른 두 값(예: 천원 단위 주석 vs 원 단위 본문)은 덜 정밀한 쪽 3단위만큼 허용
    (단수 조정으로 주석 합계를 맞추면 1~2단위 차이가 흔함)."""
    t = 0
    for f in facts:
        d = f.get("decimals")
        if d and d.lstrip("-").isdigit() and int(d) < 0:
            t = max(t, 3 * 10 ** (-int(d)))
    return t


# ---------------------------------------------------------------------------
# 표 구조 추가 점검
# ---------------------------------------------------------------------------

TOTAL_RE = re.compile(r"^\s*(합계|합\s*계|계|총계|소계|total|sum)\s*(\[구성요소\])?\s*$", re.I)
MOVE_MEMBER_RE = re.compile(r"(취득|처분|상각|대체|증가|감소|기초|기말|환입|전입)")
KEY_ACCOUNTS = {"자산총계", "부채총계", "자본총계", "유동자산", "비유동자산", "유동부채", "비유동부채",
                "매출액", "수익(매출액)", "매출원가", "매출총이익", "영업이익", "영업이익(손실)",
                "당기순이익", "당기순이익(손실)", "법인세비용차감전순이익", "자본금", "이익잉여금"}
# 가이드 p79 예시(자산·부채·자본·유동자산·유동부채·매출액·매출원가·당기순이익)와 그 이름 변형 → 수정 필요.
# 예시 밖 계정(비유동자산·비유동부채·매출총이익·영업이익·법인세비용차감전순이익·자본금·이익잉여금)은 확인 권장
KEY_GUIDE = {"자산총계", "부채총계", "자본총계", "자산합계", "부채합계", "자본합계", "유동자산", "유동부채",
             "매출액", "수익(매출액)", "매출", "영업수익", "수익", "매출원가"}


# 대상명을 앞에 붙인 합계 멤버('유형자산 합계', '채무보증 합계') — 나머지 묶음 멤버일 수 있어 확인 권장(G5-003)
TOTAL_TAIL_RE = re.compile(r"(합\s*계|총\s*계|소\s*계|\s계)$")
TOTAL_EN_RE = re.compile(r"^\s*total\b|\btotal$", re.I)


def check_total_member(r, std):
    out, seen = [], set()
    for rc, t, ax, m, src in axis_member_pairs(r):
        if std.is_std(m) or (rc, m) in seen:
            continue
        lab = r.label(m) or ""
        core = re.sub(r"\s*\[구성요소\]\s*$", "", lab).strip()
        en = re.sub(r"\s*\[member\]\s*$", "", r.label_en.get(m) or "", flags=re.I).strip()
        if TOTAL_RE.match(lab):
            seen.add((rc, m))
            out.append(Finding(S6, "축 하위 합계 멤버 확장", ERR, rc, m,
                               f"'{lab}' — 도메인이 합계 역할을 하므로 합계 멤버 확장 금지 (작성가이드 p99~100)",
                               table=t, extra={"basis": "명확"}))
        elif TOTAL_TAIL_RE.search(core) or TOTAL_EN_RE.search(en):
            seen.add((rc, m))
            out.append(Finding(S6, "축 하위 합계 멤버 확장", REV, rc, m,
                               f"'{lab}' — 합계 성격 확장 멤버로 보임, 도메인이 합계 역할을 하므로 합계 멤버면 확장 금지 (작성가이드 p99~100)",
                               table=t, extra={"basis": "약함"}))
    return out


def check_dup_member(r, std):
    """표 하나(정의 role 하나) 안에서 같은 축의 멤버가 두 부모 아래 붙은 경우.
    표시(pre) 트리는 한 주석의 여러 표가 축·멤버 노드를 공유해 합쳐지므로 정의(def) 기준으로 본다."""
    out, seen = [], set()
    for u, arcs in r.defs.items():
        rc = re.sub(r"[a-z]+$", "", r.role_code(u) or "")
        kids, doms, hd = defaultdict(list), defaultdict(list), []
        for a, p, c in arcs:
            if a in ("dimension-domain", "domain-member"):
                kids[p].append(c)
            if a == "dimension-domain":
                doms[p].append(c)
            if a == "hypercube-dimension":
                hd.append((p, c))
        for t, ax in hd:
            parents, stack, done = defaultdict(set), list(doms[ax]), set()
            for d in doms[ax]:
                parents[d].add(ax)
            while stack:
                p = stack.pop()
                if p in done:
                    continue
                done.add(p)
                for ch in kids[p]:
                    parents[ch].add(p)
                    stack.append(ch)
            for m, ps in parents.items():
                if len(ps) > 1 and (rc, t, ax, m) not in seen:
                    seen.add((rc, t, ax, m))
                    out.append(Finding(S6, "같은 축 아래 동일 멤버 중복", ERR, rc, m,
                                       f"{r.label(ax)} 하위 {len(ps)}곳({', '.join(sorted(r.label(p) for p in ps))}) "
                                       "(작성가이드 p129)", table=t, extra={"basis": "명확"}))
    return out


def check_hierarchy(r, std):
    """같은 축의 멤버 부모-자식이 표마다 다르거나(), 표준 관계를 뒤집은 경우()."""
    out = []
    parents = defaultdict(lambda: defaultdict(set))   # ax -> member -> {(parent)}
    where = defaultdict(set)
    for u in r.uris():
        rc = r.role_code(u)
        tree = r.pre[u]
        for t, info in r.tables(u).items():
            for ax in info["axes"]:
                stack = [ax]
                while stack:
                    p = stack.pop()
                    for _, ch, _ in tree.get(p, []):
                        if p != ax and name_kind(p) == "member" and not p.endswith("Domain"):
                            parents[ax][ch].add(p)
                            where[(ax, ch, p)].add(rc)
                            if (ch, p) in std.member_pairs:
                                out.append(Finding(S6, "표준 멤버 부모-자식 역전", ERR, rc, ch,
                                                   f"표준은 {r.label(ch)} ⊃ {r.label(p)}인데 반대로 계층화 (작성가이드 p131)",
                                                   table=t, extra={"basis": "명확"}))
                        stack.append(ch)
    # 표준 멤버의 조상 집합(domain-member 기준)
    up = defaultdict(set)
    for par, ch in std.member_pairs:
        up[ch].add(par)

    def std_anc(a, b):
        """a가 b의 표준 조상인가"""
        stack, seen = [b], set()
        while stack:
            for p in up.get(stack.pop(), ()):
                if p == a:
                    return True
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        return False

    for ax, mm in parents.items():
        for m, ps in mm.items():
            if len(ps) > 1:
                # ① 라벨이 같은 확장 상위 멤버를 표마다 따로 만든 것은 같은 계층으로 본다
                # ② 한 상위 멤버가 다른 상위 멤버의 표준 조상이고, 깊은 쪽도 이 멤버의 표준 조상이면
                #    중간 멤버 생략·추가로 생긴 depth 차이(p131) → 허용.
                #    깊은 쪽이 표준상 형제이면(예: 자기주식을 기타자본구성요소 아래) 계층을 바꾼 것이라 남긴다
                # 남는 경우만 확인 권장(자기주식-기타자본구성요소도 확인 권장)
                keys = {("ext", r.label(p)) if not std.is_std(p) else ("std", p) for p in ps}
                stdp = {k[1] for k in keys if k[0] == "std"}
                keys -= {("std", a) for a in stdp
                         if any(std_anc(a, b) and (not std.is_std(m) or std_anc(b, m)) for b in stdp if b != a)}
                if len(keys) <= 1:
                    continue
                detail = " / ".join(f"{r.label(p)}({','.join(sorted(where[(ax, m, p)]))})" for p in sorted(ps))
                out.append(Finding(S6, "같은 축의 멤버 계층 불일치", REV, "(여러 role)", m,
                                   f"{r.label(ax)}: 상위 멤버가 표마다 다름 — {detail} (작성가이드 p118)",
                                   extra={"basis": "명확"}))
    return out


ASSET_CLASS_ALL_AXES = {IF_ + a for a in ("ClassesOfAssetsAxis", "ClassesOfPropertyPlantAndEquipmentAxis",
                                          "ClassesOfIntangibleAssetsAndGoodwillAxis",
                                          "ClassesOfIntangibleAssetsOtherThanGoodwillAxis",
                                          "PropertyPlantAndEquipmentIncludingRightofuseAssetsAxis")}
ASSET_KIND_RE = re.compile(r"토지|건물|구축물|기계|차량|비품|공구|기구|집기|건설중|사용권|시설장치|선박|항공기|투자부동산|"
                           r"영업권|소프트웨어|산업재산권|회원권")
ADJUST_RE = re.compile(r"누계|상각|손상|충당|보조금|부담금|조정|할인|할증|차금|총장부")


def check_carrying_ext_member(r, std):
    """장부금액 축 아래 자산 종류(토지·건물 등) 멤버(작성가이드 p124 사례2). 범위:
    표준 자산분류 멤버는 수정 필요, 확장 멤버는 이름이 자산 종류일 때만 확인 권장,
    공사부담금·전환권조정처럼 장부금액의 차감·조정 구성요소는 대상 아님(p216 모범사례의 정부보조금 [구성요소])."""
    out, seen = [], set()
    for rc, t, ax, m, src in axis_member_pairs(r):
        if ax != CARRYING_AXIS or (rc, m) in seen:
            continue
        if std.is_std(m):
            if _in_axes(std, {CARRYING_AXIS}, m) or not _in_axes(std, ASSET_CLASS_ALL_AXES, m):
                continue
            level, why = ERR, "자산의 분류 축 멤버를 장부금액 축 아래 구성 — 자산의 분류 [축]과 함께 사용"
        else:
            lab = r.label(m) or ""
            if not ASSET_KIND_RE.search(lab) or ADJUST_RE.search(lab):
                continue
            level, why = REV, "자산 종류로 보이는 확장 멤버 — 자산 종류는 자산의 분류 축 등에 구성"
        seen.add((rc, m))
        out.append(Finding(S6, "장부금액 축에 확장 멤버", level, rc, m,
                           f"'{r.label(m)}' — {why} (작성가이드 p124)", table=t, extra={"basis": "명확"}))
    return out


# G5C-016(p177·202·223): 대상은 계정 변동표(기초·기말 행이 있는 표) — 유형자산·투자부동산·무형자산·매출채권 손실충당금
MOVE_ROLES = ("D82210", "D82510", "D82318", "D82242")
MOVE_CLASS_RE = re.compile(r"(약정|약속|사건|중인|완료)")   # 약정 종류·사건 종류·진행 단계 같은 분류 멤버


def check_move_member(r, std):
    from .guide_checks import _is_change_table
    chg = {(r.role_code(u)[:7], t) for u in r.uris() if (r.role_code(u) or "")[:6] in MOVE_ROLES
           for t, info in r.tables(u).items() if t != CONSEP_TABLE and _is_change_table(info)}
    out, seen = [], set()
    for rc, t, ax, m, src in axis_member_pairs(r):
        if std.is_std(m) or (rc, m) in seen or rc == "(context)":
            continue
        if (re.sub(r"[a-z]+$", "", rc or "")[:7], t) not in chg:
            continue
        lab = r.label(m) or ""
        if MOVE_MEMBER_RE.search(lab) and not MOVE_CLASS_RE.search(lab):
            seen.add((rc, m))
            out.append(Finding(S6, "변동내역을 멤버로 확장", REV, rc, m,
                               f"'{r.label(m)}' — 취득·처분 등 변동은 표준 변동 행으로 구성 (작성가이드 p177·202)",
                               table=t, extra={"basis": "명확"}))
    return out


def check_same_name_std(r, std):
    """G4-004·G5B-029: 한글 이름이 같은 표준요소가 있는데 확장요소를 쓴 경우.
    같은 요소=같은 금액 원칙에 따라, 표준요소와 같은 기준일에 같은 금액이 있으면 표준으로 바꿔야 하고,
    표준요소가 문서에서 다른 금액으로 쓰이고 있으면(본문과 범위가 다른 주석 등) 확장이 맞으므로 넘어간다.
    입력불가(abstract) 확장은 행 구분용(보기 좋게 묶는 제목)이라 데이터 검증 대상이 아니다 → 지적하지 않는다
    (작성기 표준추가 목록에 없는 표준 개요까지 걸리는 문제가 있어 개요는 보지 않는다)."""
    by_label, by_abs = defaultdict(list), defaultdict(list)
    for q, l in std.label_ko.items():
        if q in std.deprecated:
            continue
        if std.kind(q) == "item":
            by_label[l.replace(" ", "")].append(q)
        elif q.endswith("Abstract"):
            by_abs[re.sub(r"\s|\[개요\]", "", l)].append(q)
    vals = defaultdict(lambda: defaultdict(set))   # 요소 → 기준일 → 금액 집합
    for f in r.facts:
        v, c = _num(f["value"]), r.contexts.get(f["ctx"]) or {}
        if v is not None and v != 0:
            vals[f["q"]][c.get("instant") or c.get("end")].add(v)
    done = {x.element for x in check_cf_ext_alike(r, std)}   # 10번에서 이미 지적한 요소는 중복 제외
    out, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        for n, p, tb, t, pl in r.role_nodes(u):
            if std.is_std(n) or r.kind(n) != "item" or n in seen or n in done:
                continue
            if (r.ext_attr.get(n) or {}).get("abstract"):
                continue   # 개요(입력불가) 행은 점검하지 않음
            pt = (r.ext_attr.get(n) or {}).get("period_type")
            # 기간유형이 달라도 후보로 둔다(확장요소 쪽 기간유형이 틀린 경우가 많음) — 같은 유형을 앞에
            cands = sorted(by_label.get((r.label_ko.get(n) or "").replace(" ", ""), []),
                           key=lambda q: (std.concepts.get(q) or {}).get("period_type") != pt)
            # 한글 이름이 같아도 영문명이 다르면 다른 개념(G4-015) — 예: 기타수익(Other income) vs OtherRevenue
            en = re.sub(r"[^a-z]", "", (r.label_en.get(n) or "").lower())
            if en:
                cands = [q for q in cands if re.sub(r"[^a-z]", "", std.label_en.get(q, "").lower()) == en]
            # 현금흐름표·현금흐름 주석(조정 표)의 확장 행에 손익계산서용 표준요소는 용도가 다름(p134 가) — 현금흐름용 후보만 본다
            if rc.startswith("D851") or _stmt(rc) == "5":
                cands = [q for q in cands if re.search(r"^Adjustments|ClassifiedAs\w*Activities", q.split(":")[-1])]
            if not cands:
                continue
            seen.add(n)
            mine = vals.get(n, {})
            same = [q for q in cands if any(mine[d] & vals[q].get(d, set()) for d in mine)]
            used = [q for q in cands if vals.get(q)]
            if same:
                why, basis = f"표준요소 {same[0]}과 같은 기준일·같은 금액 — 표준요소로 교체", "명확"
            elif used:
                # 금액이 달라 확장한 것도 가이드에 허용 근거가 없다(p134 나·라: 같은·비슷한 개념은 확장 금지, 없을 때만 확장).
                #  개념이 정말 같은지는 자동으로 단정하지 못하므로 사람 확인(약함)으로 표시한다. 범주별 금융상품은 모범사례 구조를 안내.
                why = (f"같은 이름의 표준요소 {used[0]}이 문서에서 다른 금액으로 쓰임 — 같은 개념이면 금액 범위를 맞춰 표준요소를 쓰고, "
                       "범위가 다르면 확장 행 대신 축·구성요소로 구분(p134)")
                if rc[:6] == "D82243":
                    why += " · 범주별 금융상품은 금융자산·금융부채 행 + 금융자산(부채)의 종류 축으로 구성하면 확장이 필요 없음(모범사례 p316~317)"
                basis = "약함"
            elif mine:
                why, basis = f"같은 이름의 표준요소 {cands[0]}이 있음(문서에서 미사용) — 표준요소 사용 검토", "명확"
            elif getattr(r, "frame", False):
                continue   # 틀 단계: 금액이 아직 없어서 대조 불가 → 값 입력 후 판정
            else:
                why, basis = f"같은 이름의 표준요소 {cands[0]}이 있음(금액 미입력이라 금액 대조 불가) — 표준요소 사용 검토", "약함"
            out.append(Finding(S6, "같은 이름 표준요소가 있는 확장요소", ERR, rc, n, why,
                               table=t or "", parent=tb or "", extra={"basis": basis}))
    return out


def check_key_account_ext(r, std):
    out, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        if not re.match(r"D[1-4]", rc):
            continue
        for n, p, tb, t, pl in r.role_nodes(u):
            if std.is_std(n) or r.kind(n) != "item" or n in seen:
                continue
            lab = (r.label_ko.get(n) or "").replace(" ", "")
            # 당기순이익류(분기·반기·연결, (손실) 변형)는 G4-025와 같은 판정, 지배기업 귀속분은 제외
            ni = NI_RE.match(lab) and "지배" not in lab
            if lab in KEY_GUIDE or ni:
                basis = "명확"
            elif lab in KEY_ACCOUNTS:
                basis = "약함"
            else:
                continue
            seen.add(n)
            out.append(Finding(S6, "중요계정 확장", ERR, rc, n,
                               f"'{r.label_ko.get(n)}' — 중요계정은 표준요소로 공시 (작성가이드 p79)"
                               + ("" if basis == "명확" else ", 가이드 예시 밖 계정이라 확인"),
                               extra={"basis": basis}))
    return out


PL_DUAL_RE = re.compile(r"ExpenseIncome|IncomeExpense|GainLoss|GainsLosses")
# '(수익)'·'(환입)'·'(대손충당금환입)'처럼 괄호 안에 반대 개념이 있거나 '손익'이면 겸용 행
PL_DUAL_LAB_RE = re.compile(r"\([^)]*(수익|환입|이익)\)|손익")


def check_negative_expense(r, std):
    """G4-020·G4-022: 차변 비용 요소에 음수(p83·88). 범위는 비용 요소 —
    연결/별도 축·공시금액 멤버만 있는 fact를 차원 없음으로 보고, 이익·손실 겸용 요소와 차감 멤버 열은 제외(p150)."""
    out, neg = [], defaultdict(list)
    for f in r.facts:
        q = f["q"]
        info = std.concepts.get(q)
        if not info or info["balance"] != "debit":
            continue
        loc = q.split(":")[-1]
        if not re.search(r"(Expense|CostOfSales|CostOfRevenue|SellingGeneralAndAdministrative)", loc) or \
                loc.startswith(("Adjustments", "IncreaseDecrease", "Reversal")):
            continue
        # 이익·손실 겸용 요소는 손실이면 음수가 정상(p150, G4-024의 skip과 같은 방식)
        if PL_DUAL_RE.search(loc) or PL_DUAL_LAB_RE.search(std.label_ko.get(q, "")):
            continue
        v = _num(f["value"])
        dims = _dims_wo_consep(r, f)
        if set(dims.values()) & DEDUCT_MEMBERS:
            continue   # 차감 구성요소 열은 음수 입력이 정상(p150)
        if dims and set(dims.values()) == {REPORTED_MEMBER}:
            dims = {}
        if v is not None and v < 0 and not dims:
            neg[q].append((v, f["ctx"]))
    for q, lst in neg.items():
        v, ctx = lst[0]
        # 3개월 열에만 음수면 누적 − 이전 분기로 계산된 값일 수 있음을 알린다(G5B-058과 같은 사정)
        q3 = all(_months(r.contexts.get(c) or {}) == 3 for _, c in lst)
        out.append(Finding(S6, "비용 요소 음수 입력", REV, "(fact)", q,
                           f"차변 비용 요소에 음수 {v:,.0f} — 반대부호 라벨로 처리 (작성가이드 p88)"
                           + (" (3개월 열만 음수)" if q3 else ""),
                           extra={"basis": "명확", "ctx": ctx}))
    return out


def _months(c):
    """기간 context의 개월 수(대략). 시점 context는 0."""
    from datetime import date
    try:
        s, e = date.fromisoformat(c.get("start")), date.fromisoformat(c.get("end"))
    except (TypeError, ValueError):
        return 0
    return round(((e - s).days + 1) / 30.4)


# ---------------------------------------------------------------------------
# 확장요소 속성·라벨
# ---------------------------------------------------------------------------

CURRENCIES = {
    "USD": ["미국달러", "미화", "미달러", "US달러", "USD"], "EUR": ["유로", "EUR"], "JPY": ["엔화", "일본엔", "JPY"],
    "CNY": ["위안", "인민폐", "CNY", "RMB"], "GBP": ["파운드", "GBP"], "CHF": ["스위스프랑", "스위스 프랑", "CHF"],
    "HKD": ["홍콩달러", "HKD"], "SGD": ["싱가포르달러", "싱가폴달러", "SGD"], "AUD": ["호주달러", "AUD"],
    "CAD": ["캐나다달러", "CAD"], "TWD": ["대만달러", "TWD", "NTD"], "THB": ["태국바트", "THB"], "VND": ["베트남동", "VND"],
    "IDR": ["루피아", "IDR"], "INR": ["인도루피", "INR"], "MYR": ["링깃", "MYR"], "PHP": ["필리핀페소", "PHP"],
    "MXN": ["멕시코페소", "MXN"], "BRL": ["헤알", "BRL"], "PLN": ["즐로티", "PLN"], "CZK": ["코루나", "CZK"],
    "HUF": ["포린트", "HUF"], "SEK": ["스웨덴크로나", "SEK"], "NOK": ["노르웨이크로네", "NOK"], "DKK": ["덴마크크로네", "DKK"],
    "RUB": ["루블", "RUB"], "AED": ["디르함", "AED"], "SAR": ["사우디리얄", "SAR"], "NZD": ["뉴질랜드달러", "NZD"],
    "ZAR": ["남아공랜드", "ZAR"], "KZT": ["텡게", "KZT"],
    "PEN": ["페루솔", "PEN"], "ARS": ["아르헨티나페소", "ARS"], "CLP": ["칠레페소", "CLP"], "COP": ["콜롬비아페소", "COP"],
    "EGP": ["이집트파운드", "EGP"], "PKR": ["파키스탄루피", "PKR"], "KWD": ["쿠웨이트디나르", "KWD"], "QAR": ["카타르리얄", "QAR"],
    "ILS": ["셰켈", "ILS"], "RON": ["루마니아레우", "RON"], "BGN": ["불가리아레프", "BGN"], "MNT": ["투그릭", "MNT"],
    "KHR": ["리엘", "KHR"], "MMK": ["미얀마짯", "MMK"], "BDT": ["타카", "BDT"], "LKR": ["스리랑카루피", "LKR"],
    "UZS": ["우즈베키스탄숨", "UZS"], "TRY": ["튀르키예리라", "터키리라"], "KRW": ["원화", "KRW"],
}
ISO_RE = re.compile(r"iso4217:([A-Z]{3})")


def currencies_in_text(text):
    found = set()
    if not text:
        return found
    for code, words in CURRENCIES.items():
        for w in words:
            if w.isascii():
                if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % w, text):
                    found.add(code)
            elif w in text:
                found.add(code)
    # '달러' 단독은 USD로 본다(다른 달러가 없을 때)
    if "달러" in text and not found & {"HKD", "SGD", "AUD", "CAD", "TWD", "NZD"}:
        found.add("USD")
    return found


def currencies_in_name(q):
    """요소명(CamelCase)의 단어 중 통화코드. DART 작성기 자동 접미어(Of...LineItems...) 앞부분만 본다."""
    from .guide_checks import _local   # 확장요소는 작성기 ID 꼬리(Of+소속 항목·표)를 뗀 자기 이름
    loc = re.split(r"Of[A-Z]\w*(LineItems|Table|Domain|Axis)", _local(q))[0]
    return {w.upper() for w in split_camel(loc) if w.upper() in CURRENCIES and w.upper() != "KRW"}


def element_currencies(r, q):
    """요소명·한글명·영문명이 가리키는 통화."""
    ko = " ".join(t for lang, role, t in r.all_labels.get(q, []) if lang == "ko") or r.label(q)
    en = " ".join(t for lang, role, t in r.all_labels.get(q, []) if lang == "en")
    return dict(name=currencies_in_name(q) - {"KRW"}, ko=currencies_in_text(ko) - {"KRW"},
                en=currencies_in_text(en) - {"KRW"})


KRW_WORD_RE = re.compile(r"(원화|환산|KRW|won)", re.I)


# 약정·한도·보증·담보·명목 금액은 외화 원금으로 넣는 경우가 많은 항목(통화코드 확장 + 외화 단위).
#  표준요소 + 통화 멤버 + KRW 단위면 외화속성 미사용 의심 → 확인 권장(값이 없어 원화환산인지 단정 못 함)
COMMIT_RE = re.compile(r"(Commitment|Guarantee|Collateral|Pledge|Limit|Notional|CreditLine|LineOfCredit|Facility)", re.I)


def member_currencies(r, ctx):
    """문맥(ctx) 멤버 중 통화 멤버(이름·라벨이 통화코드)의 통화."""
    out = set()
    for m in re.findall(r"((?:ifrs-full|dart|entity\d+)_[A-Za-z0-9]+?Member)(?![A-Za-z0-9])", ctx or ""):
        q = m.replace("_", ":", 1)
        nm = q.split(":")[-1][:-len("Member")]
        lab = r.label(q).replace("[구성요소]", "").strip()
        for code in CURRENCIES:
            if code != "KRW" and (nm.upper() == code or lab.upper() == code or lab in CURRENCIES[code]):
                out.add(code)
    return out


def _code_in_name(q, code):
    """요소 이름(작성기 자동 접미어 '…LineItems…Table…' 앞부분)에 통화코드가 있는지 — 대소문자 무시(Usd·USD·…Assetsusd)."""
    from .guide_checks import _local   # 확장요소는 작성기 ID 꼬리(Of+소속 항목·표)를 뗀 자기 이름
    nm = re.split(r"(?i)line_?items?|table", _local(q))[0]
    return code.lower() in nm.lower()


def check_currency_unit(r, std):
    """요소 자체(이름·한글명·영문명)가 가리키는 통화와 입력 단위가 다르면 오류.
    통화 멤버(예: 통화 [축] 유로)로 나눈 원화환산 금액은 KRW가 정상이므로 멤버 통화는 단위 불일치 근거로 쓰지 않는다."""
    out, seen = [], set()
    cache = {}
    fx_units = defaultdict(set)   # 확장요소별 외화 단위(여러 통화가 섞이면 통화별로 각각 확장해야 함, p137)
    for f in r.facts:
        if not f["unit"] or "iso4217" not in f["unit"] or "shares" in f["unit"]:
            continue
        unit = set(ISO_RE.findall(f["unit"]))
        q = f["q"]
        if q not in cache:
            cache[q] = element_currencies(r, q)
        cur = cache[q]
        el = cur["name"] | cur["ko"] | cur["en"]
        labels = r.label(q) + " " + r.label_en.get(q, "")
        fx = unit - {"KRW"}
        if fx and not std.is_std(q):
            fx_units[q] |= fx
        # G5B-036: 외화 단위 fact는 요소 이름에 그 ISO 4217 코드가 있어야 한다(p136).
        # 라벨·멤버에만 통화가 있거나('외화', 'Fx', 'Rmb' 등) 표준요소에 외화 단위를 직접 붙인 것도 수정 필요
        if fx and std.is_std(q):
            level, why = ERR, (f"표준요소에 외화 단위 {f['unit']} 직접 입력 — 외화 금액은 표준 요소 이름 뒤에 통화코드를 붙여 확장"
                               f"(예: {q.split(':')[-1]}{sorted(fx)[0]}), 원화환산 금액이면 KRW + 통화 축(작성가이드 p136~137)")
        elif fx and not all(_code_in_name(q, x) for x in fx):
            level, why = ERR, (f"외화 단위 {f['unit']}로 입력했는데 요소 이름에 통화코드 {','.join(sorted(fx))}가 없음"
                               + (f"(라벨·이름의 통화 표시 {','.join(sorted(el))})" if el else "")
                               + " — 요소 이름에 ISO 4217 코드를 넣어 확장(작성가이드 p136)")
        elif el and not (unit & el):
            if unit == {"KRW"} and KRW_WORD_RE.search(labels):
                level, why = REV, (f"요소명·라벨에 {','.join(sorted(el))} 표시가 있으나 라벨상 원화(환산) 금액 — "
                                   "요소명/라벨 정리 필요")
            elif std.is_std(q):
                # 표준요소는 외화속성 지정 대상이 아님 — 라벨의 통화 표시가 원화환산 표기일 수 있어 확인 권장
                level, why = REV, (f"표준요소 라벨에 {','.join(sorted(el))} 표시, 입력 단위 {f['unit']} — "
                                   "원화환산 금액이면 라벨 정리, 외화 원금이면 통화코드 확장요소 사용(작성가이드 p136)")
            elif not (cur["ko"] | cur["en"]):
                # 통화가 요소 ID에만 있고 한글·영문 라벨에는 없음. → 수정 필요
                #  (예: 파생상품 미결제약정 NotionalAmountUsd·Eur 를 원화 단위로)
                level, why = ERR, (f"요소 ID에 통화코드 {','.join(sorted(el))}가 있는데 원화 단위 {f['unit']}로 입력 — "
                                   "외화 원금이면 외화 단위로, 원화 금액이면 요소 ID에서 통화코드를 빼야 함(작성가이드 p136)")
            else:
                level, why = ERR, (f"요소가 가리키는 통화 {','.join(sorted(el))} ≠ 입력 단위 {f['unit']} — "
                                   "item 외화속성 미지정으로 표 단위(KRW)가 적용된 것으로 보임")
        elif unit == {"KRW"} and std.is_std(q) and COMMIT_RE.search(q.split(":")[-1]) and not KRW_WORD_RE.search(labels):
            mc = member_currencies(r, f.get("ctx"))
            if not mc:
                continue
            level, why = REV, (f"약정·한도·보증·담보 금액을 표준요소 + 통화 멤버({','.join(sorted(mc))}) + 원화 단위로 입력 — "
                               f"외화 원금이면 통화코드 확장요소(예: {q.split(':')[-1]}{sorted(mc)[0].capitalize()}) + 외화 단위로(작성가이드 p136~137). "
                               "원화환산 금액이면 라벨에 '원화환산' 표시")
        else:
            continue
        # 외화 금액인데 item 외화속성을 안 써서 표 단위(KRW)로 들어간 것 = 정정공시 사례(정정공시 위험)
        risk = level == ERR and "외화속성 미지정" in why
        k = (q, f["unit"], level)
        if k in seen:
            continue
        seen.add(k)
        out.append(Finding(S7, "외화 item의 통화 속성", level, "(fact)", q, why,
                           extra={"basis": "명확" if level == ERR else "약함", "ctx": f["ctx"],
                                  "note": "정정공시 사례: CHF 확장 item에 스위스프랑 외화속성 미지정" if risk else "",
                                  "risk": risk}))
    for q, us in sorted(fx_units.items()):
        if len(us) > 1:
            out.append(Finding(S7, "외화 item의 통화 속성", ERR, "(요소정의)", q,
                               f"한 확장요소에 외화 단위 여러 개({','.join(sorted(us))}) — 통화별로 각각 확장(작성가이드 p137)",
                               extra={"basis": "명확"}))
    # 요소명/한글명/영문명의 통화가 서로 다른 확장요소
    for q in r.ext_kind:
        cur = element_currencies(r, q)
        sets = [x for x in (cur["name"], cur["ko"], cur["en"]) if x]
        if len(sets) >= 2 and any(a != sets[0] for a in sets[1:]):
            out.append(Finding(S7, "외화 item의 통화 속성", ERR, "(요소정의)", q,
                               f"통화 표시 불일치 — 요소명 {sorted(cur['name'])}, 한글명 {sorted(cur['ko'])}, "
                               f"영문명 {sorted(cur['en'])}", extra={"basis": "명확"}))
    return out


HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎ]")
# (한글, 한글 반대어, 영문 정상, 영문 반대)
ANTONYMS = [("이익", "손실|손익", "gain|profit|income", "loss"), ("손실", "이익|손익", "loss", "gain|profit"),
            ("수익", "비용", "revenue|income|gain", "expense"), ("비용", "수익", "expense|cost", "revenue"),
            ("증가", "감소", "increase|addition", "decrease"), ("감소", "증가", "decrease|reduction", "increase"),
            ("자산", "부채", "ass+et", "liabilit"), ("부채", "자산", "liabilit|debt|borrowing|payable", "ass+et"),
            ("채권", "채무", "receivable|loan", "payable"), ("채무", "채권", "payable|debt|liabilit", "receivable"),
            ("유입", "유출", "inflow|proceeds|receipt", "outflow|payment"),
            ("유출", "유입", "outflow|payment", "inflow|proceeds")]


def split_camel(s):
    return [w.lower() for w in re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", s)]


UDF_RE = re.compile(r"^(udf_(NOTE|IS|CF|CE|BS)_\d+|NewItem|새아이템|새\s*아이템)", re.I)
# 이익·손실 겸용 복합어(요소 ID)와 영문명의 겸용·담보 표기는 반대 개념 판정에서 뺀다(G4-016 ①②)
DUAL_ID_RE = re.compile(r"ProfitLoss|GainLoss|GainsLosses|EarningsLoss|IncomeLoss|IncomeExpense|ExpenseIncome")
# 의미 없는 영문 라벨: abc·test·aaa·x 같은 짧은 임시 글자, 숫자·기호만, 작성기 기본 이름(Title, New Item, 영어명 등)
EN_JUNK_RE = re.compile(r"^(abc+|test\d*|temp\d*|tmp\d*|a{3,}|x{3,}|z{3,}|asdf\w*|qwer\w*|sample\d*|"
                        r"title\s*\d*|new\s*item\s*(\(\d+\))?|item\s*\d*|label\s*\d*|english\s*name|eng\s*name|"
                        r"text\s*-?\s*box\s*\d*|"   # 텍스트박스 확장을 'text box'로 둔 것(설명회에서 강하게 지적)
                        r"[0-9\W_]+)$", re.I)   # 두 글자 약칭(DS·DX 등 부문명)은 정상이라 잡지 않음
EN_NEUTRAL_RE = re.compile(r"earnings\s*\(loss\)|\((loss|losses|gain|gains|income|expense)\)|"
                           r"(pledged\s*as\s*)?security\s*for\s*liabilit[a-z]*|pledged\s*as\s*security", re.I)


def _id_base(r, q, parents):
    """요소 ID에서 작성기가 붙인 'Of+상위 요소명' 꼬리를 모두 뗀 앞부분."""
    loc = q.split(":")[-1]
    cut = len(loc)
    for p in parents:
        i = loc.find("Of" + p.split(":")[-1])
        if 0 < i < cut:
            cut = i
    return re.split(r"Of[A-Z]\w*(LineItems|Table|Domain|Axis)", loc[:cut])[0]


def check_en_label(r, std):
    out = []
    used = used_elements(r)
    parents = defaultdict(set)   # 요소 → pre 상위 요소들(조상 전체)
    for u in r.pre:
        par = {ch: p for p, lst in r.pre[u].items() for _, ch, _ in lst}
        for ch in par:
            x = par.get(ch)
            while x and x not in parents[ch]:
                parents[ch].add(x)
                x = par.get(x)
    for q, sg in r.ext_kind.items():
        if q not in used:
            continue   # 스키마에만 남은 미사용 요소(표시관계·fact 없음)는 공시되지 않으므로 제외
        labs = r.all_labels.get(q, [])
        en = {role: t for lang, role, t in labs if lang == "en"}
        ko = {role: t for lang, role, t in labs if lang == "ko"}
        loc = q.split(":")[-1]
        base = _id_base(r, q, parents.get(q, ()))

        def add(level, why):
            out.append(Finding(S7, "확장요소 영문명", level, "(요소정의)", q, why,
                               extra={"basis": "명확", "en": en.get("label", ""), "ko": ko.get("label", "")}))

        # 새 아이템 기본 ID(udf_NOTE_+숫자 등)를 그대로 둔 확장요소 — ID는 만든 뒤 바꿀 수 없어 요소를 다시 만들어야 함(p81 그림)
        # 개요(입력불가)·표 제목도 영문명은 제대로 — 영문명 관련은 모두 수정 필요
        if UDF_RE.match(loc):
            # 기본 이름 ID 는 수정 필요
            add(ERR, f"요소 ID가 작성기 기본 이름({loc[:30]}) — 처음 만들 때 기본 영어명으로 ID를 정해야 함(작성가이드 p81)")
        if "label" not in en or not en["label"].strip():
            add(ERR, "영문 기본 라벨 없음")
            continue
        for role, t in en.items():
            if HANGUL_RE.search(t):
                add(ERR, f"영문 라벨({role})에 한글 포함: {t}")
        for role in ko:
            if role not in en:
                add(ERR, f"한글 {role} 라벨은 있는데 영문 {role} 라벨 없음")
        e = en["label"]
        en_cmp = EN_NEUTRAL_RE.sub(" ", e)
        if not re.match(r"Title\d{8,}", loc) and not UDF_RE.match(loc):   # Title+번호는 작성기 자동 이름(표 개요·항목)
            words = set(split_camel(DUAL_ID_RE.sub("", base)))
            ewords = set(re.findall(r"[a-z]+", en_cmp.lower()))
            # 요소명(영문명 기반 생성)에 있는 반대 개념이 영문명에서 바뀐 경우
            for a, b in (("gain", "loss"), ("profit", "loss"), ("income", "expense"), ("increase", "decrease"),
                         ("receivable", "payable"), ("asset", "liability"), ("inflow", "outflow")):
                for x, y in ((a, b), (b, a)):
                    if x in words and y not in words and any(w.startswith(y) for w in ewords) and \
                            not any(w.startswith(x) for w in ewords):
                        add(ERR, f"요소 ID에는 '{x}'인데 영문 기본 라벨에는 '{y}': {e} — 처음 만든 뒤 이름을 바꿨다면 다른 라벨(합계·부호반전 등)에 예전 이름이 남았는지 확인")
        k = ko.get("label", "")
        for kw, kw_op, ok_re, bad_re in ANTONYMS:
            if kw in k and not re.search(kw_op, k) and re.search(bad_re, en_cmp, re.I) and not re.search(ok_re, en_cmp, re.I):
                # 영문명이 한글명과 맞는지까지 따지지 않는다(의미 없는 값·기본값만 수정 필요) → 반대 의미 의심은 확인 권장
                add(REV, f"한글명 '{k}'({kw})와 영문명 '{e}' 의미 반대 의심")
                break
        # 별칭 등 영문은 '의미 없는 값·작성기 기본값'만 잡는다(한끗 차이는 무시)
        for role, er in en.items():
            s = (er or "").strip()
            if s and EN_JUNK_RE.match(s):
                add(ERR, f"영문 {role} 라벨이 의미 없는 값·작성기 기본값: '{s}'")
    return out


# ---------------------------------------------------------------------------
# 7-2. 확장요소의 기간 유형(특정기간/특정시점)·차변/대변 속성
# ---------------------------------------------------------------------------

FLOW_W = ("수익", "비용", "손익", "이익", "손실", "차익", "차손", "증가", "감소", "변동", "조정", "취득", "처분",
          "지급", "수취", "유입", "유출", "발생", "상각비", "환입", "전입", "납부", "매출", "원가", "배당금")
STOCK_W = ("잔액", "누계", "미지급", "미수", "선급", "선수", "채권", "채무", "충당금", "충당부채", "잉여금",
           "자본금", "자산", "부채", "장부금액", "기말", "기초", "적립금")
STRONG_FLOW_W = ("전입", "증가", "감소", "증감", "처분", "취득", "대체", "상환", "지급", "수취", "변동", "발생", "환입", "납부")
DEBIT_W = ("비용", "손실", "원가", "상각비", "차손")
CREDIT_W = ("수익", "이익", "차익", "환입")


def _ext_monetary(r):
    for q, a in r.ext_attr.items():
        if r.ext_kind.get(q) == "xbrli:item" and "monetary" in a["type"].lower():
            yield q, a


def _uses(r):
    """요소별 (role_code, pref_label) 사용 위치."""
    if not hasattr(r, "_use"):
        m = defaultdict(set)
        for u, tree in r.pre.items():
            rc = r.role_code(u)
            for lst in tree.values():
                for _, ch, pl in lst:
                    m[ch].add((rc, (pl or "").rsplit("/", 1)[-1]))
        r._use = m
    return r._use


def _stmt(rc):
    """본문 role: 2=재무상태표, 3·4=손익, 5=현금흐름."""
    return rc[1] if rc and rc[0] == "D" and len(rc) == 7 and rc[1] in "2345" else ""


def check_period_type(r, std):
    out, seen = [], set()
    for q, a in _ext_monetary(r):
        pt, ko = a["period_type"], r.label_ko.get(q, "")
        uses = _uses(r).get(q, set())
        rcs = sorted({rc for rc, _ in uses})
        rc0 = rcs[0] if rcs else "(요소정의)"
        pls = {pl for _, pl in uses}
        if pt == "duration" and pls & {"periodStartLabel", "periodEndLabel"}:
            out.append(Finding(S7, "확장요소 기간 유형", ERR, rc0, q,
                               "기초·기말 라벨로 쓰는 잔액 요소인데 특정기간(Duration) — 특정시점(Instant)이어야 함"))
            continue
        bad = [rc for rc in rcs if (_stmt(rc) == "2" and pt == "duration") or (_stmt(rc) in ("3", "4", "5") and pt == "instant")]
        if bad:
            want = "특정시점(Instant)" if pt == "duration" else "특정기간(Duration)"
            out.append(Finding(S7, "확장요소 기간 유형", ERR, bad[0], q,
                               f"{r.role_label(bad[0])}에 쓰인 요소인데 {'특정기간' if pt == 'duration' else '특정시점'} — {want}이어야 함"))
            continue
        # 쓰인 표가 변동표(기초·기말 라벨 행이 있는 표)인지 잔액표인지 보고 판정한다
        if (rc0, q) in seen:
            continue
        seen.add((rc0, q))
        places = _table_places(r).get(q, [])
        moves = [x for x in places if x[2] == "변동표"]
        sib = _sibling_periods(r, std).get(q) or {}
        n_all = sum(sib.values())
        k2 = ko.replace("취득일", "")
        # 증감·전입·처분 같은 거래 표현이 있으면 '잉여금 전입'처럼 잔액 단어가 섞여도 흐름으로 본다
        flow = any(w in k2 for w in STRONG_FLOW_W) or             (any(w in k2 for w in FLOW_W + ("사용", "인식", "행사", "취소")) and not any(w in k2 for w in STOCK_W))
        stock = any(w in k2 for w in STOCK_W) and not flow
        # 수익·비용·이자·배당 같은 손익(거래) 개념은 잔액표에 있어도 Duration이 맞음(p349) — 미수수익·선급비용 같은 잔액 계정은 제외
        # (요소 ID는 처음 만든 영어명이 남을 수 있고 'ImpairmentLoss'처럼 잔액 차감에도 쓰여 한글 라벨로만 본다)
        pl_concept = re.search(r"수익|비용|이익|손실|배당|차손|차익", k2)   # 손상차손 등 손익 행(G5C-076) and not re.search(r"잉여금|미수|미지급|선급|선수|충당|누계", k2)
        if pt == "instant" and moves and flow:
            rc, t, _ = moves[0]
            out.append(Finding(S7, "확장요소 기간 유형", ERR, rc, q,
                               f"변동표 '{r.label(t)}'의 변동 행인데 특정시점(Instant) — 특정기간(Duration)이어야 함",
                               table=t))
        elif pt == "duration" and not moves and not flow and not pl_concept and n_all >= 3 and sib.get("instant", 0) / n_all >= 0.8:
            rc, t, _ = places[0] if places else (rc0, "", "")
            out.append(Finding(S7, "확장요소 기간 유형", ERR, rc, q,
                               f"잔액표{(" '" + r.label(t) + "'") if t else ''}의 잔액 행인데 특정기간(Duration) — "
                               f"특정시점(Instant)이어야 함 (같은 표 표준 행 {n_all}개 중 {sib['instant']}개가 특정시점)",
                               table=t))
        elif pt == "instant" and not moves and not stock and n_all >= 3 and sib.get("duration", 0) / n_all >= 0.8 \
                and not pls & {"periodStartLabel", "periodEndLabel"}:   # 변동표의 기초·기말 잔액 행은 Instant가 정상(p219 그림)
            rc, t, _ = places[0] if places else (rc0, "", "")
            out.append(Finding(S7, "확장요소 기간 유형", ERR, rc, q,
                               f"발생액 표{(" '" + r.label(t) + "'") if t else ''}의 행인데 특정시점(Instant) — "
                               f"특정기간(Duration)이어야 함 (같은 표 표준 행 {n_all}개 중 {sib['duration']}개가 특정기간)",
                               table=t))
    return out


def _table_places(r):
    """요소별 [(role_code, 표, '변동표'|'잔액표')] — 표의 행에 기초·기말 라벨이 있으면 변동표."""
    if not hasattr(r, "_tplaces"):
        m = defaultdict(list)
        for u in r.uris():
            rc = r.role_code(u)
            tabs = r.tables(u)
            for n, par, tb, t, pl in r.role_nodes(u):
                if n not in r.ext_attr or not t or t not in tabs:
                    continue
                kind = "변동표" if any((x or "").endswith(("periodStartLabel", "periodEndLabel"))
                                      for x in tabs[t]["pref_labels"]) else "잔액표"
                if (pl or "").endswith(("periodStartLabel", "periodEndLabel")):
                    continue     # 기초·기말 행 자체는 잔액(위에서 따로 판정)
                if (rc, t, kind) not in m[n]:
                    m[n].append((rc, t, kind))
        r._tplaces = m
    return r._tplaces


def _sibling_periods(r, std):
    """확장 금액요소별, 같은 부모(같은 role) 아래 표준 금액 행들의 기간 유형 개수."""
    if not hasattr(r, "_sibp"):
        res = defaultdict(lambda: defaultdict(int))
        for u, tree in r.pre.items():
            for par, lst in tree.items():
                kids = [ch for _, ch, _ in lst]
                exts = [ch for ch in kids if ch in r.ext_attr]
                if not exts:
                    continue
                cnt = defaultdict(int)
                for _, ch, pl in lst:
                    if pl and pl.endswith(("periodStartLabel", "periodEndLabel")):
                        continue   # 변동표의 기초·기말 잔액 행은 비교에서 뺀다
                    c = std.concepts.get(ch)
                    if c and "monetary" in (c.get("type") or "").lower() and c.get("period_type"):
                        cnt[c["period_type"]] += 1
                for e in exts:
                    for k, v in cnt.items():
                        res[e][k] += v
        r._sibp = res
    return r._sibp


def _balance(r, std, q):
    if q in r.ext_attr:
        return r.ext_attr[q]["balance"]
    return (std.concepts.get(q) or {}).get("balance")


def check_balance(r, std):
    out, flagged = [], set()
    # ① 계산관계: 더하기(+)는 같은 차대, 빼기(−)는 반대 차대여야 함 (확장요소가 낀 관계만)
    for u, arcs in r.cal.items():
        rc = r.role_code(u)
        for par, ch, w in arcs:
            if par not in r.ext_attr and ch not in r.ext_attr:
                continue
            bp, bc = _balance(r, std, par), _balance(r, std, ch)
            if not bp or not bc or w is None:
                continue
            if (w > 0) != (bp == bc):
                tgt = ch if ch in r.ext_attr else par
                if (rc, tgt) in flagged:
                    continue
                flagged.add((rc, tgt))
                out.append(Finding(S7, "확장요소 차변/대변", ERR, rc, tgt,
                                   f"계산관계 불일치: {r.label(par)}({bp}) {'+' if w > 0 else '−'} {r.label(ch)}({bc}) — "
                                   f"{'더하기는 같은' if w > 0 else '빼기는 반대'} 차변/대변이어야 함"))
    # ② 이름의 뜻과 차대 비교 (비용·손실 → 차변, 수익·이익 → 대변)
    done = {q for _, q in flagged}
    for q, a in _ext_monetary(r):
        bal, ko = a["balance"], r.label_ko.get(q, "")
        if not bal or q in done or any(w in ko for w in STOCK_W if w not in ("자산", "부채")):
            continue
        # 손익·현금흐름(본문, 현금흐름 주석)에 쓰인 요소만 — 변동표의 감소 행 등은 이름만으로 판단 불가
        rcs = sorted({rc for rc, _ in _uses(r).get(q, set())})
        if not any(_stmt(rc) in ("3", "4", "5") or rc.startswith("D8511") for rc in rcs):
            continue
        # 현금 유입·유출, 차감, 증감, 전입(환입) 표현은 이름과 차대 방향이 달라도 정상일 수 있음
        if re.search(r"유입|유출|지급|수취|상환|차감|증가|감소|증감|전입", ko):
            continue
        d = any(w in ko for w in DEBIT_W)
        cr = any(w in ko for w in CREDIT_W)
        if d == cr or "손익" in ko:
            continue
        want = "debit" if d else "credit"
        if bal != want:
            # 속성이 반대여도 값을 음수로 넣어 결합 의미(속성×부호)가 이름 방향과 맞으면 정상
            vs = [_num(f["value"]) for f in r.facts if f["q"] == q]
            vs = [v for v in vs if v]
            if vs and all(v < 0 for v in vs):
                continue
            # 이름만 보고 판정하는 것이라 가이드 직접 근거가 약함 → 확인 권장(계산관계 ①은 그대로 수정 필요)
            out.append(Finding(S7, "확장요소 차변/대변", ERR, rcs[0], q,
                               f"이름은 {'비용·손실' if d else '수익·이익'}인데 {'대변(credit)' if bal == 'credit' else '차변(debit)'} — "
                               f"{'차변(debit)' if d else '대변(credit)'}인지 확인 (입력 부호도 함께 확인)",
                               extra={"basis": "약함"}))
    return out


# 블록태깅은 제출용 ixd를 DART 파일에 붙여 넣은 뒤 DART에서 한다 → ixd로는 블록태깅 여부를 판단하지 않는다(누락처럼 보이는 표현 금지).
#  작성가이드 p96·설명회 2026 하반기 교재 p63의 '블록태깅 대상 주석'을 참고 목록으로만 보여 준다.
BLOCK_TARGETS = ["일반사항(기업 정보·기준서 준수 사실)", "재무제표 작성기준", "중요한 회계정책", "회계정책·추정의 변경 및 오류",
                 "중간재무보고", "관계기업·공동기업·종속기업 투자(타 기업에 대한 지분)", "특수관계자 단순 나열 표", "내용연수 등 단일 표"]


def check_block_targets(r, std):
    return [Finding(S6, "블록태깅 대상 주석(참고)", INFO, "", "",
                    "블록태깅 대상 주석 목록(작성가이드 p96, 설명회 2026 하반기 교재 p63): " + " / ".join(BLOCK_TARGETS)
                    + ". 블록태깅은 제출용을 DART 파일에 붙여 넣은 뒤 DART에서 하는 작업이라 이 점검은 블록태깅 여부를 판단하지 않음",
                    extra={"basis": "명확"})]


CHECKS = [
    # section, 카드, level, 함수, 가이드 근거(인쇄쪽·규칙ID), 내부 호환용
    (S1, "총액 요소를 순액 자리에 사용", ERR, check_gross, "p81~82", True),
    (S1, "초과적립액(과소적립액) 단독 사용", REV, check_surplus, "p251", True),
    (S1, "재고자산 표의 장부금액 축 구성", ERR, check_inventory, "p128·160~165·169", True),
    (S1, "유동·비유동을 축으로 구분", ERR, check_cur_noncur, "p118~119·140", True),
    (S1, "같은 뜻 ifrs-full 요소 대신 dart 요소 사용", ERR, check_dart_over_ifrs, "p79", True),
    (S2, "사용중단 표시 요소", ERR, check_deprecated, "택사노미 (사용중단) 라벨", True),
    (S2, "IAS 39 대손충당금 변동표", ERR, check_allowance_table, "IFRS 9 전환(택사노미)", True),
    (S2, "금융자산 손상차손 축(만료 예정)", REV, check_impairment_axis, "p103·182", True),
    (S2, "IAS 39 대손충당금 요소", ERR, lambda r, s: check_allowance_elem(r, s) + check_recv_loss_allowance(r, s),
     "p126·127 (IFRS 9 전환)", True),
    (S3, "다른 축 소속 멤버 사용", REV, check_axis_member, "p118·123", True),
    (S3, "공시금액 멤버의 위치", ERR, check_reported_use, "p132·151", True),
    (S4, "현금흐름 표에 표준 외 요소", REV, make_other_in("현금흐름"), "p83·339", True),
    (S4, "현금흐름 요소의 다른 표 사용", REV,
     lambda r, s: check_cf_dedicated_out(r, s) + check_cf_ext_alike(r, s), "p77·83·134 (G4-003)", True),
    (S4, "판관비 표에 표준 외 요소", REV, make_other_in("판매비와관리비"), "택사노미(판관비 목차 정의)", True),
    (S4, "판관비 요소의 다른 표 사용", REV, make_dedicated_out("판매비와관리비"), "가이드 직접 조항 없음", True),
    (S4, "특수관계자 표에 표준 외 요소", REV, make_other_in("특수관계자"), "p156·340", True),
    (S4, "특수관계자 요소의 다른 표 사용", REV, make_dedicated_out("특수관계자"), "가이드 직접 조항 없음", True),
    # (XBRL 안의 숫자가 맞고 틀림은 점검하지 않는다. 숫자는 공시 원문 그대로 넣는 것이 원칙)
    (S6, "축 하위 합계 멤버 확장", ERR, check_total_member, "p99~100", False),
    (S6, "같은 축 아래 동일 멤버 중복", ERR, check_dup_member, "p129", False),
    (S6, "표준 멤버 부모-자식 역전 / 계층 불일치", ERR, check_hierarchy, "p118·131", False),
    (S6, "장부금액 축에 확장 멤버", REV, check_carrying_ext_member, "p124", False),
    (S6, "변동내역을 멤버로 확장", REV, check_move_member, "p177·202", False),
    (S6, "중요계정 확장", ERR, check_key_account_ext, "p79", False),
    (S6, "같은 이름 표준요소가 있는 확장요소", ERR, check_same_name_std, "p77·134 (G4-004·G5B-029)", False),
    (S6, "비용 요소 음수 입력", REV, check_negative_expense, "p88", False),
    (S6, "블록태깅 대상 주석(참고)", INFO, check_block_targets, "p96, 설명회 2026 하반기 교재 p63", False),
    (S7, "확장요소 기간 유형", ERR, check_period_type, "p42·67·349", False),
    (S7, "확장요소 차변/대변", ERR, check_balance, "p42·67", False),
    (S7, "외화 item의 통화 속성", ERR, check_currency_unit, "정정공시 사례(외화 확장요소 통화 속성 누락)", False),
    (S7, "확장요소 영문명", ERR, check_en_label, "p81", False),
]

# 작성가이드 규칙 검사 — 카드 이름이 규칙ID로 시작
from .guide_checks import GUIDE_CHECKS, S8  # noqa: E402
CHECKS += GUIDE_CHECKS


# 입력값(fact)을 보고 판정하는 점검 — 숫자가 비어 있는 틀 단계에서는 판정되지 않는다(값이 들어간 곳만 판정).
# 가이드 규칙 카드는 규칙ID, 기본 카드는 카드명으로 적는다.
VALUE_CHECKS = {
    "G4-011", "G4-021", "G4-023", "G4-024", "G4-026", "G5-002", "G5-006", "G5-012", "G5B-041",
    "G5B-035", "G5B-038", "G5B-039", "G5B-053", "G5B-054", "G5B-056", "G5B-058", "G5B-059", "G5B-061", "G5B-063", "G5B-064", "G5B-068",
    "G5C-001", "G5C-010", "G5C-012", "G5C-018", "G5C-042", "G5C-043", "G5C-058",
    "G5C-044", "G5C-050", "G5C-059", "G5C-064", "G5C-072", "G5C-073", "G5C-079", "G5C-082",
    "공시금액 입력값의 적정성", "비용 요소 음수 입력", "외화 item의 통화 속성",
}


def run_all(r, std, frame=False, progress=None):
    """frame=True(틀 검사): 숫자가 비어 있는 틀 단계용. 값이 없어서 생기는 검출을 빼고,
    값을 보고 판정하는 점검에는 needs_values 표시를 단다(제출 검증 결과에는 영향 없음)."""
    r.frame = frame
    from .guide_checks import _set_report
    _set_report(r)   # 확장요소 이름에서 작성기 ID 꼬리를 떼는 데 쓸 상위 요소 표
    results = []
    for _i, (sec, card, level, fn, basis, _) in enumerate(CHECKS):
        if progress:
            progress(_i, len(CHECKS), card)
        try:
            fs = fn(r, std)
        except Exception as e:  # 검사 하나가 실패해도 나머지는 돌린다
            fs = [Finding(sec, card, "실행오류", "", "", f"{type(e).__name__}: {e}")]
        for f in fs:
            f.extra.setdefault("basis", "명확")
            # 가이드 근거가 약한 건은 '수정 필요'로 단정하지 않고 '확인 권장'으로 내린다
            if f.extra["basis"] == "약함" and f.level == ERR:
                f.level = REV
            if card.split(" ", 1)[0] in REF_CARDS:
                f.level = INFO
            cid = card.split(" ", 1)[0]
            if f.level == ERR and (card in RISK_CARDS or cid in RISK_IDS or f.extra.get("risk")):
                f.level = RISK
            elif f.level == ERR and (card in IMPROVE_CARDS or cid in IMPROVE_IDS or f.extra.get("improve")):
                f.level = IMP
            elif f.level == ERR and cid in REVIEW_IDS:
                f.level = REV
            if f.level in (ERR, IMP, REV) and (cid in ADVICE_IDS or f.extra.get("advice")):
                f.level = ADV
        results.append(dict(section=sec, card=card, level=level, grade=card_grade(card, level), guide=basis, findings=fs,
                            needs_values=frame and (card in VALUE_CHECKS or card.split(" ", 1)[0] in VALUE_CHECKS)))
    # 작성가이드 모범사례에 같은 구조가 나오면 허용(검출에서 빼고 res["allowed"]에 근거 쪽과 함께 남김)
    from .examples import apply as apply_examples
    apply_examples(results, std)
    # 표시 순서: 요소 선택 → 표 구조 → 요소 속성 → 입력값
    order = [S1, S2, S4, S3, S6, S7, S8, S5]
    results.sort(key=lambda x: order.index(x["section"]) if x["section"] in order else 99)
    return results
