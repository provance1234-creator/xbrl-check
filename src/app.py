"""
배포용 진입점: 파일 하나를 골라 적재 → 점검 → 리포트까지 한 번에 처리한다.

사용법:
    python -m src.app                      # 파일 선택 창
    python -m src.app <파일.ixd | 원문XBRL폴더>   # 지정 파일 처리 (exe에 파일을 끌어다 놓아도 됨)

결과: 입력 파일과 같은 폴더에 점검_<회사>_<기준일>.html / .csv 를 만들고 html을 연다.
"""

import os
import re
import sys
import tempfile
from pathlib import Path


def company_name(src, fallback):
    """파일명 '…_<고유번호>_<회사명>.v.1.0….ixd' 에서 회사명을 뽑는다."""
    m = re.search(r"_\d{6,8}_(.+?)(?:\.v\.\d|\.ixd|$)", Path(src).name)
    return m.group(1) if m else fallback


def pick_source(root):
    from tkinter import filedialog
    root.attributes("-topmost", True)
    return filedialog.askopenfilename(parent=root, title="점검할 XBRL 파일(.ixd)을 선택하세요",
                                      filetypes=[("XBRL 작성 파일", "*.ixd"), ("모든 파일", "*.*")])


BAD_INPUT = ("제출용 XBRL 파일(.ixd)이 아닙니다.\n\n"
             "DART XBRL 작성기에서 만든 제출용 ixd 파일을 선택해 주세요.")


def check_input(src):
    """제출용 ixd(또는 원문XBRL 폴더)인지 미리 본다. 아니면 안내 문구, 맞으면 None."""
    import zipfile
    p = Path(src)
    bad = f"{BAD_INPUT}\n\n선택한 파일: {p.name}"
    if not p.exists():
        return f"파일을 찾을 수 없습니다.\n\n{p}"
    if p.is_dir():
        names = [f.name.lower() for f in p.rglob("*")]
        ok = any(n.endswith(".xbrl") for n in names) and any(n.startswith("entity") and n.endswith(".xsd") for n in names)
        return None if ok else bad
    if p.suffix.lower() != ".ixd" or not zipfile.is_zipfile(p):
        return bad
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
    except Exception:
        return bad
    has_inst = any(n.startswith("EntityTaxonomy/") and n.lower().endswith(".xbrl") for n in names)
    return None if ("meta.xml" in names or has_inst) else bad


def show_error(msg, root=None):
    print(msg)
    try:
        from tkinter import messagebox
        if root is None:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        root.attributes("-topmost", True)   # 숨은 부모 창 때문에 안내 창이 다른 창 뒤에 뜨지 않게
        messagebox.showerror("점검 오류", msg, parent=root)
    except Exception:
        pass


def run(src, progress=None):
    """progress(비율 0~1, 안내 문구) — 진행 창 갱신용(없으면 무시)."""
    say = progress or (lambda *_: None)
    say(0.01, "프로그램을 준비하는 중입니다…")
    from .ixd.parse import load
    from .compliance.common import Std, Report, disp_title
    from .compliance.checks import run_all
    from .compliance.report import write_html
    from .compliance.run import print_summary, write_csv

    src = Path(src)
    with tempfile.TemporaryDirectory(prefix="xbrlchk_") as tmp:
        db = Path(tmp) / "sig.db"
        say(0.02, "파일을 읽는 중입니다…")
        try:
            ent, period, _ = load(src, corp_name=company_name(src, None), db_path=db)
        except Exception as e:   # 구조가 다른 파일(작성 중 파일·다른 프로그램 파일 등)
            raise SystemExit(f"{BAD_INPUT}\n\n선택한 파일: {src.name}\n(세부: {type(e).__name__}: {e})")
        say(0.12, "표준 택사노미를 불러오는 중입니다…")
        std = Std()
        r = Report(ent, period, path=db, std=std)
        # 점검 단계가 전체 시간의 대부분 → 15%~92% 구간
        results = run_all(r, std, progress=lambda i, n, card: say(
            0.15 + 0.77 * i / n, f"점검 중 ({i + 1}/{n}) {disp_title(card)}"))
        print_summary(r, results)
        say(0.94, "결과 파일을 만드는 중입니다…")
        out = (src if src.is_dir() else src.parent)
        stem = f"점검_{r.corp_name}_{r.period.replace('-', '')}"
        html, csv_ = out / f"{stem}.html", out / f"{stem}.csv"
        write_html(r, results, html)
        write_csv(r, results, csv_)
    say(1.0, "완료 — 결과를 엽니다.")
    print(f"\n→ {html}")
    return html


def run_with_window(root, src):
    """점검은 별도 스레드, 창(진행바)은 메인 스레드. 끝나면 결과 html 경로 또는 오류 문구를 돌려준다."""
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk

    q = queue.Queue()
    box = {}

    def work():
        try:
            box["html"] = run(src, progress=lambda f, msg: q.put((f, msg)))
        except SystemExit as e:
            box["err"] = str(e)
        except Exception as e:
            box["err"] = f"점검 중 오류가 발생했습니다.\n{type(e).__name__}: {e}"
        q.put(None)

    root.title("XBRL 작성기준 점검")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)   # 점검 도중 닫기 막음(임시 파일 정리 보장)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=Path(src).name, font=("맑은 고딕", 9, "bold"), wraplength=420).pack(anchor="w")
    msg = tk.StringVar(value="점검을 준비하는 중입니다…")
    ttk.Label(frm, textvariable=msg, width=60, wraplength=420).pack(anchor="w", pady=(8, 6))
    bar = ttk.Progressbar(frm, length=420, mode="determinate", maximum=100)
    bar.pack(fill="x")
    pct = tk.StringVar(value="0%")
    ttk.Label(frm, textvariable=pct).pack(anchor="e", pady=(4, 0))
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.attributes("-topmost", True)
    root.deiconify()
    root.after(300, lambda: root.attributes("-topmost", False))

    def poll():
        done = False
        try:
            while True:
                item = q.get_nowait()
                if item is None:
                    done = True
                    break
                f, text = item
                bar["value"] = f * 100
                pct.set(f"{int(f * 100)}%")
                msg.set(text)
        except queue.Empty:
            pass
        if done:
            root.quit()
        else:
            root.after(80, poll)

    threading.Thread(target=work, daemon=True).start()
    root.after(80, poll)
    root.mainloop()
    root.withdraw()
    return box.get("html"), box.get("err")


def main():
    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        dropped = len(sys.argv) > 1
        while True:
            src = sys.argv[1] if dropped else pick_source(root)
            if not src:
                return
            bad = check_input(src)
            if not bad:
                break
            show_error(bad, root)
            if dropped:          # 끌어다 놓은 파일이면 안내만 하고 끝
                return           # 파일 선택창으로 고른 경우는 다시 고르게 한다
        html, err = run_with_window(root, src)
        if err:
            show_error(err, root)
            return
        if os.name == "nt":
            os.startfile(html)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
