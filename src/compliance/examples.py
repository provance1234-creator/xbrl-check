"""작성가이드 모범사례 허용 판정.

rules/guide_examples.csv(src.guide.extract_examples로 생성)의 쪽별 요소 집합을 읽어,
검출된 구조(요소 + 소속 표 + 짝 요소)가 한 모범사례 쪽에 함께 나오면 '모범사례 허용'으로 본다.
가이드 규칙 외에 모범사례로 보여 준 구조도 허용된다는 원칙에 따른 것이다. 같은 쪽 안의 조합만 본다.
"""

import csv
from collections import defaultdict

from .common import ROOT

PATH = ROOT / "rules" / "guide_examples.csv"
CONSEP_TABLE = "dart:ConsolidatedAndSeparateFinancialStatementsTable"

# 구조가 아니라 입력값·부호·기간·단위를 보는 점검 — 모범사례 구조 허용과 무관
VALUE_CHECKS = {
    "공시금액 입력값의 적정성", "공시금액 열에 문장 입력", "비용 요소 음수 입력", "외화 item의 통화 속성",
    "확장요소 기간 유형", "확장요소 차변/대변", "확장요소 영문명", "같은 이름 표준요소가 있는 확장요소",
}
VALUE_RULES = {"G4-021", "G4-023", "G4-024", "G5-006", "G5B-031", "G5B-053", "G5B-054", "G5B-057", "G5B-058",
               "G5B-062", "G5B-063", "G5B-064", "G5B-068", "G5C-010", "G5C-012", "G5C-018", "G5C-019",
               "G5C-043", "G5C-044", "G5C-047", "G5C-059", "G5C-064", "G5C-073", "G5C-079", "G5C-082"}


class Examples:
    def __init__(self, path=PATH):
        self.pages = defaultdict(set)     # 인쇄쪽 -> 요소
        if path.exists():
            with open(path, encoding="utf-8-sig") as fp:
                for r in csv.DictReader(fp):
                    self.pages[int(r["print_page"])].add(r["element"])

    def find(self, elems):
        """elems가 모두 함께 나오는 모범사례 인쇄쪽들. 같은 쪽 안에서만 본다 — 앞뒤 쪽을 묶으면
        서로 다른 예시(예: p239 리스 만기분석, p240 리스부채 유동/비유동)가 섞여 잘못 허용된다."""
        elems = set(elems)
        return [p for p in sorted(self.pages) if elems and elems <= self.pages[p]]


def structure_of(f, std):
    """검출 1건의 구조 요소(표준 요소만). 확장요소가 끼면 모범사례에 나올 수 없으므로 None."""
    elems = [f.element] + list(f.extra.get("pair", []))
    if f.table and not f.table.startswith("(") and f.table != CONSEP_TABLE:
        elems.append(f.table)
    if any(not std.is_std(q) for q in elems):
        return None
    # 요소 하나만으로는 '구조'가 아님(그 요소가 나오는 아무 쪽에서나 허용돼 버림) — 조합일 때만
    return elems if len(set(elems)) >= 2 else None


def apply(results, std):
    """모범사례로 허용되는 검출을 결과에서 빼서 res['allowed']에 [(finding, pages)]로 옮긴다."""
    ex = Examples()
    for res in results:
        card = res["card"]
        if card in VALUE_CHECKS or card.split(" ", 1)[0] in VALUE_RULES:
            continue
        keep, allowed = [], []
        for f in res["findings"]:
            elems = structure_of(f, std)
            pages = ex.find(elems) if elems else []
            if pages:
                f.extra["note"] = f"작성가이드 p{'·'.join(map(str, pages[:3]))} 모범사례에 같은 구조 — 허용"
                allowed.append((f, pages))
            else:
                keep.append(f)
        res["findings"], res["allowed"] = keep, allowed
    return results
