"""틀 검사 — 숫자를 넣기 전 작성기 틀(.ixd)의 표 구성(표·축·멤버·행)을 점검한다. ixd는 읽기만 한다.

    python -m src.compliance.frame <틀.ixd> <출력폴더>

결과: <출력폴더>/틀검사_<ixd이름>_<기준일>.html·.csv. 임시 DB를 쓰므로 data/signatures.db는 건드리지 않는다.
일반 점검과 같은 점검을 돌리되, 값이 없어서 생기는 검출은 빼고 입력값으로 판정하는 점검은 '값 입력 후 판정'으로 표시한다.
"""
import sys
import tempfile
from pathlib import Path

from ..ixd.parse import load
from .checks import run_all
from .common import Report, Std
from .report import write_html
from .run import print_summary, write_csv


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xbrlframe_") as tmp:
        db = Path(tmp) / "sig.db"
        ent, period, _ = load(src, corp_name=src.stem, db_path=db)
        std = Std()
        r = Report(ent, period, path=db, std=std)
        results = run_all(r, std, frame=True)
        print_summary(r, results)
        stem = f"틀검사_{src.stem}_{r.period.replace('-', '')}"   # 파일명에 '.'이 있을 수 있어 with_suffix 대신 직접 붙임
        write_html(r, results, outdir / f"{stem}.html", tool_name="XBRL 틀 검사")
        write_csv(r, results, outdir / f"{stem}.csv")
    print("결과:", outdir / f"{stem}.html")


if __name__ == "__main__":
    main()
