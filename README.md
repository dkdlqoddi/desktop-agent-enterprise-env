# Desktop Agent Enterprise Environment

원하는 LLM을 연결해 쓰는 데스크톱 지식 관리·업무 자동화 앱입니다. Electron, Next.js, Python으로 구성되어 있으며, 문서·대화·작업 기록을 사용자가 지정한 폴더의 SQLite 데이터베이스에 저장합니다.

사이트 주소와 인증 방식, LLM 연결은 사용 환경에 맞게 직접 등록합니다.

## 주요 기능

- 첫 화면의 채팅: 말로 맡기면 내장 Hermes Agent가 자기 도구(터미널·파일·코드·웹 검색·브라우저)와 앱의 작업(문서 찾기·웹 기록·할 일·예약)으로 바로 실행하고, 위험한 명령만 사용자 확인을 받음
- 웹 기록: 쿠팡·아마존·네이버·구글 검색 결과를 눈앞의 브라우저 창에서 읽어 광고·상품·검색 결과와 스크린샷을 이 PC에 기록하고 CSV로 내보내기. 앱은 광고를 누르지 않고, 사용자가 직접 누른 광고만 도착 페이지까지 기록
- Android 앱(`android/`): 채팅, 앱 안의 보이는 브라우저, 광고·상품 기록과 CSV 공유를 휴대폰 안에서 처리. 데스크톱과 같은 페이지 읽기 스크립트를 씁니다
- Wiki 문서 작성, Markdown·이미지 가져오기, 문서 검색과 지식 관계도
- Windows Office 문서(Word·Excel·PowerPoint·Visio), RTF, 텍스트 파일·폴더 가져오기
- JIRA 티켓(키워드·사용자로 프로젝트의 티켓까지)과 Confluence 페이지 가져오기
- OpenAI 호환 또는 Anthropic 형식의 LLM 연결(클라우드 API, 게이트웨이, 로컬 LLM), 사용자 지정 헤더와 사설 CA 인증서 설정
- 문서를 근거로 한 AI 질문·답변(쓰는 대로 보여 주고 중지 가능), 요약, 키워드 분석, 긴 문서의 목차 색인
- 프롬프트 템플릿, 예약 실행, 할 일·알림, 대시보드 위젯
- JIRA·Confluence 변경 확인, 파일 갱신, 새 문서 분석, 업무 보고 자동화
- 로컬 REST API·MCP를 통한 도구 연동, 작업 기록과 진단 로그
- 가져온 Office 문서의 변경 사항을 원본 파일에 반영하고 백업 보관

## 문서

| 문서                                                     | 내용                                                                        |
| -------------------------------------------------------- | --------------------------------------------------------------------------- |
| [ARCHITECTURE.md](ARCHITECTURE.md)                       | 세 프로세스와 경계, 요청 경로, 데이터 위치, 모듈 지도, 기능 추가 순서, 빌드 |
| [CONTRIBUTING.md](CONTRIBUTING.md)                       | 개발 환경, 테스트 실행, 환경변수, 생성 파일, 커밋 메시지, CI                |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md)                 | 로그와 진단 정보, 메시지별 원인과 해결                                      |
| [PRIVACY.md](PRIVACY.md)                                 | 기능별로 PC 밖으로 나가는 것과 남는 것, 비밀 값과 로그                      |
| [SECURITY.md](SECURITY.md)                               | 보안 설계, 위협 모델, 취약점 신고                                           |
| [docs/api.md](docs/api.md), [OpenAPI](docs/openapi.json) | 로컬 REST API와 MCP의 메서드, 허용 범위, 매개변수                           |
| [CHANGELOG.md](CHANGELOG.md)                             | 변경 기록과 버전 정책                                                       |
| [AGENTS.md](AGENTS.md)                                   | AI 코딩 에이전트를 위한 규약                                                |
| [TODO.md](TODO.md)                                       | 남은 일, 결정이 필요한 것, 로드맵 밖 기능 제안                              |
| [android/README.md](android/README.md)                   | Android 앱의 화면, 휴대폰에 남는 것, 빌드와 테스트                          |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)         | 함께 배포하는 오픈소스 구성 요소의 라이선스 고지                            |
| [LICENSE](LICENSE)                                       | MIT 라이선스                                                                |

## 개발 환경

Node.js 22.13 이상(`.nvmrc`)과 Python 3.10 이상을 사용합니다. 배포 파일은 Python 3.13(`.python-version`)으로 빌드합니다. 데스크톱 실행에는 그래픽 환경이 필요합니다. Windows Office·RTF 가져오기와 원본 반영에는 해당 Office 데스크톱 앱이 설치되어 있어야 합니다. Windows 텍스트 가져오기는 메모장을 사용합니다.

```bash
npm ci
npm run setup:python
npm run setup:browser
npm run setup:electron
npm run dev
```

Windows에서는 `run-dev.cmd`를 사용할 수 있습니다. 처음 실행할 때 Wiki 저장 폴더를 선택합니다. 웹 화면만 개발하려면 `npm run dev:web`를 실행합니다.

`npm run dev`는 백엔드의 오류 출력과 앱·백엔드 로그를 터미널에도 보여 줍니다. `electron/`이나 `backend/main.py`·`backend/agent/`의 파일을 고치면 Electron과 백엔드를 다시 시작하고, 화면 코드는 Next.js가 바로 반영합니다. 다음 환경변수로 동작을 바꿀 수 있습니다.

| 환경변수                    | 설명                                                                                                                               |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `PRIVATE_AGENT_LOG_LEVEL`   | `debug`·`info`(기본)·`warning`·`error`. `debug`이면 앱 로그에 요청별 처리 시간도 남깁니다                                          |
| `PRIVATE_AGENT_LOG_CONSOLE` | `1`이면 로그를 터미널(표준 오류)에도 씁니다. `npm run dev`의 기본값이며 `0`으로 끕니다                                             |
| `PRIVATE_AGENT_DEV_WATCH`   | `0`이면 `npm run dev`가 파일 변경을 감시하지 않습니다                                                                              |
| `PRIVATE_AGENT_DEBUGPY`     | 포트 번호. 소스로 실행한 백엔드가 `127.0.0.1`의 그 포트에서 debugpy 디버거 연결을 기다립니다. 앱이 1분 기다리니 그 안에 연결합니다 |

## LLM 연결

**관리 → LLM 연결**에서 다음 값을 설정합니다.

1. API 형식: OpenAI 호환 또는 Anthropic
2. 전체 API 엔드포인트: 요청을 보낼 서버의 주소(OpenAI·Anthropic API, 게이트웨이, 로컬 LLM 서버 등)
3. 모델 이름과 필요한 경우 API Key
4. 필요한 사용자 지정 HTTP 헤더
5. 사설·자체 서명 인증서를 쓰는 서버라면 CA 인증서의 PEM 경로

사용자 지정 헤더 값의 `{api_key}`는 API Key로, `{uuid}`는 요청마다 새 ID로 바뀝니다. 헤더 값은 API Key처럼 암호화해 저장하며 화면과 AI 도구에는 값이 보이지 않습니다. 키가 필요 없는 로컬 LLM도 연결할 수 있습니다. TLS 인증서 검증은 항상 활성화됩니다. 서버 주소를 다른 서버로 바꾸면 기존 키와 헤더 값을 다시 입력해야 합니다.

개발 환경에서는 `.env.example`을 `.env`로 복사해 초기값을 지정할 수 있습니다. 기본 엔드포인트와 모델은 비어 있습니다. 패키징한 앱에는 `.env`가 포함되지 않으며, 환경변수는 `PRIVATE_AGENT_` 접두사가 있는 이름만 읽습니다.

문서는 로컬에 저장됩니다. AI 기능을 실행하면 해당 기능에 필요한 내용이 설정된 LLM 서버로 전송됩니다.

## 사이트 로그인

**관리 → 사이트 계정**에서 JIRA 또는 Confluence 주소를 등록합니다. 예시는 `https://jira.example.com/jira`, `https://wiki.example.com/wiki`처럼 실제로 쓰는 주소로 바꿔 입력합니다.

- 표준 SSO 링크, Microsoft ADFS, Atlassian Server/Data Center·Cloud 및 일반 로그인 폼을 감지합니다.
- 그 밖의 로그인 화면은 사용자 지정 단계에서 CSS 선택자나 XPath를 직접 등록합니다.
- 추가 인증이 필요하면 **자동 입력 안 함**과 **로그인 창 열기**로 세션을 생성합니다.
- 자격 증명은 등록한 사이트와 그 사이트가 직접 이동시킨 인증 화면에만, 비밀번호 입력란에만 입력합니다. 처음 로그인에 성공한 인증 화면의 주소(사이트 자신의 로그인 폼이면 그 사이트 주소)는 계정에 고정되어 이후에는 그 주소 밖의 로그인 화면에 입력하지 않으며, 로그인 화면이 바뀌면 **로그인 주소 초기화**로 다시 저장합니다.
- 브라우저 프로필에 로그인된 세션이 남아 있으면 비밀번호를 다시 보내지 않고 그 세션을 씁니다. **로그인 확인**은 항상 새로 로그인합니다.
- 로그인·접속 실패 시 AI 분석은 기본으로 꺼져 있습니다. 켜면 로그인 화면의 글과 주소가 설정한 LLM 서버로 전송됩니다.
- `https://`를 생략하면 HTTPS 주소로 저장하며, HTTP로 내려가지 않습니다. HTTP만 쓰는 사이트는 `http://`를 붙여 입력합니다(비밀번호가 암호화되지 않은 연결로 전송되므로 신뢰할 수 있는 네트워크에서만 쓰세요).

API Key, 사용자 지정 헤더 값과 사이트 비밀번호는 운영체제 보안 저장소로 암호화합니다. 보안 저장소를 사용할 수 없으면 새 API Key와 비밀번호는 앱을 닫을 때까지 메모리에만 보관하고, 헤더 값은 설정 파일에 그대로 둡니다. 로그와 진단 복사본에서는 키·토큰·비밀번호를 가립니다. 보안 설계와 신고 방법은 [SECURITY.md](SECURITY.md)를, 기능별로 PC 밖으로 나가는 내용은 [PRIVACY.md](PRIVACY.md)를 보세요.

## 자료 가져오기

**지식 보관함 → 자료 가져오기**에서 파일·폴더 경로나 JIRA·Confluence 링크를 입력합니다.

| 종류       | 입력                                 | 실행 환경                   |
| ---------- | ------------------------------------ | --------------------------- |
| Markdown   | `.md`, `.markdown`                   | 모든 지원 OS                |
| 이미지     | PNG, JPEG, SVG, TIFF 등              | EMF/WMF 변환은 Windows 필요 |
| Office     | Word, Excel, PowerPoint, Visio 파일  | Windows와 해당 앱           |
| RTF        | `.rtf` 파일                          | Windows와 Word              |
| 텍스트     | `.txt` 파일                          | Windows와 메모장            |
| Confluence | 페이지 링크                          | 사이트 접근 권한과 브라우저 |
| JIRA       | 티켓 링크(키워드·사용자로 범위 선택) | 사이트 접근 권한과 브라우저 |

같은 경로나 링크를 다시 가져오면 원본의 변경 사항을 기존 문서에 반영합니다. Wiki에서 직접 고친 내용은 명시적으로 덮어쓰기를 선택하기 전까지 보존합니다. 폴더는 5,000개씩 나누어 처리하며, 진행 중 취소할 수 있습니다. Office 문서는 해당 앱이 실제로 열 수 있어야 합니다.

## 채팅과 웹 기록

앱을 열면 **채팅**이 먼저 보입니다. "쿠팡에서 노트북 광고 기록해 줘", "매일 9시에 네이버 캠핑의자 광고 기록해 줘"처럼 적으면 내장 [Hermes Agent](https://github.com/NousResearch/hermes-agent)가 연결한 LLM으로 일을 바로 처리하고, 한 일을 작업 카드와 답으로 보여 줍니다.

- Hermes는 자기 도구(터미널, 파일, 코드 실행, 웹 검색, 브라우저, 메모리, 스킬)와 앱의 작업(문서 찾기, 웹 기록, 사이트 열기, 할 일 추가, 예약)을 직접 씁니다. 앱의 작업은 백엔드가 이름과 인수를 확인한 뒤 실행하고 카드로 남깁니다.
- 위험하다고 판단한 명령에서는 멈추고 **이번만 실행**·**항상 허용**·**거부**를 묻습니다. 모르는 것은 되물어 답을 받습니다. 5분 안에 답하지 않으면 명령은 거부, 질문은 취소로 처리합니다.
- 진행 중인 작업과 작성 중인 답을 보여 주며, **중지**로 언제든 멈춥니다(확인이나 답을 기다리는 중에도). 다른 화면으로 옮기거나 화면을 다시 불러와도 작업은 이어지고, 에이전트가 확인을 기다리거나 일을 마치면 알림으로 알려 줍니다.
- 사이트 작업은 별도 Chromium 창에서 사용자에게 보이게 진행합니다. 앱의 웹 기록은 페이지의 광고나 링크를 누르지 않고, 로봇 확인(CAPTCHA)이 나오면 멈추고 알려 줍니다. 그 창에서 사용자가 직접 누른 광고는 도착 페이지(60초 안)까지 함께 기록합니다.
- 모델 호출은 모두 **관리 → LLM 연결**의 연결로 보내며, API Key와 헤더 값은 Hermes에 넘기지 않습니다. 웹 검색·이미지 같은 서비스 키, MCP 서버, 에이전트의 작업 폴더, 컴퓨터 사용은 **관리 → 채팅 에이전트**에서 정합니다.
- **메신저 게이트웨이**를 켜면 텔레그램·디스코드·슬랙의 내 봇에 글을 보내 이 PC의 에이전트에 일을 시킬 수 있습니다. 앱이 켜져 있는 동안만 동작하고, **관리 → 채팅 에이전트**에 적은 계정에만 답합니다. 위험한 명령은 그 메신저에서 확인을 받습니다(5분 안에 답이 없으면 실행하지 않음).
- 채팅과 에이전트의 설정은 사용자만 다룹니다. 로컬 API로 연결한 AI 도구는 허용 범위와 관계없이 채팅과 **관리 → 채팅 에이전트**에 글을 넣거나 그 버튼을 누를 수 없습니다.

에이전트는 이 PC에서 사용자 권한으로 명령을 실행합니다. 운영체제 샌드박스가 아니라 위험 명령의 확인으로 통제하므로, 맡기는 일과 확인 창의 명령을 살펴보세요.

**웹 기록** 화면에서 사이트·종류(광고, 상품, 검색 결과, 누른 광고)·검색어·날짜로 기록을 거르고, **CSV 내보내기**로 Wiki 폴더의 `exports`에 저장합니다(Excel에서 바로 열리는 UTF-8 BOM). 브라우저 창에 직접 연 페이지는 **열린 페이지 기록**으로 남깁니다. 채팅으로 만든 예약은 **AI 작업 → 자동 작업**의 ‘웹 기록’ 작업으로 보이고 거기서 바꾸거나 끌 수 있습니다.

사이트의 이용약관과 접근 정책을 지키는 범위에서 쓰세요. 기록은 이 PC의 Wiki 폴더에만 남습니다. 채팅 내용과 에이전트의 도구가 돌려준 결과(문서 검색 결과, 웹 기록 요약, 에이전트가 읽은 파일과 페이지의 내용, 명령의 출력)는 설정한 LLM 서버로 전송되고, 에이전트의 웹 검색·브라우저·MCP 도구는 그 일에 필요한 사이트와 서비스에 접속합니다. 자세한 내용은 [PRIVACY.md](PRIVACY.md)에 있습니다.

## API와 MCP

**관리 → AI 도구 연동**에서 서버를 활성화하고 연결 정보를 복사합니다. 기능별 매개변수와 접근 권한은 **API 매뉴얼**이나 [docs/api.md](docs/api.md)에서 확인합니다.

읽기, 쓰기, 전체 접근 권한을 구분합니다. 로컬 파일 가져오기와 원본 반영, 등록하지 않은 사이트의 링크 가져오기, 휴지통 비우기, 백업 복원, 앱 화면의 삭제 확인 버튼에는 전체 접근 권한이 필요합니다. 쓰기 권한의 문서 삭제는 휴지통으로 가며 복원할 수 있습니다. 채팅 화면은 어느 권한에서도 AI 도구가 입력하거나 누를 수 없습니다. Python 백엔드는 별도 공개 HTTP 포트 없이 표준 입출력 JSON RPC로 Electron과 통신합니다.

## 화면 테마와 배율

**관리 → 화면·검색·대시보드**의 **화면 테마**에서 시스템 설정(Windows의 밝은·어두운 모드를 따름), 밝게, 어둡게 중 하나를 고릅니다. 테마는 이 PC의 앱에 저장되어 Wiki 저장 위치를 바꿔도 유지됩니다. 가져온 HTML 문서는 문서 고유의 색이 그대로 보이도록 어두운 테마에서도 흰 바탕으로 표시합니다.

화면 배율은 `Ctrl` + `=`(또는 `Ctrl` + 마우스 휠)로 키우고 `Ctrl` + `-`로 줄이며, `Ctrl` + `0`으로 100%로 되돌립니다. 50%~200% 사이에서 바뀌고 다음 실행 때도 유지됩니다.

## 검증과 빌드

```bash
npm run format:check
npm run lint:python
npm run format:python:check
npm run check
npm run build:web
npm run build:python
npm run build:hermes
npm run test:e2e
```

`build:hermes`는 채팅이 쓰는 Hermes Agent를 `runtime/hermes/manifest.json`에 고정한 소스·CPython 3.14.0·해시 고정 wheel(`runtime/hermes/requirements-desktop.txt`)로 `dist/hermes`에 준비합니다(Windows는 python.org embeddable package, Linux는 python-build-standalone을 받아 SHA-256을 확인합니다). 이것 없이 소스로 실행하면 채팅에는 도구가 없습니다. 연결한 LLM이 JSON으로 답한 계획을 카드로 보여 주고 사이트 열기·기록·할 일·예약은 **확인 후 실행**을 눌러야 실행하며, 화면에 ‘기본 계획기’로 표시합니다.

`check`는 ESLint 검사(`npm run lint`), 화면과 Electron 메인 프로세스의 TypeScript 검사(`npm run typecheck`, `npm run typecheck:electron`), Python 테스트와 Electron 메인 프로세스 테스트를 실행합니다. `test:e2e`는 Linux에서 xvfb가 설치되어 있으면 가상 디스플레이에서 창을 띄우지 않고 실행되며(`PRIVATE_AGENT_E2E_SHOW=1`이면 실제 화면에 띄웁니다), 같은 E2E를 `npm run ci`가 핵심 검사와 함께 로컬에서 돌립니다. 메인 프로세스 테스트에는 두 테마의 글자색 대비(WCAG AA) 측정이 포함되고, E2E의 `tests/a11y.spec.ts`는 주요 화면을 두 테마에서 axe로 검사해 critical·serious 위반이 있으면 실패합니다. 브라우저 테스트에는 `setup:browser`가 필요하며, Chromium이 없으면 건너뜁니다. E2E 테스트는 빌드된 웹 화면과 Python 백엔드를 실행하며, Linux에서는 가상 디스플레이 등 Electron 실행 환경도 필요합니다. `PRIVATE_AGENT_E2E_SOURCE_BACKEND=1`이면 빌드한 백엔드 대신 `backend/.venv`의 Python으로 `backend/main.py`를 실행하므로 `build:python` 없이 E2E를 돌릴 수 있습니다. Office 통합 테스트는 해당 앱이 설치된 Windows에서만 실행됩니다.

`lint`는 ESLint로 화면(TypeScript, React 훅, 접근성, Next.js 규칙), Electron 메인 프로세스와 preload, 스크립트, 테스트를 검사하고 경고 하나도 실패로 봅니다(`npm run lint:fix`는 고칠 수 있는 것을 고칩니다). `typecheck:electron`은 JavaScript로 쓴 `electron/*.cjs`와 `scripts/*.mjs`를 TypeScript(checkJs)로 검사합니다. 설정은 `eslint.config.mjs`와 `tsconfig.electron.json`에 있고, 형식은 Prettier만 맞춥니다. 메인 프로세스 테스트에는 `agent://` 프로토콜의 경로·CSP 검사(`tests/protocol.test.cjs`), IPC를 보낸 곳과 `ALLOWED` 검사(`tests/trusted-sender.test.cjs`), 가져온 HTML 정제기의 XSS 사례 95가지(`tests/sanitize.test.cjs`)가 들어 있습니다.

`lint:python`은 Python 백엔드를 ruff로 검사하고(`npm run lint:python -- --fix`는 고칠 수 있는 것을 고칩니다), `format:python`은 형식을 맞추며, `format:python:check`는 형식이 맞는지만 확인합니다. 규칙은 `backend/pyproject.toml`의 `[tool.ruff]`에 있습니다. 백엔드 전체의 형식을 한 번에 맞춘 커밋은 `.git-blame-ignore-revs`에 있으니, `git config blame.ignoreRevsFile .git-blame-ignore-revs`로 설정하면 `git blame`이 그 커밋을 건너뜁니다.

`npm run test:main:coverage`와 `npm run test:python:coverage`는 테스트 뒤에 커버리지 보고서를 출력합니다. 의존성을 바꾸면 `npm run notices`로 `THIRD_PARTY_NOTICES.md`를 다시 만들어 함께 커밋합니다(`npm run notices:check`가 최신인지 확인합니다). 화면이 부를 수 있는 메서드 타입(`src/lib/api/methods.generated.ts`)은 `electron/api-catalog.cjs`의 `METHODS`와 `electron/trusted-sender.cjs`의 `ALLOWED`에서 만들어지므로, 둘 중 하나를 바꾸면 `npm run generate:methods`를 실행해 함께 커밋합니다. 메인 프로세스 테스트가 이 파일이 최신인지, 화면이 부르는 메서드가 모두 `ALLOWED`에 있고 메인 프로세스나 백엔드가 처리하는지 확인합니다. 로컬 API의 문서(`docs/api.md`, `docs/openapi.json`)도 카탈로그에서 만들어지므로, API를 바꾸면 `npm run docs:api`를 실행해 함께 커밋합니다(`tests/api-docs.test.cjs`가 최신인지 확인합니다).

Windows 배포 파일은 `build-exe.cmd` 또는 `npm run build:exe`로 만듭니다. 빌드 결과는 `dist/`, `out/`, `release/`에 생성되며 Git에서 제외됩니다.

검사는 GitHub Actions 대신 로컬에서 돌립니다. `npm run ci`는 형식·ESLint·타입 검사(화면과 Electron), 메인 프로세스 테스트, 웹 빌드, Python 테스트와 ruff 검사, 서드파티 고지 확인에 더해 E2E까지 설치된 Node.js·Python 하나로 한 번에 실행하고(`npm run ci:core`는 E2E를 뺀 핵심 검사만), 끝에 통과·실패를 정리합니다(`scripts/ci-local.mjs`). Windows 배포 파일은 Windows에서 `npm run build:exe`로 만들며, 환경 변수 `WIN_CSC_LINK`·`WIN_CSC_KEY_PASSWORD`가 있으면 exe에 코드 서명합니다. 의존성 업데이트는 `renovate.json`에 따라 매달 PR로 올라오며, 이를 위해 저장소 소유자가 Renovate GitHub 앱을 설치해야 합니다.

## 저장소 구조

```text
backend/agent/       Wiki, 검색, LLM, 가져오기와 자동화, 채팅과 웹 기록
backend/tests/       Python 단위·브라우저 테스트
src/                Next.js 기반 화면과 공유 타입
electron/           데스크톱 실행, 계정 저장, 로컬 API와 MCP
shared/web-capture/ 웹 기록의 페이지 읽기·클릭 확인 스크립트와 사이트 목록(데스크톱·Android 공용)
runtime/hermes/     내장 Hermes의 고정 버전, 해시 고정 의존성, 워커
android/            Android 앱(Kotlin, Jetpack Compose)
scripts/            개발 환경과 패키징 도구
tests/              Electron 단위 테스트와 Playwright E2E
assets/             앱 아이콘
```

## 데이터와 배포

Wiki 데이터는 선택한 폴더의 `wiki.db`에 저장됩니다. 삭제한 문서는 휴지통에 30일 보관되며 **지식 보관함 → 문서 → 휴지통**에서 복원하거나 영구 삭제할 수 있습니다. 연결 설정과 브라우저 로그인 세션은 운영체제의 앱 설정 폴더에 저장되며, 개발 환경의 기본 설정 폴더는 `.local-data/dev-user-data`입니다.

앱이 켜져 있으면 하루에 한 번 같은 폴더의 `backups`에 `wiki-YYYYMMDD-HHMMSS.db`로 백업하고 최근 7개를 남깁니다. **관리 → 저장 위치·정보 → 지금 백업**이나 API(`wiki.backup`)로 만든 백업(`wiki-manual-…`)은 따로 최근 7개를 남깁니다. 백업으로 되돌리기는 같은 화면의 **백업과 복원** 목록이나 API(`wiki.restore_backup`)로 예약하고 앱을 다시 실행할 때 이루어지며(목록의 **지금 다시 시작**으로 바로 다시 실행하고, 그 전에는 **복원 취소**로 거둘 수 있습니다), 그때까지의 Wiki는 `backups`에 `wiki-before-restore-….db`로 남습니다. 원본에 반영한 Office 문서는 문서 창의 **원본 백업**에서 반영 전 파일로 되돌릴 수 있습니다. 앱은 시작할 때마다 데이터베이스를 검사(`PRAGMA quick_check`)하고 스키마 버전(`PRAGMA user_version`)을 확인해, 손상되었거나 더 새로운 버전의 앱이 바꾼 파일은 열지 않고 그대로 둔 채 할 일을 알려 줍니다. 실행 기록은 최근 90일 안의 최근 2,000개를, 대화는 최근 1,000개와 대화마다 최근 메시지 200개를, 채팅은 최근 메시지 1,000개를 보관합니다. 웹 기록의 스크린샷은 Wiki 폴더의 `web-captures`, CSV는 `exports`, 내장 Hermes의 메모리·스킬·세션 기록은 `hermes`에 있습니다. 웹 기록용 브라우저의 프로필(그 창에서 로그인한 사이트의 세션)은 앱 설정 폴더의 `web-profiles`에 남습니다.

`.env`, 인증서·키, 데이터베이스, 브라우저 프로필, 로그와 빌드 결과를 저장소에 추가하지 마세요.

## 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE)로 배포합니다. 함께 배포하는 Electron·Chromium·Python·Hermes Agent와 각 라이브러리는 저마다의 라이선스를 따르며, 목록과 고지는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다.
