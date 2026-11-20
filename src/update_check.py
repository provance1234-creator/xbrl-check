"""
새 버전 확인: GitHub 최신 릴리스 번호만 물어보고, 새 버전이면 다운로드 페이지를 열지 묻는다.

- 점검 파일 내용은 어디로도 보내지 않는다. 보내는 것은 GitHub API 요청 한 번뿐이다.
- 파일을 내려받거나 자기 자신을 바꾸지 않는다(받기는 사용자가 브라우저에서).
- 인터넷 접속은 Windows 내장 WinHTTP(ctypes)로 한다 → 실행 파일에 OpenSSL·socket을 넣지 않아도 된다.
- 인터넷이 없거나 막혀 있거나 3초 안에 응답이 없으면 조용히 넘어간다.
- 끄기: 환경 변수 XBRL_CHECK_NO_UPDATE=1
"""

import json
import os
import re

VERSION = "1.02"
REPO = "provance1234-creator/xbrl-check"
API_URL_PATH = f"/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT_MS = 2000


def _winhttp_get(host, path):
    """HTTPS GET → 본문 bytes. 실패하면 None."""
    import ctypes
    from ctypes import wintypes as wt
    w = ctypes.WinDLL("winhttp", use_last_error=True)
    w.WinHttpOpen.restype = wt.LPVOID
    w.WinHttpOpen.argtypes = [wt.LPCWSTR, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    w.WinHttpConnect.restype = wt.LPVOID
    w.WinHttpConnect.argtypes = [wt.LPVOID, wt.LPCWSTR, wt.WORD, wt.DWORD]
    w.WinHttpOpenRequest.restype = wt.LPVOID
    w.WinHttpOpenRequest.argtypes = [wt.LPVOID, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
                                     wt.LPCWSTR, ctypes.c_void_p, wt.DWORD]
    w.WinHttpSetTimeouts.argtypes = [wt.LPVOID, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    w.WinHttpSendRequest.argtypes = [wt.LPVOID, wt.LPCWSTR, wt.DWORD, wt.LPVOID, wt.DWORD,
                                     wt.DWORD, ctypes.c_size_t]
    w.WinHttpReceiveResponse.argtypes = [wt.LPVOID, wt.LPVOID]
    w.WinHttpQueryHeaders.argtypes = [wt.LPVOID, wt.DWORD, wt.LPCWSTR, wt.LPVOID,
                                      ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]
    w.WinHttpReadData.argtypes = [wt.LPVOID, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    w.WinHttpCloseHandle.argtypes = [wt.LPVOID]

    ACCESS_AUTOMATIC_PROXY = 4          # Windows 8.1 이상: 시스템 프록시 설정 사용
    ACCESS_DEFAULT_PROXY = 0
    FLAG_SECURE = 0x00800000
    QUERY_STATUS_CODE = 19
    QUERY_FLAG_NUMBER = 0x20000000

    handles = []
    try:
        agent = f"XBRL-Check/{VERSION}"
        s = w.WinHttpOpen(agent, ACCESS_AUTOMATIC_PROXY, None, None, 0) \
            or w.WinHttpOpen(agent, ACCESS_DEFAULT_PROXY, None, None, 0)
        if not s:
            return None
        handles.append(s)
        w.WinHttpSetTimeouts(s, TIMEOUT_MS, TIMEOUT_MS, TIMEOUT_MS, TIMEOUT_MS)
        c = w.WinHttpConnect(s, host, 443, 0)
        if not c:
            return None
        handles.append(c)
        r = w.WinHttpOpenRequest(c, "GET", path, None, None, None, FLAG_SECURE)
        if not r:
            return None
        handles.append(r)
        hdr = "Accept: application/vnd.github+json\r\n"
        if not w.WinHttpSendRequest(r, hdr, len(hdr), None, 0, 0, 0):
            return None
        if not w.WinHttpReceiveResponse(r, None):
            return None
        status = wt.DWORD(0)
        size = wt.DWORD(ctypes.sizeof(status))
        if not w.WinHttpQueryHeaders(r, QUERY_STATUS_CODE | QUERY_FLAG_NUMBER, None,
                                     ctypes.byref(status), ctypes.byref(size), None):
            return None
        if status.value != 200:
            return None
        out = bytearray()
        buf = ctypes.create_string_buffer(8192)
        while True:
            n = wt.DWORD(0)
            if not w.WinHttpReadData(r, buf, len(buf), ctypes.byref(n)):
                return None
            if n.value == 0:
                break
            out += buf.raw[:n.value]
            if len(out) > 1_000_000:
                break
        return bytes(out)
    except Exception:
        return None
    finally:
        for h in reversed(handles):
            try:
                w.WinHttpCloseHandle(h)
            except Exception:
                pass


def _num(v):
    """'v1.02' → 1.02. 버전은 1.0 → 1.01 → 1.02 … 처럼 소수로 비교한다."""
    m = re.search(r"\d+(?:\.\d+)?", v or "")
    return float(m.group(0)) if m else None


def latest_version():
    """GitHub 최신 릴리스 태그(예: 'v1.02'). 확인 못 하면 None."""
    if os.name != "nt" or os.environ.get("XBRL_CHECK_NO_UPDATE"):
        return None
    body = _winhttp_get("api.github.com", API_URL_PATH)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8")).get("tag_name")
    except Exception:
        return None


def check_and_offer(root=None):
    """새 버전이 있으면 다운로드 페이지를 열지 묻는다. 페이지를 열었으면 True(호출한 쪽에서 종료)."""
    tag = latest_version()
    cur, new = _num(VERSION), _num(tag)
    if cur is None or new is None or new <= cur:
        return False
    try:
        from tkinter import messagebox
        if root is not None:
            root.attributes("-topmost", True)
        ok = messagebox.askyesno(
            "새 버전 안내",
            f"새 버전 {tag}이(가) 나왔습니다. (지금 버전: v{VERSION})\n\n"
            "다운로드 페이지를 열까요?\n"
            "'아니요'를 누르면 지금 버전으로 계속 점검합니다.",
            parent=root)
    except Exception:
        return False
    if ok:
        import webbrowser
        webbrowser.open(RELEASES_PAGE)
        return True
    return False
