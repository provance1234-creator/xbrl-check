"""
작성가이드 규칙(rules/rules.csv) 중 자동으로 판정할 수 있는 규칙 검사.

검사 1건 = 규칙 1개 = 함수 1개. 카드 이름은 '규칙ID 규칙제목'으로 해서 rules.csv와 바로 대응된다.
판정 근거가 규칙 문구 그대로면 basis "명확", 이름·라벨 휴리스틱이면 "약함"(→ '확인 권장'으로 내려 표시).
checks.py 끝에서 GUIDE_CHECKS를 CHECKS에 붙인다.
"""

import re
import sqlite3
from collections import defaultdict, Counter
from datetime import date, timedelta

from .common import Finding, CARRYING_AXIS, REPORTED_MEMBER, CONSEP_AXIS, STD_DB

S8 = "작성가이드 규칙"
ERR, REV = "오류", "검토필요"
IF, DART = "ifrs-full:", "dart:"

COMPONENTS_AXIS = IF + "ComponentsOfEquityAxis"
SHARE_AXIS = IF + "ClassesOfShareCapitalAxis"
ORD_M, PREF_M = IF + "OrdinarySharesMember", IF + "PreferenceSharesMember"
DEDUCT_MEMBERS = {IF + "AccumulatedDepreciationAndAmortisationMember", IF + "AccumulatedImpairmentMember",
                  IF + "AccumulatedDepreciationAmortisationAndImpairmentMember", DART + "GovernmentGrantsMember"}
CUR_RE = re.compile(r"(Noncurrent|Current)(?=[A-Z])")


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(r, rid, rc, q, why, basis="명확", level=ERR, table="", parent="", pair=None):
    """pair: 이 위반을 이루는 짝 요소 — 모범사례 허용 판정(compliance.examples)에서 함께 대조한다."""
    extra = {"basis": basis}
    if pair:
        extra["pair"] = list(pair)
    return Finding(S8, rid, level, rc or "", q, why, table=table, parent=parent, extra=extra)


def _ctx(r, f):
    if "_dims" in f:   # 표 구성에서 만든 가상 fact(틀 검사)
        return {"dims": f["_dims"]}
    return r.contexts.get(f["ctx"]) or {}


def _dims(r, f):
    """연결/별도 축을 뺀 차원."""
    d = dict(_ctx(r, f).get("dims") or {})
    d.pop(CONSEP_AXIS, None)
    return d


def _sep(r, f):
    return (_ctx(r, f).get("dims") or {}).get(CONSEP_AXIS, "") == IF + "SeparateMember"


def _is_ext(r, q):
    return not r.std.is_std(q)


def _attr(r, q):
    """요소 속성: balance, period_type, type (표준·확장 공통)."""
    if r.std.is_std(q):
        c = r.std.concepts[q]
        return dict(balance=c["balance"], period_type=c["period_type"], type=c["type"] or "")
    a = r.ext_attr.get(q) or {}
    return dict(balance=a.get("balance"), period_type=a.get("period_type"), type=a.get("type") or "")


def _lab(r, q):
    return (r.label_ko.get(q) or r.std.label_ko.get(q) or "").strip()


_OWN = {"anc": {}, "cache": {}}   # 지금 점검 중인 보고서의 요소별 상위 요소(작성기 ID 꼬리 떼기용)


def _set_report(r):
    """점검할 보고서가 바뀌면 요소별 상위 요소(조상) 표를 새로 만든다."""
    if _OWN.get("r") is r:
        return
    anc = {}
    for u in r.pre:
        par = {ch: p for p, lst in r.pre[u].items() for _, ch, _ in lst}
        for ch in par:
            s, p = anc.setdefault(ch, set()), par.get(ch)
            while p and p not in s:
                s.add(p)
                p = par.get(p)
    _OWN.update(r=r, anc=anc, cache={})


def _full(q):
    """요소 ID 전체(꼬리 포함). 부호 점검의 제외 조건은 상위 행 맥락(…IncreaseDecreaseIn…, …FromUsedIn…)도 근거로 쓴다."""
    return q.split(":", 1)[-1]


def _local(q):
    """요소 이름(접두어 제외). 확장요소는 작성기가 ID 뒤에 붙인 'Of+상위 요소명'(소속 항목·표) 꼬리를 떼고 요소 자신의 이름만."""
    loc = q.split(":", 1)[-1]
    if q.startswith(("ifrs-full:", "dart:", "dart-gcd:")) or not _OWN["anc"]:
        return loc
    hit = _OWN["cache"].get(q)
    if hit is None:
        cut = len(loc)
        for p in _OWN["anc"].get(q, ()):
            i = loc.find("Of" + p.split(":", 1)[-1])
            if 0 < i < cut:
                cut = i
        hit = _OWN["cache"][q] = loc[:cut]
    return hit


def _stmt(rc):
    """본문 role: BS / IS / CF / SCE, 주석은 ''."""
    if not rc or rc[0] != "D" or len(rc) != 7:
        return ""
    return {"2": "BS", "3": "IS", "4": "IS", "5": "CF", "6": "SCE"}.get(rc[1], "")


def _index(r):
    """role별 노드·축 집합과 요소별 사용 role, 그리고 fact별 소속 role."""
    if hasattr(r, "_gidx"):
        return r._gidx
    nodes, axes, used = defaultdict(set), defaultdict(set), defaultdict(set)
    for u in r.uris():
        rc = r.role_code(u)
        for n, *_ in r.role_nodes(u):
            nodes[rc].add(n)
            used[n].add(rc)
            if r.kind(n) == "axis":
                axes[rc].add(n)
    place = []
    for f in r.facts:
        d = set(_dims(r, f))
        cands = sorted(rc for rc in used.get(f["q"], ()) if d <= axes[rc])
        sep = _sep(r, f)
        cands.sort(key=lambda rc: (rc.endswith("5") != sep, _stmt(rc) == ""))   # 같은 연결/별도, 본문 우선
        place.append(cands[0] if cands else "")
    r._gidx = dict(nodes=nodes, axes=axes, used=used, place=place)
    return r._gidx


def _facts(r, pred=None, struct=False):
    """(fact, 소속 role) 반복. struct=True이고 틀 검사면 표 구성(행 × 축 멤버)으로 만든 가상 fact도 함께 낸다
    → 값이 비어 있어도 '어떤 행을 어떤 멤버와 쓰는지' 같은 구조 판정이 가능하다(가상 fact는 값이 없다)."""
    pl = _index(r)["place"]
    for f, rc in zip(r.facts, pl):
        if pred is None or pred(f, rc):
            yield f, rc
    if struct and getattr(r, "frame", False):
        for f, rc in _struct_facts(r):
            if pred is None or pred(f, rc):
                yield f, rc


def _struct_facts(r):
    """표마다 행 × (축, 멤버) 하나씩 가상 fact. 축이 없는 표는 차원 없는 가상 fact 하나. 연결/별도 축·도메인은 뺀다."""
    if not hasattr(r, "_gstruct"):
        out = []
        for u, rc, t, info in _tables(r):
            axes = {ax: [m for m in ms if not m.endswith("Domain")] for ax, ms in info["axes"].items() if ax != CONSEP_AXIS}
            pairs = [(ax, m) for ax, ms in axes.items() for m in ms]
            for q in dict.fromkeys(info["items"]):
                base = dict(q=q, value=None, ctx=None, unit=None, nil=False, decimals=None)
                out += [(dict(base, _dims={ax: m}), rc) for ax, m in pairs] or [(dict(base, _dims={}), rc)]
        r._gstruct = out
    return r._gstruct


def _tables(r):
    """(uri, rc, 표, info) 반복. info = r.tables 결과 + pls(행별 pref_label)."""
    if not hasattr(r, "_gtab"):
        r._gtab = [(u, r.role_code(u), t, info) for u in r.uris() for t, info in r.tables(u).items()]
    return r._gtab


def _is_change_table(info):
    return any(pl and pl.rsplit("/", 1)[-1].startswith(("periodStart", "periodEnd", "negatedPeriodStart", "negatedPeriodEnd"))
               for pl in info["pref_labels"])


def _subtree(r, u, root):
    return set(r._descendants(r.pre[u], root))


def _def_axis_members(r):
    """보고서 정의링크: 축 -> {도메인}, 축 -> 하위 멤버 전체, 부모 -> 자식."""
    if hasattr(r, "_gdef"):
        return r._gdef
    doms, kids = defaultdict(set), defaultdict(set)
    for arcs in r.defs.values():
        for a, p, c in arcs:
            if a == "dimension-domain":
                doms[p].add(c)
            if a in ("dimension-domain", "domain-member"):
                kids[p].add(c)
    under = defaultdict(set)
    for ax, ds in doms.items():
        stack = list(ds)
        while stack:
            m = stack.pop()
            if m not in under[ax]:
                under[ax].add(m)
                stack.extend(kids.get(m, ()))
    r._gdef = (doms, under, kids)
    return r._gdef


def _std_extra(std):
    """표준 정의링크에서 축 -> 도메인, 표 -> 축 집합 (Std에 없는 것만 따로 읽는다)."""
    if hasattr(std, "_gx"):
        return std._gx
    c = sqlite3.connect(STD_DB)
    dom, tax = defaultdict(set), defaultdict(set)
    for p, n, par, arc in c.execute("SELECT prefix,name,parent,arcrole FROM def_arcs"):
        a = (arc or "").rsplit("/", 1)[-1]
        if a == "dimension-domain" and par:
            dom[par].add(f"{p}:{n}")
        if a == "hypercube-dimension" and par:
            tax[par].add(f"{p}:{n}")
    c.close()
    std._gx = (dom, tax)
    return std._gx


def _ext_items_in(r, rcs=None):
    """확장 item과 그것이 쓰인 role (rcs로 role 제한)."""
    out = {}
    for u in r.uris():
        rc = r.role_code(u)
        if rcs and not rcs(rc):
            continue
        for n, *_ in r.role_nodes(u):
            if _is_ext(r, n) and r.kind(n) == "item":
                out.setdefault(n, rc)
    return out


def _ext_members(r):
    """확장 멤버·축과 처음 쓰인 role."""
    out = {}
    for u in r.uris():
        rc = r.role_code(u)
        for n, *_ in r.role_nodes(u):
            if _is_ext(r, n) and r.kind(n) in ("member", "axis"):
                out.setdefault(n, rc)
    return out


def _end(r):
    """보고기간 종료일."""
    try:
        return date.fromisoformat(r.period)
    except (TypeError, ValueError):
        ends = Counter(c.get("instant") for c in r.contexts.values() if c.get("instant"))
        return date.fromisoformat(ends.most_common(1)[0][0])


def _ymd(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 4장: 본문
# ---------------------------------------------------------------------------

def _whole_concepts(std):
    """전체 개념 요소 → 그 유동·비유동 요소들.
    ① 이름: Current/Noncurrent를 뗀 이름. ② X+'Total': X의 영문 라벨이 'Current …'이거나 NoncurrentX가 있으면
    XTotal이 전체(예: ifrs-full:InventoriesTotal, 가이드 p83 재고자산). ③ 라벨: '유동 X'·'비유동 X'가 모두 있는 'X'(G4-018)."""
    if hasattr(std, "_gwhole"):
        return std._gwhole
    variants = defaultdict(set)
    for q in std.concepts:
        n = _local(q)
        if CUR_RE.search(n):
            variants[q.split(":")[0] + ":" + CUR_RE.sub("", n, count=1)].add(q)
    for q in std.concepts:
        p, n = q.split(":", 1)
        if not n.endswith("Total") or std.kind(q) != "item":
            continue
        x = f"{p}:{n[:-5]}"
        nc = f"{p}:Noncurrent{n[:-5]}"
        if x in std.concepts and (re.match(r"current\b", std.label_en.get(x, ""), re.I) or nc in std.concepts):
            variants[q] |= {c for c in (x, nc) if c in std.concepts}
    by_lab = defaultdict(list)
    for q, l in std.label_ko.items():
        if std.kind(q) == "item" and q not in std.deprecated:
            by_lab[l.strip()].append(q)
    for l, qs in by_lab.items():
        if l.startswith(("유동 ", "비유동 ")):
            continue
        cur, nonc = by_lab.get("유동 " + l), by_lab.get("비유동 " + l)
        if cur and nonc:
            for q in qs:
                variants[q] |= set(cur) | set(nonc)
    std._gwhole = variants
    return variants


def g4_018(r, std):
    """유동/비유동 구분 영역 아래에 유동·비유동을 포함하는 '전체' 요소."""
    variants = _whole_concepts(std)
    secs = [IF + x for x in ("CurrentAssets", "NoncurrentAssets", "CurrentLiabilities", "NoncurrentLiabilities")]
    out, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        if _stmt(rc) != "BS":
            continue
        for sec in secs:
            for q in _subtree(r, u, sec) | _subtree(r, u, sec + "Abstract"):
                if q in variants and std.kind(q) == "item" and (rc, q) not in seen and \
                        not re.search(r"\b(non-?)?current\b", std.label_en.get(q, ""), re.I):
                    seen.add((rc, q))
                    out.append(_f(r, "G4-018", rc, q, f"{_lab(r, sec)} 아래에 전체 개념 요소 사용 — "
                                                     f"유동·비유동 요소({', '.join(sorted(variants[q])[:2])}) 사용"))
    return out


def _sign_rule(r, rid, kind, want_bal, why, skip=r"IncreaseDecrease", skip_lab=None):
    out, seen = [], set()
    for f, rc in _facts(r, lambda f, rc: _stmt(rc) == kind):
        v = _num(f["value"])
        a = _attr(r, f["q"])
        if v is None or v >= 0 or a["balance"] != want_bal or re.search(skip, _full(f["q"])) or \
                (skip_lab and re.search(skip_lab, _lab(r, f["q"]))):
            continue
        if (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, rid, rc, f["q"], f"{why} — 음수 {f['value']} 입력, 양수로 입력하고 표시는 반대부호 라벨",
                          basis="약함" if re.search(r"Refund|ReceivedPaid|PaidReceived", f["q"]) else "명확"))
    return out


def g4_021(r, std):
    """자산 영역의 대변 속성(차감계정) 요소에 음수."""
    out = []
    for x in _sign_rule(r, "G4-021", "BS", "credit", "자산 차감계정(대변)"):
        u = next(u for u in r.uris() if r.role_code(u) == x.role_code)
        if x.element in set().union(*(_subtree(r, u, IF + n) for n in
                                      ("Assets", "AssetsAbstract", "CurrentAssets", "CurrentAssetsAbstract",
                                       "NoncurrentAssets", "NoncurrentAssetsAbstract"))):
            out.append(x)
    return out


CONTRA_NAME = re.compile(r"^(Accumulated|Allowance|PresentValueDiscount|GovernmentGrant|Discount|Unamortised)")
CONTRA_LAB = re.compile(r"충당금|누계액|정부보조금|국고보조금|할인차금|차감")


def g5b_059(r, std):
    """주석의 자산 차감계정(대변 속성) 잔액은 양수 입력(p148 가). 본문 재무상태표는 G4-021이 맡는다.
    겸용 행(Liability로 시작)·증감 행은 제외(로 신설)."""
    out, seen = [], set()
    for f, rc in _facts(r, lambda f, rc: _stmt(rc) == ""):
        v, a, n = _num(f["value"]), _attr(r, f["q"]), _local(f["q"])
        if v is None or v >= 0 or a["balance"] != "credit" or a["period_type"] != "instant" or \
                n.startswith("Liabilit") or "IncreaseDecrease" in n or (rc, f["q"]) in seen:
            continue
        if not (CONTRA_NAME.match(n) or CONTRA_LAB.search(_lab(r, f["q"]))) or \
                re.search(r"OtherComprehensive|Equity|Reserve", n) or re.search(r"포괄|평가손익|잉여금|자본", _lab(r, f["q"])):
            continue   # 기타포괄손익누계액 등 자본 항목은 음수가 정상
        seen.add((rc, f["q"]))
        out.append(_f(r, "G5B-059", rc, f["q"], f"자산 차감계정(대변) '{_lab(r, f['q'])}'에 음수 {f['value']} — 양수로 입력하고 표시는 반대부호 라벨"))
    return out


RATE_LE1 = re.compile(r"InterestRate|DiscountRate|GrowthRate|ProportionOfOwnership|ProportionOfVotingRights|"
                      r"이자율|할인율|성장률|상승률|인상률|지분율|의결권")


def g5b_061(r, std):
    """비율은 소수로 입력(70% → 0.7, p148 나). 100%를 넘을 수 없는 비율(이자율·할인율·성장률·지분율)이 1 초과면 수정 필요,
    그 밖의 비율(부채비율·배당률 등은 100% 초과가 정상)은 100 초과일 때만 확인 권장."""
    out, seen = [], set()
    for f, rc in _facts(r):
        v = _num(f["value"])
        if v is None or abs(v) <= 1 or "percent" not in _attr(r, f["q"])["type"].lower() or (rc, f["q"]) in seen:
            continue
        sure = bool(RATE_LE1.search(_local(f["q"]) + _lab(r, f["q"])))
        if not sure and abs(v) <= 100:
            continue
        seen.add((rc, f["q"]))
        out.append(_f(r, "G5B-061", rc, f["q"], f"비율 '{_lab(r, f['q'])}' 값 {f['value']} — 백분율이 아니라 소수로(70% → 0.7)",
                      basis="명확" if sure else "약함"))
    return out


def g5b_056(r, std):
    """주당 액면가액은 표 단위와 무관하게 실제 값(5,000원 → 5000, p147 모범사례). 상법상 액면가는 100원 이상이므로
    100 미만이면 표 단위(천원)로 나눠 입력한 것."""
    out, seen = [], set()
    for f, rc in _facts(r):
        v = _num(f["value"])
        if f["q"].endswith(":ParValuePerShare") and v is not None and 0 < v < 100 and (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5B-056", rc, f["q"], f"주당 액면가액 {f['value']} — 표 단위와 무관하게 실제 금액(예: 5,000원 → 5000) 입력"))
    return out


def _by_ctx(r, names):
    """요소 이름 → {context id: 값} (차원 없는 값만)."""
    res = defaultdict(dict)
    for f in r.facts:
        if f["q"] in names and not _dims(r, f):
            v = _num(f["value"])
            if v is not None:
                res[f["q"]][f["ctx"]] = v
    return res


def g5c_042(r, std):
    """순확정급여부채 변동표의 재측정손익 행(차변, 양수 = 부채 감소 = 이익)은 포괄손익계산서 세전 재측정손익(대변, 양수 = 이익)과
    부호가 같아야 한다. 반대면 보험수리적손실(부채 증가)을 양수로 넣은 것(p250 가)."""
    # 합계 행이 없으면(반기 등) 보험수리적손익 개별 행 + 사외적립자산 수익 행의 합으로 비교. 도메인 값이 없으면
    # 확정급여채무·사외적립자산 멤버 값을 더한다. 포괄손익은 세전, 없으면 세후(부호 같음). 크기가 비슷할 때(0.3~3배)만 판정.
    tot = IF + "GainLossOnRemeasurementOfNetDefinedBenefitLiabilityAsset"
    parts = {IF + n for n in ("ActuarialGainsLossesArisingFromChangesInDemographicAssumptionsNetDefinedBenefitLiabilityAsset",
                              "ActuarialGainsLossesArisingFromChangesInFinancialAssumptionsNetDefinedBenefitLiabilityAsset",
                              "ActuarialGainsLossesArisingFromExperienceAdjustmentsNetDefinedBenefitLiabilityAsset",
                              "ReturnOnPlanAssetsNetDefinedBenefitLiabilityAsset")}
    ocis = (IF + "OtherComprehensiveIncomeBeforeTaxGainsLossesOnRemeasurementsOfDefinedBenefitPlans",
            IF + "OtherComprehensiveIncomeNetOfTaxGainsLossesOnRemeasurementsOfDefinedBenefitPlans")
    ndb_ax = IF + "NetDefinedBenefitLiabilityAssetAxis"
    place = dict(zip(map(id, r.facts), _index(r)["place"]))
    dom, mem, oci, where = defaultdict(dict), defaultdict(lambda: defaultdict(float)), defaultdict(dict), {}
    for f in r.facts:
        v = _num(f["value"])
        c = _ctx(r, f)
        if v is None or not c.get("start"):
            continue
        k = (_sep(r, f), c["start"], c["end"])
        d = _dims(r, f)
        if f["q"] in ocis and not d:
            oci[k][f["q"]] = v
        elif f["q"] == tot or f["q"] in parts:
            if not d:
                dom[k][f["q"]] = v
                where.setdefault(k, place[id(f)])
            elif set(d) == {ndb_ax}:
                mem[k][f["q"]] += v
                where.setdefault(k, place[id(f)])
    out, seen = [], set()
    for k in set(dom) | set(mem):
        y = next((oci[k][q] for q in ocis if q in oci.get(k, {})), None)
        vals = dict(mem.get(k, {}))
        vals.update(dom.get(k, {}))
        x = vals.get(tot)
        if x is None:
            x = sum(vals[q] for q in parts if q in vals) if any(q in vals for q in parts) else None
        if not x or not y or not 0.3 <= abs(x / y) <= 3 or (x > 0) == (y > 0) or where[k] in seen:
            continue
        seen.add(where[k])
        out.append(_f(r, "G5C-042", where[k], tot if tot in vals else sorted(q for q in parts if q in vals)[0],
                      f"{k[1]}~{k[2]} 재측정손익(보험수리적손익) 행 합계 {x:,.0f}인데 포괄손익계산서 재측정손익 {y:,.0f} — "
                      "보험수리적손실(부채 증가)은 음수 입력 + 반대부호 라벨"))
    return out


def g5c_058(r, std):
    """법인세비용(수익) 행은 차변 — 법인세수익이면 음수 입력(p277 라). 같은 기간의 법인세차감전순이익 − 계속영업이익과 부호 비교."""
    tax, pbt, pl_ = IF + "IncomeTaxExpenseContinuingOperations", IF + "ProfitLossBeforeTax", IF + "ProfitLossFromContinuingOperations"
    v = _by_ctx(r, {tax, pbt, pl_, IF + "ProfitLoss"})
    place = dict(zip(map(id, r.facts), _index(r)["place"]))
    rc_of = {f["ctx"]: place[id(f)] for f in r.facts if f["q"] == tax}
    out, seen = [], set()
    for c, t in v[tax].items():
        p = v[pbt].get(c)
        n = v[pl_].get(c)
        if n is None:   # 중단영업이 없으면 계속영업이익 행 없이 당기순이익만 있음
            n = v[IF + "ProfitLoss"].get(c)
        if p is None or n is None or not t:
            continue
        d = p - n
        if abs(d) > max(1000, abs(t) * 0.01) and (d > 0) != (t > 0) and rc_of.get(c) not in seen:
            seen.add(rc_of.get(c))
            how = "법인세수익은 음수 입력 + 반대부호 라벨" if d < 0 else "법인세비용은 양수 입력"
            out.append(_f(r, "G5C-058", rc_of.get(c), tax, f"법인세비용(수익) {t:,.0f}인데 세전이익 − 계속영업이익 = {d:,.0f} — {how}"))
    return out


# ---------------------------------------------------------------------------
# 4장 본문 원칙
# ---------------------------------------------------------------------------

def _stmt_roles(r, kind=None):
    """본문 role (uri, rc)."""
    return [(u, r.role_code(u)) for u in r.uris() if _stmt(r.role_code(u)) and (kind is None or _stmt(r.role_code(u)) == kind)]


def g4_001(r, std):
    """비금융업 유동/비유동 표시 회사는 D21000x 재무상태표 서식(p77). 유동성배열(D22000x)·금융업 서식이면 감사보고서 표시방식 확인."""
    out = []
    for u, rc in _stmt_roles(r):
        if rc[:2] == "D2" and rc[:5] != "D2100":
            out.append(_f(r, "G4-001", rc, "", f"재무상태표 서식 {rc} — 비금융업 유동/비유동 표시면 D210000(연결)/D210005(별도) 서식", basis="약함"))
    for u in r.uris():
        rc = r.role_code(u) or ""
        if re.match(r"D[BISX]\d", rc) and _stmt("D" + rc[2:]) == "BS":
            out.append(_f(r, "G4-001", rc, "", f"금융업 재무상태표 서식 {rc} 사용 — 업종에 맞는 서식인지 확인", basis="약함"))
    return out


def g4_008(r, std):
    """비금융·금융 혼재 시 본문은 전체 금액 표준계정과목(p78). 금융업 자산·부채를 확장한 흔적이면 확인 권장."""
    out = []
    for u, rc in _stmt_roles(r, "BS"):
        for n, *_ in r.role_nodes(u):
            if _is_ext(r, n) and r.kind(n) == "item" and (re.search(r"금융업|금융부문", _lab(r, n)) or "FinancialService" in n):
                out.append(_f(r, "G4-008", rc, n, f"본문에 금융업 확장 행 '{_lab(r, n)}' — 혼재 기업은 전체 금액 표준계정과목으로 공시", basis="약함"))
    return out


def _stmt_tree(r, u):
    """본문 role의 (자식→부모) 맵과 부모→자식 목록."""
    par, kids = {}, defaultdict(list)
    for n, p, tb, t, pl in r.role_nodes(u):
        par.setdefault(n, p)
        if p:
            kids[p].append(n)
    return par, kids


def g4_011(r, std):
    """표준 요소 아래 확장한 하위 행의 값이 상위 표준 요소 값과 같으면(그 외 구성항목 없음) 확장 불가 — 표준 요소 + 별칭(p79-80)."""
    vals = defaultdict(dict)
    for f in r.facts:
        v = _num(f["value"])
        if v is not None:
            vals[f["q"]][f["ctx"]] = v
    out, seen = [], set()
    for u, rc in _stmt_roles(r):
        par, kids = _stmt_tree(r, u)
        for n, p in par.items():
            if not p or not _is_ext(r, n) or r.kind(n) != "item" or _is_ext(r, p) or r.kind(p) != "item" or (rc, n) in seen:
                continue
            # 다른 구성항목 없이(상위 아래 유일한 행) 모든 기간 값이 같을 때만 — 우연히 한 기간만 같은 경우 제외
            shared = [c for c in vals.get(n, {}) if c in vals.get(p, {})]
            if len(kids.get(p, [])) == 1 and shared and all(vals[n][c] == vals[p][c] for c in shared)                     and any(vals[n][c] for c in shared):
                seen.add((rc, n))
                out.append(_f(r, "G4-011", rc, n, f"확장 '{_lab(r, n)}' 값이 상위 표준 '{_lab(r, p)}'와 같음 — 확장하지 말고 표준 요소에 별칭 사용",
                              pair=[p]))
    return out


def _core(l):
    return re.sub(r"\s|\(.*?\)|,.*$", "", l or "")


def g4_012_015(r, std, rid):
    """확장 행 라벨이 본문 표준 행 X의 라벨을 포함(예: 종업원단기대여금 ⊃ 단기대여금).
    G4-015: X와 같은 층(형제)에 있으면 표준 X는 하위 구성이 다른 개념 → X도 확장 검토(p80-81).
    G4-012: X가 본문에 없거나 X 아래가 아니면 X 하위로 확장 또는 X로 통합 검토(p80)."""
    out, seen = [], set()
    for u, rc in _stmt_roles(r, "BS"):
        par, kids = _stmt_tree(r, u)
        stds = {n: _core(_lab(r, n)) for n in par if std.is_std(n) and r.kind(n) == "item" and len(_core(_lab(r, n))) >= 3}
        all_std = {q: _core(l) for q, l in std.label_ko.items() if q in std.role_elems.get(rc[:6] + "0", set()) | std.role_elems.get(rc, set())
                   and std.kind(q) == "item" and len(_core(l)) >= 3}
        for n, p in par.items():
            if not _is_ext(r, n) or r.kind(n) != "item" or (rc, n) in seen:
                continue
            ln = _core(_lab(r, n))
            hit = [(q, c) for q, c in stds.items() if c != ln and ln.endswith(c)]
            if rid == "G4-015":
                sib = [q for q, c in hit if par.get(q) == p]
                if sib:
                    seen.add((rc, n))
                    out.append(_f(r, rid, rc, n, f"표준 '{_lab(r, sib[0])}'와 그 하위 개념 확장 '{_lab(r, n)}'을 같은 층에 공시 — "
                                                 "표준 요소는 하위를 포함한 개념이므로 회사 금액이 하위 제외면 확장으로 정의", basis="약함", pair=[sib[0]]))
            else:
                if any(par.get(q) == p or p == q for q, c in hit):
                    continue   # 형제(G4-015) 또는 표준 아래(정상·G4-011)
                cand = [q for q, c in all_std.items() if c != ln and ln.endswith(c) and q not in par]
                if cand and not hit:
                    q = max(cand, key=lambda x: len(all_std[x]))
                    seen.add((rc, n))
                    out.append(_f(r, rid, rc, n, f"하위 개념 확장 '{_lab(r, n)}'을 표준 '{_lab(r, q)}' 없이 단독 사용 — "
                                                 "표준 요소 하위로 확장하거나 표준 요소로 통합 공시 검토", basis="약함"))
    return out


def g4_012(r, std):
    return g4_012_015(r, std, "G4-012")


def g4_015(r, std):
    return g4_012_015(r, std, "G4-015")


def g4_014(r, std):
    """투자·재무활동 현금흐름은 총유입·총유출로(p80·91). 순증감 확장 행은 기준서 1007호 문단 22·24 해당 여부 확인."""
    out = []
    for u, rc in _stmt_roles(r, "CF"):
        par, kids = _stmt_tree(r, u)
        for n, p in par.items():
            if not _is_ext(r, n) or r.kind(n) != "item" or not re.search(r"순증감|순증가|순감소|증감|\(순\)", _lab(r, n)):
                continue
            x, sec = p, ""
            while x:
                if re.search(r"Investing|Financing", _local(x)):
                    sec = "투자" if "Investing" in x else "재무"
                    break
                x = par.get(x)
            if sec:
                out.append(_f(r, "G4-014", rc, n, f"{sec}활동 순증감 확장 '{_lab(r, n)}' — 총현금유입·총현금유출로 구분(순액은 1007호 문단 22·24 해당 시만)",
                              basis="약함"))
    return out


def g4_026(r, std):
    """본문과 주석의 입력 단위(decimals) 일치 권고(p84). 다르면 같은 요소·context 값이 마지막 입력값으로 덮어써질 수 있음."""
    place = _index(r)["place"]
    body, note = Counter(), Counter()
    for f, rc in zip(r.facts, place):
        d = f.get("decimals")
        if d in (None, "", "INF") or not (f["unit"] or "").startswith("iso4217:KRW"):
            continue
        (body if _stmt(rc) else note)[str(d)] += 1
    if not body or not note:
        return []
    b, n = body.most_common(1)[0][0], note.most_common(1)[0][0]
    if b == n:
        return []
    unit = {"0": "원", "-3": "천원", "-6": "백만원"}
    return [_f(r, "G4-026", "", "", f"본문 입력 단위 {unit.get(b, b)}(decimals {b}), 주석 {unit.get(n, n)}(decimals {n}) — 단위 일치 권고")]


def g4_027(r, std):
    """무형자산 행은 영업권 포함 여부에 맞게(p85): 영업권 행을 따로 두면 '영업권 이외의 무형자산', 포함하면 '무형자산 및 영업권'."""
    gw, iag, iog = IF + "Goodwill", IF + "IntangibleAssetsAndGoodwill", IF + "IntangibleAssetsOtherThanGoodwill"
    out = []
    note_gw = {(_sep(r, f), _ctx(r, f).get("instant")) for f in r.facts if f["q"] == gw and (_num(f["value"]) or 0) > 0}
    for u, rc in _stmt_roles(r, "BS"):
        ns = {n for n, *_ in r.role_nodes(u)}
        if gw in ns and iag in ns:
            out.append(_f(r, "G4-027", rc, iag, "영업권 행을 따로 두면서 '무형자산 및 영업권' 사용 — '영업권 이외의 무형자산' 행", pair=[gw]))
        elif iog in ns and gw not in ns:
            sep = rc.endswith("5")
            if any(s == sep for s, i in note_gw):
                out.append(_f(r, "G4-027", rc, iog, "재무상태표에 영업권 행 없이 '영업권 이외의 무형자산' 사용인데 주석에 영업권 있음 — "
                                                   "무형자산에 영업권이 포함되면 '무형자산 및 영업권' 행", basis="약함"))
    return out


def g4_029(r, std):
    """금융기관 차입금만 있으면 '금융기관 차입금(사채 제외)' 계열 권장(p86). 사채 행 없이 '차입금(사채 포함)' 계열이면 확인 권장."""
    out = []
    # 주석 등 어디에든 사채 요소가 있으면 사채가 차입금에 포함된 것 — '사채 포함' 계열이 맞음
    if any(re.search(r"Bond|Debenture", f["q"]) or (re.search(r"사채", _lab(r, f["q"]) or "")
                                                   and not re.search(r"포함|제외", _lab(r, f["q"]) or "")) for f in r.facts):
        return out
    for u, rc in _stmt_roles(r, "BS"):
        ns = [n for n, *_ in r.role_nodes(u) if r.kind(n) == "item"]
        incl = [n for n in ns if "사채 포함" in _lab(r, n) or _local(n) in ("Borrowings", "LongtermBorrowings")]
        bond = [n for n in ns if "Bond" in n or (re.search(r"사채", _lab(r, n)) and not re.search(r"포함|제외", _lab(r, n)))]
        if incl and not bond:
            out.append(_f(r, "G4-029", rc, incl[0], f"사채 없이 '{_lab(r, incl[0])}' 사용 — 금융기관 차입금만이면 '금융기관 차입금(사채 제외)' 계열 권장",
                          basis="약함"))
    return out


SCE_STD = [(r"유상증자", IF + "IssueOfEquity", "명확"),
           (r"배당", IF + "DividendsPaid", "명확"),
           (r"전환사채.*(발행)|복합금융.*발행", DART + "CompoundFinancialInstrumentIssue", "명확"),
           (r"전환사채.*(상환|전환)|복합금융.*상환", DART + "CompoundFinancialInstrumentRedemption", "명확"),
           (r"주식선택권|주식기준보상|주식보상", IF + "IncreaseDecreaseThroughSharebasedPaymentTransactions", "명확"),
           (r"종속기업", IF + "IncreaseDecreaseThroughChangesInOwnershipInterestsInSubsidiariesThatDoNotResultInLossOfControl", "약함")]


def g4_034(r, std):
    """자본변동표 주요 거래는 표준계정과목(p90 표): 유상증자→지분의 발행, 배당→소유주에 대한 배분으로 인식된 배당금 등."""
    out, seen = [], set()
    for u, rc in _stmt_roles(r, "SCE"):
        for n, *_ in r.role_nodes(u):
            if not _is_ext(r, n) or r.kind(n) != "item" or (rc, n) in seen:
                continue
            lab = _lab(r, n)
            for pat, q, basis in SCE_STD:
                if re.search(pat, lab) and not re.search(r"수령|수취|미지급|비지배|기타포괄|재측정|지분법", lab):
                    seen.add((rc, n))
                    out.append(_f(r, "G4-034", rc, n, f"자본변동표 확장 '{lab}' — 표준 '{_lab(r, q)}'({q}) 사용", basis=basis))
                    break
    return out


def g4_023(r, std):
    return _sign_rule(r, "G4-023", "SCE", "debit", "자본 감소 항목(차변)")


def g4_024(r, std):
    # 납부(환급)처럼 양방향 요소는 환급이면 음수가 맞으므로 제외. 이익·손실 겸용(…손익 조정) 요소도 순손실이면 음수가 정상(p148)
    return _sign_rule(r, "G4-024", "CF", "credit", "현금흐름표 대변 항목",
                      skip=r"IncreaseDecrease|FromUsedIn|ProfitLoss|GainLoss|GainsLosses|IncomeLoss|Effect|NetCash|Refund|ReceivedPaid|PaidReceived",
                      skip_lab=r"손익")


NI_RE = re.compile(r"^(연결)?(당기|분기|반기)?순(이익|손익|손실)(\((이익|손실)\))?$")


def g4_025(r, std):
    """본문(손익·현금흐름·자본변동)의 당기순이익을 ProfitLoss가 아닌 요소로 공시."""
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        if _stmt(rc) not in ("IS", "CF", "SCE"):
            continue
        for n, *_ in r.role_nodes(u):
            lab = _lab(r, n).replace(" ", "")
            if r.kind(n) == "item" and NI_RE.match(lab) and "지배" not in lab and n != IF + "ProfitLoss":
                out.append(_f(r, "G4-025", rc, n, f"'{_lab(r, n)}'을 ifrs-full:ProfitLoss가 아닌 요소로 공시 — 재무제표 간 연동 끊김"))
    return out


def g4_028(r, std):
    """재무상태표에 '유형자산' 확장 행. 유형자산 본체로 보이는 라벨이거나 같은 role에 표준 유형자산이 없으면 수정 필요,
    그 밖의 '유형자산' 포함 확장 행(유형자산 선급금·매각예정 유형자산 등)은 확인 권장(G4-028)."""
    nodes = _index(r)["nodes"]
    out = []
    for q, rc in _ext_items_in(r, lambda rc: _stmt(rc) == "BS").items():
        lab = _lab(r, q).replace(" ", "")
        if "유형자산" not in lab:
            continue
        body = re.match(r"^유형자산(\(.*사용권.*\))?$", lab) or IF + "PropertyPlantAndEquipment" not in nodes.get(rc, ())
        out.append(_f(r, "G4-028", rc, q, "유형자산을 확장요소로 공시 — ifrs-full:PropertyPlantAndEquipment 사용"
                      + ("" if body else "(유형자산 본체가 아니면 해당 없음)"), basis="명확" if body else "약함"))
    return out


def g4_030(r, std):
    """유동·비유동 차입금의 '사채 포함/제외' 수준이 다르면."""
    out = []
    for u in r.uris():
        rc = r.role_code(u)
        if _stmt(rc) != "BS":
            continue
        lv, bonds = {}, {}
        for side in ("CurrentLiabilities", "NoncurrentLiabilities"):
            qs = _subtree(r, u, IF + side) | _subtree(r, u, IF + side + "Abstract")
            labs = [_lab(r, q) for q in qs if "차입금" in _lab(r, q)]
            lv[side] = {("포함" if "사채 포함" in l else "제외") for l in labs if "사채" in l}
            bonds[side] = any("BondsIssued" in _local(q) or ("사채" in _lab(r, q) and "차입금" not in _lab(r, q))
                              for q in qs if r.kind(q) == "item")
        # '사채 제외' 차입금 옆에 사채 행이 따로 있으면 범위는 '사채 포함'과 같다 → 지적은 '사채 제외' 쪽에 사채 행이 없을 때만
        # (G4-030, 가이드 p86 예시)
        excl = next((s for s, v in lv.items() if v == {"제외"}), None)
        if len(lv["CurrentLiabilities"]) == 1 and len(lv["NoncurrentLiabilities"]) == 1 and \
                lv["CurrentLiabilities"] != lv["NoncurrentLiabilities"] and not bonds.get(excl):
            out.append(_f(r, "G4-030", rc, IF + "NoncurrentLiabilities",
                          f"유동 차입금은 사채 {lv['CurrentLiabilities'].pop()}, 비유동 차입금은 사채 "
                          f"{lv['NoncurrentLiabilities'].pop()} 수준 — 같은 수준으로 공시", basis="약함"))
    return out


def g4_032(r, std):
    doms, under, _ = _def_axis_members(r)
    return [_f(r, "G4-032", "", m, f"자본의 구성요소 축에 합계 확장 멤버 '{_lab(r, m)}' — 도메인이 합계 열")
            for m in sorted(under.get(COMPONENTS_AXIS, ())) if _is_ext(r, m) and
            re.search(r"합계|총계|total", _lab(r, m) + _local(m), re.I)]


def g4_033(r, std):
    out = []
    used = _index(r)["used"]
    for q, rc in _ext_items_in(r, lambda rc: _stmt(rc) == "SCE").items():
        lab = _lab(r, q)
        # 같은 확장요소를 손익·포괄손익계산서에서도 쓰면 같은 ID로 연동되므로 정상
        if any(_stmt(x) == "IS" for x in used.get(q, ())):
            continue
        # 기타포괄손익 행 자체만 수정 필요, 그 밖의 '기타포괄' 행(누계액의 이익잉여금 대체, FVOCI 처분 등 자본 내부 대체)은 확인 권장(G4-033)
        if NI_RE.match(lab.replace(" ", "")) or OCI_RE.match(lab.replace(" ", "")):
            out.append(_f(r, "G4-033", rc, q, f"자본변동표 '{lab}'을 확장요소로 공시 — 손익계산서와 같은 요소 사용"))
        elif "기타포괄" in lab:
            out.append(_f(r, "G4-033", rc, q, f"자본변동표 '{lab}'을 확장요소로 공시 — 손익계산서 기타포괄손익 행이면 같은 요소 사용",
                          basis="약함"))
    return out


OCI_RE = re.compile(r"^(기타|총)포괄(손익|이익|손실)(\((이익|손실|세후|세후순액|세후기타포괄손익)\))?(,?세후(순액)?)?$")


def g4_035(r, std):
    return [_f(r, "G4-035", rc, IF + "ProfitLossBeforeTax",
               "간접법 영업현금흐름이 법인세비용차감전순이익에서 출발 — 당기순손익에서 출발해야 함(감사보고서 수정 필요)")
            for rc, ns in _index(r)["nodes"].items()
            if (_stmt(rc) == "CF" or rc.startswith("D8511")) and IF + "ProfitLossBeforeTax" in ns]


PL_NAME_RE = re.compile(r"(Gain|Loss|Profit|Income|Expense|Revenue|Impairment|Cost|Depreciation|Amortisation|Reversal)")
PL_LAB_RE = re.compile(r"(수익|비용|이익|손실|손익|차익|차손|원가|상각비|전입|환입)")
MOVE_LAB_RE = re.compile(r"(증가|감소|증감|변동)")


def _cf_std_elems(std):
    """표준 현금흐름 목차(제목에 '현금흐름')의 pre에 나오는 요소 전체."""
    if not hasattr(std, "_gcf"):
        std._gcf = set().union(*(std.role_elems[x] for x, t in std.role_title.items() if "현금흐름" in t))
    return std._gcf


def _adj_counterpart(std, q):
    """손익 요소에 대응하는 현금흐름 조정 요소: AdjustmentsFor+이름 또는 'X 조정'·'X에 대한 조정' 라벨."""
    cf = _cf_std_elems(std)
    loc = _local(q)
    for c in (IF + "AdjustmentsFor" + loc, DART + "AdjustmentsFor" + loc):
        if c in cf:
            return c
    lab = re.sub(r"\s", "", std.label_ko.get(q, ""))
    want = {lab + "조정", lab + "에대한조정"}
    return next((c for c in sorted(cf) if std.kind(c) == "item" and re.sub(r"\s", "", std.label_ko.get(c, "")) in want), None)


def cf_operating_pl_rows(r, std):
    """간접법 영업활동 부분(현금흐름표 본문의 영업활동 영역, 현금흐름 주석의 영업활동현금흐름 [표])에 쓰인 손익 성격 행.
    kind: plbt(법인세비용차감전순이익 → G4-035), g4019(대응 조정 요소가 있고 차변/대변이 같음 → p83 나 예외 확인),
          g5c074(표준 손익 요소 → 수정 필요), ext(확장 행 이름이 AdjustmentsFor로 시작하지 않음 → 확인 권장).
    비현금거래 표·재무활동 부채 조정 표·투자/재무활동 부분은 대상이 아니다(G4-019·G5C-074)."""
    if hasattr(r, "_gcfpl"):
        return r._gcfpl
    cf = _cf_std_elems(std)
    rows, seen = [], set()
    for u in r.uris():
        rc = r.role_code(u)
        if _stmt(rc) == "CF":
            area = {(q, None, None) for root in (IF + "CashFlowsFromUsedInOperatingActivities",
                                                 IF + "CashFlowsFromUsedInOperatingActivitiesAbstract")
                    for q in _subtree(r, u, root)}
            tbl = {n: (t, tb) for n, p, tb, t, pl in r.role_nodes(u)}
            area = {(q, *tbl.get(q, (None, None))) for q, _, _ in area}
        elif rc.startswith("D8511"):
            area = {(n, t, tb) for n, p, tb, t, pl in r.role_nodes(u)
                    if t and "OperatingActivities" in _local(t) and not re.search(r"Investing|Financing", _local(t))}
        else:
            continue
        for q, t, tb in sorted(area, key=lambda x: x[0]):
            if (rc, q) in seen or r.kind(q) != "item":
                continue
            loc, lab = _local(q), _lab(r, q)
            if q == IF + "ProfitLossBeforeTax":
                kind, cp = "plbt", None
            elif std.is_std(q):
                c = std.concepts[q]
                if q in cf or c["period_type"] != "duration" or loc.startswith(("AdjustmentsFor", "IncreaseDecrease", "DecreaseIncrease")) \
                        or MOVE_LAB_RE.search(lab):
                    continue
                if not (PL_NAME_RE.search(loc) or PL_LAB_RE.search(lab) or any(x[:2] in ("D3", "D4") for x in std.elem_roles.get(q, ()))):
                    continue
                cp = _adj_counterpart(std, q)
                kind = "g4019" if cp and std.concepts[cp]["balance"] == c["balance"] else "g5c074"
            else:
                # 이름이 Adjustment…(AdjustmentTo·AdjustmentOf 포함)이거나 라벨에 '조정'이 있으면 조정 요소로 만든 것
                if (r.ext_attr.get(q) or {}).get("abstract") or loc.startswith("Adjustment") or "조정" in lab or \
                        not PL_LAB_RE.search(lab) or MOVE_LAB_RE.search(lab):
                    continue
                kind, cp = "ext", None
            seen.add((rc, q))
            rows.append(dict(rc=rc, q=q, t=t or "", tb=tb or "", kind=kind, cp=cp))
    r._gcfpl = rows
    return rows


def g4_019(r, std):
    """현금흐름 조정 행에 손익 요소를 썼는데 대응 조정 요소(AdjustmentsFor…)가 있고 차변/대변이 같은 경우 — p83 나 예외 확인."""
    return [_f(r, "G4-019", x["rc"], x["q"],
               f"영업활동 조정 행에 손익 요소 '{_lab(r, x['q'])}' 사용 — 대응 조정 요소 {x['cp']}가 있음. 손익계산서 요소와 "
               f"개념·차변/대변·금액이 모두 같을 때만 그대로 사용 가능(작성가이드 p83 나), 아니면 {x['cp']} 사용",
               level=REV, table=x["t"], parent=x["tb"])
            for x in cf_operating_pl_rows(r, std) if x["kind"] == "g4019"]


def g5c_074(r, std):
    """간접법 영업활동 조정 행에 손익 계정 사용(p339). 법인세비용차감전순이익 출발은 G4-035, 대응 조정 요소가 있는 건은 G4-019가 맡는다."""
    out = []
    for x in cf_operating_pl_rows(r, std):
        lab = _lab(r, x["q"])
        if x["kind"] == "g5c074":
            out.append(_f(r, "G5C-074", x["rc"], x["q"],
                          f"영업활동 조정 행에 손익 요소 '{lab}' 사용 — " +
                          (f"조정 요소 {x['cp']} 사용" if x["cp"] else "현금흐름 주석·본문의 조정 행(없으면 'AdjustmentsFor' 이름의 확장 행) 사용")
                          + " (작성가이드 p339)", table=x["t"], parent=x["tb"]))
        elif x["kind"] == "ext":
            out.append(_f(r, "G5C-074", x["rc"], x["q"],
                          f"영업활동 조정 확장 행 '{lab}'의 요소 ID가 AdjustmentsFor로 시작하지 않음 — 손익 요소가 아닌 조정 요소로 만들었는지 확인"
                          " (작성가이드 p83·339)", basis="약함", table=x["t"], parent=x["tb"]))
    return out


def g4_036(r, std):
    out = []
    acts = ("Operating", "Investing", "Financing")
    for u in r.uris():
        rc = r.role_code(u)
        if _stmt(rc) != "CF":
            continue
        parent = {ch: p for p, lst in r.pre[u].items() for _, ch, _ in lst}
        for n in {ch for lst in r.pre[u].values() for _, ch, _ in lst}:
            m = re.search(r"ClassifiedAs(Operating|Investing|Financing)Activities", _local(n))
            if not m:
                continue
            p, sec = parent.get(n), None
            while p and not sec:
                sec = next((a for a in acts if f"CashFlowsFromUsedIn{a}Activities" in _local(p)), None)
                p = parent.get(p)
            if sec and sec != m.group(1):
                out.append(_f(r, "G4-036", rc, n, f"{m.group(1)} 활동 분류 요소를 {sec} 활동 영역에 배치"))
    return out


# ---------------------------------------------------------------------------
# 5장: 주석 공통
# ---------------------------------------------------------------------------

def g5_002(r, std):
    if getattr(r, "frame", False):
        return []   # 틀 단계: 숫자 fact가 없는 게 정상 → 값 입력 후 판정
    num_roles = {rc for f, rc in _facts(r) if _num(f["value"]) is not None}
    return [_f(r, "G5-002", rc, rc, "우발부채 주석에 숫자 fact 없음(블록태깅만) — 보증·한도 등 디테일태깅")
            for rc in _index(r)["nodes"] if rc[:6] in ("D82757", "D82758") and rc not in num_roles]


def g5_006(r, std):
    """같은 요소·같은 context에 값이 다른 fact (도메인 합계열에 부분금액 입력 등)."""
    vals = defaultdict(set)
    for f in r.facts:
        # 단위 있는 숫자 fact만 — 표지(dart-gcd)의 코드값(감사인고유번호 등)은 금액·합계와 무관
        if _num(f["value"]) is not None and f["unit"] and not f["q"].startswith("dart-gcd:"):
            vals[(f["q"], f["ctx"])].add(_num(f["value"]))
    pl = dict(zip(map(id, r.facts), _index(r)["place"]))
    out, seen = [], set()
    for f in r.facts:
        k = (f["q"], f["ctx"])
        if len(vals.get(k, ())) > 1 and f["q"] not in seen:
            seen.add(f["q"])
            v = sorted(vals[k])
            out.append(_f(r, "G5-006", pl.get(id(f)), f["q"],
                          f"같은 요소·같은 기간·같은 차원에 다른 금액 {v[:3]} — 도메인 열엔 전체 합계, 부분금액은 분류 축으로"))
    return out


G5_026_SKIP = ("D80010", "D80020", "D80030", "D80040", "D88000")   # 범용 추가공시·추가정보 목차(거의 모든 요소를 담음)


# ---- 5장 Ⅱ.1~2 목차·표 선택 ----
def _used6(r):
    return {rc[:6] for rc in _index(r)["nodes"]}


def _homes(std, q):
    """표준 요소가 정의된 주석 목차(6자리, 연결/별도 구분 없이)."""
    return {x[:6] for x in std.elem_roles.get(q, ()) if x.startswith("D8")}


def _rtitle(std, rc6):
    t = std.role_title.get(rc6 + "0", "")
    m = re.search(r"주석 - (.+?) - 연결", t)
    return f"{rc6}0 {m.group(1)}" if m else rc6 + "0"


# 세분화 목차(p110~111 표): 통합 목차 -> 세분 목차
G5_016_SUB = {"D82239": ("D82234", "D82235", "D82236", "D82237", "D82238", "D82243", "D82244",
                         "D82241", "D82242", "D82245", "D82240"),
              "D82757": ("D82758",), "D86120": ("D86130", "D86131")}


def g5_016(r, std):
    """통합 목차(금융상품·기타충당부채·자본금)에 세분 목차 전용 요소가 들어 있고 그 세분 목차는 안 쓰면 확인 권장(p110-111)."""
    used, out = _used6(r), []
    for rc, ns in sorted(_index(r)["nodes"].items()):
        subs = G5_016_SUB.get(rc[:6])
        if not subs:
            continue
        cnt = Counter()
        for n in ns:
            if std.is_std(n) and std.kind(n) in ("item", "table"):
                h = _homes(std, n)
                if rc[:6] not in h:
                    cnt.update(s for s in subs if s in h)
        for s, k in sorted(cnt.items()):
            if s not in used and k >= 3:
                out.append(_f(r, "G5-016", rc, rc, f"통합 목차에 세분 목차 {_rtitle(std, s)} 전용 요소 {k}개 — 세분 목차 사용", basis="약함"))
    return out


# 주석 목차(6자리) -> 기준서. 확인된 목차만 싣는다(기준서가 다른 통합 주석만 개선 권장).
#  ① 가이드 p110~111 세분화 표(금융상품 12·리스 2·충당부채 2·자본 3) ② 표준 택사노미 참조(refs): 그 목차 전용 요소의 60% 이상이 한 기준서
#  ③ DART가 따로 만든 목차는 목차 전용 요소에 참조가 없어, 그 목차에 실린 ifrs-full 요소의 문단 참조로 정했다(여러 기준서면 묶음).
#     기타자산·기타부채·기타채권·기타채무·매입채무 및 기타채무 = IAS 1.55·78 / 담보제공자산 = IFRS 7.14·IAS 16.74·IAS 2.36·IAS 38.122·IAS 41.49 /
#     사용이 제한된 금융자산 = IAS 1.55(요소)·IFRS 7(축) / 가상자산 = IAS 1(한138.5~)·IAS 38.118 / 기타자본구성요소·추정불확실성·비용·수익 목차 = IAS 1
#  온실가스배출권(D82410)은 해당 기준서가 없어 싣지 않는다 → 기준서 미확인 = 수정 필요 유지
ROLE_STD_029 = {
    **{k: "IFRS 7" for k in ("D82239", "D82234", "D82235", "D82236", "D82237", "D82238", "D82243", "D82244",
                             "D82241", "D82242", "D82245", "D82240")},
    "D83261": "IFRS 16", "D83262": "IFRS 16", "D82757": "IAS 37", "D82758": "IAS 37",
    "D86120": "IAS 1", "D86130": "IAS 1", "D86131": "IAS 1",
    "D81000": "IAS 1", "D81100": "IAS 8", "D81300": "IAS 34", "D81500": "IAS 10", "D81600": "IAS 29", "D81700": "IFRS 3",
    "D81800": "IAS 24", "D81910": "IFRS 1", "D82210": "IAS 16", "D82220": "IFRS 6", "D82300": "IFRS 13", "D82318": "IAS 38",
    "D82418": "IAS 41", "D82450": "IFRS 14", "D82510": "IAS 40", "D82548": "IAS 27", "D82570": "IFRS 12", "D82590": "IFRS 5",
    "D82638": "IAS 2", "D83115": "IFRS 15", "D83140": "IAS 20", "D83241": "IAS 36", "D83412": "IFRS 2", "D83448": "IAS 19",
    "D83449": "IAS 26", "D83511": "IAS 12", "D83660": "IFRS 17", "D83800": "IAS 33", "D84200": "IAS 21", "D85110": "IAS 7",
    "D87110": "IFRS 8",
    "D82230": "IAS 1", "D82231": "IAS 1", "D82246": "IAS 1", "D82248": "IAS 1", "D82249": "IAS 1", "D86140": "IAS 1",
    "D81001": "IAS 1", "D83430": "IAS 1", "D83431": "IAS 1", "D83432": "IAS 1", "D83433": "IAS 1",
    "D82232": ("IFRS 7", "IAS 16", "IAS 2", "IAS 38", "IAS 41"), "D82247": ("IAS 1", "IFRS 7"), "D82319": ("IAS 1", "IAS 38"),
    "D83620": "IAS 23", "D83290": "SIC 29"}


def _std029(rc6):
    v = ROLE_STD_029.get(rc6)
    return set() if v is None else ({v} if isinstance(v, str) else set(v))


def g5_029(r, std):
    """다른 목차의 표준 표 차용은 그 원래 목차를 이미 다른 주석에서 쓸 때만(p115). 원래 목차가 보고서에 없으면 그 목차로 구성.
    (상이한 기준서 통합 공시 G5-017(p111)도 이 검출로 함께 본다)"""
    used, out, seen = _used6(r), [], set()
    for u, rc, t, info in _tables(r):
        if not rc.startswith("D8") or not std.is_std(t) or t == "dart:ConsolidatedAndSeparateFinancialStatementsTable":
            continue
        h = _homes(std, t)
        if not h or rc[:6] in h or (rc, t) in seen or h & used or (rc[:6] == "D83261" and h <= {"D83262"}):
            continue
        seen.add((rc, t))
        # 수정 필요(p115). 단, 기준서가 다른 내용을 한 주석으로 통합한 경우는 하나의 목차 가능(p111 다. 단서) → 개선 권장
        a, bs = _std029(rc[:6]), [_std029(x) for x in h]
        diff = bool(a) and all(bs) and not any(a & b for b in bs)   # 양쪽 기준서가 모두 확인되고 겹치지 않을 때만
        why = (f"다른 목차({', '.join(_rtitle(std, x) for x in sorted(h))})의 표준 표 — "
               + ("기준서가 다른 내용을 한 주석으로 통합(중요성 판단이면 하나의 목차 가능, p111) — 기준서별 목차로 하위 번호(예: 13-1, 13-2) 분리 권장"
                  if diff else "원래 목차를 쓰지 않았으므로 하위 번호(예: 13-1, 13-2)로 그 목차를 만들어 분리 공시"))
        f = _f(r, "G5-029", rc, t, why, table=t)
        if diff:
            f.extra["advice"] = True   # 가이드 p111 분리가 적절·하나의 목차 가능 = 권고(5단계)
        out.append(f)
    return out


NOTE_NO = re.compile(r"(?:^|,\s*)(\d+)(?:-(\d+))?\.")


def _note_numbers(r):
    """작성기 주석 제목의 번호: 연결/별도별 [(rc, [(번호, 하위번호)])]. ixd에만 있음(DART 원문 XBRL은 비어 있음)."""
    by = defaultdict(list)
    for rc, title in sorted(r.note_title.items()):
        if rc[:2] in ("D8", "U8"):   # 확장 목차 주석도 번호에 포함
            nums = [(int(a), int(b) if b else None) for a, b in NOTE_NO.findall(title.strip())]
            if nums:
                by[rc.endswith("5")].append((rc, nums))
    return by


def g5_019(r, std):
    """한 주석을 여러 목차로 나누면 '13-1','13-2' 하위 번호(p111). 같은 번호를 하위 번호 없이 여러 목차에 쓰면 확인 권장."""
    out = []
    for sep, lst in _note_numbers(r).items():
        rcs = defaultdict(list)
        for rc, nums in lst:
            for a, b in nums:
                rcs[a].append((rc, b))
        for a, xs in sorted(rcs.items()):
            if len(xs) >= 2 and any(b is None for _, b in xs):
                for rc, b in xs:
                    out.append(_f(r, "G5-019", rc, rc, f"주석 번호 {a}을(를) {len(xs)}개 목차에 사용 — 나눈 경우 '{a}-1', '{a}-2' 하위 번호",
                                  basis="약함"))
    return out


def g5_027(r, std):
    """병합·분할 시 주석 번호를 다시 매겨 순서대로(p113). 번호가 1부터 이어지지 않으면 확인 권장(빠진 번호 안내)."""
    out = []
    for sep, lst in _note_numbers(r).items():
        got = {a for _, nums in lst for a, _ in nums}
        miss = sorted(set(range(1, max(got) + 1)) - got) if got else []
        if miss:
            out.append(_f(r, "G5-027", "", "", f"{'별도' if sep else '연결'} 주석 번호 {', '.join(map(str, miss))} 없음 — "
                                              "감사보고서 주석과 맞는지, 병합·분할했으면 번호를 다시 매겨 순서대로", basis="약함"))
    return out


def _excl_use(r, std, home, pred):
    """home 목차(6자리) 전용 요소 중 pred에 맞는 것이 다른 주석 목차에 쓰인 곳: {rc: [요소]}."""
    res = defaultdict(list)
    for rc, ns in _index(r)["nodes"].items():
        if not rc.startswith("D8") or rc[:6] == home:
            continue
        for n in sorted(ns):
            if std.is_std(n) and std.kind(n) in ("item", "table") and pred(n) and _homes(std, n) == {home}:
                res[rc].append(n)
    return res


def g5_022(r, std):
    """고객과의 계약 부채·수익을 별도 주석으로 공시하면 D831150 목차(p112)."""
    if "D83115" in _used6(r):
        return []
    return [_f(r, "G5-022", rc, ns[0], f"고객과의 계약 수익 목차 전용 요소 {len(ns)}개를 다른 목차에 사용 — D831150 고객과의 계약에서 생기는 수익 목차",
               basis="약함") for rc, ns in sorted(_excl_use(r, std, "D83115", lambda n: True).items())]


def g5_023(r, std):
    """부동산 PF는 별도 주석이어도 '우발부채, 우발자산 및 약정사항'(D827580) 목차(p112)."""
    out = []
    for rc in sorted(_index(r)["nodes"]):
        if rc.startswith("D8") and rc[:6] != "D82758" and re.search(r"PF|프로젝트\s*파이낸싱", r.role_label(rc) or ""):
            out.append(_f(r, "G5-023", rc, rc, f"PF 주석을 '{r.role_label(rc)}' 목차로 공시 — D827580 우발부채, 우발자산 및 약정사항 목차",
                          basis="약함"))
    return out


def g5_024(r, std):
    """사용권자산을 유형자산에 포함해 공시하면 유형자산·리스 목차를 각각 쓰는 것을 권장(p112). 리스 목차가 없으면 확인 권장."""
    if _used6(r) & {"D83261", "D83262"}:
        return []
    out = []
    for rc, ns in sorted(_index(r)["nodes"].items()):
        if rc[:6] == "D82210":
            rou = sorted(n for n in ns if "Rightofuse" in n and r.kind(n) == "item")
            if rou:
                out.append(_f(r, "G5-024", rc, rou[0], "유형자산 목차에 사용권자산 포함, 리스 목차 없음 — 유형자산·리스 목차 각각 사용 권장"
                                                       "(사용권자산이 중요하지 않아 유형자산에 포함 공시하면 유형자산 목차만 가능)", basis="약함"))
    return out


def g5_025(r, std):
    """리스부채는 D832620 리스부채(또는 D832610 리스) 목차 권장, 기타부채에 포함하면 기타부채 목차 가능(p112-113)."""
    if _used6(r) & {"D83261", "D83262"}:
        return []
    out = []
    for rc, ns in sorted(_index(r)["nodes"].items()):
        if rc.startswith("D8") and rc[:6] != "D82231" and not rc.startswith("D85"):
            ll = sorted(n for n in ns if std.is_std(n) and re.search(r"LeaseLiabilit", n) and r.kind(n) == "item")
            if ll:
                out.append(_f(r, "G5-025", rc, ll[0], "리스부채를 리스 목차 없이 공시 — D832620 리스부채(또는 D832610 리스) 목차 권장", basis="약함"))
    return out


def _core_lab(s):
    return re.sub(r"\s|\[표\]|에\s*대한|공시|세부\s*정보|내역|의", "", s or "")


def _bigram(s):
    return {s[i:i + 2] for i in range(len(s) - 1)}


def g5_028(r, std):
    """표준 목차에 비슷한 이름의 표준 표가 있는데 확장 표를 만들면 확인 권장(p114)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if not rc.startswith("D8") or not _is_ext(r, t) or (rc, t) in seen:
            continue
        seen.add((rc, t))
        a = _bigram(_core_lab(_lab(r, t)))
        mine = _index(r)["nodes"].get(rc, set())
        best = None
        for x in sorted(std.role_elems.get(rc[:6] + "0", set()) | std.role_elems.get(rc[:6] + "5", set())):
            if std.kind(x) != "table" or x == "dart:ConsolidatedAndSeparateFinancialStatementsTable" or x in mine:
                continue
            b = _bigram(_core_lab(std.label_ko.get(x, "")))
            if a and b:
                sim = len(a & b) / len(a | b)
                if sim >= 0.6 and (best is None or sim > best[0]):
                    best = (sim, x)
        if best:
            out.append(_f(r, "G5-028", rc, t, f"확장 표 '{_lab(r, t)}' — 같은 목차의 표준 표 '{std.label_ko.get(best[1])}'(미사용) 사용",
                          basis="명확" if best[0] == 1 else "약함", table=t, pair=[best[1]]))
    return out


G5_031_ROLES = ("D82210", "D82318", "D82510", "D83261")   # 유형자산·무형자산·투자부동산·리스(p116·195·200·216·219)


def g5_031(r, std):
    """변동내역(기초·기말 행) 표는 ifrs-full '세부 정보 공시 [표]'(p116, 유형자산 p195·200, 투자부동산 p216·219 = G5C-015).
    같은 목차에 ifrs-full 세부 정보 표가 있는데 dart·확장 표로 변동내역을 공시하면 확인 권장."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if rc[:6] not in G5_031_ROLES or t.startswith(IF) or (rc, t) in seen or not _is_change_table(info) or _sibling_tables(r, u, t) \
                or t == "dart:ConsolidatedAndSeparateFinancialStatementsTable":
            continue
        seen.add((rc, t))
        rows = _period_rows(info)
        ti = _std_table_items(std)
        detail = [x for x in std.role_elems.get(rc[:6] + "0", set()) | std.role_elems.get(rc[:6] + "5", set())
                  if x.startswith(IF) and std.kind(x) == "table" and re.search(r"세부 정보|변동", std.label_ko.get(x, ""))]
        # 그 목차의 ifrs-full 세부 정보·변동 표를 이미 쓰면 나눠 공시(…합계 등, p116 다.)로 보고 제외
        if any(x in _index(r)["nodes"].get(rc, set()) for x in detail):
            continue
        cand = sorted(x for x in detail if rows and rows <= ti.get(x, set()))
        if cand:
            out.append(_f(r, "G5-031", rc, t, f"변동내역 표를 '{_lab(r, t)}'로 공시 — ifrs-full 세부 정보 공시 표 '{std.label_ko.get(cand[0])}' 사용",
                          basis="약함", table=t))
    return out


def _std_table_items(std):
    """표준 pre: 표 -> 그 표의 행(표와 같은 부모 아래 LineItems의 하위 요소, 모든 role 합집합)."""
    if hasattr(std, "_gti"):
        return std._gti
    c = sqlite3.connect(STD_DB)
    tree = defaultdict(lambda: defaultdict(list))
    for rc, p, n, par in c.execute("SELECT role_code,prefix,name,parent FROM pre_arcs"):
        if par:
            tree[rc][par].append(f"{p}:{n}")
    c.close()
    res = defaultdict(set)
    for rc, km in tree.items():
        for par, kids in km.items():
            tabs = [k for k in kids if std.kind(k) == "table"]
            for t in tabs:
                stack = [k for k in kids if k not in tabs]
                while stack:
                    x = stack.pop()
                    res[t].add(x)
                    stack.extend(km.get(x, ()))
    std._gti = res
    return res


NET_DB_TABLE = IF + "DisclosureOfNetDefinedBenefitLiabilityAssetTable"


def g5_032(r, std):
    """순확정급여부채(자산) 조정내역은 확정급여채무·사외적립자산을 따로 공시해도 '순확정급여부채(자산)에 대한 공시 [표]' 하나로(p116).
    확정급여채무·사외적립자산 변동(기초·기말 행)을 확장 표로 따로 공시하면 수정 필요."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if rc[:6] != "D83448" or not _is_ext(r, t) or (rc, t) in seen or not _is_change_table(info) or _sibling_tables(r, u, t):
            continue
        if not any(re.search(r"DefinedBenefit|PlanAssets", q) for q in _period_rows(info)):
            continue
        seen.add((rc, t))
        out.append(_f(r, "G5-032", rc, t, f"확정급여채무·사외적립자산 변동을 '{_lab(r, t)}'로 공시 — 순확정급여부채(자산)에 대한 공시 [표] 하나로 구성",
                      table=t))
    return out


def g5_026(r, std):
    """확장 목차의 표준요소 70% 이상이 한 표준 주석 목차 소속이면 그 목차 사용 검토(p113).
    G5-026: 범용 추가공시 목차는 후보에서 빼고, 표준 축·멤버도 함께 세며, 표준요소 3개 미만 목차는 띄우지 않는다."""
    out = []
    for rc, ns in sorted(_index(r)["nodes"].items()):
        if rc in std.role_title:
            continue
        cnt = Counter()
        items = sorted(n for n in ns if std.is_std(n) and std.kind(n) in ("item", "axis", "member")
                       and n != CONSEP_AXIS and not (std.kind(n) == "member" and n in std.axis_members.get(CONSEP_AXIS, ())))
        for n in items:
            for x in {x[:6] for x in std.elem_roles.get(n, ()) if x.startswith("D8") and not x.startswith(G5_026_SKIP)}:
                cnt[x] += 1     # 요소당 한 번, 연결/별도 구분 없이, 주석 목차만
        if len(items) >= 3 and cnt:
            # 건수가 같으면 목차 코드순(집합 순회 순서에 따라 안내 목차가 바뀌지 않게)
            top, k = min(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
            if k / len(items) >= 0.7:
                # 연결/별도는 검출된 목차 끝자리(0 연결, 5 별도)에 맞춰 7자리 코드와 목차 이름으로 안내
                code = top + ("5" if rc.endswith("5") else "0")
                title = std.role_title.get(code) or std.role_title.get(top + "0") or std.role_title.get(top + "5") or ""
                m = re.search(r"\]\s*(?:주석\s*-\s*)?(.+?)(?:\s*-\s*(?:연결|별도))?\s*(?:\||$)", title)
                name = f" '{m.group(1).strip()}'" if m else ""
                out.append(_f(r, "G5-026", rc, rc, f"확장 목차의 표준 요소·축·멤버 {k}/{len(items)}개가 표준 목차 {code}{name} 소속 — 표준 목차 사용 검토",
                              basis="약함"))
    return out


# 범용 상각 행 → 자산 한정 행 이름 앞부분(한정 행 이름엔 'Expense'가 없음: DepreciationPropertyPlantAndEquipment 등)
G5B005_ROW = {IF + "DepreciationExpense": ("Depreciation",), IF + "AmortisationExpense": ("Amortisation",),
              IF + "DepreciationAndAmortisationExpense": ("DepreciationAndAmortisation", "Depreciation", "Amortisation")}
# 자산별 분류 축 → 그 축 도메인의 자산(분류 축 자체에 범용 행을 쓴 구조)
G5B005_AXIS = {IF + "ClassesOfPropertyPlantAndEquipmentAxis": "PropertyPlantAndEquipment",
               IF + "ClassesOfIntangibleAssetsOtherThanGoodwillAxis": "IntangibleAssetsOtherThanGoodwill",
               IF + "ClassesOfIntangibleAssetsAndGoodwillAxis": "IntangibleAssetsOtherThanGoodwill",   # 상각은 영업권 이외만
               IF + "ClassesOfRightofuseAssetsAxis": "RightofuseAssets",
               IF + "ClassesOfInvestmentPropertyAxis": "InvestmentProperty"}


def _g5b005_cand(std, q, stem):
    """범용 행 q + 자산 이름 → 표준에 있는 자산 한정 행(대응표 우선, 이름 결합은 보조)."""
    for c in [IF + p + stem for p in G5B005_ROW.get(q, ())] + [q + stem]:
        if c in std.concepts:
            return c
    return None


def g5b_005(r, std):
    """자산분류 축 멤버 + 범용 행 → 자산 한정 표준 행이 있으면 위반(p121~122).
    G5B-005: 대응표로 한정 행을 찾고, 가이드 예시(자산의 분류 축)가 아닌 자산별 분류 축은 확인 권장."""
    out, seen = [], set()
    for f, rc in _facts(r, struct=True):
        q = f["q"]
        if not std.is_std(q):
            continue
        for ax, m in _dims(r, f).items():
            if "ClassesOf" not in ax or not re.search(r"Assets|PropertyPlant|Intangible|InvestmentProperty", ax):
                continue
            stem = re.sub(r"(Member|Domain)$", "", _local(m))
            cand = _g5b005_cand(std, q, stem)
            if cand and (rc, q, m) not in seen:
                seen.add((rc, q, m))
                out.append(_f(r, "G5B-005", rc, q, f"'{_lab(r, m)}' 멤버와 범용 행 사용 — 자산 한정 표준 행 {cand} 사용",
                              basis="명확" if ax == IF + "ClassesOfAssetsAxis" else "약함"))
    # 자산별 분류 축 표(도메인 = 그 자산 전체)에 범용 상각 행 — 가이드 예시와 축이 달라 확인 권장
    for u, rc, t, info in _tables(r):
        for ax, stem in G5B005_AXIS.items():
            if ax not in info["axes"]:
                continue
            for q in dict.fromkeys(info["items"]):
                cand = q in G5B005_ROW and _g5b005_cand(std, q, stem)
                if cand and (rc, q, ax) not in seen:
                    seen.add((rc, q, ax))
                    out.append(_f(r, "G5B-005", rc, q, f"'{_lab(r, ax)}' 표에 범용 행 '{_lab(r, q)}' — 자산 한정 표준 행 {cand} 사용 검토",
                                  basis="약함", table=t))
    return out


def g5b_008(r, std):
    out = []
    _, under, _ = _def_axis_members(r)
    if IF + "AtCostMember" in under.get(CARRYING_AXIS, ()):
        out.append(_f(r, "G5B-008", "", IF + "AtCostMember", "장부금액 축 아래 취득원가 멤버 — 총장부금액 멤버 사용"))
    for f, rc in _facts(r, lambda f, rc: rc[:6] in ("D82210", "D82510")):
        if IF + "AtCostMember" in _dims(r, f).values():
            out.append(_f(r, "G5B-008", rc, f["q"], "유형자산·투자부동산에 취득원가(AtCost) 멤버 — 장부금액 축의 총장부금액 멤버 사용"))
            break
    return out


def g5b_019(r, std):
    sdom, _ = _std_extra(std)
    doms, _, _ = _def_axis_members(r)
    out = []
    for ax, ds in doms.items():
        if std.is_std(ax) and sdom.get(ax) and not ds <= sdom[ax]:
            out.append(_f(r, "G5B-019", "", ax, f"표준 축에 정의 밖 도메인 {sorted(ds - sdom[ax])[:2]} 연결"))
        elif not std.is_std(ax) and len(ds) > 1:
            out.append(_f(r, "G5B-019", "", ax, f"확장 축에 도메인 {len(ds)}개 — 1축 1도메인"))
    return out


PERIOD_WORD = re.compile(r"(^|[\s(])(기초|기말|전기말|당기말)($|[\s)]|잔액)")


def g5b_031(r, std):
    out = []
    # 변동표(기초·기말 라벨 행이 있는 표)의 행 집합
    chg = {(rc, q) for u, rc, t, info in _tables(r) if _is_change_table(info) for q in info["items"]}
    std_labs = {re.sub(r"\s", "", l) for q, l in std.label_ko.items() if std.kind(q) == "item"}
    for q, rc in _ext_items_in(r).items():
        lab = _lab(r, q)
        m = PERIOD_WORD.search(lab) or re.search(r"20\d\d년", lab)
        if not m or re.search(r"(기초|기말|전기말|당기말)\s*기준|중\s*\S*정산", lab):
            continue   # '전기말 기준 …'처럼 기준일 수식어는 기초·기말 행 분할이 아님
        # 변동표의 행이거나, 기간 표현을 뺀 같은 이름의 표준요소가 있을 때만 위반
        core = re.sub(r"\s|\(|\)|기초|기말|전기말|당기말|잔액|20\d\d년", "", lab)
        if (rc, q) not in chg and not any(c in std_labs for c in (core, core + "수")):
            continue
        if m:
            out.append(_f(r, "G5B-031", rc, q, f"확장 행 '{lab}'에 기간 표현('{m.group(0)}') — 같은 요소에 기초·기말 라벨 사용"))
    return out


def g5b_035(r, std):
    groups = defaultdict(dict)
    pl = {}
    for f, rc in _facts(r):
        if "percent" not in _attr(r, f["q"])["type"].lower():
            continue   # 단일 이자율 규칙 — 이자율(percent 타입) 요소만(금액 요소 제외)
        d = _dims(r, f)
        m = d.pop(IF + "RangeAxis", None)
        if m:
            c = _ctx(r, f)
            k = (f["q"], c.get("instant") or (c.get("start"), c.get("end")), tuple(sorted(d.items())), _sep(r, f))
            groups[k][m] = _num(f["value"])
            pl[k] = rc
    out, seen = [], set()
    for k, ms in groups.items():
        top, bot = ms.get(IF + "TopOfRangeMember"), ms.get(IF + "BottomOfRangeMember")
        if not top and not bot:
            continue   # 상·하위 모두 0 = 해당 없음(-)을 0으로 넣은 것, 단일 이자율 아님
        if (top is not None and bot is not None and top == bot) or (top is None) != (bot is None):
            if (pl[k], k[0]) not in seen:
                seen.add((pl[k], k[0]))
                out.append(_f(r, "G5B-035", pl[k], k[0], "범위 축에 단일값(최상위=최하위 또는 한쪽만) — 단일 이자율은 도메인 열"))
    return out


def g5b_039(r, std):
    bad = {DART + "FinancialAssetsDenominatedInForeignCurrencyOriginalCurrency",
           DART + "FinancialLiabilitiesDenominatedInForeignCurrencyOriginalCurrency"}
    return [_f(r, "G5B-039", rc, f["q"], "기초통화금액 표준요소 직접 사용 — 통화코드 붙여 확장(…OriginalCurrencyUSD)")
            for f, rc in {(f["q"], rc): (f, rc) for f, rc in _facts(r) if f["q"] in bad}.values()]


SUFFIX = re.compile(r"(FinancialAssets|FinancialLiabilities|FinancialInstruments|Assets|Liabilities)$")


def _period_rows(info):
    """변동표의 기초·기말(periodStart/End 라벨) 행."""
    return {q for q, pl in zip(info["items"], info["pref_labels"]) if pl and re.search(r"period(Start|End)", pl)}


def _sibling_tables(r, u, t):
    """표 t와 같은 부모 아래 있는 다른 [표] — 있으면 info['items']에 여러 표의 행이 섞인다."""
    nodes = r.role_nodes(u)
    par = next((p for n, p, *_ in nodes if n == t), None)
    return {n for n, p, *_ in nodes if p == par and n != t and r.kind(n) == "table"}


def g5b_043(r, std):
    """G5B-043: 여러 표의 행이 한 부모 아래 섞인 표는 제외. p139 예시와 같은 혼재
    (기초·기말 금융자산 + '…, 공정가치 측정, 자산' 행 / 기초·기말 자산 + '…, 금융자산' 행)는 수정 필요."""
    out = []
    for u, rc, t, info in _tables(r):
        if not _is_change_table(info) or t == "dart:ConsolidatedAndSeparateFinancialStatementsTable":
            continue   # 연결/별도 공통 표는 여러 블록의 행이 섞여 있어 판정 대상 아님
        if _sibling_tables(r, u, t):
            continue
        sx ={SUFFIX.search(_local(q)).group(1) for q in info["items"] if SUFFIX.search(_local(q))}
        if len(sx) > 1 and not ({"Assets", "Liabilities"} >= sx):
            base = _period_rows(info)
            rows = set(info["items"]) - base
            guide = (any("FinancialAssets" in _local(q) for q in base) and any(_local(q).endswith("FairValueMeasurementAssets") for q in rows)) \
                or (IF + "Assets" in base and any(_local(q).endswith("FinancialAssets") for q in rows))
            out.append(_f(r, "G5B-043", rc, t, f"변동표 행의 분류 기준 혼재({', '.join(sorted(sx))}) — 기초~기말 같은 기준 행"
                          + (" (작성가이드 p139: 금융자산 기준 변동표에 '…, 자산' 행 혼용 불가)" if guide else ""),
                          basis="명확" if guide else "약함", table=t, parent=info["parent_tb"] or ""))
    return out


# 한정 행 수식어가 하위 범위·측정 기준인 것(범용 행이 맞을 수 있음 → 확인 권장). '총액'은 다른 개념이라 제외(G5B-044 ③)
G5B044_SUBRANGE = {"확정급여제도", "미할인현금흐름", "의무적으로측정된공정가치"}
G5B044_ALIAS = {"판관비": "판매비와관리비"}


def _nospace(s):
    return re.sub(r"\s", "", s or "")


def g5b_044(r, std):
    """표준 목차에 이 행의 한정판(라벨 'X, …')이 있는데 범용 행 X를 사용.
    G5B-044: 주석(D8) 목차만(p139 (8) '주석 공시'). 수식어가 목차·표준 표 이름의 주제(판관비·특수관계자거래·
    유형자산·영업권 이외의 무형자산·비현금거래 등)면 수정 필요, 하위 범위(확정급여제도 등)는 확인 권장, 총액은 제외."""
    out, seen = [], set()
    for rc, ns in _index(r)["nodes"].items():
        se = std.role_elems.get(rc)
        if not se or not rc.startswith("D8"):
            continue
        labs = {std.label_ko.get(q, ""): q for q in se if std.kind(q) == "item"}
        # 목차 주제: 목차 제목('[D…] 주석 - 무형자산 - 연결'의 가운데)과 그 목차의 표준 표 이름
        title = (std.role_title.get(rc) or "").split("|")[0]
        topic = [_nospace(title.split(" - ")[1] if title.count(" - ") >= 2 else title)] + \
            [_nospace(std.label_ko.get(q, "")) for q in se if std.kind(q) == "table"]
        for n in ns:
            if not std.is_std(n) or std.kind(n) != "item" or n in se or (rc, n) in seen:
                continue
            base = std.label_ko.get(n, "")
            hit = next((q for l, q in labs.items() if base and l.startswith(base + ",")), None)
            if not hit:
                continue
            qual = _nospace(std.label_ko[hit][len(base) + 1:])
            if qual == "총액":
                continue
            seen.add((rc, n))
            qa = G5B044_ALIAS.get(qual, qual)
            on_topic = qual not in G5B044_SUBRANGE and any(t and (qa in t or t in qa) for t in topic)
            # 한정 행이 더 좁은 개념(확정급여제도 등)이면 범용 행이 맞을 수 있다 → 확인 권장
            out.append(_f(r, "G5B-044", rc, n, f"표준 목차에 해당 행 '{std.label_ko[hit]}'({hit})이 있는데 다른 표의 '{base}' 사용 — "
                          + ("표준 목차의 한정 행 사용(작성가이드 p139)" if on_topic else "공시 범위가 같으면 한정 행으로"),
                          basis="명확" if on_topic else "약함"))
    return out


def g5b_046(r, std):
    # G5B-046: 변동표(기초·기말 행 있는 표)에 쓴 유동/비유동 확장 멤버는 수정 필요(p140·187), 잔액표는 지금처럼
    ax = DART + "ClassesOfCurrentAndNoncurrentValuesUsedForChangesInAssetAndLiabilityAxis"
    in_chg = {m for u, rc, t, info in _tables(r) if _is_change_table(info) for ms in info["axes"].values() for m in ms}
    out = []
    for m, rc in _ext_members(r).items():
        if not re.search(r"유동(?!화)", _lab(r, m)):
            continue
        what = r.kind(m) == 'axis' and '축' or '멤버'
        if m in in_chg:
            out.append(_f(r, "G5B-046", rc, m, f"변동표에 유동/비유동 확장 {what} '{_lab(r, m)}' — 자산부채변동의 유동비유동 분류 [축]({ax}) 사용"))
        else:
            out.append(_f(r, "G5B-046", rc, m, f"유동/비유동 확장 {what} '{_lab(r, m)}' — 변동 공시엔 dart 전용 축, 잔액은 행으로",
                          basis="명확" if re.fullmatch(r"\s*(비)?유동(분|성)?\s*(\[구성요소\])?\s*", _lab(r, m)) else "약함"))
    for u, rc, t, info in _tables(r):
        if ax in info["axes"] and not _is_change_table(info):
            out.append(_f(r, "G5B-046", rc, ax, "유동/비유동 전용 축을 변동표가 아닌 표에 사용", table=t))
    return out


PERIOD_MEMBER = re.compile(r"(^|[\s(])(당|전)(기|분기|반기)말?($|[\s)])|기말|current\s*year|prior\s*year", re.I)


YEAR_ONLY = re.compile(r"^\s*(20\d\d)\s*(년|년도|년말|\.12\.31)?\s*(\[구성요소\])?\s*$")


def g5b_052(r, std):
    """G5B-052: 배출권 계획기간 축(dart 표준) 아래 이행연도 멤버·주식 변동 사건 날짜는 보고기간 열이 아님(p144) →
    기간어 멤버에서 계획기간·이행연도·배출권은 빼고, 연도 멤버는 라벨이 보고연도·직전연도 표현뿐일 때만."""
    _, under, _ = _def_axis_members(r)
    planned = under.get(DART + "PlannedPeriodAxis", set())
    end = _end(r)
    out = []
    for m, rc in _ext_members(r).items():
        lab = _lab(r, m)
        if m in planned or re.search(r"이행연도|계획기간|배출권", lab):
            continue
        y = YEAR_ONLY.match(lab)
        if PERIOD_MEMBER.search(lab) or PERIOD_MEMBER.search(r.label_en.get(m) or ""):
            out.append(_f(r, "G5B-052", rc, m, f"기간 성격 확장 멤버 '{lab}' — 당기말/전기말은 기간(context)으로 구분",
                          basis="약함" if re.search(r"20\d\d|이행", lab) else "명확"))
        elif y and int(y.group(1)) in (end.year, end.year - 1):
            out.append(_f(r, "G5B-052", rc, m, f"연도 확장 멤버 '{lab}' — 보고기간 구분 용도면 기간(context)으로", basis="약함"))
    return out


def g5b_053(r, std):
    end = _end(r)
    fy = date(end.year, 1, 1)
    py_end = date(end.year - 1, 12, 31)
    same_py = date(end.year - 1, end.month, end.day) if not (end.month == 2 and end.day == 29) else date(end.year - 1, 2, 28)
    q_start = date(end.year if end.month > 2 else end.year - 1, (end.month - 3) % 12 + 1, 1)   # 3개월 시작일
    inst = {end, py_end, date(end.year - 2, 12, 31), same_py}
    dur = {(fy, end), (q_start, end), (date(end.year - 1, 1, 1), same_py),
           (q_start.replace(year=q_start.year - 1), same_py), (date(end.year - 1, 1, 1), py_end)}
    bad = Counter()
    first = {}
    for f, rc in _facts(r):
        c = _ctx(r, f)
        if c.get("instant"):
            ok = _ymd(c["instant"]) in inst
            k = c["instant"]
        else:
            ok = (_ymd(c.get("start")), _ymd(c.get("end"))) in dur
            k = f"{c.get('start')}~{c.get('end')}"
        if not ok:
            bad[k] += 1
            first.setdefault(k, (rc, f["q"]))
    return [_f(r, "G5B-053", first[k][0], first[k][1], f"보고기간 밖 기간 {k}에 fact {n}건", basis="약함")
            for k, n in sorted(bad.items())]


def g5b_054(r, std):
    out, seen = [], set()
    for f, rc in _facts(r):
        a = _attr(r, f["q"])
        if "monetary" not in a["type"].lower() or f["nil"]:
            continue
        unit = f["unit"] or ""
        if not unit.startswith("iso4217:"):
            why, basis = f"금액 요소에 통화 단위 없음({unit or '없음'})", "명확"
        elif std.is_std(f["q"]) and unit != "iso4217:KRW" and "Currency" not in _local(f["q"]):
            why, basis = f"표준 금액 요소에 외화 단위 {unit} — 외화는 통화코드 확장요소로", "약함"
        else:
            continue
        if (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5B-054", rc, f["q"], why, basis=basis))
    return out


SHARE_KO = re.compile(r"주식\s?수|주식[^,()]{0,8}수량|발행주식")
SHARE_ID = re.compile(r"NumberOf\w*Shares|Shares\w*(Issued|Outstanding)")
SHARE_NOT_KO = re.compile(r"액면|총액|금액|배당|이익|손익|차이|자본금|일수|\(일\)|적수|선택권|전환권|양도제한|성과보상|선택형|"
                          r"기타자본|금융상품|가상자산|배출권|tCO2|톤|파생")
SHARE_NOT_ID = re.compile(r"Option|Unit|Right|Instrument|Incentive|Emission|Crypto|Derivative|Dividend|Days|GrossValue|Difference|Amount")


def _is_share_count(r, q):
    """주식 수 행인지: 요소 자신의 한글명·이름(소속 표 꼬리 제외)으로 판정. 금액·일수·주식 아닌 수량(선택권·배출권 등)은 제외.
    한글명이 주식 수를 분명히 가리키면 한글 제외어만 본다(ID의 'ConvertibleInstruments'·'UnitShares' 같은 단어로 빼지 않음)."""
    ko, own = _lab(r, q), _local(q)
    if SHARE_KO.search(ko):
        return not SHARE_NOT_KO.search(ko)
    return bool(SHARE_ID.search(own)) and not (SHARE_NOT_KO.search(ko) or SHARE_NOT_ID.search(own))


def g5b_057(r, std):
    return [_f(r, "G5B-057", rc, q, f"주식 수 요소 '{_lab(r, q)}'의 타입이 {_attr(r, q)['type']} — sharesItemType")
            for q, rc in _ext_items_in(r).items()
            if _is_share_count(r, q)
            if "shares" not in _attr(r, q)["type"].lower()
            and re.search(r"decimal|integer|monetary|pure", _attr(r, q)["type"].lower())]


GAIN_ONLY = re.compile(r"(이익|수익|차익)")
GAIN_BOTH = re.compile(r"손실|손익|차손|비용|결손|이익잉여금")


def _is_3m(r, f):
    c = _ctx(r, f)
    if c.get("instant") or not c.get("start"):
        return False
    return (_ymd(c["end"]) - _ymd(c["start"])).days < 100


def g5b_058(r, std):
    # 3개월 열 음수는 확인 권장(3개월 금액을 누적에서 이전 분기를 빼서 만들면 음수가 생길 수 있음)
    hits = {}
    for f, rc in _facts(r):
        if _stmt(rc) == "CF" or rc.startswith("D8511"):
            continue   # 현금흐름표·영업에서 창출된 현금 주석의 조정 항목은 손실 공시가 아님
        v = _num(f["value"])
        q = f["q"]
        lab = _lab(r, q)
        if v is None or v >= 0 or _attr(r, q)["period_type"] == "instant" or not GAIN_ONLY.search(lab) or GAIN_BOTH.search(lab) or \
                re.search(r"GainLoss|ProfitLoss|IncomeLoss|IncomeExpense|IncreaseDecrease|Adjustments", _full(q)):
            continue
        if set(_dims(r, f).values()) - {REPORTED_MEMBER, IF + "CarryingAmountMember"}:
            continue
        h = hits.setdefault((rc, q), {"lab": lab, "cum": None, "q3": None})
        h["q3" if _is_3m(r, f) else "cum"] = h["q3" if _is_3m(r, f) else "cum"] or f["value"]
    out = []
    for (rc, q), h in hits.items():
        if h["cum"]:
            out.append(_f(r, "G5B-058", rc, q, f"이익 전용 행 '{h['lab']}'에 음수 {h['cum']} — 손실 행에 양수로"))
        else:
            out.append(_f(r, "G5B-058", rc, q, f"이익 전용 행 '{h['lab']}'의 3개월 열에 음수 {h['q3']} — 누적 열은 양수, 3개월 차감 결과인지 확인",
                          basis="약함"))
    return out


def g5b_062(r, std):
    # 비율 여부는 현재 한글 라벨로 본다(요소 ID엔 처음 만든 영어명이 남을 수 있음, p81). 통화 단위로 입력된 요소는 금액이므로 제외
    money = {f["q"] for f in r.facts if (f["unit"] or "").startswith("iso4217:")}
    return [_f(r, "G5B-062", rc, q, f"비율 확장요소 '{_lab(r, q)}'의 타입이 {_attr(r, q)['type']} — percentItemType")
            for q, rc in _ext_items_in(r).items()
            if (re.search(r"[율률]$|비율", _lab(r, q)) if _lab(r, q) else re.search(r"(Rate|Ratio)$", _local(q).split("Of")[0]))
            and q not in money
            and not re.search(r"환율|ExchangeRate", _lab(r, q) + _local(q))
            and "percent" not in _attr(r, q)["type"].lower()
            and re.search(r"decimal|integer|monetary|pure", _attr(r, q)["type"].lower())]


ISO_DUR = re.compile(r"^-?P(?=\d|T)(\d+Y)?(\d+M)?(\d+D)?(T(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$")


def g5b_063(r, std):
    out = []
    for f, rc in _facts(r):
        if "duration" in _attr(r, f["q"])["type"].lower() and f["value"] and not ISO_DUR.match(f["value"].strip()):
            out.append(_f(r, "G5B-063", rc, f["q"], f"기간 타입 fact 값 '{f['value']}' — P3Y6M 형식으로"))
    for q, rc in _ext_items_in(r).items():
        if re.search(r"만기|기간", _lab(r, q)) and re.search(r"decimal|pure", _attr(r, q)["type"], re.I):
            out.append(_f(r, "G5B-063", rc, q, f"기간을 숫자 확장요소 '{_lab(r, q)}'로 입력 — durationItemType 요소 사용", basis="약함"))
    return out


RATE_IDX = re.compile(r"SOFR|LIBOR|KOFR|COFIX|EURIBOR|TIBOR|SHIBOR|CD|국고채|기준금리", re.I)


def g5b_064(r, std):
    out, seen = [], set()
    for f, rc in _facts(r):
        v = (f["value"] or "").strip()
        # 기준이자율명에 가산금리 숫자가 붙은 경우만(예: 'CD+1.5%'). '3M SOFR'처럼 기준이자율명만 있는 값은 정상
        # G5B-064: 기준이자율 행에 가산 문자열을 통째로 넣은 것이 가이드 금지 형태(p149·231) → 기준이자율 행 제외 조건 삭제
        if _num(v) is None and len(v) < 120 and RATE_IDX.search(v) and re.search(r"[A-Za-z가-힣].*[+\-]\s*\d|%", v) and (rc, f["q"]) not in seen \
                and (re.search(r"이자율|금리", _lab(r, f["q"])) or re.search(r"InterestRate", _local(f["q"]))):
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5B-064", rc, f["q"], f"변동이자율을 문자열 '{v[:40]}'로 입력 — 기준이자율(문자)+조정(소수) 행으로"))
    return out


def g5b_068(r, std):
    out, seen = [], set()
    for f, rc in _facts(r):
        d = _dims(r, f)
        v = _num(f["value"])
        a = _attr(r, f["q"])
        if v is None or a["period_type"] != "instant" or not v:
            continue
        hit = d.get(CARRYING_AXIS) in DEDUCT_MEMBERS or d.get(IF + "NetDefinedBenefitLiabilityAssetAxis") == IF + "PlanAssetsMember"
        bad = v > 0   # 차감 멤버 열 = 행을 줄이는 금액 → 음수 입력(로 대변 행 판정 오류 수정)
        if hit and bad and (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            m = d.get(CARRYING_AXIS) or IF + "PlanAssetsMember"
            out.append(_f(r, "G5B-068", rc, f["q"], f"차감 멤버 '{_lab(r, m)}' 값 {f['value']} — 차감 열은 음수로 입력"))
    return out


# ---------------------------------------------------------------------------
# 5장: 계정별
# ---------------------------------------------------------------------------

INV_RE = re.compile(r"Inventor|RawMaterials|FinishedGoods|WorkInProgress|Merchandise|Supplies")


INV_COST_COLS = {IF + "GrossCarryingAmountMember", DART + "AllowanceForInventoryValuationMember"}


def g5c_001(r, std):
    """재고자산 분류별 총장부금액·평가충당금은 있는데 같은 분류의 장부금액(장부금액 축 없는 값 또는 장부금액 멤버)이
    없으면 누락(p168). 평가손실·비용인식 표의 축 구성은 '재고자산 표의 장부금액 축 구성' 카드가 맡는다."""
    # 차원 없는 장부금액 값은 본문(재무상태표) role로 배정되므로 role은 묶음 기준에서 뺀다(같은 요소·시점·차원이면 같은 값)
    grp, where = defaultdict(set), {}
    for f, rc in _facts(r):
        c = _ctx(r, f)
        if not c.get("instant") or _num(f["value"]) is None or not INV_RE.search(_local(f["q"])):
            continue
        d = _dims(r, f)
        col = d.pop(CARRYING_AXIS, None)
        k = (f["q"], c["instant"], _sep(r, f), tuple(sorted(d.items())))
        grp[k].add(col)
        if col in INV_COST_COLS:
            where.setdefault(k, rc)
    out = []
    for k, cols in grp.items():
        q, inst = k[0], k[1]
        rc = where.get(k, "")
        if cols & INV_COST_COLS and not cols & {None, IF + "CarryingAmountMember"}:
            out.append(_f(r, "G5C-001", rc, q, f"{inst} '{_lab(r, q)}' 총장부금액·평가충당금만 있고 장부금액 값 없음 — 분류별 장부금액도 입력"))
    return out


INV_SKIP_RE = re.compile(r"Pledged|Transfer|IncreaseDecrease|Adjustments")


def g5c_002(r, std):
    # 재고자산 분류와 무관한 담보제공·대체·증감·조정 요소는 제외, 재고자산 주석(D82638x) 밖은 확인 권장
    out, seen = [], set()
    for f, rc in _facts(r, struct=True):
        n = _local(f["q"])
        if INV_RE.search(n) and not INV_SKIP_RE.search(n):
            for ax in _dims(r, f):
                if _is_ext(r, ax) and (rc, ax) not in seen:
                    seen.add((rc, ax))
                    out.append(_f(r, "G5C-002", rc, ax, f"재고자산에 확장 분류 축 '{_lab(r, ax)}' — 재고자산 분류는 행으로",
                                  basis="명확" if rc[:6] == "D82638" else "약함"))
    return out


LA_MOVE = re.compile(r"AdditionReversalOfCreditLoss|DecreaseThroughDerecognition|IncreaseDecreaseThroughOtherMovements\w*FinancialAssets|"
                     r"WriteOff\w*FinancialAssets|ImpairmentLoss\w*FinancialAssets")
AGING_AX = re.compile(r"PastDueStatus|CreditImpair")


def _loss_allowance_table(t, info):
    """손실충당금 변동표 — 표 이름 또는 변동 행으로 판정, 연령분석(연체상태·신용손상 축) 표는 제외."""
    if any(AGING_AX.search(ax) for ax in info["axes"]):
        return False
    return "LossAllowance" in t or any(LA_MOVE.search(_local(q)) for q in info["items"])


def g5c_010(r, std):
    # 규칙 대상은 손실충당금 변동표(p179). 연령분석 표의 값은 제외
    out = []
    for f, rc in _facts(r):
        d = _dims(r, f)
        if f["q"] != IF + "FinancialAssets" or not _ctx(r, f).get("instant") or \
                d.get(CARRYING_AXIS) != IF + "AccumulatedImpairmentMember" or (_num(f["value"]) or 0) <= 0:
            continue
        if any(AGING_AX.search(ax) for ax in d):
            continue
        tabs = [(t, info) for u, rc2, t, info in _tables(r) if rc2 == rc and f["q"] in info["items"] and set(d) <= set(info["axes"])]
        if not any(_loss_allowance_table(t, info) for t, info in tabs):
            continue
        out.append(_f(r, "G5C-010", rc, f["q"], f"금융자산 × 손상차손누계액 멤버 값 {f['value']} — 음수로"))
    return out[:5]


def g5c_012(r, std):
    out, seen = [], set()
    for f, rc in _facts(r, lambda f, rc: rc[:6] == "D82242"):
        a = _attr(r, f["q"])
        v = _num(f["value"])
        d = _dims(r, f)
        if a["balance"] == "debit" and a["period_type"] == "instant" and v is not None and v < 0 and \
                set(d.values()) <= {IF + "CarryingAmountMember", REPORTED_MEMBER, IF + "GrossCarryingAmountMember"} and \
                (rc, f["q"]) not in seen:   # 공시금액·총장부금액 열도 포함(p169 표상자 추가 표)
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5C-012", rc, f["q"], f"차변 채권 '{_lab(r, f['q'])}'에 음수 {f['value']} — 양수+반대부호 라벨"))
    return out


def g5c_013(r, std):
    _, stax = _std_extra(std)
    uses = defaultdict(set)
    out = []
    for u, rc, t, info in _tables(r):
        uses[t].add(rc[:6])
    for u, rc, t, info in _tables(r):
        if not std.is_std(t) or t == "dart:ConsolidatedAndSeparateFinancialStatementsTable" or rc in std.elem_roles.get(t, ()):
            continue
        ax = set(info["axes"]) - {CONSEP_AXIS}
        sax = stax.get(t, set()) - {CONSEP_AXIS}
        if sax and ax != sax:
            out.append(_f(r, "G5C-013", rc, t, f"다른 목차의 표준 표 이름을 축 구성이 다르게 사용(표준 {len(sax)}축/보고서 {len(ax)}축)",
                          basis="약함", table=t))
        elif len(uses[t]) >= 3:
            out.append(_f(r, "G5C-013", rc, t, f"같은 표준 표 이름을 {len(uses[t])}개 목차에서 사용", table=t))
    return out


PPE_ROLES = ("D82210",)
IP_ROLES = ("D82510",)


def _ppe_tables(r, roles=PPE_ROLES):
    return [(u, rc, t, info) for u, rc, t, info in _tables(r) if rc[:6] in roles]


def g5c_018(r, std):
    # 대상: 변동표(기초·기말 행이 있는 표)의 한 방향 변동 행(감가상각비·상각비·손상차손 등). 범위:
    # 차감 멤버(정부보조금·누계액) 열은 음수가 정답(p361-362), '이익(손실)'·'증가(감소)' 같은 양방향 행은 의미대로 부호(p224)
    # (연결/별도 공통 표는 여러 블록의 행이 섞여 있어 변동표 판정에서 뺀다)
    chg = {(rc, q) for u, rc, t, info in _tables(r) if _is_change_table(info) and t != DART + "ConsolidatedAndSeparateFinancialStatementsTable"
           for q in info["items"]}
    out, seen = [], set()
    for f, rc in _facts(r, lambda f, rc: rc[:6] in PPE_ROLES + IP_ROLES + ("D82318",)):
        a = _attr(r, f["q"])
        v = _num(f["value"])
        d = _dims(r, f)
        if d.get(CARRYING_AXIS) in DEDUCT_MEMBERS or any(re.search(r"누계|정부보조금", _lab(r, m)) for m in d.values()):
            continue
        if (rc, f["q"]) not in chg or re.search(r"이익\(손실\)|증가\(감소\)|손익|\(환입\)", _lab(r, f["q"])) or \
                re.search(r"GainLoss|GainsLosses|IncreaseDecrease", _local(f["q"])):
            continue
        if a["balance"] is None and a["period_type"] == "duration" and "monetary" in a["type"].lower() and \
                v is not None and v < 0 and (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5C-018", rc, f["q"], f"차변/대변 속성 없는 변동 행에 음수 {f['value']} — 양수+Negated 표시"))
    return out


def g5c_019(r, std):
    """변동표 롤포워드: 기초 + Σ변동(속성 부호) = 기말이 안 맞을 때, 변동 행 하나의 부호만 뒤집으면
    정확히 맞는 경우만 그 행을 지목한다(소계 행·표 구조 차이로 생기는 불일치는 판정하지 않음)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if not _is_change_table(info):
            continue
        base = next((q for q, pl in zip(info["items"], info["pref_labels"])
                     if pl and re.search(r"period(Start|End)", pl)), None)
        if not base:
            continue
        bb = _attr(r, base)["balance"]
        moves = {q for q in info["items"] if _attr(r, q)["period_type"] == "duration" and q != base
                 and not _local(q).startswith("IncreaseDecreaseIn")}   # 순증감 합계 행 제외
        if not moves:
            continue
        inst, chg = {}, defaultdict(list)
        for f in r.facts:
            v = _num(f["value"])
            if v is None:
                continue
            c = _ctx(r, f)
            d = tuple(sorted((c.get("dims") or {}).items()))
            if f["q"] == base and c.get("instant"):
                inst[(d, c["instant"])] = v
            elif f["q"] in moves and c.get("start"):
                b = _attr(r, f["q"])["balance"]
                s = -1 if b is None else (1 if b == bb or not bb else -1)   # 속성 없는 감가상각비 등은 감소
                chg[(d, c["start"], c["end"])].append((f["q"], s * v))
        for (d, s, e), lst in chg.items():
            st = _ymd(s)
            o, cl = inst.get((d, str(st - timedelta(days=1)) if st else "")), inst.get((d, e))
            if o is None or cl is None:
                continue
            diff = o + sum(v for _, v in lst) - cl
            tol = max(1000, abs(cl) * 1e-6)
            if abs(diff) <= tol:
                continue
            for q, v in lst:
                # 규칙 대상은 '증가(감소)' 금액 행 — 전입·취득처럼 이름에 방향이 있는 행은 속성만으로 부호를 정할 수 없음
                if "IncreaseDecrease" not in q or "monetary" not in _attr(r, q)["type"].lower():
                    continue
                if v and abs(diff - 2 * v) <= tol and (rc, q) not in seen:
                    seen.add((rc, q))
                    out.append(_f(r, "G5C-019", rc, q, f"{s}~{e} 이 행 부호가 반대 — 뒤집으면 기초 {o:,.0f} + 변동 = 기말 {cl:,.0f}",
                                  table=t))
    return out


def g5c_020(r, std):
    # 대상은 '변동을 나타내는 행'(p203 자·p224 바): 금액·기간형 확장 행 중 유형자산·투자부동산 기초·기말 행과
    # 같은 부모 아래에 있거나, 표·부모 개요 이름이 증감·대체·조정·변동인 것만. 공통 연결/별도 표는 표 전체를
    # 변동표로 보지 않고 이 행 조건으로만 판정(설명 문장·보험·담보 행 제외)
    MOVE_NAME = r"IncreaseDecrease|Transfers|Reconciliation|ChangesIn"
    out, seen = [], set()
    for u, rc, t, info in _ppe_tables(r, PPE_ROLES + IP_ROLES):
        common = t == DART + "ConsolidatedAndSeparateFinancialStatementsTable"
        if not common and not _is_change_table(info) and not re.search(MOVE_NAME, _local(t)):   # 대체 세부 표 등은 이름으로
            continue
        want = "InvestmentProperty" if rc[:6] in IP_ROLES else "PropertyPlantAndEquipment"
        nodes = [(n, p, pl) for n, p, tb, tt, pl in r.role_nodes(u) if tt == t]
        base_par = {p for n, p, pl in nodes if pl and re.search(r"period(Start|End)", pl) and
                    _local(n).endswith(("PropertyPlantAndEquipment", "PropertyPlantAndEquipmentIncludingRightofuseAssets",
                                        "InvestmentProperty"))}
        move_tab = not common and bool(re.search(MOVE_NAME, _local(t)))
        for n, p, pl in nodes:
            a = _attr(r, n)
            if not _is_ext(r, n) or r.kind(n) != "item" or a["period_type"] != "duration" or \
                    "monetary" not in a["type"].lower() or (rc, n) in seen:
                continue
            if not (p in base_par or move_tab or re.search(MOVE_NAME, _local(p or ""))):
                continue
            head = re.split(r"Of(?:Disclosure|Reconciliation|Details|Detailed|Changes)", _local(n))[0]
            if not head.endswith((want, want + "IncludingRightofuseAssets")):
                seen.add((rc, n))
                out.append(_f(r, "G5C-020", rc, n, f"확장 변동 행 이름 '{head}'가 {want}로 끝나지 않음", table=t))
    return out


ROU_AXIS = IF + "PropertyPlantAndEquipmentIncludingRightofuseAssetsAxis"


def g5c_021(r, std):
    out = []
    for u, rc, t, info in _tables(r):
        ms = {m for ms in info["axes"].values() for m in ms}
        if ROU_AXIS not in info["axes"] and IF + "RightofuseAssetsMember" not in ms:
            continue
        for q in info["items"]:
            n = _local(q)
            if n.endswith("PropertyPlantAndEquipment") and std.is_std(q) and _attr(r, q)["period_type"] == "duration":
                out.append(_f(r, "G5C-021", rc, q, "사용권자산 포함 변동표에 일반 유형자산 변동 행 — '…(사용권자산 포함)' 행 사용", table=t))
    return out


def g5c_022(r, std):
    out = []
    for u, rc, t, info in _tables(r):
        if t != IF + "DisclosureOfPropertyPlantAndEquipmentTable":   # 가이드 p201은 변동표(세부 정보 공시 [표]) 규정
            continue
        if ROU_AXIS in info["axes"]:
            out.append(_f(r, "G5C-022", rc, t, "사용권자산 포함 축을 일반 유형자산 표에 사용 — "
                                             "'유형자산(사용권자산 포함)의 변동 내용에 대한 공시 [표]' 사용", table=t, pair=[ROU_AXIS]))
            continue
        for ax, ms in info["axes"].items():
            if "ClassesOfPropertyPlantAndEquipment" in ax:
                rou = [m for m in ms if "Rightofuse" in m or "사용권" in _lab(r, m)]
                if rou:
                    out.append(_f(r, "G5C-022", rc, t, f"유형자산 표·분류 축에 사용권자산 멤버 '{_lab(r, rou[0])}' — 사용권자산 포함 전용 표·축 사용", table=t))
    return out


def _items_in_roles(r, roles, names):
    return [(rc, q) for rc, ns in _index(r)["nodes"].items() if rc[:6] in roles for q in ns if q in names]


def g5c_024(r, std):
    return [_f(r, "G5C-024", rc, q, "유형자산 변동표에 전체 손상차손 요소 — 'ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment' 사용")
            for rc, q in _items_in_roles(r, PPE_ROLES, {IF + "ImpairmentLossRecognisedInProfitOrLoss"})]


PPE_IMP_ADJ = {IF + "AdjustmentsForImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",
               DART + "AdjustmentsForImpairmentLossesOfPropertyPlantAndEquipment"}   # 현금흐름 조정 전용
IMP_REV_RE = re.compile(r"손상차손\s*\(\s*환입\s*\)|손상.*환입|환입.*손상")


def g5c_025(r, std):
    out = [_f(r, "G5C-025", rc, q, "유형자산 변동표에 현금흐름 조정용 손상차손(환입) 요소 — 손상차손·환입 각각의 행 사용")
           for rc, q in _items_in_roles(r, PPE_ROLES, PPE_IMP_ADJ)]
    # 회사가 만든 '손상차손(환입)' 결합 확장 행(처분·폐기와 손상을 합친 행은 근거가 직접적이지 않아 제외)
    seen = set()
    for u, rc, t, info in _ppe_tables(r):
        if not _is_change_table(info):
            continue
        for q in info["items"]:
            if _is_ext(r, q) and IMP_REV_RE.search(_lab(r, q)) and (rc, q) not in seen:
                seen.add((rc, q))
                out.append(_f(r, "G5C-025", rc, q, f"손상차손과 환입을 한 확장 행 '{_lab(r, q)}'로 — 각각의 표준 행 사용",
                              basis="약함", table=t))
    return out


def g5c_026_027(r, std, rid):
    out = []
    pair = {IF + "DisclosureOfIntangibleAssetsTable": "IntangibleAssetsOtherThanGoodwill",
            IF + "DisclosureOfReconciliationOfChangesInIntangibleAssetsAndGoodwillTable": "IntangibleAssetsAndGoodwill"}
    for u, rc, t, info in _tables(r):
        if not _is_change_table(info) or "Intangible" not in t:
            continue
        base = next((q for q, pl in zip(info["items"], info["pref_labels"]) if pl and re.search(r"period(Start|End)", pl)), "")
        kind = "IntangibleAssetsAndGoodwill" if base.endswith("IntangibleAssetsAndGoodwill") else \
            "IntangibleAssetsOtherThanGoodwill" if base.endswith("IntangibleAssetsOtherThanGoodwill") else ""
        if rid == "G5C-027":
            if t in pair and kind and pair[t] != kind:
                out.append(_f(r, rid, rc, t, f"표와 기초·기말 행 불일치({_local(base)}) — 영업권 포함 여부에 맞는 표준 표", table=t,
                              pair=[base]))
            continue
        other = "IntangibleAssetsOtherThanGoodwill" if kind == "IntangibleAssetsAndGoodwill" else "IntangibleAssetsAndGoodwill"
        se = std.role_elems.get(rc, set())
        for q in info["items"]:
            if q in se and q != IF + "AmortisationExpense":   # 표준 목차에 정의된 행(영업권 포함 표의 상각비 등)은 정상
                continue
            if kind and _local(q).endswith(other):
                out.append(_f(r, rid, rc, q, f"기초·기말이 {kind}인데 {other} 계열 변동 행 혼용", table=t))
            if q == IF + "AmortisationExpense":
                out.append(_f(r, rid, rc, q, "무형자산 변동표에 성격별 '무형자산상각비' — '무형자산상각비, 영업권 이외의 무형자산' 사용", table=t))
    return out


def g5c_026(r, std):
    return g5c_026_027(r, std, "G5C-026")


def g5c_027(r, std):
    return g5c_026_027(r, std, "G5C-027")


def g5c_028(r, std):
    out = [_f(r, "G5C-028", rc, q, f"'{_lab(r, q)}' 확장 — 내부창출 여부 축(MethodsOfGenerationAxis)으로 구분")
           for q, rc in list(_ext_items_in(r).items()) + list(_ext_members(r).items())
           if re.search(r"내부창출|개별취득|내부개발|외부취득", _lab(r, q))]
    # '반드시 구분 입력'은 사업결합 외 증가 행에만(p212 다). 사업결합 취득 행은 축 없이 장부금액 열에 입력(p212),
    # 기초·기말·상각·처분 행은 대상 아님(p210 모범사례)
    add = IF + "AdditionsOtherThanThroughBusinessCombinationsIntangibleAssetsOtherThanGoodwill"
    ms, bc = defaultdict(set), set()
    for f, rc in _facts(r, struct=True):
        m = _dims(r, f).get(IF + "MethodsOfGenerationAxis")
        if not m:
            continue
        if f["q"] == add:
            ms[(rc, f["q"])].add(m)
        elif _local(f["q"]).startswith("AcquisitionsThroughBusinessCombinations") and not m.endswith("Domain"):
            bc.add((rc, f["q"]))
    out += [_f(r, "G5C-028", rc, q, "내부창출 여부 축을 쓰면서 증가 행에 한쪽 멤버만 입력", basis="약함")
            for (rc, q), s in ms.items() if len(s) == 1]
    out += [_f(r, "G5C-028", rc, q, "사업결합 취득은 내부창출 여부 축 없이 장부금액(도메인 합계) 열에 입력", basis="약함")
            for rc, q in sorted(bc)]
    return out


def _ext_axis_on(r, rid, pred, why):
    out, seen = [], set()
    for f, rc in _facts(r):
        if pred(f, rc):
            for ax in _dims(r, f):
                if _is_ext(r, ax) and (rc, ax) not in seen:
                    seen.add((rc, ax))
                    out.append(_f(r, rid, rc, ax, f"{why} — 확장 축 '{_lab(r, ax)}'"))
    return out


GW_ROLES = ("D82318", "D83241")   # 현금창출단위 축이 정의된 무형자산·자산손상 목차


def g5c_029(r, std):
    # 무형자산·자산손상 목차의 영업권만 명확, 다른 목차(사업결합 등)는 확인 권장. 영업권 확장 금액 행 배분도 확인 권장
    out = _ext_axis_on(r, "G5C-029", lambda f, rc: f["q"] == IF + "Goodwill", "영업권 배분에 현금창출단위 축 대신 확장 축")
    for x in out:
        if x.role_code[:6] not in GW_ROLES:
            x.extra["basis"] = "약함"
    for q, rc in _ext_items_in(r, lambda rc: rc[:6] in GW_ROLES).items():
        a = _attr(r, q)
        if "영업권" in _lab(r, q) and a["period_type"] == "instant" and "monetary" in a["type"].lower() and "상각" not in _lab(r, q):
            out.append(_f(r, "G5C-029", rc, q, f"영업권 배분을 확장 금액 행 '{_lab(r, q)}'로 — 현금창출단위 축 사용", basis="약함"))
    return out


IP_TYPE_OK = {IF + "InvestmentPropertyCompletedMember", IF + "InvestmentPropertyUnderConstructionOrDevelopmentMember",
              IF + "TypesOfInvestmentPropertyDomain"}


def g5c_030(r, std):
    out = _ext_axis_on(r, "G5C-030", lambda f, rc: "InvestmentProperty" in f["q"], "투자부동산 분류에 자산의 분류 축 대신 확장 축")
    _, under, _ = _def_axis_members(r)
    for m in under.get(IF + "TypesOfInvestmentPropertyAxis", ()):
        # 표준 멤버와 정확히 같을 때만 허용(확장 멤버 이름에 Completed가 들어가도 걸러냄)
        if r.kind(m) == "member" and m not in IP_TYPE_OK:
            out.append(_f(r, "G5C-030", "", m, f"투자부동산의 유형 축에 '{_lab(r, m)}' — 토지·건물 등은 자산의 분류 축"))
    return out


def g5c_036(r, std):
    # 값(fact)과 함께 표 구성(만기 축이 있는 표의 행)도 본다 → 숫자가 비어 있는 틀에서도 판정
    # 실제 공시 파일은 만기 축이 붙은 fact가 있는 행만, 표 구성 후보(만기 축 표의 행 전체)는 틀 검사에서만
    UND = r"Undiscounted|GrossLeaseLiabilities"
    cands = [(f["q"], rc) for f, rc in _facts(r) if IF + "MaturityAxis" in _dims(r, f)]
    under_und = set()   # 미할인 행의 하위 행(통화선도 유입·유출 등)은 미할인 현금흐름
    for u, rc, t, info in _tables(r):
        if IF + "MaturityAxis" not in info["axes"]:
            continue
        nodes = [(n, p) for n, p, tb, tt, pl in r.role_nodes(u) if tt == t]
        par = dict(nodes)
        for n, p in nodes:
            x = p
            while x:
                if re.search(UND, _local(x)):
                    under_und.add((rc, n))
                    break
                x = par.get(x)
        if getattr(r, "frame", False):
            cands += [(n, rc) for n, p in nodes if r.kind(n) == "item"]
    # 만기별 금액 합계 vs 같은 요소의 차원 없는 금액(장부금액)
    buckets, totals = defaultdict(float), {}
    for f, rc in _facts(r):
        v, d = _num(f["value"]), _dims(r, f)
        if v is None:
            continue
        key = (rc, f["q"], _sep(r, f), _ctx(r, f).get("instant"))
        if set(d) == {IF + "MaturityAxis"}:
            buckets[key] += v
        elif not d:
            totals[key] = v
    # 만기 축 표에 총 리스부채·미할인 행이 함께 있으면 그 표의 나머지 장부금액 행은 대사(장부금액) 열 — 표 구성으로 판정(틀에서도)
    recon = {(rc, q) for u, rc, t, info in _tables(r) if IF + "MaturityAxis" in info["axes"]
             and any(re.search(UND, _local(x)) for x in info["items"]) for q in info["items"]}
    out, seen = [], set()
    for q, rc in cands:
        # 우발부채·약정의 보증 만기(D8275)와 수익의 수행의무·계약부채 만기(D8311)는 대상 아님
        if "monetary" not in _attr(r, q)["type"].lower() or rc[:5] == "D8275" or rc[:5] == "D8311":
            continue
        n = _local(q)
        if re.search(r"Undiscounted|GrossLeaseLiabilities|EstimateOfBenefitPayments|LeasePaymentsReceivable|LeasePayments", n):
            continue
        # 대사 행 예외: 리스부채는 항상, 그 밖의 행은 틀 검사에서만(실제 파일은 만기 축 값이 있는 행만 후보라 불필요)
        if (rc, q) in recon and ("LeaseLiabilities" in n or getattr(r, "frame", False)) or (rc, q) in under_und:
            continue
        keys = [k for k in buckets if k[:2] == (rc, q)]
        same = [k for k in keys if k in totals and abs(buckets[k] - totals[k]) <= max(3000, abs(totals[k]) * 1e-6)]
        if same:
            continue   # 만기 구간 합계 = 장부금액 → 현금흐름이 아니라 장부금액을 만기별로 나눈 공시
        # 리스부채 만기분석 표준 표에 장부금액 '리스부채' 행 = 명확(가이드 p239 모범사례는 총 리스부채). 유동/비유동 리스부채의 만기 구분은 장부금액 공시일 수 있음
        lease = q == IF + "LeaseLiabilities" and any("MaturityAnalysisOfLeaseLiabilities" in t for _, rc2, t, _ in _tables(r) if rc2 == rc)
        diff = any(k in totals for k in keys)   # 합계 ≠ 장부금액 → 미할인 금액을 장부금액 행에 넣은 것
        if (rc, q) not in seen:
            seen.add((rc, q))
            # 리스부채의 미할인현금흐름은 '총 리스부채' 행(가이드 p302, p239 모범사례)
            want = "'총 리스부채'(ifrs-full:GrossLeaseLiabilities)" if "LeaseLiabilities" in n else "'…, 미할인현금흐름'"
            out.append(_f(r, "G5C-036", rc, q, f"만기분석에 장부금액 행 '{_lab(r, q)}' — {want} 행 사용",
                          basis="명확" if (lease or diff) and not _is_ext(r, q) else "약함", pair=[IF + "MaturityAxis"]))
    return out


def g5c_037(r, std):
    # 같은 이름 멤버는 정의 role의 '같은 표' 안에서만 센다(연결·별도 주석이나 다른 표에 하나씩 있는 것은 정상).
    # 가이드 문구가 '번호 붙여 구분할 수 있음'(허용)이라 확인 권장
    out, seen = [], set()
    for u in r.defs:
        rc = re.sub(r"[a-z]+$", "", r.role_code(u) or "")
        for (t, ax), ms in r.def_table_members(rc).items():
            if ax != IF + "BorrowingsByNameAxis":
                continue
            for l, k in Counter(_lab(r, m) for m in ms if _is_ext(r, m)).items():
                if k > 1 and l and (rc, t, l) not in seen:
                    seen.add((rc, t, l))
                    out.append(_f(r, "G5C-037", rc, ax, f"차입금 명칭 축에 같은 이름 멤버 '{l}' {k}개 — 번호 붙여 구분",
                                  basis="약함", table=t))
    # '차입금 명칭 축 대신 확장 축' 검사는 가이드 근거가 없어 삭제(p228 모범사례는 거래상대방 축)
    return out


NUMBOX = (IF + "InterestExpenseOnLeaseLiabilities", IF + "ExpenseRelatingToShorttermLeases",
          IF + "ExpenseRelatingToLeasesOfLowvalueAssets", IF + "ExpenseRelatingToVariableLeasePayments",
          IF + "KeyManagementPersonnelCompensation")


def g5c_040(r, std):
    # 숫자 상자 행이 연결/별도 외 축이 있는 표 안에 있으면 숫자 상자가 아님 — 차원 없는 fact는 그 표의 도메인 합계열 값
    # (한 role의 pre는 표끼리 행 노드를 공유하므로 행 단위로 모아서 본다. 값 판정·틀 검사 공통)
    ok, rows = set(), set()
    for u, rc, t, info in _tables(r):
        has = any(ax != CONSEP_AXIS for ax in info["axes"])
        for q in info["items"]:
            if q.startswith(NUMBOX):
                rows.add((rc, q))
                if has:
                    ok.add((rc, q))
    out, seen = [], set()
    for f, rc in _facts(r):
        if f["q"].startswith(NUMBOX) and not _dims(r, f) and (rc, f["q"]) not in seen and (rc, f["q"]) not in ok:   # 차원이 하나도 없는 숫자 상자만
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5C-040", rc, f["q"], "숫자 상자 행을 차원 없이 공시 — 장부금액 축 + 공시금액 구성요소 표로"))
    if getattr(r, "frame", False):   # 틀 검사: 숫자 상자 행이 공시금액 멤버가 있는 표에 하나도 없으면
        for rc, q in sorted(rows - ok - seen):
            out.append(_f(r, "G5C-040", rc, q, "숫자 상자 행을 차원 없이 공시 — 장부금액 축 + 공시금액 구성요소 표로"))
    return out


def g5c_043(r, std):
    out = []
    for f, rc in _facts(r):
        if _dims(r, f).get(IF + "NetDefinedBenefitLiabilityAssetAxis") != IF + "PlanAssetsMember":
            continue
        v = _num(f["value"]) or 0
        n = _local(f["q"])
        if n == "LiabilityAssetOfDefinedBenefitPlans" and v > 0:
            out.append(_f(r, "G5C-043", rc, f["q"], f"순확정급여부채 × 사외적립자산 값 {f['value']} — 음수로"))
        elif n.startswith("ContributionsToPlanByEmployer") and v < 0:
            out.append(_f(r, "G5C-043", rc, f["q"], f"기여금 × 사외적립자산 값 {f['value']} — 양수로"))
    return out[:5]


def g5c_044(r, std):
    out = [_f(r, "G5C-044", rc, f["q"], f"가중평균만기 값 '{f['value']}' — P8Y6M 형식")
           for f, rc in _facts(r) if _local(f["q"]).startswith("WeightedAverageDurationOfDefinedBenefitObligation")
           and not ISO_DUR.match((f["value"] or "").strip())]
    out += [_f(r, "G5C-044", rc, q, f"가중평균만기를 확장요소 '{_lab(r, q)}'로 입력 — 표준 기간 타입 요소 사용")
            for q, rc in _ext_items_in(r).items() if "가중평균만기" in _lab(r, q)
            and (rc[:6] == "D83448" or "확정급여" in _lab(r, q))]   # 차입금·리스의 가중평균만기는 대상 아님
    return out


def _share_groups(r):
    g = defaultdict(dict)
    for f, rc in _facts(r):
        d = _dims(r, f)
        m = d.pop(SHARE_AXIS, None)
        c = _ctx(r, f)
        k = (f["q"], c.get("instant") or (c.get("start"), c.get("end")), tuple(sorted(d.items())), _sep(r, f), rc)
        g[k][m] = _num(f["value"])
    return g


ADDABLE_TYPE = re.compile(r"(^|:)(shares|monetary)ItemType$")


def g5c_047(r, std):
    # 더할 수 있는 주식수·금액 행만(주당금액·비율·문자 제외), 숫자 비교로 입력 위치를 추정하므로 확인 권장
    out, seen = [], set()
    for k, ms in _share_groups(r).items():
        if not ADDABLE_TYPE.search(_attr(r, k[0])["type"]):
            continue
        dom, o, p = ms.get(None), ms.get(ORD_M), ms.get(PREF_M)
        if dom is not None and o is not None and p is not None and abs(dom - o - p) > 1 and (k[4], k[0]) not in seen:
            seen.add((k[4], k[0]))
            out.append(_f(r, "G5C-047", k[4], k[0], f"합계 {dom:,.0f} ≠ 보통주 {o:,.0f} + 우선주 {p:,.0f}", basis="약함"))
    return out


def g5c_049(r, std):
    # 주식의 종류 축과 함께 쓴 세부 확장 축(1우선주·2우선주 등)은 제외
    out = _ext_axis_on(r, "G5C-049", lambda f, rc: f["q"] == IF + "IssuedCapital" and SHARE_AXIS not in _dims(r, f),
                       "자본금을 주식의 종류 축 대신 확장 축으로 구분")
    out += [_f(r, "G5C-049", rc, q, f"'{_lab(r, q)}' 확장 — dart:IssuedCapitalOfCommonStock/PreferredStock 또는 주식의 종류 축")
            for q, rc in _ext_items_in(r).items() if re.search(r"(보통주|우선주)\s?자본금", _lab(r, q))
            # 금액 요소만 — 자본금과 액면총액 차이 사유 같은 서술 요소는 대상 아님
            and "monetary" in _attr(r, q)["type"].lower()
            and not re.search(r"기술|설명|사유", _lab(r, q)) and not re.search(r"Description|Explanation", _local(q))]
    return out


ORD_ONLY = re.compile(r"^(Basic|Diluted)EarningsLossPerShare|^(Adjusted)?WeightedAverageShares|AttributableToOrdinaryEquityHolders")


def g5c_051(r, std):
    out, seen = [], set()
    for f, rc in _facts(r, struct=True):
        m = _dims(r, f).get(SHARE_AXIS)
        n = _local(f["q"])
        bad = (m == PREF_M and ORD_ONLY.search(n)) or (m == ORD_M and "ParticipatingEquityInstrumentsOtherThanOrdinaryShares" in n)
        if bad and (rc, f["q"], m) not in seen:
            seen.add((rc, f["q"], m))
            out.append(_f(r, "G5C-051", rc, f["q"], f"{'보통주 전용' if m == PREF_M else '참가적 지분상품 전용'} 행을 '{_lab(r, m)}' 멤버와 사용"))
    return out


def g5c_052(r, std):
    out = [_f(r, "G5C-052", rc, q, f"'{_lab(r, q)}' 확장 — 주식수 행 + 주식의 종류 멤버로 구분")
           for q, rc in _ext_items_in(r).items() if re.search(r"(보통주|우선주)\s?(식|주식)\s?수", _lab(r, q))
           and not re.search(r"기술|설명|근거", _lab(r, q)) and "string" not in _attr(r, q)["type"].lower()
           # 주당이익 산정 행(가중평균·희석·잠재적)은 원래 보통주 전용(p285) — 구분 없는 주식수 개념의 확장만 대상
           and not re.search(r"가중평균|희석|잠재적|주당", _lab(r, q))]
    has_pref = any(_dims(r, f).get(SHARE_AXIS) == PREF_M for f, rc in _facts(r, struct=True))
    if has_pref:
        # 수권주식수는 도메인 합계에만 입력(p255 모범사례) → 제외. 멤버 구분 값이 다른 목차(주당이익 등)에 배정될 수 있어
        # 목차가 아니라 연결/별도 단위로 판정
        names = {IF + "NumberOfSharesIssued", IF + "NumberOfSharesOutstanding"}
        g, where = defaultdict(set), {}
        for f, rc in _facts(r, struct=True):
            if f["q"] in names:
                k = (_sep(r, f) if f.get("ctx") else rc.endswith("5"), f["q"])
                g[k].add(SHARE_AXIS in _dims(r, f))
                where.setdefault(k, rc)
        out += [_f(r, "G5C-052", where[k], k[1], "우선주가 있는데 주식수를 주식의 종류 멤버 없이만 입력", basis="약함")
                for k, s in g.items() if s == {False}]
    return out


def g5c_054(r, std):
    # D834320의 실제 행은 dart:OtherGains·OtherLosses(— ifrs-full에는 이 이름 없음)
    pairs = [((IF + "OtherIncome", DART + "OtherGains"), (IF + "OtherExpenseByFunction", DART + "OtherLosses"), "기타수익/기타비용"),
             ((IF + "FinanceIncome",), (IF + "FinanceCosts",), "금융수익/금융원가")]
    out = []
    for inc, exp, nm in pairs:
        for sep in ("0", "5"):
            ri = {rc for rc, ns in _index(r)["nodes"].items() if not _stmt(rc) and rc.endswith(sep) and ns & set(inc)}
            re_ = {rc for rc, ns in _index(r)["nodes"].items() if not _stmt(rc) and rc.endswith(sep) and ns & set(exp)}
            if ri and re_ and not ri & re_:
                out.append(_f(r, "G5C-054", sorted(ri)[0], inc[0], f"{nm}를 서로 다른 목차({sorted(ri)[0]}, {sorted(re_)[0]})로 공시 — 한 표 구성 권고",
                              level=REV))
    return out


def g5c_059(r, std):
    out = []
    bs = defaultdict(dict)
    for f, rc in _facts(r):
        if f["q"] in (IF + "DeferredTaxAssets", IF + "DeferredTaxLiabilities") and _stmt(rc) == "BS":
            bs[f["ctx"]][f["q"]] = _num(f["value"]) or 0
    for f, rc in _facts(r):
        # 차원 없음 또는 장부금액·공시금액 열(표상자 추가 표)까지 대상
        d = _dims(r, f)
        if f["q"] != IF + "DeferredTaxLiabilityAsset" or set(d) - {CARRYING_AXIS} or \
                set(d.values()) - {IF + "CarryingAmountMember", REPORTED_MEMBER}:
            continue
        b = bs.get(f["ctx"])
        v = _num(f["value"])
        if not b or v is None:
            continue
        net_liab = b.get(IF + "DeferredTaxLiabilities", 0) - b.get(IF + "DeferredTaxAssets", 0)
        if net_liab and (net_liab > 0) != (v > 0):
            out.append(_f(r, "G5C-059", rc, f["q"], f"이연법인세부채(자산) {v:,.0f}인데 재무상태표 순액 {net_liab:,.0f} — 순자산이면 음수"))
            break
    return out


def g5c_060(r, std):
    out = [_f(r, "G5C-060", rc, q, f"민감도 변동률을 확장 '{_lab(r, q)}'로 — '시장위험의 각 유형별 민감도분석 기술' 행 사용")
           for q, rc in list(_ext_members(r).items()) + list(_ext_items_in(r).items())
           if rc[:6] == "D82238" and re.search(r"%|상승|하락|증가시|감소시", _lab(r, q))
           and "monetary" not in _attr(r, q)["type"].lower()]
    # 표준 기술 행 대신 만든 확장 문자 행
    out += [_f(r, "G5C-060", rc, q, f"확장 '{_lab(r, q)}' — '시장위험의 각 유형별 민감도분석 기술'"
                                    "(dart:DescriptionOfSensitivityAnalysisForEachTypeOfMarketRisk) 행 사용", basis="약함")
            for q, rc in _ext_items_in(r, lambda rc: rc[:6] == "D82238").items()
            if "string" in _attr(r, q)["type"].lower() and "민감도" in _lab(r, q) and re.search(r"기술|설명", _lab(r, q))
            and (q, rc) not in {(x.element, x.role_code) for x in out}]
    return out


def g5c_061(r, std):
    return [_f(r, "G5C-061", rc, q, f"민감도 행을 세전/세후로 확장 '{_lab(r, q)}' — 표준 당기손익 영향 행 공통 사용")
            for q, rc in _ext_items_in(r, lambda rc: rc[:6] == "D82238").items()
            if re.search(r"세전|세후|법인세", _lab(r, q)) and re.search(r"손익|이익|자본", _lab(r, q))]


def g5c_064(r, std):
    return [_f(r, "G5C-064", rc, f["q"], f"투입변수 감소율 {f['value']} — 감소 개념 행이므로 양수")
            for f, rc in _facts(r) if _local(f["q"]).startswith("PercentageOfReasonablyPossibleDecreaseInUnobservableInput")
            and (_num(f["value"]) or 0) < 0]


def g5c_066(r, std):
    # 금융상품 주석(D822·D823)의 공정가치 행이 함께 있는 표만, 측정 축을 이미 쓴 표는 제외 → 수정 필요(p313 마)
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if rc[:4] not in ("D822", "D823") or IF + "MeasurementAxis" in info["axes"]:
            continue
        if not any("FairValue" in _local(x) or "공정가치" in _lab(r, x) for x in info["items"]):
            continue
        for q in info["items"]:
            if _is_ext(r, q) and "취득원가" in _lab(r, q) and (rc, q) not in seen:
                seen.add((rc, q))
                out.append(_f(r, "G5C-066", rc, q, f"'{_lab(r, q)}' 확장 행 — 측정 축의 취득원가/공정가치 구성요소 사용", table=t))
    return out


def g5c_067(r, std):
    out = []
    for u, rc, t, info in _tables(r):
        if rc[:6] != "D82300" or t == DART + "ConsolidatedAndSeparateFinancialStatementsTable" or not _is_change_table(info):
            continue
        base = next((q for q, pl in zip(info["items"], info["pref_labels"]) if pl and re.search(r"period(Start|End)", pl)), "")
        if base == IF + "Assets" and IF + "ClassesOfAssetsAxis" not in info["axes"]:
            out.append(_f(r, "G5C-067", rc, t, "기초·기말이 '자산' 수준인데 자산의 분류 축 미사용", table=t, basis="약함"))
        # G5C-067 ②: 기초·기말 행의 수준(자산/금융자산)과 다른 접미사의 변동 행 혼용(p313~314 모범사례는 기초~기말 모두 자산 기준)
        bases = _period_rows(info)
        bsx = {SUFFIX.search(_local(q)).group(1) for q in bases if SUFFIX.search(_local(q))}
        bad = []
        for q in dict.fromkeys(info["items"]):
            m = SUFFIX.search(_local(q))
            if q in bases or not m:
                continue
            sx = m.group(1)
            if ("Assets" in bsx and sx in ("FinancialAssets", "FinancialLiabilities", "FinancialInstruments")) or \
                    ("Assets" in bsx and sx == "Liabilities" and "Liabilities" not in bsx) or \
                    ("FinancialAssets" in bsx and sx == "Assets"):
                bad.append(q)
        if bad:
            out.append(_f(r, "G5C-067", rc, t, f"기초·기말 행({', '.join(sorted(bsx))} 수준)과 다른 수준의 변동 행 혼용: "
                          + ", ".join(_lab(r, q) for q in bad[:3]) + " — 기초~기말 같은 수준 행", table=t, basis="약함"))
    return out


ACCT_WORD = re.compile(r"매입채무|차입금|사채|매출채권|미수금|미지급금|예금|대여금|보증금|리스부채")


def g5c_068(r, std):
    _, under, _ = _def_axis_members(r)
    classes = set().union(*(std.axis_members.get(IF + a, set()) for a in
                            ("ClassesOfFinancialLiabilitiesAxis", "ClassesOfFinancialAssetsAxis", "ClassesOfFinancialInstrumentsAxis")))
    out = []
    for ax in (IF + "CategoriesOfFinancialLiabilitiesAxis", IF + "CategoriesOfFinancialAssetsAxis"):
        for m in sorted(under.get(ax, ())):
            if m in std.axis_members.get(ax, ()):   # 범주 축 자체의 표준 멤버(상각후원가 측정 등)는 정상
                continue
            # 측정기준·범주 성격 멤버(공정가치·상각후원가 측정, 금융보증계약, IFRS 7 범위 외)는 계정과목 종류가 아님 — 축-멤버 점검(15번) 대상.
            # 범주 명칭으로 시작하는 확장 멤버는 같은 기준의 세분(p322)
            if re.search(r"AtFairValue|AtAmortisedCost|FinancialGuaranteeContracts|OutsideScopeOfIFRS7", _local(m)) or \
                    (_is_ext(r, m) and re.match(r"\s*(당기손익|상각후원가|기타포괄손익)", _lab(r, m))):
                continue
            if m in classes or (_is_ext(r, m) and ACCT_WORD.search(_lab(r, m))):
                out.append(_f(r, "G5C-068", "", m, f"범주 축 아래 종류 멤버 '{_lab(r, m)}' — 범주 축과 종류 축 분리"))
    return out


LOAN_W = re.compile(r"대출|차입|여신|일반자금|당좌|무역금융|시설자금|운영자금|유산스|usance|loan|borrow", re.I)


def g5c_071(r, std):
    # 약정의 유형 멤버가 차입 성격일 때만 대상. 대여약정은 제외,
    # 신용장·지급보증·D/A 등만 있으면 차입금 계상 여부를 알 수 없어 확인 권장 — 단, 같은 금액이 차입금 행에 있으면 위반 유지
    ax = DART + "TypesOfCommitmentsAxis"
    end = str(_end(r))
    cur = [f for f in r.facts if (_ctx(r, f).get("instant") or _ctx(r, f).get("end")) == end]   # 당기말 약정 기준
    mem = defaultdict(set)
    for f in cur:
        m = _dims(r, f).get(ax)
        if m:
            mem[f["q"]].add(m)
    borrow_vals = {_num(f["value"]) for f in cur if _num(f["value"]) and IF + "BorrowingsByNameAxis" in _dims(r, f)}
    out = []
    for u, rc, t, info in _tables(r):
        if ax not in info["axes"]:
            continue
        for q in info["items"]:
            if not (_is_ext(r, q) and re.search(r"실행|사용", _lab(r, q)) and not re.search(r"미사용|한도", _lab(r, q))):
                continue
            ms = mem.get(q) or {m for m in info["axes"][ax] if not m.endswith("Domain")}
            labs = [_lab(r, m) for m in ms]
            if labs and all("대여" in l for l in labs):
                continue
            # 외화 실행액: 차입금 이름+통화 확장(BorrowingsUSD)은 맞는 방식(p136-137) → 제외, 다른 이름이면 안내만 바꿈
            units = {f["unit"] or "" for f in r.facts if f["q"] == q and (f["unit"] or "").startswith("iso4217:")}
            fx = bool(units - {"iso4217:KRW"})
            if fx and _local(q).startswith("Borrowings"):
                continue
            basis = "명확"
            if labs and not any(LOAN_W.search(l) for l in labs):
                vs = {_num(f["value"]) for f in cur if f["q"] == q and _num(f["value"])}
                basis = "명확" if vs & borrow_vals else "약함"
            why = (f"외화 실행액을 '{_lab(r, q)}'로 확장 — 차입금 이름+통화코드 확장(BorrowingsUSD, 작성가이드 p136-137)으로" if fx
                   else f"대출약정 실행액을 확장 행 '{_lab(r, q)}'로 — 차입금 행 사용")
            out.append(_f(r, "G5C-071", rc, q, why, table=t, basis=basis))
    return out


CUR_SUFFIX = re.compile(r"\s*[\(\[]\s*[A-Z]{3}\s*[\)\]]\s*$")


def _currency_variant(r, q, targets):
    """외화 금액을 담으려고 표준 행을 '(USD)'처럼 통화별로 확장한 요소면 True (외화는 통화코드 확장요소로 — G5B-054)."""
    if not _is_ext(r, q):
        return False
    lab = CUR_SUFFIX.sub("", _lab(r, q))
    return lab != _lab(r, q) and any(lab.replace(" ", "") == _lab(r, t).replace(" ", "") for t in targets)


RP_GUAR_HEAD = ("ProvisionOfGuaranteesOrCollateralByEntity", "ProvisionOfGuaranteesOrCollateralToEntity",
                "AmountOfGuaranteesProvided", "AmountOfCollateralProvided")
FX_MARK = re.compile(r",\s*[A-Z]{3}\s*$|\(\s*[A-Z]{3}\s*(,\s*천)?\s*\)|외화로\s*된|,?\s*외화")


def g5c_072(r, std):
    # 이름에 RelatedPart가 있는 표준 요소(ifrs-full·dart 특수관계자 보증 행)는 전용 행으로 통과,
    # 외화 확장(통화 표기·외화 단위)·보증금·부수 정보(기간·담보권자) 제외, 설명 문장 확장은 표준 설명 행이 있어 수정 필요
    ok = {IF + "ProvisionOfGuaranteesOrCollateralByEntityRelatedPartyTransactions",
          IF + "ProvisionOfGuaranteesOrCollateralToEntityRelatedPartyTransactions"}
    out, seen = [], set()
    for f, rc in _facts(r):
        rp = rc[:5] == "D8180" or any("RelatedPart" in ax for ax in _dims(r, f))
        if rp and not _is_ext(r, f["q"]) and f["q"] not in ok and "RelatedPart" not in f["q"] \
                and re.search(r"Guarantee|Collateral", _local(f["q"])) and "Deposit" not in f["q"] \
                and _num(f["value"]) is not None \
                and (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5C-072", rc, f["q"], "특수관계자 보증에 전용 행이 아닌 요소 — '보증이나 담보의 제공, 특수관계자거래' 행",
                          basis="약함" if f["q"].startswith(DART) else "명확"))
    okl = {re.sub(r"\s", "", _lab(r, t)) for t in ok}
    fx_items = {f["q"] for f in r.facts if (f["unit"] or "").startswith("iso4217:") and f["unit"] != "iso4217:KRW"}
    guar_tab = {(rc, q) for u, rc, t, info in _tables(r) if rc[:5] == "D8180" and re.search(r"보증|담보", r.label(t) or "")
                for q in info["items"]}
    for q, rc in _ext_items_in(r, lambda rc: rc[:5] == "D8180").items():
        lab, a = _lab(r, q), _attr(r, q)
        if _local(q).startswith(RP_GUAR_HEAD) or re.sub(r"\s", "", FX_MARK.sub("", lab)) in okl or q in fx_items \
                or _currency_variant(r, q, ok) or "보증금" in lab or "Deposit" in q:
            continue
        money = "monetary" in a["type"].lower()
        if not money:
            if re.search(r"보증|담보", lab) and re.search(r"설명|기술|내역|주기", lab):
                out.append(_f(r, "G5C-072", rc, q, f"특수관계자 보증 설명을 확장 '{lab}'로 — 'ExplanationOfDetailsOfGuaranteesGivenOrReceived…' "
                                                   "또는 '보증에 대한 추가 설명' 행 사용"))
            continue
        if re.search(r"보증|담보", lab) or (rc, q) in guar_tab:
            out.append(_f(r, "G5C-072", rc, q, f"특수관계자 보증을 확장 '{lab}'로", basis="약함"))
    return out


def g5c_073(r, std):
    names = {DART + "ProvisionOfGuaranteesByToEntityOfNonPF", DART + "LimitAmountOfProvisionOfGuaranteesByToEntityOfNonPF"}
    ax = DART + "TypesOfPaymentGuaranteesAxis"
    # 같은 요소·기간·연결/별도의 지급보증의 구분 축 멤버별 값 — 축 없는 값이 같은 표(정의 role) 멤버 값의 합계
    # (또는 단일 멤버 값)이면 그림의 '계' 행. 거래상대방 등 다른 축으로 나뉜 값도 합친다
    grp = defaultdict(list)
    for f, rc in _facts(r):
        d = _dims(r, f)
        if f["q"] in names and ax in d and _num(f["value"]) is not None:
            c = _ctx(r, f)
            grp[(f["q"], c.get("instant") or (c.get("start"), c.get("end")), _sep(r, f))].append((d[ax], _num(f["value"])))

    def _is_total(rc, q, c, sep, v):
        vs = grp.get((q, c.get("instant") or (c.get("start"), c.get("end")), sep)) or []
        if v is None or not vs or v in [x for _, x in vs]:
            return bool(vs) and v is not None
        sets = [ms for (t, a), ms in r.def_table_members(rc).items() if a == ax] or [{m for m, _ in vs}]
        return any(abs(sum(x for m, x in vs if m in ms) - v) <= 1 for ms in sets)
    out, seen = [], set()
    for f, rc in _facts(r):
        if f["q"] not in names:
            continue
        if (_num(f["value"]) or 0) < 0 and (rc, f["q"], 1) not in seen:
            seen.add((rc, f["q"], 1))
            out.append(_f(r, "G5C-073", rc, f["q"], f"비PF 보증 값 {f['value']} — 양수로"))
        if ax not in _dims(r, f) and _is_total(rc, f["q"], _ctx(r, f), _sep(r, f), _num(f["value"])):
            continue
        if ax not in _dims(r, f) and (rc, f["q"], 2) not in seen:
            seen.add((rc, f["q"], 2))
            out.append(_f(r, "G5C-073", rc, f["q"], "비PF 보증에 지급보증의 구분 축 없음 — 제공/수취 구분 필수"))
    return out


def g5c_079(r, std):
    ax = IF + "ContinuingAndDiscontinuedOperationsAxis"
    _, under, _ = _def_axis_members(r)
    out = [_f(r, "G5C-079", "", m, f"계속영업을 확장 멤버 '{_lab(r, m)}'로 — 계속영업은 도메인에 입력")
           for m in under.get(ax, ()) if _is_ext(r, m) and "계속" in _lab(r, m)]
    g = defaultdict(dict)
    for f, rc in _facts(r):
        d = _dims(r, f)
        m = d.pop(ax, None)
        c = _ctx(r, f)
        g[(f["q"], c.get("instant") or (c.get("start"), c.get("end")), tuple(sorted(d.items())), _sep(r, f), rc)][m] = _num(f["value"])
    seen = set()
    for k, ms in g.items():
        dom, agg, dis = ms.get(None), ms.get(IF + "AggregateContinuingAndDiscontinuedOperationsMember"), ms.get(IF + "DiscontinuedOperationsMember")
        if None not in (dom, agg, dis) and abs(dom - agg - dis) > 1:
            out.append(_f(r, "G5C-079", k[4], k[0], f"계속영업 {dom:,.0f} ≠ 합계 {agg:,.0f} + 중단영업 {dis:,.0f} — 중단영업 멤버는 음수"))
        # 합계 멤버 없는 구조(p354 모범사례): 한 방향 행(수익·매출원가·비용)의 중단영업 값이 양수면 위반
        elif agg is None and dis is not None and dis > 0 and ONE_WAY_PL.search(_local(k[0])) \
                and (_stmt(k[4]) == "IS" or "중단영업" in std.role_title.get(k[4], "")) \
                and not TWO_WAY_PL.search(_local(k[0])) and (k[4], k[0]) not in seen:
            seen.add((k[4], k[0]))
            out.append(_f(r, "G5C-079", k[4], k[0], f"중단영업 멤버 값 {dis:,.0f} — 중단영업 열은 음수 입력"))
    return out


RP_TX_TABLES = {IF + "DisclosureOfTransactionsBetweenRelatedPartiesTable",
                DART + "DisclosureOfOutstandingBalancesForRelatedPartyTransactionsTable"}
RP_SIDE_TABLE = re.compile(r"보증|담보|현황|Guarantee|Collateral|DetailsOfRelatedParty")


def g5c_075(r, std):
    """특수관계자 거래·채권채무 잔액 표에는 '…, 특수관계자거래' 행(p349 가). 대응 행이 없어 확장하면 영문명·라벨에 특수관계자 특성 반영.
    금액 요소만, 이름에 RelatedPart가 있거나 표준 특수관계자 목차에 정의된 요소는 통과, 보증·담보·현황 표 건은 확인 권장."""
    rp_std = std.role_elems.get("D818000", set()) | std.role_elems.get("D818005", set())
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if rc[:5] != "D8180":
            continue
        tl = _local(t) + " " + (r.label(t) or "")
        side = bool(RP_SIDE_TABLE.search(tl))
        if t not in RP_TX_TABLES and not side and not (_is_ext(r, t) and re.search(r"거래|채권|채무", tl)):
            continue
        for q in dict.fromkeys(info["items"]):
            if "monetary" not in _attr(r, q)["type"].lower() or (rc, q) in seen or "RelatedPart" in q:
                continue
            if _is_ext(r, q):
                if not side and "특수관계자" not in _lab(r, q):
                    seen.add((rc, q))
                    out.append(_f(r, "G5C-075", rc, q, f"확장 '{_lab(r, q)}' — 영문명·라벨에 특수관계자거래 특성 반영", basis="약함", table=t))
                continue
            if q in rp_std:
                continue
            seen.add((rc, q))
            # 같은 뜻의 특수관계자 전용 표준 행(대응 행)이 있는데 일반 행을 쓴 것만 수정 필요,
            # 대응 행이 없어 일반 행을 쓴 것은 확인 권장(가이드는 확장해 특수관계자 특성 반영 권고)
            cp = _rp_counterpart(std, q)
            if cp:
                out.append(_f(r, "G5C-075", rc, q, f"특수관계자 표에 일반 행 '{_lab(r, q)}' — 특수관계자 전용 표준 행 "
                              f"'{std.label_ko.get(cp, cp)}'({cp}) 사용", basis="약함" if side else "명확", table=t))
            else:
                out.append(_f(r, "G5C-075", rc, q, f"특수관계자 표에 일반 행 '{_lab(r, q)}' — 특수관계자 전용 표준 행은 없음, "
                              f"확장해 영문명·라벨에 특수관계자거래 특성 반영 권고",
                              basis="약함" if side else "명확", level="검토필요" if side else "개선", table=t))  # 개선 권장(3단계)
    return out


_RP_KO_IDX = {}


def _rp_counterpart(std, q):
    """일반 행 q 와 같은 뜻의 특수관계자 전용 표준 행: 이름+RelatedPartyTransactions(ifrs-full·dart) 또는 한글명+', 특수관계자거래'."""
    if not _RP_KO_IDX:
        for c, t in std.label_ko.items():
            if t.endswith(", 특수관계자거래") and c in std.concepts:
                _RP_KO_IDX.setdefault(t[: -len(", 특수관계자거래")].strip(), c)
        _RP_KO_IDX.setdefault("", None)
    base = q.split(":", 1)[-1]
    for p in (IF, DART):
        if p + base + "RelatedPartyTransactions" in std.concepts:
            return p + base + "RelatedPartyTransactions"
    ko = (std.label_ko.get(q) or "").strip()
    return _RP_KO_IDX.get(ko) if ko else None


SGA_SUBTOTAL = {IF + "SellingExpense", IF + "AdministrativeExpense", IF + "SellingGeneralAndAdministrativeExpense",
                IF + "GeneralAndAdministrativeExpense", DART + "TotalSellingGeneralAdministrativeExpenses"}
FUNC_IS = {IF + "CostOfSales", IF + "GrossProfit", DART + "TotalSellingGeneralAdministrativeExpenses"}


def g5c_053(r, std):
    """기능별 손익계산서 기업의 판관비 주석(D83431x)에는 '~, 판관비' 행(p265). 기업전체 비용 요소가 다른 목차에도 쓰여
    (같은 요소·context = 같은 금액) 판관비와 일치하는 경우는 예외. 예외 판정이 불완전할 수 있어 확인 권장."""
    idx = _index(r)
    sga_std = set().union(*(ns for rc, ns in std.role_elems.items() if rc.startswith("D83431")))
    out = []
    for sep in "05":
        if not any(_stmt(rc) == "IS" and rc.endswith(sep) and ns & FUNC_IS for rc, ns in idx["nodes"].items()):
            continue   # 성격별 손익계산서는 대상 아님
        for rc, ns in sorted(idx["nodes"].items()):
            if rc[:6] != "D83431" or not rc.endswith(sep):
                continue
            for q in sorted(ns):
                if _is_ext(r, q) or r.kind(q) != "item" or q in sga_std or q in SGA_SUBTOTAL or \
                        _local(q).endswith("SellingGeneralAdministrativeExpenses"):
                    continue
                a = _attr(r, q)
                if a["balance"] != "debit" or a["period_type"] != "duration" or "monetary" not in a["type"].lower():
                    continue
                if any(x != rc and x.endswith(sep) and x[:6] != "D83431" for x in idx["used"].get(q, ())):
                    continue   # 기업전체 금액 = 판관비 금액 예외
                out.append(_f(r, "G5C-053", rc, q, f"판관비 표에 기업전체 요소 '{_lab(r, q)}' — '~, 판관비' 행 사용", basis="약함"))
    return out


ONE_WAY_PL = re.compile(r"^(Revenue|CostOfSales|SellingGeneralAndAdministrativeExpense|TotalSellingGeneralAdministrativeExpenses)|Expense(s)?$")
TWO_WAY_PL = re.compile(r"ProfitLoss|GainLoss|GainsLosses|IncomeLoss|IncomeExpense|IncomeTaxExpenseContinuingOperations")


def g5c_080(r, std):
    doms, under, kids = _def_axis_members(r)
    # 도메인→멤버 연결이 정의된 주석(role)을 찾아 함께 표시(연결/별도·주석명이 보이도록). 정의 role 접미사(a, b…)는 뗀다
    where = defaultdict(set)
    for u, arcs in r.defs.items():
        rc = re.sub(r"[a-z]+$", "", r.role_code(u) or "")
        for a, p, c in arcs:
            if a in ("dimension-domain", "domain-member"):
                where[(p, c)].add(rc)
    out = []
    for d in doms.get(IF + "SegmentsAxis", ()):
        for m in kids.get(d, ()):
            if m not in (IF + "ReportableSegmentsMember", IF + "AllOtherSegmentsMember") and _is_ext(r, m):
                for rc in sorted(where.get((d, m)) or {""}):
                    out.append(_f(r, "G5C-080", rc, m, f"부문 축 도메인 바로 아래 부문 멤버 '{_lab(r, m)}' — 보고부문 멤버 하위로"))
    allowed = {IF + "OperatingSegmentsMember", IF + "MaterialReconcilingItemsMember"}
    for d in doms.get(IF + "SegmentConsolidationItemsAxis", ()):
        for m in kids.get(d, ()):
            if m not in allowed:
                for rc in sorted(where.get((d, m)) or {""}):
                    out.append(_f(r, "G5C-080", rc, m, f"부문의 합계 축에 '{_lab(r, m)}' — 영업부문/중요한 조정사항 멤버만"))
    return out


def g5c_081(r, std):
    # 영업부문 표(부문 축·부문의 합계 축이 있는 표)의 행이면 수정 필요, 지역 표 등 다른 표의 행이면 가이드 방식을
    # 그대로 적용할 수 없어 확인 권장
    seg = {(rc, q) for u, rc, t, info in _tables(r)
           if {IF + "SegmentsAxis", IF + "SegmentConsolidationItemsAxis"} & set(info["axes"]) for q in info["items"]}
    return [_f(r, "G5C-081", rc, q, f"'{_lab(r, q)}' 확장 행 — 내부매출은 중요한 조정사항 하위 멤버, 외부매출은 영업부문 멤버",
               basis="명확" if (rc, q) in seg else "약함")
            for q, rc in _ext_items_in(r, lambda rc: rc[:5] == "D8711").items() if re.search(r"내부매출|외부매출|부문간", _lab(r, q))]


def g5c_082(r, std):
    out, seen = [], set()
    for f, rc in _facts(r):
        a = _attr(r, f["q"])
        if DART + "GovernmentGrantsMember" in _dims(r, f).values() and a["balance"] == "debit" and \
                a["period_type"] == "instant" and (_num(f["value"]) or 0) > 0 and (rc, f["q"]) not in seen:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5C-082", rc, f["q"], f"자산 × 정부보조금 멤버 값 {f['value']} — 음수로"))
    return out


GRANT_KEY = re.compile(r"과제|협약|교부|기관|사업")


def g5c_083(r, std):
    # 정부보조금 주석 + 정부보조금 멤버가 붙은 fact(유형·무형자산 주석의 정부보조금 표)까지. 과제·기관 성격의 확장 축만
    # 수정 필요, 그 밖의 확장 축(상환의무 유무 등)은 확인 권장
    out = _ext_axis_on(r, "G5C-083", lambda f, rc: rc[:5] == "D8314" or DART + "GovernmentGrantsMember" in _dims(r, f).values(),
                       "정부보조금 과제·교부기관을 표준 축 대신 확장 축으로")
    _, under, _ = _def_axis_members(r)
    for x in out:
        ax = x.element
        if not any(GRANT_KEY.search(_lab(r, n)) for n in [ax] + sorted(under.get(ax, ()))):
            x.extra["basis"] = "약함"
    return out


# ---------------------------------------------------------------------------

def _g(rid, fn, pages, title):
    return (S8, f"{rid} {title}", ERR, lambda r, s, fn=fn, rid=rid: [
        Finding(x.section, f"{rid} {title}", x.level, x.role_code, x.element, x.detail, x.table, x.parent, x.extra)
        for x in (_set_report(r) or fn(r, s))], f"p{pages} ({rid})", False)


# ---- 5장 Ⅱ.3~4 축·멤버 구성 ----
def _mlab(r, q):
    return re.sub(r"\s*\[(구성요소|도메인|축)\]\s*$", "", _lab(r, q) or "")


def _member_axes(r):
    """보고서 정의링크: 멤버 -> 그 멤버가 속한 축들."""
    _, under, _ = _def_axis_members(r)
    res = defaultdict(set)
    for ax, ms in under.items():
        for m in ms:
            res[m].add(ax)
    return res


def g5_007(r, std):
    """연체·손상 여부는 행이 아니라 연체상태 [축]·금융상품의 신용손상 [축]으로(p104)."""
    return [_f(r, "G5-007", rc, q, f"연체·손상 여부를 행 '{_lab(r, q)}'로 확장 — '매출채권 및 기타채권' 등 행 + 연체상태 [축]·금융상품의 신용손상 [축]")
            for q, rc in sorted(_ext_items_in(r).items())
            if re.search(r"연체|손상되지|손상된", _lab(r, q)) and not re.search(r"연체이자|연체료", _lab(r, q))]


def g5_033(r, std):
    """표준 축과 비슷한 분류체계의 축을 확장하지 않는다(p117, 예: 제품과 용역 [축] 대신 제품 [축])."""
    std_axes = [q for q, c in std.concepts.items() if c["sg"] == "xbrldt:dimensionItem" and not q.startswith(("DB", "DI", "DS", "DX"))]
    slab = {q: re.sub(r"\s|\[축\]", "", std.label_ko.get(q, "")) for q in std_axes}
    out = []
    for q, rc in sorted(_ext_members(r).items()):
        if r.kind(q) != "axis":
            continue
        a = re.sub(r"\s|\[축\]", "", _lab(r, q) or "")
        if len(a) < 2:
            continue
        best = None
        for s, b in slab.items():
            if not b:
                continue
            sim = 1.0 if a == b else len(_bigram(a) & _bigram(b)) / max(1, len(_bigram(a) | _bigram(b)))
            if sim >= 0.7 and (best is None or sim > best[0]):
                best = (sim, s)
        if best:
            out.append(_f(r, "G5-033", rc, q, f"확장 축 '{_lab(r, q)}' — 표준 축 '{std.label_ko.get(best[1])}' 사용",
                          basis="명확" if best[0] == 1 else "약함", pair=[best[1]]))
    return out


TAIL_AXES = (IF + "MaturityAxis", IF + "RangeAxis", IF + "PastDueStatusAxis")


def _std_axis_order(std):
    """표준 pre: 표 -> 축 순서(처음 나온 role 기준)."""
    if hasattr(std, "_gaxo"):
        return std._gaxo
    c = sqlite3.connect(STD_DB)
    res = {}
    for rc, p, n, par, o in c.execute("SELECT role_code,prefix,name,parent,ord FROM pre_arcs ORDER BY role_code,seq"):
        q = f"{p}:{n}"
        if par and std.kind(par) == "table" and std.kind(q) == "axis":
            res.setdefault((rc, par), []).append((o or 0, q))
    order = {}
    for (rc, t), xs in res.items():
        order.setdefault(t, [q for _, q in sorted(xs)])
    std._gaxo = order
    return order


def g5_041(r, std):
    """축이 2개 이상이면 측정 [축] 최상단, 분류 축은 장부금액 축 위, 만기·범위 축은 맨 아래(p119-120)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        axes = [a for a in info["axes"] if a != CONSEP_AXIS]
        if len(axes) < 2 or (rc, t) in seen:
            continue
        seen.add((rc, t))
        so = [a for a in _std_axis_order(std).get(t, []) if a in axes]
        if so and so == [a for a in axes if a in so] and len(so) == len(axes):
            continue
        why = []
        if IF + "MeasurementAxis" in axes and axes[0] != IF + "MeasurementAxis":
            why.append("측정 [축]이 최상단이 아님")
        if CARRYING_AXIS in axes:
            ci = axes.index(CARRYING_AXIS)
            if any(_local(a).startswith("ClassesOf") for a in axes[ci + 1:]):
                why.append("분류 축이 장부금액 축 아래")
        tails = [i for i, a in enumerate(axes) if a in TAIL_AXES]
        if tails and any(a not in TAIL_AXES for a in axes[min(tails) + 1:]):
            why.append("만기·범위·연체 축 아래에 다른 축")
        if why:
            out.append(_f(r, "G5-041", rc, t, f"축 순서 — {', '.join(why)}", basis="약함", table=t))
    return out


G5B003_ROLES = {"D82210": "PropertyPlantAndEquipment", "D82318": "IntangibleAssetsOtherThanGoodwill",
                "D82510": "InvestmentProperty", "D83261": "RightofuseAssets"}


def g5b_003(r, std):
    """자산 장부가액 변동에서 행에 자산 분류가 없으면(예: 감가상각비) 자산 분류 축 필수, 공시금액 멤버만으로는 안 됨(p121).
    (G5B-001·002 함께: 행만으로 설명 안 되는 fact에 축 누락)"""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        stem = G5B003_ROLES.get(rc[:6])
        if not stem or any(_local(a).startswith("ClassesOf") for a in info["axes"]):
            continue
        for q in dict.fromkeys(info["items"]):
            if q in G5B005_ROW and (rc, q) not in seen:
                seen.add((rc, q))
                c = _g5b005_cand(std, q, stem)
                out.append(_f(r, "G5B-003", rc, q, f"범용 행 '{_lab(r, q)}'에 자산 분류 축 없음 — "
                                                   + (f"'{_lab(r, c)}' 행 사용" if c else "자산 분류 축 사용"), table=t))
    return out


def g5b_004(r, std):
    """장부가액의 특정 부분(손상차손누계액 등) 변동은 행 확장이 아니라 장부금액 축의 해당 멤버로(p121·127)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if not _is_change_table(info):
            continue
        for q in dict.fromkeys(info["items"]):
            if _is_ext(r, q) and (rc, q) not in seen and "monetary" in _attr(r, q)["type"].lower() \
                    and re.search(r"손상차손누계액|감가상각누계액|상각누계액|대손충당금|손실충당금", _lab(r, q)):
                seen.add((rc, q))
                out.append(_f(r, "G5B-004", rc, q, f"장부금액 일부 변동을 행 '{_lab(r, q)}'로 확장 — 장부금액 축의 해당 멤버(손상차손누계액 등)", table=t))
    return out


G5B006 = {IF + "Assets": (IF + "ClassesOfFinancialAssetsAxis", IF + "ClassesOfFinancialInstrumentsAxis"),
          IF + "Liabilities": (IF + "ClassesOfFinancialLiabilitiesAxis", IF + "ClassesOfFinancialInstrumentsAxis")}


def g5b_006(r, std):
    """계정을 분류하는 축은 행과 같거나 커야 함 — '자산' 행 × 금융자산의 종류 축 금지(p122)."""
    out, seen = [], set()
    for f, rc in _facts(r, struct=True):
        for ax in _dims(r, f):
            if ax in G5B006.get(f["q"], ()) and (rc, f["q"], ax) not in seen:
                seen.add((rc, f["q"], ax))
                out.append(_f(r, "G5B-006", rc, f["q"], f"'{_lab(r, f['q'])}' 행을 '{_lab(r, ax)}'로 분류 — 축보다 큰 행(금융자산·금융부채 행 사용)"))
    return out


def g5b_010(r, std):
    """행에 이미 든 분류 개념을 멤버로 다시 분류하지 않는다(p118·124-125, 예: '급여, 판관비' × 판매비와 일반관리비 멤버).
    (G5-038 함께: '감가상각비, 유형자산' 행 + 유형자산 멤버)"""
    def core(s):
        return re.sub(r"\s", "", s).replace("판매비와일반관리비", "판관비").replace("판매비와관리비", "판관비")
    out, seen = [], set()
    doms, _, _ = _def_axis_members(r)
    sdom, _ = _std_extra(std)
    alldom = {m for ms in sdom.values() for m in ms}
    for f, rc in _facts(r, struct=True):
        lab = _lab(r, f["q"]) or ""
        if ", " not in lab:
            continue
        qual = core(lab.rsplit(", ", 1)[1])
        for ax, m in _dims(r, f).items():
            if m.endswith("Domain") or m in doms.get(ax, ()) or m in alldom or (rc, f["q"], m) in seen \
                    or re.fullmatch(r"[A-Z]{3}", _mlab(r, m)):   # 통화 열(행 'BorrowingsUSD' + USD 열, p136-137)은 허용
                continue
            if core(_mlab(r, m)) == qual:
                seen.add((rc, f["q"], m))
                out.append(_f(r, "G5B-010", rc, f["q"], f"행 '{lab}'의 분류 개념을 멤버 '{_mlab(r, m)}'로 중복 — "
                                                        "장부금액 축 + 공시금액 멤버 등 표준 표의 축 사용", pair=[m],
                              basis="명확" if qual == "판관비" else "약함"))
    return out


def g5b_012(r, std):
    """측정 [축]은 금액을 원가모형·공정가치모형으로 나눠 공시할 때만(회계정책 표시 목적 사용 안 함, p125-126·223)."""
    out, seen = [], set()
    fv = {IF + "FairValueModelMember", IF + "AtFairValueMember", IF + "AtCostOrInAccordanceWithIFRS16WithinFairValueModelMember"}
    for u, rc, t, info in _tables(r):
        ms = [m for m in info["axes"].get(IF + "MeasurementAxis", []) if not m.endswith("Domain")]
        if rc[:6] != "D82510" or not ms or (rc, t) in seen:
            continue
        seen.add((rc, t))
        if not (set(ms) & fv and set(ms) - fv):
            out.append(_f(r, "G5B-012", rc, t, f"측정 [축]에 한 가지 측정 기준({', '.join(_mlab(r, m) for m in ms)})만 — 측정 기준별로 나누지 않으면 사용 안 함",
                          basis="약함", table=t))
    return out


def g5b_013(r, std):
    """대손충당금(손실충당금) 등 특정 부분의 변동은 분류 기준 축(금융상품의 종류 축 등) 필수(p126-127)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if not _is_change_table(info) or (rc, t) in seen or _sibling_tables(r, u, t):
            continue
        tl = _lab(r, t) or ""
        allow = IF + "AccumulatedImpairmentMember" in info["axes"].get(CARRYING_AXIS, [])
        if not (re.search(r"손실충당금|대손충당금", tl) or allow):
            continue
        other = [a for a in info["axes"] if a not in (CONSEP_AXIS, CARRYING_AXIS)]
        if not other:
            seen.add((rc, t))
            out.append(_f(r, "G5B-013", rc, t, "충당금 변동표에 분류 축 없음 — 금융상품의 종류 축(매출채권·미수금 등) 사용", table=t))
    return out


RANGE_RE = re.compile(r"상위\s*범위|하위\s*범위|[(,\s](상위|하위|상한|하한)\)?\s*$|^(상위|하위|상한|하한)$")


def g5b_017(r, std):
    """범위(이자율 상·하한, 투입변수 범위)는 범위 [축]의 상위·하위 범위 멤버(p129·135)."""
    return [_f(r, "G5B-017", rc, q, f"범위를 확장 멤버 '{_mlab(r, q)}'로 — 범위 [축](상위범위·하위범위 구성요소) 사용")
            for q, rc in sorted(_ext_members(r).items()) if r.kind(q) == "member" and RANGE_RE.search(_lab(r, q) or "")]


INPUT_RE = re.compile(r"영구성장률|가중평균자본비용|무위험\s*이자율|가격\s*변동성|투입변수")


def g5b_033(r, std):
    """축으로 정의된 개념(범위 상·하한, 관측할 수 없는 투입변수)을 행으로 확장 금지(p135-136·312).
    (G5B-034 함께: 수준3 투입변수는 관측할 수 없는 투입변수 [축] 하위로)"""
    out = []
    for q, rc in sorted(_ext_items_in(r).items()):
        lab = _lab(r, q) or ""
        if re.search(r"string|textBlock", _attr(r, q)["type"], re.I):
            continue
        if RANGE_RE.search(lab):
            out.append(_f(r, "G5B-033", rc, q, f"범위를 행 '{lab}'로 확장 — 범위 [축] 사용"))
        elif rc[:5] == "D8230" and INPUT_RE.search(lab):
            out.append(_f(r, "G5B-033", rc, q, f"투입변수를 행 '{lab}'로 확장 — 관측할 수 없는 투입변수 [축] 하위 구성요소"))
    return out


BAND_RE = re.compile(r"\d+\s*(년|개월|일)\s*(이내|이하|초과|미만|~)|\d+\s*(년|개월|일)\s*~")


def g5b_018(r, std):
    """만기·연체 구간은 행 확장이 아니라 만기 [축]·연체상태 [축](p129). 확장 축 아래 구간 멤버도 표준 축 검토."""
    out = []
    for q, rc in sorted(_ext_items_in(r).items()):
        if BAND_RE.search(_lab(r, q) or ""):
            out.append(_f(r, "G5B-018", rc, q, f"기간 구간을 행 '{_lab(r, q)}'로 확장 — 만기 [축]·연체상태 [축] 사용", basis="약함"))
    ma = _member_axes(r)
    for q, rc in sorted(_ext_members(r).items()):
        if r.kind(q) == "member" and BAND_RE.search(_lab(r, q) or "") and ma.get(q) and all(_is_ext(r, a) for a in ma[q]):
            out.append(_f(r, "G5B-018", rc, q, f"기간 구간 멤버 '{_mlab(r, q)}'를 확장 축에 — 만기 [축]·연체상태 [축] 사용", basis="약함"))
    return out


def g5b_021(r, std):
    """자산 분류 축에 장부금액 성격(취득원가·누계액 등) 확장 멤버 금지 — 축-도메인-멤버 논리관계(p130)."""
    ma = _member_axes(r)
    return [_f(r, "G5B-021", rc, q, f"분류 축에 장부금액 성격 확장 멤버 '{_mlab(r, q)}' — 장부금액 축의 멤버 사용")
            for q, rc in sorted(_ext_members(r).items())
            if r.kind(q) == "member" and any(_local(a).startswith("ClassesOf") for a in ma.get(q, ()))
            and re.search(r"장부금액|취득원가|감가상각누계액|상각누계액|손상차손누계액|정부보조금|국고보조금", _lab(r, q) or "")]


def g5b_022(r, std):
    """'우발부채1'처럼 연속 숫자 멤버 확장은 지양 — 거래상대방·차입금 명칭 축 등으로 분류 가능하면 사용(p130)."""
    ma, groups = _member_axes(r), defaultdict(list)
    for q, rc in _ext_members(r).items():
        m = re.match(r"^(.*?[가-힣])\s*\d+$", _mlab(r, q))
        if r.kind(q) == "member" and m:
            for ax in ma.get(q, ()) or ("",):
                groups[(ax, m.group(1).strip())].append((rc, q))
    out = []
    for (ax, stem), xs in sorted(groups.items()):
        if len(xs) >= 2:
            rc, q = sorted(xs)[0]
            out.append(_f(r, "G5B-022", rc, q, f"연속 숫자 멤버 '{stem}1, {stem}2…' {len(xs)}개" + (f"({_lab(r, ax)})" if ax else "")
                                               + " — 거래상대방·차입금 명칭 축 등으로 분류 가능하면 사용", basis="약함"))
    return out


CCY_RE = re.compile(r"\b(USD|EUR|JPY|CNY|GBP|HKD|VND|IDR|CHF|SGD|AUD|CAD)\b|달러|유로|엔화|위안|파운드|동화")


def g5b_038(r, std):
    """통화별 외화자산·부채의 원화환산액은 '외화금융자산(부채), 원화환산금액' 행 + 정보를 표시하는 통화 [축](p137).
    원화(KRW) 값이 든 통화별 확장 행이면 수정 필요."""
    out, seen = [], set()
    for f, rc in _facts(r):
        q = f["q"]
        if rc.startswith("D8") and _is_ext(r, q) and (f["unit"] or "").startswith("iso4217:KRW") and q not in seen \
                and CCY_RE.search(_lab(r, q) or "") and re.search(r"외화|화폐성", _lab(r, q) or ""):
            seen.add(q)
            out.append(_f(r, "G5B-038", rc, q, f"통화별 원화환산액을 행 '{_lab(r, q)}'로 확장 — '외화금융자산(부채), 원화환산금액' 행 + 정보를 표시하는 통화 [축]"))
    return out


# ---- 5장 Ⅰ·Ⅱ.5 입력 일반 ----
AMT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?\s*(백만원|천원|억원|원)")
AMT_SCALE = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}


def _is_text(r, q):
    t = _attr(r, q)["type"].lower()
    return "string" in t and "textblock" not in t


def g5_012(r, std):
    """문장으로 된 공시 정보, 표 밖 문장 속 금액도 공시 정보이면 표(숫자 행)로 구성(p107-108·141-142).
    주석의 문자열 fact 문장에 금액이 있는데 그 금액이 보고서의 어느 숫자 fact에도 없으면 확인 권장."""
    nums = {abs(v) for v in (_num(f["value"]) for f in r.facts) if v}
    out, seen = [], set()
    for f, rc in _facts(r):
        if not rc.startswith("D8") or not _is_text(r, f["q"]) or (rc, f["q"]) in seen:
            continue
        miss = []
        for a, u in AMT_RE.findall(f["value"] or ""):
            v = float(a.replace(",", "")) * AMT_SCALE[u]
            if v and not any(abs(v - n) <= AMT_SCALE[u] for n in nums):
                miss.append(f"{a}{u}")
        if miss:
            seen.add((rc, f["q"]))
            out.append(_f(r, "G5-012", rc, f["q"], f"문장 행 '{_lab(r, f['q'])}'의 금액 {', '.join(miss[:3])}이(가) 숫자 fact로 없음 — "
                                                    "공시 정보면 표의 숫자 행(약정 금액 등)으로", basis="약함"))
    return out


def g5b_041(r, std):
    """문자열·금액 행이 모두 가능하면 금액 행 우선(p138): 숫자만 든 문자열 fact, 외화 차입금을 '차입금, 발행통화' 문자열로만 공시."""
    out, seen = [], set()
    fx_roles = {rc for f, rc in _facts(r) if (f["unit"] or "").startswith("iso4217:") and not f["unit"].endswith("KRW")}
    for f, rc in _facts(r):
        q, v = f["q"], (f["value"] or "").strip()
        if not rc.startswith("D8") or not _is_text(r, q) or (rc, q) in seen:
            continue
        if q == IF + "BorrowingsOriginalCurrency" and rc not in fx_roles and re.search(r"[A-Z]{3}|달러|유로|엔|위안", v) \
                and not re.fullmatch(r"KRW|원화|원", v):
            seen.add((rc, q))
            out.append(_f(r, "G5B-041", rc, q, f"외화 차입금 통화를 문자열 '{v[:20]}'로만 공시 — 통화코드 붙인 금액 행(BorrowingsUSD 등) 확장",
                          basis="약함"))
        elif re.fullmatch(r"\(?-?\d{1,3}(,\d{3})+\)?|\(?-?\d{5,}\)?", v):
            seen.add((rc, q))
            out.append(_f(r, "G5B-041", rc, q, f"문자열 행 '{_lab(r, q)}'에 숫자 '{v[:20]}' — 숫자·금액 행 사용", basis="약함"))
    return out


# ---- 5장 Ⅲ 계정별 ----
G5C017_ROLES = {"D82210": "PropertyPlantAndEquipment", "D82510": "InvestmentProperty"}


def g5c_017(r, std):
    """유형자산·투자부동산 변동표의 환율 변동은 '순외환차이를 통한 증가(감소)' 표준 행, 확장 금지(p202·223)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        stem = G5C017_ROLES.get(rc[:6])
        if not stem or not _is_change_table(info):
            continue
        for q in dict.fromkeys(info["items"]):
            if _is_ext(r, q) and (rc, q) not in seen and re.search(r"외환|환율|환산", _lab(r, q) or ""):
                seen.add((rc, q))
                out.append(_f(r, "G5C-017", rc, q, f"환율 변동을 확장 행 '{_lab(r, q)}'로 — "
                                                   f"'{_lab(r, IF + 'IncreaseDecreaseThroughNetExchangeDifferences' + stem)}' 행 사용", table=t))
    return out


def g5c_023(r, std):
    """사용권자산 포함 유형자산 변동을 공시하면서 사용권자산 변동을 따로 공시하면 같은 구조(유형자산(사용권자산 포함) 행·축)로(p201)."""
    nodes = _index(r)["nodes"]
    incl = any("IncludingRightofuseAssets" in n for rc, ns in nodes.items() if rc[:6] == "D82210" for n in ns)
    if not incl:
        return []
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if rc[:6] != "D83261" or not _is_change_table(info) or _sibling_tables(r, u, t) \
                or t == "dart:ConsolidatedAndSeparateFinancialStatementsTable":
            continue
        bad = [q for q in dict.fromkeys(info["items"]) if r.kind(q) == "item" and re.search(r"Rightofuse", q)
               and "IncludingRightofuseAssets" not in q]
        if IF + "ClassesOfAssetsAxis" in info["axes"]:
            bad.append(IF + "ClassesOfAssetsAxis")
        if bad and (rc, t) not in seen:
            seen.add((rc, t))
            out.append(_f(r, "G5C-023", rc, t, f"유형자산(사용권자산 포함) 변동과 별도로 사용권자산 변동을 다른 구조로 공시('{_lab(r, bad[0])}' 등 {len(bad)}개) — "
                                               "유형자산(사용권자산 포함) 행 + 사용권자산을 포함하는 유형자산 축·유형자산의 분류 축", table=t))
    return out


G5C038_PAIRS = [(IF + "ShorttermBorrowings", DART + "CurrentBondsIssued"),
                (IF + "LongtermBorrowings", DART + "NoncurrentBondsIssued"),
                (IF + "Borrowings", IF + "BondsIssued")]


def g5c_038(r, std):
    """차입금은 만기·유형 수준을 맞춰 선택 — '차입금(사채 포함)' 수준 행과 사채 행을 함께 쓰면 지양(p233-234)."""
    out = []
    for rc, ns in sorted(_index(r)["nodes"].items()):
        for a, b in G5C038_PAIRS:
            if a in ns and b in ns:
                out.append(_f(r, "G5C-038", rc, a, f"'{_lab(r, a)}'와 '{_lab(r, b)}'를 함께 사용 — 금융기관 차입금은 '금융기관 차입금(사채 제외)' 계열 행으로 유형 수준 맞춤",
                              basis="약함", pair=[b]))
    return out


def g5c_055(r, std):
    """자본에 직접 반영된 법인세는 '기타포괄손익의 각 구성요소와 관련된 법인세' 표준 행(p277). 법인세 주석의 기타포괄 법인세 확장 행이면 수정 필요."""
    return [_f(r, "G5C-055", rc, q, f"기타포괄손익 관련 법인세를 확장 '{_lab(r, q)}'로 — '기타포괄손익의 각 구성요소와 관련된 법인세' 계열 행")
            for q, rc in sorted(_ext_items_in(r, lambda rc: rc[:6] == "D83511").items())
            if re.search(r"법인세", _lab(r, q) or "") and re.search(r"기타포괄|재측정|자본에\s*직접", _lab(r, q) or "")
            and "monetary" in _attr(r, q)["type"].lower()]


TEMP_AXIS = IF + "TemporaryDifferenceUnusedTaxLossesAndUnusedTaxCreditsAxis"


def g5c_057(r, std):
    """이연법인세 일시적차이 멤버는 가장 비슷한 표준 멤버, 계정 주석과 일관(대손충당금 → 손상차손누계액 멤버)(p277)."""
    _, under, _ = _def_axis_members(r)
    stdm = [m for m in std.axis_members.get(TEMP_AXIS, ()) if not m.endswith("Domain")]
    slab = {m: re.sub(r"\s|\[구성요소\]", "", std.label_ko.get(m, "")) for m in stdm}
    out = []
    rc0 = next((rc for rc in sorted(_index(r)["nodes"]) if TEMP_AXIS in _index(r)["nodes"][rc]), "")
    for m in sorted(under.get(TEMP_AXIS, ())):
        lab = _mlab(r, m)
        if re.search(r"대손충당금", lab):
            out.append(_f(r, "G5C-057", rc0, m, f"일시적차이 멤버 '{lab}' — 금융자산 대손충당금은 손상차손누계액 계열 멤버(계정 주석과 일관)", basis="약함"))
            continue
        if not _is_ext(r, m):
            continue
        a = re.sub(r"\s", "", lab)
        best = max(((len(_bigram(a) & _bigram(b)) / max(1, len(_bigram(a) | _bigram(b))), s) for s, b in slab.items() if b), default=None)
        if best and best[0] >= 0.7:
            out.append(_f(r, "G5C-057", rc0, m, f"일시적차이 확장 멤버 '{lab}' — 표준 멤버 '{_mlab(r, best[1])}' 사용", basis="약함", pair=[best[1]]))
    return out


def g5c_062(r, std):
    """민감도 분석의 자본 영향은 확장 행 하나가 아니라 당기손익 영향 + 기타포괄손익 영향 표준 행(p301)."""
    return [_f(r, "G5C-062", rc, q, f"민감도 자본 영향을 확장 '{_lab(r, q)}'로 — 당기손익에 미치는 영향·기타포괄손익에 미치는 영향 표준 행")
            for q, rc in sorted(_ext_items_in(r, lambda rc: rc[:6] == "D82238").items())
            if re.search(r"자본", _lab(r, q) or "") and re.search(r"영향|민감도|상승|하락|증가|감소", _lab(r, q) or "")
            and "monetary" in _attr(r, q)["type"].lower()]


FV_BV_TABLES = (DART + "FairValueAndBookValueOfAssetsTable", DART + "FairValueAndBookValueOfLiabilitiesTable")


def g5c_065(r, std):
    """공정가치-장부금액 비교표는 '금융자산'·'금융자산, 공정가치' 행 + 금융자산의 종류 축, 측정·장부금액 축 사용 안 함(p312)."""
    out = []
    for u, rc, t, info in _tables(r):
        if t in FV_BV_TABLES:
            bad = [a for a in (IF + "MeasurementAxis", CARRYING_AXIS) if a in info["axes"]]
            if bad:
                out.append(_f(r, "G5C-065", rc, t, f"공정가치·장부금액 비교표에 '{_lab(r, bad[0])}' — 축 없이 '금융자산'·'금융자산, 공정가치' 행으로", table=t))
    return out


NONPF = (DART + "ProvisionOfGuaranteesByToEntityOfNonPF", DART + "LimitAmountOfProvisionOfGuaranteesByToEntityOfNonPF")


def g5c_070(r, std):
    """'기업의(으로의) 보증금액·보증한도 (비 PF)' 행은 비PF 우발부채 공시에서만(p332). PF 표·우발부채 밖 목차에 쓰면 수정 필요.
    (특수관계자 주석은 G5C-072에서 본다)"""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        for q in NONPF:
            if q in info["items"] and (rc, q) not in seen and rc[:5] != "D8180":
                tl = _lab(r, t) or ""
                pf = re.search(r"PF|프로젝트", tl) and not re.search(r"[비非]\s*PF", tl)
                if rc[:5] != "D8275" or pf:
                    seen.add((rc, q))
                    out.append(_f(r, "G5C-070", rc, q, f"비PF 보증 행을 {'PF 표' if pf else '우발부채 밖 목차'}('{_lab(r, t)}')에 사용 — 비PF 우발부채 공시에서만", table=t))
    return out


def g5c_077(r, std):
    """특수관계자 목록만 있는 표(금액 없음)는 목차만 만들고 블록태깅 권고(p349)."""
    out = []
    for u, rc, t, info in _tables(r):
        if rc[:5] != "D8180" or not info["items"]:
            continue
        if not any("monetary" in _attr(r, q)["type"].lower() or "decimal" in _attr(r, q)["type"].lower()
                   or "percent" in _attr(r, q)["type"].lower() for q in info["items"]):
            out.append(_f(r, "G5C-077", rc, t, f"금액 없는 특수관계자 표 '{_lab(r, t)}' — 목록만 있으면 목차만 만들고 블록태깅 권고", basis="약함", table=t))
    return out


# ---- 사람 확인 항목 중 XBRL만으로 판정 가능한 것 ----
def g4_010(r, std):
    """금융업·비금융업 혼재 회사의 주석은 축으로 금융업/비금융업을 구분해 한 표에 보여주는 것을 권고(p78).
    비금융 목차와 금융업 목차(DB·DI·DS·DX)를 함께 쓰면 혼재 회사로 보고 확인 권장."""
    rcs = sorted(_index(r)["nodes"])
    fin = [rc for rc in rcs if re.match(r"D[BISX]\d", rc)]
    if not fin or not any(rc.startswith("D8") for rc in rcs):
        return []
    return [_f(r, "G4-010", fin[0], fin[0], f"비금융 목차와 금융업 목차({', '.join(fin[:3])}{' 등' if len(fin) > 3 else ''})를 함께 사용 — "
                                           "혼재 회사 주석은 축으로 금융업/비금융업을 구분해 한 표로(표를 나누면 전체 금액 표 추가) 권고", basis="약함")]


def g4_013(r, std):
    """표준 개별 요소(예: 건물, 구축물)를 합친 확장 계정(건물 및 구축물)은 중요성·기준서상 정당할 때만(p80)."""
    by_lab = defaultdict(list)
    for q, l in std.label_ko.items():
        if std.kind(q) == "item" and q not in std.deprecated:
            by_lab[re.sub(r"\s", "", l)].append(q)
    out = []
    for q, rc in sorted(_ext_items_in(r).items()):
        lab = _lab(r, q) or ""
        if ", " in lab or re.sub(r"\s", "", lab) in by_lab:
            continue
        parts = [re.sub(r"\s", "", x) for x in re.split(r"\s*(?:및|와|과)\s*", lab) if x.strip()]
        if len(parts) >= 2 and all(p in by_lab for p in parts) and "monetary" in _attr(r, q)["type"].lower():
            out.append(_f(r, "G4-013", rc, q, f"표준 개별 요소({', '.join(parts)})를 합친 확장 '{lab}' — 기준서상 구분 공시 의무·중요성 검토(필요하면 표준 요소로 각각)",
                          basis="약함"))
    return out


def g5_013(r, std):
    """사업보고서와 감사보고서의 주석 목차·순서를 일치(p108). 작성기 문서의 주석 순서가 번호 순서와 다르면 확인 권장(ixd만)."""
    out = []
    for sep in (False, True):
        seq = []
        for rc, title in r.note_title.items():   # 작성기 문서 순서
            if rc.startswith("D8") and rc.endswith("5") == sep:
                m = NOTE_NO.search(title.strip())
                if m:
                    seq.append((int(m.group(1)), int(m.group(2) or 0), rc, title.strip()))
        for a, b in zip(seq, seq[1:]):
            if b[:2] < a[:2]:
                out.append(_f(r, "G5-013", b[2], b[2], f"{'별도' if sep else '연결'} 주석 순서 — '{a[3][:20]}' 다음에 '{b[3][:20]}' — 감사보고서 주석 순서와 일치",
                              basis="약함"))
    return out


SHARE_AXES = (IF + "ClassesOfShareCapitalAxis", IF + "ClassesOfOrdinarySharesAxis")


def g5_039(r, std):
    """행에 분류(보통주·우선주)가 든 행과 안 든 행이 섞이면 그 분류 축을 함께(p118·280, 주당이익 계산내역)."""
    out, seen = [], set()
    for u, rc, t, info in _tables(r):
        if (rc, t) in seen or any(a in info["axes"] for a in SHARE_AXES) or _sibling_tables(r, u, t):
            continue
        items = [q for q in dict.fromkeys(info["items"]) if r.kind(q) == "item"]
        cls = [q for q in items if re.search(r"보통주|우선주", _lab(r, q) or "")]
        if cls and len(cls) < len(items) and re.search(r"주당|배당|주식", _lab(r, t) or ""):
            seen.add((rc, t))
            out.append(_f(r, "G5-039", rc, t, f"보통주·우선주가 든 행('{_lab(r, cls[0])}' 등 {len(cls)}개)과 안 든 행이 섞임 — 주식의 종류 축 포함",
                          basis="약함", table=t))
    return out


DIV_PAYABLE = (DART + "DividendsPayableOrdinarySharesPerShare", DART + "DividendsPayablePreferredSharesPerShare")


def g5c_050(r, std):
    """배당 처분 예정(지급될)과 처분 후(지급된) 행 구분(p260). 분기·반기 보고서(기준일이 연말 아님)의 당기 '지급될 주당배당금'은
    주총이 끝난 배당이면 '지급된 주당배당금'(DividendsPaid…PerShare) — 확인 권장."""
    end = _end(r)
    if not end or (end.month, end.day) == (12, 31):
        return []
    out, seen = [], set()
    for f, rc in _facts(r):
        if f["q"] in DIV_PAYABLE and f["q"] not in seen:
            c = _ctx(r, f)
            d = c.get("instant") or c.get("end")
            if d and d[:4] == str(end.year):
                seen.add(f["q"])
                out.append(_f(r, "G5C-050", rc, f["q"], f"분기·반기 보고서 당기에 '{_lab(r, f['q'])}'(처분 예정) — 주총 후 확정·지급 배당이면 '지급된 주당배당금' 행",
                              basis="약함"))
    return out


GUIDE_CHECKS = [
    _g("G4-001", g4_001, "77", "업종·작성방식에 맞는 본문 서식"),
    _g("G4-008", g4_008, "78", "혼재 기업 본문은 전체 금액 표준계정"),
    _g("G4-010", g4_010, "78", "혼재 회사 주석은 축으로 금융/비금융 구분"),
    _g("G4-011", g4_011, "79-80", "상위 표준과 금액 같으면 하위 확장 불가"),
    _g("G4-012", g4_012, "80", "하위 확장은 표준 요소 하위에"),
    _g("G4-013", g4_013, "80", "개별 표준요소 통합 확장 검토"),
    _g("G4-014", g4_014, "80·91", "투자·재무 현금흐름 총액 표시"),
    _g("G4-015", g4_015, "80-81", "같은 라벨이라도 개념 다르면 확장"),
    _g("G4-018", g4_018, "82-83·160·170", "유동/비유동/전체 개념 요소 구분"),
    _g("G4-019", g4_019, "83", "현금흐름 조정 행의 손익 요소(예외 확인)"),
    _g("G4-021", g4_021, "83·87", "자산 차감계정 양수 입력"),
    _g("G4-023", g4_023, "90", "자본 감소 계정 양수 입력"),
    _g("G4-024", g4_024, "92", "현금유출 계정 양수 입력"),
    _g("G4-025", g4_025, "84", "재무제표 간 당기순이익 동일 요소"),
    _g("G4-026", g4_026, "84", "본문·주석 입력 단위 일치 권고"),
    _g("G4-027", g4_027, "85", "무형자산 행은 영업권 포함 여부에 맞게"),
    _g("G4-028", g4_028, "85", "유형자산 확장 금지"),
    _g("G4-029", g4_029, "86", "금융기관 차입금만이면 사채 제외 계열"),
    _g("G4-030", g4_030, "86", "차입금 유동/비유동 공시수준 일관성"),
    _g("G4-032", g4_032, "89", "자본변동표 합계 구성요소 추가 금지"),
    _g("G4-033", g4_033, "89", "자본변동표 순이익·기타포괄손익 동일 요소"),
    _g("G4-034", g4_034, "90", "자본변동표 주요 거래 표준계정"),
    _g("G4-035", g4_035, "91", "간접법 영업현금흐름 출발점"),
    _g("G4-036", g4_036, "92", "활동별 분류 요소 위치"),
    _g("G5-002", g5_002, "95", "우발부채 주석 디테일태깅"),
    _g("G5-006", g5_006, "103-104·149-150", "도메인 열은 전체 합계(중복 fact)"),
    _g("G5-012", g5_012, "107-108·141-142", "문장 속 금액도 표로"),
    _g("G5-013", g5_013, "108", "주석 순서 일치"),
    _g("G5-016", g5_016, "110-111", "세분화된 표준 주석 목차 우선"),
    _g("G5-019", g5_019, "111", "주석 분리 시 하위 번호"),
    _g("G5-022", g5_022, "112", "고객과의 계약 수익 목차"),
    _g("G5-023", g5_023, "112", "부동산 PF는 우발부채 목차"),
    _g("G5-024", g5_024, "112", "사용권자산 포함 유형자산은 리스 목차도"),
    _g("G5-025", g5_025, "112-113", "리스부채는 리스 목차"),
    _g("G5-026", g5_026, "113", "확장 목차 대신 표준 목차 검토"),
    _g("G5-027", g5_027, "113", "주석 번호 재부여·순서"),
    _g("G5-028", g5_028, "114", "표준 표 대신 확장 표 금지"),
    _g("G5-029", g5_029, "111·115", "다른 목차 표준 표 차용 제한"),
    _g("G5-031", g5_031, "116·195·200·216", "변동내역은 ifrs-full 세부 정보 표"),
    _g("G5-032", g5_032, "116", "순확정급여부채 조정내역 한 표"),
    _g("G5-007", g5_007, "104", "연체·손상은 축으로 분류"),
    _g("G5-033", g5_033, "117", "표준 축과 비슷한 축 확장 금지"),
    _g("G5-039", g5_039, "118·280", "분류 섞인 행은 분류 축 포함"),
    _g("G5-041", g5_041, "119-120", "축 상·하위 순서"),
    _g("G5B-003", g5b_003, "121", "범용 변동 행은 자산 분류 축"),
    _g("G5B-004", g5b_004, "121·127", "장부금액 일부 변동은 장부금액 축 멤버"),
    _g("G5B-005", g5b_005, "121-122", "자산분류 축+범용 행 조합 금지"),
    _g("G5B-006", g5b_006, "122", "분류 축은 행과 같거나 커야"),
    _g("G5B-010", g5b_010, "118·124-125", "행과 멤버 개념 중복 금지"),
    _g("G5B-012", g5b_012, "125-126·223", "측정 축은 측정 기준별 분류 때만"),
    _g("G5B-013", g5b_013, "126-127", "충당금 변동은 분류 축"),
    _g("G5B-017", g5b_017, "129·135", "범위는 범위 축"),
    _g("G5B-018", g5b_018, "129", "만기·연체 구간은 만기·연체 축"),
    _g("G5B-021", g5b_021, "130", "분류 축에 장부금액 성격 멤버 금지"),
    _g("G5B-022", g5b_022, "130", "연속 숫자 멤버 지양"),
    _g("G5B-033", g5b_033, "135-136·312", "축 개념을 행으로 확장 금지"),
    _g("G5B-038", g5b_038, "137", "외화 원화환산액은 통화 축"),
    _g("G5B-008", g5b_008, "123-124·222-223", "총장부금액에 취득원가 멤버 금지"),
    _g("G5B-019", g5b_019, "129", "축의 도메인은 택사노미 정의대로"),
    _g("G5B-031", g5b_031, "134", "기초·기말 행 확장 금지"),
    _g("G5B-035", g5b_035, "135", "단일 이자율에 범위 축 금지"),
    _g("G5B-039", g5b_039, "137-138", "기초통화금액 표준요소 직접 사용 금지"),
    _g("G5B-041", g5b_041, "138", "문자열보다 금액 행 우선"),
    _g("G5B-043", g5b_043, "139", "변동표 분류기준 일관성"),
    _g("G5B-044", g5b_044, "139", "해당 표준 목차의 표준 행 사용"),
    _g("G5B-046", g5b_046, "140·187-188", "유동/비유동 열은 전용 축·변동 공시만"),
    _g("G5B-052", g5b_052, "143-144", "당기말/전기말 멤버 금지"),
    _g("G5B-053", g5b_053, "142·144", "보고기간 밖 기간 fact"),
    _g("G5B-054", g5b_054, "144", "금액 fact 통화 단위"),
    _g("G5B-057", g5b_057, "148", "주식 수는 Shares 타입"),
    _g("G5B-056", g5b_056, "147", "주당금액은 표 단위와 무관하게 실제값"),
    _g("G5B-058", g5b_058, "148", "이익 행에 음수 금지"),
    _g("G5B-059", g5b_059, "148", "주석 차감계정 양수 입력"),
    _g("G5B-061", g5b_061, "148", "비율은 소수로 입력"),
    _g("G5B-062", g5b_062, "148", "비율 확장요소는 percent 타입"),
    _g("G5B-063", g5b_063, "148", "기간 타입은 기간 형식"),
    _g("G5B-064", g5b_064, "149·231", "변동이자율 문자열 입력 금지"),
    _g("G5B-068", g5b_068, "150-151", "차감 멤버 값 부호"),
    _g("G5C-001", g5c_001, "168", "재고자산 분류별 장부금액 누락 금지"),
    _g("G5C-002", g5c_002, "160·169", "재고자산 분류 축 확장 금지"),
    _g("G5C-010", g5c_010, "179", "손실충당금 × 금융자산 부호"),
    _g("G5C-012", g5c_012, "190", "차감 표시 채권 양수 입력"),
    _g("G5C-013", g5c_013, "191", "표준 표 이름 재사용 제한"),
    _g("G5C-017", g5c_017, "202·223", "외환차이 변동은 표준 행"),
    _g("G5C-018", g5c_018, "202·224", "속성 없는 변동 행 양수 입력"),
    _g("G5C-020", g5c_020, "203·224", "확장 변동 행 이름 접미"),
    _g("G5C-021", g5c_021, "201", "사용권자산 포함 변동 행"),
    _g("G5C-022", g5c_022, "201", "사용권자산 포함 전용 표·축"),
    _g("G5C-023", g5c_023, "201", "사용권자산 변동은 포함 표와 같은 구조"),
    _g("G5C-024", g5c_024, "203", "유형자산 손상차손 전용 행"),
    _g("G5C-025", g5c_025, "203", "손상차손·환입 각각 행"),
    _g("G5C-026", g5c_026, "211", "무형자산 영업권 포함 여부 행"),
    _g("G5C-027", g5c_027, "212", "무형자산 영업권 포함 여부 표"),
    _g("G5C-028", g5c_028, "212", "내부창출 여부 축"),
    _g("G5C-029", g5c_029, "212", "영업권 배분은 현금창출단위 축"),
    _g("G5C-030", g5c_030, "222-223", "투자부동산 분류는 자산의 분류 축"),
    _g("G5C-036", g5c_036, "231·243·250·302-303", "만기분석은 미할인 현금흐름 행"),
    _g("G5C-037", g5c_037, "231", "차입금 명칭 축 멤버"),
    _g("G5C-038", g5c_038, "233-234", "차입금 만기·유형 수준 맞춤"),
    _g("G5C-040", g5c_040, "243·350", "숫자 상자 행은 공시금액 표로"),
    _g("G5C-042", g5c_042, "250", "재측정손익 행 부호"),
    _g("G5C-043", g5c_043, "250", "사외적립자산 멤버 음수"),
    _g("G5C-044", g5c_044, "250", "가중평균만기 기간 형식"),
    _g("G5C-047", g5c_047, "256·260", "주식 종류별 값과 합계"),
    _g("G5C-049", g5c_049, "256", "자본금 주식의 종류 축"),
    _g("G5C-050", g5c_050, "260", "배당 예정·지급 행 구분"),
    _g("G5C-051", g5c_051, "284-285", "보통주/우선주 전용 행"),
    _g("G5C-052", g5c_052, "286", "주식수 행 주식 종류 멤버"),
    _g("G5C-053", g5c_053, "265", "판관비 세부내역은 '~, 판관비' 행"),
    _g("G5C-054", g5c_054, "265", "기타·금융 수익/비용 한 표"),
    _g("G5C-055", g5c_055, "277", "자본 반영 법인세는 기타포괄 법인세 행"),
    _g("G5C-057", g5c_057, "277", "일시적차이 멤버는 표준·계정 주석과 일관"),
    _g("G5C-058", g5c_058, "277", "법인세수익 음수 입력"),
    _g("G5C-059", g5c_059, "278", "이연법인세부채(자산) 부호"),
    _g("G5C-060", g5c_060, "301", "민감도 변동률 기술 행"),
    _g("G5C-061", g5c_061, "301", "민감도 세전/세후 확장 금지"),
    _g("G5C-062", g5c_062, "301", "민감도 자본 영향은 손익·기타포괄 행"),
    _g("G5C-064", g5c_064, "312", "투입변수 감소율 양수"),
    _g("G5C-065", g5c_065, "312", "공정가치·장부금액 비교표는 행으로"),
    _g("G5C-066", g5c_066, "313", "취득원가 행 확장 금지"),
    _g("G5C-067", g5c_067, "313-314", "금융상품 변동 행 수준"),
    _g("G5C-068", g5c_068, "321-322", "범주 축 아래 종류 멤버 금지"),
    _g("G5C-070", g5c_070, "332", "비PF 보증 행은 비PF 공시만"),
    _g("G5C-071", g5c_071, "329·333", "대출약정 실행액은 차입금 행"),
    _g("G5C-072", g5c_072, "333·350", "특수관계자 보증 전용 행"),
    _g("G5C-073", g5c_073, "333", "비PF 보증 양수·구분 축"),
    _g("G5C-075", g5c_075, "349", "특수관계자 표는 '…, 특수관계자거래' 행"),
    _g("G5C-077", g5c_077, "349", "특수관계자 목록 표는 블록태깅 권고"),
    _g("G5C-079", g5c_079, "353-354", "중단영업 멤버 부호"),
    _g("G5C-074", g5c_074, "337·339", "영업활동 조정은 현금흐름 전용 행"),
    _g("G5C-080", g5c_080,"355·358-359", "부문 축 구성"),
    _g("G5C-081", g5c_081, "359", "내부·외부매출 멤버 구성"),
    _g("G5C-082", g5c_082, "362", "정부보조금 멤버 음수"),
    _g("G5C-083", g5c_083, "362", "정부보조금 과제·교부기관 축"),
]
