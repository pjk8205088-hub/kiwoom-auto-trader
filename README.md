# 키움 자동매매

Windows EXE로 빌드 가능한 주식 자동매매 MVP입니다.

기본 주문 실행은 모의 모드입니다. 실계좌 매수/매도 주문은 아직 연결하지
않도록 잠금이 기본 적용되어 있으며, 키움 OpenAPI+ 로그인 후 모의투자 서버에서
조회와 시장가 주문 요청을 테스트할 수 있습니다.

## 포함 기능

- 한글 Tkinter 데스크톱 대시보드
- 키움 OpenAPI+ 계좌 연결 버튼
- 키움 공식 OpenAPI+ 설치파일 다운로드 버튼
- 로그인 오류코드 안내 및 모의투자/실거래 서버 구분 표시
- 로그인 후 계좌번호를 읽기 전용 `앞 4자리 - 뒤 4자리` 칸에 자동 표시
- 키움 현재가 조회 `opt10001`
- 키움 3분봉 조회 `opt10080`
- 키움 계좌 잔고 조회 `opw00018`
- 키움 실시간 시세 등록 `SetRealReg`
- 종목번호 입력 시 회사명 자동 조회 `GetMasterCodeName`
- 실제 3분봉 데이터를 전략 엔진에 연결하는 판단 버튼
- 전략 판단 결과를 모의투자 서버용 시장가 주문 `SendOrder`에 연결
- 회사명과 계좌가 확인된 뒤 활성화되는 수동 매수/매도 버튼
- 실거래 주문 잠금 및 확인창
- 공식 TR 제한(초당 5건, 분당 100건, 시간당 1,000건) 자동 대기
- CCI 계산 및 강세 패턴 전환 기반 전략 엔진
- 종목별 운용 한도 위험관리
- 모의 브로커, 주문 관리자, 매도 재시도 흐름
- SQLite 주문/로그 저장
- 단위 테스트
- Windows EXE 빌드 스크립트
- GitHub Actions Windows EXE 빌드 워크플로

## 키움 계좌 연결

`키움 계좌 연결` 버튼은 키움 OpenAPI+ ActiveX의 `CommConnect()`로 로그인 창을
띄운 뒤 `GetLoginInfo("ACCNO")`로 계좌 목록을 조회합니다.
화면에는 계좌번호 앞 4자리와 뒤 4자리만 표시하고, 내부 조회와 주문 요청에는
키움에서 받은 원본 계좌번호를 사용합니다.

공식 페이지:

- https://www1.kiwoom.com/h/main
- https://www.kiwoom.com/h/customer/download/VOpenApiInfoView
- https://download.kiwoom.com/web/openapi/OpenAPISetup.exe

로그인 창의 `키움 홈페이지 열기` 버튼은 위 공식 홈페이지를 엽니다. 홈페이지의
로그인 상태와 OpenAPI+ ActiveX 로그인 상태는 별도이므로, 홈페이지에 로그인한
뒤에도 앱의 `로그인` 버튼을 눌러 OpenAPI+ 공식 로그인 절차를 완료해야 합니다.
앱의 연결 표시는 `GetConnectState()`가 연결을 확인한 경우에만 `ON 연결됨`으로
바뀝니다.

필요 조건:

- Windows PC
- 키움 OpenAPI+ 설치
- pywin32 설치
- 32비트 실행 파일
- 키움 OpenAPI+ 서비스 사용 등록
- HTS ID 및 공동인증서 준비

키움 OpenAPI+가 설치되어 있지 않거나 비트 수가 맞지 않으면 앱은 한글 오류
메시지를 보여주고 실거래 기능은 실행하지 않습니다.

서비스 신청 화면에 `등록`이 표시되어도 PC에 OpenAPI+ 모듈이 별도로 설치되어
있어야 합니다. 앱의 `OpenAPI+ 설치파일 받기` 버튼으로 공식 설치파일을 받은 뒤
설치하고, `KiwoomAutoTrader-32bit.exe`를 다시 실행해 주세요. 로그인 창에서는
처음에는 반드시 `모의투자 접속`을 선택해 충분히 검증하는 것을 권장합니다.

## 키움 연결용 32비트 EXE 빌드

키움 OpenAPI+는 32비트 ActiveX이므로 계좌 연결 버튼을 사용하려면 32비트 EXE를
실행해야 합니다.

```bat
scripts\build_windows_exe_32bit.bat
```

실행 파일 생성 위치:

```text
dist\KiwoomAutoTrader-32bit.exe
```

## 로컬 실행

```bat
python -m kiwoom_auto_trader.main
```

## 로컬 EXE 빌드

```bat
scripts\build_windows_exe.bat
```

실행 파일 생성 위치:

```text
dist\KiwoomAutoTrader.exe
```

## GitHub Actions

저장소를 GitHub에 푸시한 뒤 Actions 탭에서 `Build Windows EXE` 워크플로를
실행하면 `KiwoomAutoTrader-windows-exe` 아티팩트가 생성됩니다.

## 실거래 주의

실계좌 주문 실행은 `BrokerClient` 어댑터 구현, 모의 검증, 키움 API 요구사항
검토, 계좌 단위 위험관리 확인이 끝나기 전까지 활성화하면 안 됩니다.

## 아직 검증이 필요한 부분

- 키움 OpenAPI+가 설치된 실제 32비트 Windows 환경에서 TR 응답 확인
- 사용자가 말한 분홍/파랑 강세·약세 지표의 정확한 원천 연결
- 쾌속주문 툴 직접 연동
- 장중 장시간 운용 테스트
- 실계좌 주문 전 모의투자 계좌 반복 검증
