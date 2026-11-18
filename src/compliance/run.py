"""
XBRL 작성 적정성 검사 실행.

사용법:
    python -m src.compliance.run entity<고유번호>           # signatures.db에 적재된 보고서
    python -m src.compliance.run <파일.ixd | 원문XBRL폴더>   # 먼저 적재 후 검사
    python -m src.compliance.run --all                     # 적재된 전체

결과: 콘솔 요약 + report/점검_<회사>_<기준일>.html (+ 같은 이름 .csv)
"""

import csv
import sqlite3
import argparse
from pathlib import Path
from collections import defaultdict, OrderedDict

from .common import Std, Report, SIG_DB, ROOT, consep
from .checks import grade_label, grade_name, run_all
from .report import write_html, TITLE, AREA
from .common import disp_title

REPORT_DIR = ROOT / "report"


def summarize(results):
    secs = OrderedDict()
    for res in results:
        secs.setdefault(res["section"], []).append(res)
    return secs


def print_summary(r, results):
    print(f"\n■ {r.corp_name} ({r.entity}, {r.period})")
    no = 0
    for sec, cards in summarize(results).items():
        n = sum(len(c["findings"]) for c in cards)
        _, area = AREA.get(sec, ("", sec))
        print(f"  [{area}] — {n}건")
        for c in cards:
            no += 1
            k = len(c["findings"])
            mark = "  " if k == 0 else ("‼ " if any(f.level == "정정위험" for f in c["findings"]) else "✖ " if any(f.level in ("오류", "실행오류") for f in c["findings"]) else "◇ " if any(f.level == "개선" for f in c["findings"]) else "· " if all(f.level == "참고" for f in c["findings"]) else "△ ")
            nv = " (값 입력 후 판정)" if c.get("needs_values") and k == 0 else (" (값 입력분만)" if c.get("needs_values") else "")
            print(f"    {mark}{no:>2}. {disp_title(TITLE.get(c['card'], c['card']))}: {k}{nv}")


def write_csv(r, results, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["영역", "점검항목", "등급", "연결/별도", "role_code", "주석", "문장영역", "표",
                    "요소", "요소명", "내용", "근거강도", "비고", "check_id"])
        for no, res in enumerate(results, 1):
            for x in res["findings"]:
                _, area = AREA.get(x.section, ("", x.section))
                w.writerow([area, f"{no}. {disp_title(TITLE.get(res['card'], res['card']))}",
                            grade_label(x.level),
                            consep(x.role_code) if x.role_code[:1] in "DU" else "",
                            x.role_code, r.role_label(x.role_code), r.label(x.parent) if x.parent else "",
                            r.label(x.table) if x.table else "", x.element, r.label(x.element), x.detail,
                            x.extra.get("basis", ""), x.extra.get("note", ""), disp_title(x.check)])



def resolve(target, std):
    p = Path(target)
    if p.exists():
        from ..ixd.parse import load
        ent, period, _ = load(p)
        return Report(ent, period, std=std)
    return Report(target, std=std)


def main():
    ap = argparse.ArgumentParser(description="XBRL 작성 적정성 검사")
    ap.add_argument("target", nargs="?", help="entity 코드, .ixd 파일 또는 원문XBRL 폴더")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    std = Std()
    if args.all:
        c = sqlite3.connect(SIG_DB)
        reps = [Report(e, p, std=std) for e, p in c.execute("SELECT entity, period FROM entities ORDER BY source DESC, corp_name")]
    elif args.target:
        reps = [resolve(args.target, std)]
    else:
        ap.error("target 또는 --all")

    REPORT_DIR.mkdir(exist_ok=True)
    for r in reps:
        results = run_all(r, std)
        print_summary(r, results)
        stem = f"점검_{r.corp_name}_{r.period.replace('-', '')}"
        write_html(r, results, REPORT_DIR / f"{stem}.html")
        write_csv(r, results, REPORT_DIR / f"{stem}.csv")
        print(f"  → {REPORT_DIR / (stem + '.html')}")


if __name__ == "__main__":
    main()
