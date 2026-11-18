"""
XBRL 구조 시그니처 추출 → SQLite(signatures.db).

입력 두 종류를 같은 스키마로 적재한다.
    - 금감원 작성기 저장파일 .ixd (zip: meta.xml + EntityTaxonomy/*)  → 자사
    - DART 원문XBRL 폴더 (entityXXX_YYYY-MM-DD.xbrl/.xsd/_pre/_cal/_def/_lab-ko)

사용법:
    python -m src.ixd.parse <ixd파일 또는 원문XBRL폴더> [--name 회사명]

같은 entity+period를 다시 적재하면 기존 행을 지우고 덮어쓴다.
파싱은 lxml만 사용한다(Arelle 미사용). 파서 골격은 xbrl_report_tool/generate_report.py에서 가져왔다.
"""

import re
import csv
import sys
import json
import zipfile
import sqlite3
import argparse
import tempfile
import shutil
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "signatures.db"

XLINK = "{http://www.w3.org/1999/xlink}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSD = "{http://www.w3.org/2001/XMLSchema}"
XBRLI_ATTR = "{http://www.xbrl.org/2003/instance}"
LABEL_ROLE_STANDARD = "http://www.xbrl.org/2003/role/label"

ROLE_CODE_RE = re.compile(r"role-([A-Z]*\d+)$")
STD_ROLE_RE = re.compile(r"^https?://(dart\.fss\.or\.kr/role/ifrs|xbrl\.ifrs\.org/role)")

parser = etree.XMLParser(huge_tree=True, remove_blank_text=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(entity TEXT, period TEXT, corp_name TEXT, source TEXT,
    ext_prefix TEXT, ext_ns TEXT, src_path TEXT, PRIMARY KEY(entity, period));
CREATE TABLE IF NOT EXISTS roles(entity TEXT, period TEXT, role_uri TEXT, role_code TEXT,
    definition TEXT, is_std INT);
CREATE TABLE IF NOT EXISTS facts(entity TEXT, period TEXT, prefix TEXT, name TEXT, is_ext INT,
    value TEXT, is_neg INT, decimals TEXT, unit TEXT, ctx_id TEXT, is_nil INT);
CREATE TABLE IF NOT EXISTS contexts(entity TEXT, period TEXT, ctx_id TEXT, period_type TEXT,
    start_date TEXT, end_date TEXT, instant TEXT, dims TEXT);
CREATE TABLE IF NOT EXISTS pre_arcs(entity TEXT, period TEXT, role_uri TEXT, role_code TEXT,
    parent TEXT, child TEXT, ord REAL, pref_label TEXT);
CREATE TABLE IF NOT EXISTS cal_arcs(entity TEXT, period TEXT, role_uri TEXT, role_code TEXT,
    parent TEXT, child TEXT, ord REAL, weight REAL);
CREATE TABLE IF NOT EXISTS def_arcs(entity TEXT, period TEXT, role_uri TEXT, role_code TEXT,
    arcrole TEXT, parent TEXT, child TEXT, ord REAL, target_role TEXT, closed TEXT,
    context_element TEXT, usable TEXT);
CREATE TABLE IF NOT EXISTS ext_elems(entity TEXT, period TEXT, prefix TEXT, name TEXT,
    data_type TEXT, substitution_group TEXT, balance TEXT, period_type TEXT, abstract TEXT);
CREATE TABLE IF NOT EXISTS labels(entity TEXT, period TEXT, prefix TEXT, name TEXT, lang TEXT,
    label_role TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS meta_metrics(entity TEXT, period TEXT, key TEXT, role_code TEXT,
    value TEXT);
CREATE TABLE IF NOT EXISTS doc_tables(entity TEXT, period TEXT, aclass TEXT, title TEXT);
CREATE INDEX IF NOT EXISTS ix_facts ON facts(entity, prefix, name);
CREATE INDEX IF NOT EXISTS ix_pre ON pre_arcs(entity, role_code);
CREATE INDEX IF NOT EXISTS ix_def ON def_arcs(entity, role_code);
CREATE INDEX IF NOT EXISTS ix_lab ON labels(entity, prefix, name);
"""
DATA_TABLES = ["roles", "facts", "contexts", "pre_arcs", "cal_arcs", "def_arcs", "ext_elems",
               "labels", "meta_metrics", "doc_tables"]


# ---------------------------------------------------------------------------
# 파일 찾기
# ---------------------------------------------------------------------------

def locate_files(base_dir):
    """원문XBRL 폴더 또는 ixd 해제 폴더에서 표준 XBRL 파일들을 찾는다."""
    meta_path = base_dir / "meta.xml"
    if meta_path.exists():
        meta = etree.parse(str(meta_path), parser)
        files = {}
        for child in meta.find("XBRL"):
            fn = child.get("filename")
            if fn:
                files[etree.QName(child.tag).localname] = base_dir / fn.replace("\\", "/")
        return files, meta
    files = {}
    for f in base_dir.rglob("*"):
        n = f.name.lower()
        if n.endswith(".xbrl"):
            files["INSTANCE"] = f
        elif n.endswith("_pre.xml"):
            files["PRESENTATION"] = f
        elif n.endswith("_cal.xml"):
            files["CALCULATION"] = f
        elif n.endswith("_def.xml"):
            files["DEFINITION"] = f
        elif n.endswith("_lab-ko.xml"):
            files["LABEL_KO"] = f
        elif n.endswith("_lab-en.xml"):
            files["LABEL_EN"] = f
        elif n.endswith(".xsd") and n.startswith("entity"):
            files["SCHEMA"] = f
    return files, None


# ---------------------------------------------------------------------------
# 개별 파서
# ---------------------------------------------------------------------------

def split_concept(href, prefixes):
    """loc href의 fragment(prefix_LocalName)를 (prefix, local)로 나눈다. prefix 긴 것부터 대조."""
    frag = href.rsplit("#", 1)[-1]
    for p in prefixes:
        if frag.startswith(p + "_"):
            return p, frag[len(p) + 1:]
    return None, frag


def role_code(uri):
    m = ROLE_CODE_RE.search(uri or "")
    return m.group(1) if m else None


def parse_instance(path):
    root = etree.parse(str(path), parser).getroot()
    nsmap = {k: v for k, v in root.nsmap.items() if k}
    uri2prefix = {v: k for k, v in nsmap.items()}

    contexts = []
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        p = ctx.find(f"{{{XBRLI}}}period")
        inst = p.findtext(f"{{{XBRLI}}}instant")
        start = p.findtext(f"{{{XBRLI}}}startDate")
        end = p.findtext(f"{{{XBRLI}}}endDate")
        dims = []
        for em in ctx.iter(f"{{{XBRLDI}}}explicitMember"):
            dims.append([em.get("dimension"), (em.text or "").strip()])
        for tm in ctx.iter(f"{{{XBRLDI}}}typedMember"):
            dims.append([tm.get("dimension"), "typed:" + "".join(tm.itertext()).strip()])
        contexts.append([ctx.get("id"), "instant" if inst else "duration", start, end, inst,
                         json.dumps(dims, ensure_ascii=False)])

    units = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        units[u.get("id")] = " ".join(m.text.strip() for m in u.iter(f"{{{XBRLI}}}measure"))

    facts = []
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        q = etree.QName(el.tag)
        val = (el.text or "").strip() if len(el) == 0 else etree.tostring(el, encoding="unicode")
        is_nil = el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        facts.append([uri2prefix.get(q.namespace, q.namespace), q.localname, val,
                      int(val.startswith("-")), el.get("decimals"),
                      units.get(el.get("unitRef"), el.get("unitRef")), el.get("contextRef"),
                      int(is_nil)])
    return nsmap, contexts, facts


def detect_ext(nsmap):
    for p, u in nsmap.items():
        if re.search(r"/entity\d{6,}", u):
            return p, u
    raise RuntimeError("회사 확장 네임스페이스(entityXXXX)를 찾지 못했습니다.")


def parse_schema(path, ext_prefix):
    root = etree.parse(str(path), parser).getroot()
    roles = []
    for rt in root.iter(f"{LINK}roleType"):
        uri = rt.get("roleURI")
        roles.append([uri, role_code(uri), (rt.findtext(f"{LINK}definition") or "").strip(),
                      int(bool(STD_ROLE_RE.match(uri)))])
    elems = []
    for el in root.iter(f"{XSD}element"):
        if el.get("name"):
            elems.append([ext_prefix, el.get("name"), el.get("type"), el.get("substitutionGroup"),
                          el.get(f"{XBRLI_ATTR}balance"), el.get(f"{XBRLI_ATTR}periodType"),
                          el.get("abstract")])
    return roles, elems


def iter_links(path, link_tag):
    root = etree.parse(str(path), parser).getroot()
    for link in root.iter(f"{LINK}{link_tag}"):
        yield link.get(f"{XLINK}role"), link


def loc_map(link, prefixes):
    m = {}
    for loc in link.findall(f"{LINK}loc"):
        p, local = split_concept(loc.get(f"{XLINK}href"), prefixes)
        m[loc.get(f"{XLINK}label")] = f"{p}:{local}" if p else local
    return m


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_pre(path, prefixes):
    rows = []
    for role, link in iter_links(path, "presentationLink"):
        lm = loc_map(link, prefixes)
        for a in link.findall(f"{LINK}presentationArc"):
            rows.append([role, role_code(role), lm.get(a.get(f"{XLINK}from")),
                         lm.get(a.get(f"{XLINK}to")), fnum(a.get("order")),
                         a.get("preferredLabel")])
    return rows


def parse_cal(path, prefixes):
    rows = []
    for role, link in iter_links(path, "calculationLink"):
        lm = loc_map(link, prefixes)
        for a in link.findall(f"{LINK}calculationArc"):
            rows.append([role, role_code(role), lm.get(a.get(f"{XLINK}from")),
                         lm.get(a.get(f"{XLINK}to")), fnum(a.get("order")),
                         fnum(a.get("weight"))])
    return rows


def parse_def(path, prefixes):
    xbrldt = "{http://xbrl.org/2005/xbrldt}"
    rows = []
    for role, link in iter_links(path, "definitionLink"):
        lm = loc_map(link, prefixes)
        for a in link.findall(f"{LINK}definitionArc"):
            rows.append([role, role_code(role), a.get(f"{XLINK}arcrole").rsplit("/", 1)[-1],
                         lm.get(a.get(f"{XLINK}from")), lm.get(a.get(f"{XLINK}to")),
                         fnum(a.get("order")), a.get(f"{xbrldt}targetRole"),
                         a.get(f"{xbrldt}closed"), a.get(f"{xbrldt}contextElement"),
                         a.get(f"{xbrldt}usable")])
    return rows


def parse_labels(path, prefixes, lang):
    rows = []
    for _role, link in iter_links(path, "labelLink"):
        lm = loc_map(link, prefixes)
        res = {}
        for lab in link.findall(f"{LINK}label"):
            res.setdefault(lab.get(f"{XLINK}label"), []).append(
                (lab.get(f"{XLINK}role"), "".join(lab.itertext()).strip()))
        for a in link.findall(f"{LINK}labelArc"):
            concept = lm.get(a.get(f"{XLINK}from"))
            for role, text in res.get(a.get(f"{XLINK}to"), []):
                p, _, local = concept.partition(":")
                rows.append([p, local, lang, role.rsplit("/", 1)[-1], text])
    return rows


def parse_meta(meta):
    rows = []
    node = meta.find("DARTCOA_RATE")
    if node is not None:
        rows.append(["DARTCOA_RATE_TOTAL", None, node.get("Total")])
        for r in node.findall("RATE"):
            rows.append(["DARTCOA_RATE", r.get("RoleNumber"), r.text])
    v = meta.find("XBRL_VALIDATE")
    if v is not None:
        for k, val in v.attrib.items():
            rows.append([f"XBRL_VALIDATE.{k}", None, val])
    return rows


def parse_doc_tables(base_dir, meta):
    """작성기 본문 문서(X00083.xml 류)의 표 그룹 ACLASS·제목 = 감사보고서 주석 번호/제목."""
    doc = meta.find("XBRLDOCUMENT") if meta is not None else None
    fn = doc.get("FileName") if doc is not None else None
    if not fn or not (base_dir / fn).exists():
        return []
    txt = (base_dir / fn).read_text(encoding="utf-8", errors="ignore")
    return [[a, t.strip()] for a, t in re.findall(
        r'<TABLE-GROUP ACLASS="\{XBRL\}([^"]*)"[^>]*>\s*<TITLE[^>]*>([^<]*)</TITLE>', txt)]


# ---------------------------------------------------------------------------
# 적재
# ---------------------------------------------------------------------------

def load(src, corp_name=None, source="self", db_path=DEFAULT_DB):
    src = Path(src).resolve()
    tmp = None
    if src.is_file() and src.suffix.lower() == ".ixd":
        tmp = Path(tempfile.mkdtemp(prefix="ixd_"))
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist()
                     if n == "meta.xml" or n.startswith("EntityTaxonomy/") or n.startswith("X0")]
            z.extractall(tmp, names)
        base = tmp
    else:
        base = src
    try:
        files, meta = locate_files(base)
        nsmap, contexts, facts = parse_instance(files["INSTANCE"])
        ext_prefix, ext_ns = detect_ext(nsmap)
        prefixes = sorted(nsmap.keys(), key=len, reverse=True)
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.xbrl$", files["INSTANCE"].name)
        period = m.group(1) if m else ""
        if not corp_name:   # 파일명에 회사명이 없으면 인스턴스의 회사명(dart-gcd:EntityRegistrantName)
            names = [str(f[2]).strip() for f in facts
                     if f[1] == "EntityRegistrantName" and f[2] and str(f[2]).strip()]
            # 국문·영문이 함께 있으므로 한글이 든 이름 우선
            corp_name = next((n for n in names if re.search("[가-힣]", n)), names[0] if names else None)
            if corp_name:
                corp_name = re.sub(r'[\\/:*?"<>|]', "", corp_name)
        roles, ext_elems = parse_schema(files["SCHEMA"], ext_prefix)
        data = {
            "roles": roles,
            "facts": [f[:2] + [int(f[0] == ext_prefix)] + f[2:] for f in facts],
            "contexts": contexts,
            "pre_arcs": parse_pre(files["PRESENTATION"], prefixes),
            "cal_arcs": parse_cal(files["CALCULATION"], prefixes),
            "def_arcs": parse_def(files["DEFINITION"], prefixes),
            "ext_elems": ext_elems,
            "labels": parse_labels(files["LABEL_KO"], prefixes, "ko")
                      + (parse_labels(files["LABEL_EN"], prefixes, "en")
                         if "LABEL_EN" in files else []),
            "meta_metrics": parse_meta(meta) if meta is not None else [],
            "doc_tables": parse_doc_tables(base, meta),
        }
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    key = (ext_prefix, period)
    conn.execute("DELETE FROM entities WHERE entity=? AND period=?", key)
    for t in DATA_TABLES:
        conn.execute(f"DELETE FROM {t} WHERE entity=? AND period=?", key)
    conn.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?)",
                 [ext_prefix, period, corp_name or ext_prefix, source, ext_prefix, ext_ns,
                  str(src)])
    counts = {}
    for t, rows in data.items():
        if rows:
            ph = ",".join("?" * (len(rows[0]) + 2))
            conn.executemany(f"INSERT INTO {t} VALUES ({ph})", [list(key) + r for r in rows])
        counts[t] = len(rows)
    conn.commit()
    conn.close()
    return ext_prefix, period, counts


def main():
    ap = argparse.ArgumentParser(description="XBRL 구조 시그니처 적재")
    ap.add_argument("src", nargs="?", help=".ixd 파일 또는 원문XBRL 폴더")
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    if not args.src:
        ap.error("src를 지정하세요.")
    targets = [(args.src, args.name, "self")]

    for src, name, source in targets:
        ent, period, counts = load(src, name, source)
        summary = " ".join(f"{k}={v:,}" for k, v in counts.items())
        print(f"{name or ent} ({ent}, {period}): {summary}")


if __name__ == "__main__":
    main()
