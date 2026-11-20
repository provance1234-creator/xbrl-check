"""
XBRL 작성 적정성 검사 공통 로더.

signatures.db(보고서 구조)와 std_taxonomy.db(표준 택사노미)를 읽어 검사 함수가 쓰는 형태로 정리한다.
"표"의 단위는 보고서 pre 트리의 [표](hypercubeItem) 노드다. 한 role에 표가 여러 개 있을 수 있다.
"""

import re
import sys
import json
import re
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field, asdict
from collections import defaultdict

# 화면 표시용: 내부 규칙번호(G5-029 등)를 제목 앞머리와 근거 괄호에서 뺀다. 내부 로직은 원래 값을 그대로 쓴다.
_RID_HEAD = re.compile(r"^G\d+[A-Z]?-\d+\s+")
_RID_PAREN = re.compile(r"\s*\(G\d+[A-Z]?-\d+(?:[·,/]\s*G\d+[A-Z]?-\d+)*\)")


def disp_title(s):
    return _RID_HEAD.sub("", s) if s else s


def disp_basis(s):
    return _RID_PAREN.sub("", s) if s else s

ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parents[2])
SIG_DB = ROOT / "data" / "signatures.db"
STD_DB = ROOT / "data" / "std_taxonomy.db"

STD_PREFIXES = ("ifrs-full", "dart")
CARRYING_AXIS = "ifrs-full:CarryingAmountAccumulatedDepreciationAmortisationAndImpairmentAndGrossCarryingAmountAxis"
REPORTED_MEMBER = "dart:ReportedAmountMember"
CONSEP_AXIS = "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"
CONSEP_TABLE = "dart:ConsolidatedAndSeparateFinancialStatementsTable"


@dataclass
class Finding:
    section: str          # 1~5장 이름
    check: str            # 카드 이름
    level: str            # 오류 | 검토필요
    role_code: str
    element: str          # 검출 대상 요소(표·요소·멤버)
    detail: str = ""
    table: str = ""       # 소속 [표]
    parent: str = ""      # 소속 [문장영역]
    extra: dict = field(default_factory=dict)

    def key(self):
        return (self.check, self.role_code, self.table, self.element)

    def as_dict(self):
        return asdict(self)


class Std:
    """표준 택사노미."""

    def __init__(self, path=STD_DB):
        c = sqlite3.connect(path)
        self.concepts = {}
        for p, n, t, sg, bal, pt, ab in c.execute(
                "SELECT prefix,name,type,substitution_group,balance,period_type,abstract FROM concepts"):
            self.concepts[f"{p}:{n}"] = dict(type=t, sg=sg, balance=bal, period_type=pt,
                                             abstract=(ab or "").lower() == "true")
        self.label_ko, self.label_en = {}, {}
        self.labels_all = {}   # (q, lang, role) -> text  (구조 보기의 표시명용)
        for p, n, lang, role, text in c.execute("SELECT prefix,name,lang,label_role,text FROM labels"):
            q = f"{p}:{n}"
            self.labels_all[(q, lang, role)] = text
            if role == "label":
                (self.label_ko if lang == "ko" else self.label_en)[q] = text
        self.deprecated = {q for q, t in self.label_ko.items() if "(사용중단)" in t}

        # role_code -> 표준 pre에 나타나는 요소 집합
        self.role_elems = defaultdict(set)
        self.elem_roles = defaultdict(set)
        for rc, p, n in c.execute("SELECT role_code,prefix,name FROM pre_arcs"):
            q = f"{p}:{n}"
            self.role_elems[rc].add(q)
            self.elem_roles[q].add(rc)
        self.role_title = {rc: t for rc, t in c.execute("SELECT role_code,title FROM role_index")}

        # 축 -> 표준 def에서 그 축 아래(도메인 포함)에 오는 멤버 전체(모든 role 합집합)
        # role은 role_uri 단위로 묶는다. 표별 정의 role(…a/b)은 role_code가 NULL이라 role_code로 묶으면
        # 서로 다른 표의 arc가 한 그래프로 합쳐져 실제로 없는 축-멤버 연결이 생긴다(G5-037).
        self.axis_members = defaultdict(set)
        rows = c.execute("SELECT role_uri,prefix,name,parent,arcrole FROM def_arcs ORDER BY role_uri,seq").fetchall()
        kids = defaultdict(lambda: defaultdict(set))   # role -> parent -> children
        dom_of = defaultdict(set)                       # (role) axis -> domains
        for rc, p, n, par, arc in rows:
            q = f"{p}:{n}"
            arc = (arc or "").rsplit("/", 1)[-1]
            if par and arc in ("dimension-domain", "domain-member"):
                kids[rc][par].add(q)
            if arc == "dimension-domain":
                dom_of[(rc, par)].add(q)
        for (rc, axis), doms in dom_of.items():
            stack, seen = list(doms), set()
            while stack:
                m = stack.pop()
                if m in seen:
                    continue
                seen.add(m)
                self.axis_members[axis].add(m)
                stack.extend(kids[rc].get(m, ()))
        self.member_axes = defaultdict(set)
        for ax, ms in self.axis_members.items():
            for m in ms:
                self.member_axes[m].add(ax)
        # 축 -> 표준 pre에서 그 축 아래에 표시되는 멤버 전체(모든 role 합집합). def와 함께 소속 판정에 쓴다
        self.pre_axis_members = defaultdict(set)
        pk = defaultdict(lambda: defaultdict(set))
        pre_axes = defaultdict(set)
        self.pre_parent = defaultdict(set)   # (role_code, 요소) -> 표준 서식에서의 상위 행
        for rc, p, n, par in c.execute("SELECT role_code,prefix,name,parent FROM pre_arcs"):
            q = f"{p}:{n}"
            if par:
                pk[rc][par].add(q)
                self.pre_parent[(rc, q)].add(par)
            if self.concepts.get(q, {}).get("sg") == "xbrldt:dimensionItem":
                pre_axes[rc].add(q)
        for rc, axs in pre_axes.items():
            for ax in axs:
                stack, seen = [ax], set()
                while stack:
                    for ch in pk[rc].get(stack.pop(), ()):
                        if ch not in seen:
                            seen.add(ch)
                            self.pre_axis_members[ax].add(ch)
                            stack.append(ch)

        # 표준 멤버 부모-자식 쌍 (domain-member)
        self.member_pairs = {(par, f"{p}:{n}") for rc, p, n, par, arc in rows
                             if par and (arc or "").endswith("domain-member")
                             and name_kind(par) == "member"}

        # 작성가이드 모범사례 매핑의 축 -> {멤버: 인쇄쪽들} (src.guide.extract_axis_members로 생성)
        self.guide_pairs = defaultdict(lambda: defaultdict(set))
        gp = ROOT / "rules" / "guide_axis_members.csv"
        if gp.exists():
            import csv
            with open(gp, encoding="utf-8-sig") as fp:
                for row in csv.DictReader(fp):
                    self.guide_pairs[row["axis"]][row["member"]].add(int(row["page"]))

        # 표준 문장영역 -> 그 아래에 오는 요소 전체(모든 role 합집합)
        pkids = defaultdict(lambda: defaultdict(set))
        for rc, p, n, par in c.execute("SELECT role_code,prefix,name,parent FROM pre_arcs"):
            if par:
                pkids[rc][par].add(f"{p}:{n}")
        self.tb_desc = defaultdict(set)
        for rc, km in pkids.items():
            for par in km:
                if self.kind(par) != "textblock":
                    continue
                stack, seen = [par], set()
                while stack:
                    for ch in km.get(stack.pop(), ()):
                        if ch not in seen:
                            seen.add(ch)
                            stack.append(ch)
                self.tb_desc[par] |= seen

    def is_std(self, q):
        return q in self.concepts

    def kind(self, q):
        """table | axis | member | abstract | textblock | item"""
        info = self.concepts.get(q)
        if info:
            if info["sg"] == "xbrldt:hypercubeItem":
                return "table"
            if info["sg"] == "xbrldt:dimensionItem":
                return "axis"
        return name_kind(q, info)

    def label(self, q):
        return self.label_ko.get(q) or q


def name_kind(q, info=None):
    n = q.split(":", 1)[-1]
    if n.endswith("Table"):
        return "table"
    if n.endswith("Axis"):
        return "axis"
    if n.endswith("Member") or n.endswith("Domain"):
        return "member"
    if n.endswith("Abstract") or n.endswith("LineItems"):
        return "abstract"
    if n.endswith("Explanatory") or n.endswith("TextBlock") or (
            info and info["type"] and "textBlock" in info["type"]):
        return "textblock"
    return "item"


class Report:
    """보고서 1건(entity+period)의 구조."""

    def __init__(self, entity, period=None, path=SIG_DB, std=None):
        c = sqlite3.connect(path)
        if period is None:
            period = c.execute("SELECT max(period) FROM entities WHERE entity=?", [entity]).fetchone()[0]
        self.entity, self.period = entity, period
        row = c.execute("SELECT corp_name, ext_prefix FROM entities WHERE entity=? AND period=?",
                        [entity, period]).fetchone()
        if not row:
            raise SystemExit(f"signatures.db에 {entity} {period} 없음. 먼저 src.ixd.parse로 적재하세요.")
        self.corp_name, self.ext_prefix = row
        k = [entity, period]
        self.std = std

        self.roles = {}   # role_uri -> (role_code, definition)
        for uri, rc, d in c.execute("SELECT role_uri,role_code,definition FROM roles WHERE entity=? AND period=?", k):
            self.roles[uri] = (rc, d)
        self.note_title = {}
        for aclass, title in c.execute("SELECT aclass,title FROM doc_tables WHERE entity=? AND period=?", k):
            m = re.search(r"([DU]\d{6})$", aclass)   # 확장 목차(U)도 주석 제목 사용
            if m:
                self.note_title[m.group(1)] = title

        self.ext_attr = {}   # 확장요소 속성: type, balance, period_type, abstract(작성기 '입력불가')
        for p, n, t, bal, pt, ab in c.execute("SELECT prefix,name,data_type,balance,period_type,abstract FROM ext_elems "
                                              "WHERE entity=? AND period=?", k):
            self.ext_attr[f"{p}:{n}"] = dict(type=t or "", balance=bal, period_type=pt, abstract=ab == "true")
        self.cal = defaultdict(list)   # role_uri -> [(parent, child, weight)]
        for uri, par, ch, w in c.execute("SELECT role_uri,parent,child,weight FROM cal_arcs "
                                         "WHERE entity=? AND period=?", k):
            self.cal[uri].append((par, ch, w))

        self.ext_kind = {}
        for p, n, sg in c.execute("SELECT prefix,name,substitution_group FROM ext_elems WHERE entity=? AND period=?", k):
            self.ext_kind[f"{p}:{n}"] = sg
        self.label_ko, self.label_en = {}, {}
        self.all_labels = defaultdict(list)   # q -> [(lang, role, text)]
        for p, n, lang, role, text in c.execute("SELECT prefix,name,lang,label_role,text FROM labels "
                                                "WHERE entity=? AND period=?", k):
            q = f"{p}:{n}"
            self.all_labels[q].append((lang, role, text))
            if role == "label":
                (self.label_ko if lang == "ko" else self.label_en)[q] = text

        # pre 트리: role_uri -> parent -> [(ord, child, pref_label)]
        self.pre = defaultdict(lambda: defaultdict(list))
        for uri, par, ch, o, pl in c.execute("SELECT role_uri,parent,child,ord,pref_label FROM pre_arcs "
                                             "WHERE entity=? AND period=?", k):
            self.pre[uri][par].append((o or 0, ch, pl))
        for uri in self.pre:
            for par in self.pre[uri]:
                self.pre[uri][par].sort(key=lambda x: x[0])

        self.defs = defaultdict(list)
        for uri, arc, par, ch in c.execute("SELECT role_uri,arcrole,parent,child FROM def_arcs "
                                           "WHERE entity=? AND period=?", k):
            self.defs[uri].append((arc, par, ch))

        self.contexts = {}
        for cid, pt, s, e, i, dims in c.execute("SELECT ctx_id,period_type,start_date,end_date,instant,dims "
                                                "FROM contexts WHERE entity=? AND period=?", k):
            self.contexts[cid] = dict(period_type=pt, start=s, end=e, instant=i,
                                      dims={d: m for d, m in json.loads(dims or "[]")})
        self.facts = []
        for p, n, v, unit, cid, nil, dec in c.execute("SELECT prefix,name,value,unit,ctx_id,is_nil,decimals "
                                                      "FROM facts WHERE entity=? AND period=?", k):
            self.facts.append(dict(q=f"{p}:{n}", value=v, unit=unit, ctx=cid, nil=nil, decimals=dec))

    # ---- 표시 도우미 ----
    def role_code(self, uri):
        """role 코드. 정의링크의 표별 role(…role-D822420a)도 D822420으로 본다."""
        rc = self.roles.get(uri, (None, ""))[0]
        if rc:
            return rc
        m = re.search(r"role-([A-Z]*\d+)[a-z]?$", uri or "")
        return m.group(1) if m else uri.rsplit("/", 1)[-1]

    def role_label(self, rc):
        if rc in self.note_title:
            return self.note_title[rc]
        for uri, (c, d) in self.roles.items():
            if c == rc:
                return d
        return rc

    def label(self, q):
        if self.std and q in self.std.label_ko:
            return self.std.label_ko[q]
        return self.label_ko.get(q, q)

    def kind(self, q):
        if q in self.ext_kind:
            sg = self.ext_kind[q]
            if sg == "xbrldt:hypercubeItem":
                return "table"
            if sg == "xbrldt:dimensionItem":
                return "axis"
            k = name_kind(q)
            if k == "table":
                # 이름만 …Table로 끝나는 확장 행(hypercubeItem 아님)은 표가 아니다(G5C-067 오탐)
                return "textblock" if "textBlock" in self.ext_attr.get(q, {}).get("type", "") else "item"
            return k
        return self.std.kind(q) if self.std else name_kind(q)

    # ---- 구조 ----
    def role_nodes(self, uri):
        """role의 pre 트리에서 (노드, 부모, 경로상 문장영역, 경로상 표, pref_label) 전부."""
        tree = self.pre[uri]
        kids = {ch for lst in tree.values() for _, ch, _ in lst}
        roots = [p for p in tree if p not in kids]
        out = []

        def walk(node, parent, tb, table, pl, seen):
            if node in seen:
                return
            k = self.kind(node)
            out.append((node, parent, tb, table, pl))
            ntb = node if k == "textblock" else tb
            ntable = node if k == "table" else table
            children = tree.get(node, [])
            # [표]와 같은 부모 아래의 형제(LineItems)는 그 표에 속한다
            sib_table = next((ch for _, ch, _ in children if self.kind(ch) == "table"), None)
            for _, ch, cpl in children:
                t2 = ntable if (sib_table is None or ch == sib_table) else sib_table
                walk(ch, node, ntb, t2, cpl, seen | {node})

        for r in roots:
            walk(r, None, None, None, None, frozenset())
        return out

    def tables(self, uri):
        """role 안의 [표]별 구조: {table: dict(parent_tb, axes{axis:[members]}, items[], pref_labels[])}"""
        tree = self.pre[uri]
        nodes = self.role_nodes(uri)
        res = {}
        for node, parent, tb, table, pl in nodes:
            if self.kind(node) != "table" or node in res:
                continue
            axes = {}
            dm = self.def_table_members(self.role_code(uri))
            for _, ax, _ in tree.get(node, []):
                if self.kind(ax) == "axis":
                    ms = self._descendants(tree, ax)
                    # 한 role의 pre는 표들이 같은 축·멤버 노드를 공유해 합쳐진다.
                    # 정의링크(표별 role)에 그 표의 멤버가 있으면 그것으로 거른다.
                    if (node, ax) in dm:
                        ms = [m for m in ms if m in dm[(node, ax)]]
                    axes[ax] = ms
            # 표와 같은 부모 아래의 LineItems 형제 = 그 표의 행
            items, pls = [], []
            for _, sib, _ in tree.get(parent, []):
                if sib != node and self.kind(sib) != "table":
                    for q, p2 in self._descendants(tree, sib, with_pl=True):
                        if self.kind(q) not in ("abstract", "table", "axis", "member"):
                            items.append(q)
                            pls.append(p2)
            res[node] = dict(parent_tb=tb, axes=axes, items=items, pref_labels=pls)
        return res

    def def_table_members(self, rc):
        """정의링크 기준 {(표, 축): 멤버 집합} — 같은 role 코드의 표별 정의 role(a, b, …)을 합친다."""
        cache = self.__dict__.setdefault("_dtm", {})
        if rc in cache:
            return cache[rc]
        res = defaultdict(set)
        for u, arcs in self.defs.items():
            if self.role_code(u) != rc:
                continue
            kids, doms, hd = defaultdict(list), defaultdict(list), []
            for a, p, c in arcs:
                if a in ("dimension-domain", "domain-member"):
                    kids[p].append(c)
                if a == "dimension-domain":
                    doms[p].append(c)
                if a == "hypercube-dimension":
                    hd.append((p, c))
            for t, ax in hd:
                stack = list(doms[ax])
                while stack:
                    m = stack.pop()
                    if m not in res[(t, ax)]:
                        res[(t, ax)].add(m)
                        stack += kids[m]
        cache[rc] = res
        return res

    @staticmethod
    def _descendants(tree, node, with_pl=False):
        out, stack, seen = [], [(node, None)], set()
        while stack:
            n, pl = stack.pop()
            for _, ch, cpl in reversed(tree.get(n, [])):
                if ch not in seen:
                    seen.add(ch)
                    out.append((ch, cpl) if with_pl else ch)
                    stack.append((ch, cpl))
        return out

    def uris(self):
        return [u for u in self.pre if self.role_code(u)]


def consep(rc):
    """role 코드 끝자리 5 = 별도, 0 = 연결 (DART 관행)."""
    return "별도" if rc and rc[-1] == "5" else "연결"
