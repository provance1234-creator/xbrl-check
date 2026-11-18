# XBRL 작성기준 점검 도구


DART XBRL 작성기로 만든 `.ixd` 파일을 읽어, 금융감독원 「DART XBRL 재무제표 본문·주석 작성 가이드(2026)」와 표준 택사노미가 정한 **작성 구조를 지켰는지**를 점검하고, HTML·CSV 보고서를 만듭니다.

숫자(금액)의 정확성이 아니라 **표·축·멤버·행을 어떻게 구성했는지**를 봅니다.

## 사용법

1. 릴리스 첨부파일 `XBRL_check.zip`을 내려받습니다.
2. **압축을 풉니다**(파일 오른쪽 클릭 → 모두 압축 풀기). zip 안에서 바로 실행하면 필요한 파일이 없어 오류가 납니다.
3. 풀린 `XBRL_check` 폴더 안의 `XBRL_check.exe`를 실행해 파일을 고르거나, 점검할 `.ixd` 파일을 `XBRL_check.exe` 위로 끌어다 놓습니다.
4. `.ixd` 파일과 같은 폴더에 `점검_<회사명>_<기준일>.html`과 `.csv`가 만들어지고, 보고서가 브라우저로 열립니다.

폴더 안의 다른 파일(DLL, `data`, `rules` 등)은 실행에 필요하니 지우거나 옮기지 마세요. 폴더째 원하는 곳에 두고, 바탕화면에는 바로가기를 만들어 쓰면 편합니다.

`.ixd` 파일은 읽기만 하며 고치지 않습니다.

## 다른 회사 공시 XBRL 점검하기

DART에 공시된 다른 회사의 XBRL도 점검할 수 있습니다. 현재 버전은 **압축을 푼 폴더**로만 받습니다(zip을 바로 넣는 기능은 다음 버전에 추가 예정).

1. [DART](https://dart.fss.or.kr)에서 회사를 검색하고, 공시 목록의 보고서명 옆 **XBRL Viewer** 단추를 누릅니다.
2. XBRL 뷰어 오른쪽 위의 **다운로드**를 누릅니다.
3. 파일 목록에서 **`[회사명]보고서명_IFRS(원문XBRL)(날짜).zip`** 을 내려받습니다(`_재무제표(…)_ko.xlsx`는 필요 없음).
4. 내려받은 zip의 압축을 풀고, 풀린 **폴더**를 `XBRL_check.exe` 위로 끌어다 놓습니다(파일 선택창으로는 폴더를 고를 수 없습니다).
5. 그 폴더 안에 `점검_<회사명>_<기준일>.html`과 `.csv`가 만들어집니다.

공시된 XBRL에는 작성기 정보(주석 번호·제목 등)가 없어 일부 안내가 제출용 ixd를 점검할 때보다 덜 자세할 수 있습니다.

## 실행 시 경고가 뜨면

- **Windows SmartScreen** "Windows의 PC 보호" 창이 뜨면, [추가 정보] → [실행]을 누르세요. 코드 서명을 하지 않은 무료 프로그램이라 나오는 안내이며, 파일 자체에는 문제가 없습니다.
- Microsoft Defender 등 일부 백신이 이런 형식의 실행 파일을 오진할 수 있습니다(아래 "보안·바이러스 확인").

## 보안·바이러스 확인

이 프로그램이 안전한지 직접 확인하실 수 있습니다.

- **VirusTotal 검사 결과**: 전 세계 여러 백신으로 검사한 공개 결과입니다. 누구나 아래에서 직접 확인할 수 있습니다.
  - 내려받는 `XBRL_check.zip`: https://www.virustotal.com/gui/file/a42d3b087bc39a74762db6960ebb84019509aef0946f81f043c2515e772b7ed0 (탐지 없음)
  - 압축을 푼 `XBRL_check.exe`: https://www.virustotal.com/gui/file/66f36948aad66cbd298e26d0118d4e68580181afc6aabe28d6a4f58d151d4d53
  - 실행 파일은 Microsoft Defender가 `Trojan:Win32/Wacatac.B!ml`로 탐지할 수 있습니다. 끝의 `!ml`은 **기계학습 추정**이라는 표시로, 파이썬을 변환해 만든 실행 파일에서 자주 나오는 오진입니다. 안랩 V3를 비롯한 나머지 백신은 "탐지 없음"입니다.
  - Defender가 파일을 격리하면 Windows 보안 → 바이러스 및 위협 방지 → 보호 기록에서 "허용"하거나, 소스에서 직접 빌드해 쓰실 수 있습니다(아래 "소스에서 실행·빌드").
- **파일 지문(SHA-256)**: 내려받은 `XBRL_check.zip`이 위변조 없이 원본 그대로인지 아래 값과 대조하세요.
  - `a42d3b087bc39a74762db6960ebb84019509aef0946f81f043c2515e772b7ed0`
  - 확인 방법(PowerShell): `Get-FileHash XBRL_check.zip -Algorithm SHA256`
  - 압축을 푼 `XBRL_check.exe`: `66f36948aad66cbd298e26d0118d4e68580181afc6aabe28d6a4f58d151d4d53`
  - 위 값과 다르면 받는 중 손상됐거나 변조된 파일이니 쓰지 마세요.

> 참고: 이 프로그램은 파이썬을 기계어로 변환(Nuitka)해 만들었습니다. 이런 형식의 실행 파일을 일부 백신이 드물게 오진하는 경우가 있는데, 바이러스가 아니라 형식을 보고 의심하는 것입니다. 위 VirusTotal 결과와 공개된 소스로 직접 확인하실 수 있습니다.

## 보고서 보는 법

검출된 항목은 6단계로 나뉩니다. 이 단계는 보고서를 먼저 볼 순서를 정하려고 **작성 가이드와 금융감독원 설명회 교재에 적힌 내용을 근거로** 나눈 것이며, **금융감독원의 공식 판단이 아닙니다.**

- 근거 쪽수(p)는 금융감독원 「DART XBRL 재무제표 본문·주석 작성 가이드(2026)」의 인쇄 쪽 번호입니다.

## 소스에서 실행·빌드

이 저장소에는 exe를 만드는 소스가 모두 들어 있습니다(Python 3.13 이상, Windows).

```
pip install -r requirements.txt
```

1. 릴리스 첨부파일 `std_taxonomy.db.zip`(DART 표준 택사노미를 가공한 DB)을 내려받아 압축을 풀고 `data/std_taxonomy.db`로 둡니다.
2. 실행: `python run_app.py` (파일 선택창) 또는 `python run_app.py <파일.ixd>`
3. exe 만들기(Nuitka):

```
pip install nuitka pefile ordered-set zstandard
python -m nuitka --standalone --windows-console-mode=disable --enable-plugin=tk-inter ^
  --experimental=force-dependencies-pefile ^
  --nofollow-import-to=ssl,_ssl,socket,_socket,hashlib,_hashlib,bz2,_bz2,lzma,_lzma,compression.zstd,_zstd,decimal,_decimal,_pydecimal,unicodedata,email,http,urllib.request,lxml.objectify,lxml.sax,lxml.builder,_wmi ^
  --include-data-files=data/std_taxonomy.db=data/std_taxonomy.db ^
  --include-data-files=rules/guide_axis_members.csv=rules/guide_axis_members.csv ^
  --include-data-files=rules/guide_examples.csv=rules/guide_examples.csv ^
  --windows-product-name="XBRL Check" --windows-file-version=1.0.0.0 --windows-product-version=1.0.0.0 ^
  --output-filename=XBRL_check.exe run_app.py
```

빌드 결과 `run_app.dist` 폴더가 배포본입니다(폴더 이름을 `XBRL_check`로 바꿔 zip으로 묶음).

### 구성

| 경로 | 내용 |
|---|---|
| `src/ixd/parse.py` | `.ixd`(zip)를 읽어 표·축·멤버·행 구조를 임시 DB로 정리 |
| `src/compliance/checks.py` | 기본 점검과 단계(1~6단계) 분류 |
| `src/compliance/guide_checks.py` | 작성 가이드 규칙별 점검(함수 하나 = 규칙 하나) |
| `src/compliance/examples.py` | 가이드 모범사례에 나온 구조는 허용 |
| `src/compliance/report.py` | HTML 보고서 |
| `rules/` | 가이드 모범사례의 축·멤버·요소 목록(쪽 번호) |

## 라이선스·책임

- [PolyForm Noncommercial 1.0.0](LICENSE.md) — **비상업적 목적**이면 누구나 보고, 고치고, 다시 배포할 수 있습니다. 판매하거나 유료 제품·서비스에 넣는 등 **상업적 이용은 허용하지 않습니다.**
- 점검 결과는 참고용이며, 작성·공시의 최종 책임은 사용자에게 있습니다. 결과의 정확성을 보증하지 않으며, 사용으로 생긴 문제에 대해 제작자는 책임지지 않습니다.
- 문의·오류 제보는 이 저장소의 Issues에 남겨 주세요.
