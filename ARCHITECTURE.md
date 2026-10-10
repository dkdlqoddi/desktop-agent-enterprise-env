# 구조

Private Agent가 어떻게 나뉘어 있고, 요청과 데이터가 어디로 흐르는지 설명합니다. 개발 환경과 검사는 [CONTRIBUTING.md](CONTRIBUTING.md), 보안 설계는 [SECURITY.md](SECURITY.md), 로컬 API의 메서드는 [docs/api.md](docs/api.md)를 보세요.

## 세 프로세스

```text
렌더러: Next.js 정적 화면(out/, agent://bundle/)
   │  api() → window.privateAgent.request → ipcRenderer.invoke('agent:request')
   ▼
Electron 메인 프로세스(electron/main.cjs) ◀── AI 도구: 127.0.0.1 REST·MCP(electron/api-server.cjs)
   │  stdin/stdout JSON 줄(electron/backend-process.cjs)
   ▼
Python 백엔드(backend/main.py → agent/service.py) ──▶ wiki.db, LLM 서버, Chromium(Playwright), Office(COM)
```

| 프로세스      | 맡는 일                                                                                                                                                               | 경계                                                                                                                                                                         |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 렌더러        | 화면. Next.js 16(React 19)의 정적 내보내기(`output: 'export'`)를 메인 프로세스가 `agent://bundle/` 프로토콜로 제공합니다                                              | `sandbox`·`contextIsolation`, Node.js 없음, 스크립트 해시 기반 CSP. 외부 이동·새 창·webview·권한 요청을 막습니다. 요청 통로는 preload가 연 하나뿐입니다                      |
| Electron 메인 | 창, IPC 허용 목록, 설정 파일과 비밀 값(운영체제 보안 저장소), 사이트 계정과 로그인 계획, 로컬 API·MCP 서버, 알림·자동 작업의 시각 확인, 로그·진단, 백엔드 시작·재시작 | 렌더러의 요청은 보낸 프레임과 `ALLOWED`로, AI 도구의 요청은 토큰과 허용 범위로 거릅니다                                                                                      |
| Python 백엔드 | `wiki.db`, 검색, LLM 호출, 가져오기(Office COM 워커, Playwright Chromium), 원본 반영, 자동 작업 실행, 백업                                                            | 포트를 열지 않고 메인 프로세스와 표준 입출력으로만 통신합니다. LLM 설정(API Key 포함)은 시작할 때 받아 메모리에만 두고, 사이트 비밀번호는 로그인이 필요한 요청에 담겨 옵니다 |

메인 프로세스는 앱이 시작할 때 저장된 Wiki 폴더로 백엔드를 띄우고(`electron/storage.cjs`), 처음 실행이면 화면이 폴더를 고르게 합니다. 백엔드가 예기치 않게 끝나면 1초, 5초, 15초 뒤에 다시 시작합니다. 2분 안에 세 번 다시 시작하고도 또 끝나면 더 시작하지 않고 사용자에게 알립니다(`electron/restarts.cjs`).

## 요청 경로

1. 화면이 `api(method, params)`를 부릅니다(`src/lib/api/transport.ts`). `method`는 `src/lib/api/methods.generated.ts`의 `Method` 타입이어야 합니다.
2. preload(`electron/preload.cjs`)가 `ipcRenderer.invoke('agent:request', method, params)`로 보냅니다.
3. 메인 프로세스의 `ipcMain.handle('agent:request')`는 `allowedRequest()`(`electron/trusted-sender.cjs`)로 보낸 곳이 메인 창의 메인 프레임이고 주소가 `agent://bundle/` 또는 개발 서버인지(`trustedSender`), 메서드가 `ALLOWED`에 있는지 확인하고, 매개변수가 객체이며 JSON으로 900,000자 이하인지 봅니다.
4. `api.*`는 로컬 API 설정(`apiRequest`), `app.relaunch`(예약한 백업 복원을 적용하는 ‘지금 다시 시작’)는 앱을 닫고 같은 프로그램으로 다시 실행하며, 나머지는 `handle()`로 갑니다. `handle()`은 메인 프로세스만 할 수 있는 일(저장 위치, 진단, 앱 창 제어, 사이트 계정, LLM 비밀 값, 클립보드, 파일 선택 대화 상자, JIRA·Confluence 가져오기에 로그인 계획 붙이기)을 직접 처리하고, 나머지는 `rpc()`로 백엔드에 넘깁니다.
5. `rpc()`는 백엔드가 (다시) 시작하는 동안 기다렸다가 `{"id", "method", "params"}` 한 줄을 표준 입력에 씁니다. 응답 제한 시간은 `RPC_TIMEOUTS`(`electron/main.cjs`)에 있으면 그 값, 없으면 330초입니다. 시간이 지나면 메인 프로세스 쪽만 실패로 끝나고 백엔드의 작업은 계속됩니다.
6. 백엔드(`backend/main.py`)는 줄마다 처리 줄을 고릅니다. `SLOW`의 메서드와 `mode`가 `prompt`인 `wiki.search`는 느린 줄(4개), 나머지는 빠른 줄(24개)입니다. 줄이 차 있으면 기다리지 않고 바로 "진행 중인 요청이 많습니다"로 답합니다. `INTERACTIVE_METHODS`의 LLM 요청은 일괄 작업보다 먼저 갑니다(`agent/llm.py`의 `Slots`).
7. `Service.dispatch`(`agent/service.py`)가 처리 함수를 부르고, 백엔드는 `{"id", "result"}`나 `{"id", "error"}`로 답합니다. `UserError`는 그 메시지를, 예기치 않은 예외는 로그에 남긴 뒤 오류 ID가 붙은 일반 메시지를 돌려줍니다. 스택이나 비밀 값은 화면으로 가지 않습니다.
8. 메인 프로세스가 `{ok: true, result}`나 `{ok: false, error}`로 답하고, `api()`는 실패를 `Error`로 던집니다.

AI 도구의 요청은 `electron/api-server.cjs`(127.0.0.1, Host·Origin 검사, 토큰)에서 `callApi()`로 옵니다. `authorize()`(`electron/api-catalog.cjs`)가 메서드의 범위와 사용자가 고른 허용 범위를 맞춰 보고, 파일 경로 검사(`electron/api-methods.cjs`)를 거친 뒤 화면과 같은 `handle()`로 처리합니다. 무엇을 바꾼 요청이면 창에 `api:changed`를 보내 화면이 다시 읽게 합니다.

## 이벤트

- 백엔드가 스스로 보내는 이벤트는 `id` 없는 줄 `{"event", "params"}`입니다. 메인 프로세스는 `BACKEND_EVENTS`에 있는 `assistant.delta`(LLM이 쓰는 답변의 조각)와 `assistant.done`(끝)만 창의 `assistant:delta` 채널로 넘기고, 나머지는 버립니다.
- 화면은 `stream`과 자기 `request_id`를 붙여 물은 답변의 이벤트만 상태 조각 하나(`src/components/answers.ts`)에 모읍니다. 검색창은 그 글을 쓰는 대로(150ms마다) 그리고, 최근 대화 목록은 답변 중인 대화와 아직 저장되지 않은 새 대화를 보여 줍니다. ‘답변 중지’는 `assistant.cancel {request_id}`를 보냅니다.
- 메인 프로세스가 보내는 이벤트: `job:progress`(가져오기·AI 분석·자동 작업의 진행, `electron/job-progress.cjs`가 따라가며 번호를 붙임), `schedule:event`, `routine:event`, `import:notice`, `assistant:open`, `api:changed`, `control:command`(로컬 API의 화면 명령, 화면은 `control:result`로 답함)
- 화면은 백엔드의 응답을 메서드와 매개변수별로 캐시합니다(`src/lib/query.ts`). 변경, 끝난 작업, 메인 프로세스의 알림이 태그(`documents`, `prompts`, `runs`, `routines`, `tasks`, `conversations`)를 무효로 하면 그 태그를 가진 응답만 다시 읽습니다. 보관함의 문서 목록은 같은 캐시의 나누어 받는 목록(`QueryCache.list`, `useDocumentPages`)으로 100개씩 불러오고, 무효가 되면 보이던 만큼을 첫 쪽부터 다시 읽으며, 보이는 줄만 그립니다(`src/components/windowing.ts`).

## 데이터 위치

### Wiki 폴더

사용자가 처음 실행할 때 고른 폴더입니다(기본 제안은 문서 폴더의 `Private Agent`).

| 경로                   | 내용                                                                                                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wiki.db`              | SQLite(WAL). 스키마 버전은 `PRAGMA user_version`이고 지금은 1입니다(`backend/agent/migrations.py`)                                                         |
| `backups/`             | `wiki-YYYYMMDD-HHMMSS.db`(하루 한 번 자동, 최근 7개), `wiki-manual-….db`(직접 백업, 최근 7개), `wiki-before-restore-….db`(복원으로 바뀐 Wiki, 지우지 않음) |
| `backups/restore.json` | 예약된 복원. 함께 있는 `restore-<id>.db`를 다음 시작 때 `wiki.db` 자리에 넣고, 결과를 `last-restore.json`에 남깁니다                                       |
| `원본 백업/`           | 원본 반영 전에 복사한 Office 파일(파일마다 최근 5개)과 저장 단계를 적는 `.writing` 파일                                                                    |

`wiki.db`의 테이블은 다음과 같이 묶입니다(`backend/agent/db.py`).

- 문서: `documents`(삭제하면 `deleted_at`을 적고 30일 휴지통에 둠), `document_html`(가져온 파일의 HTML 페이지), `document_search`와 FTS5 색인 `document_fts`(trigram), `doc_nodes`(긴 문서의 목차 색인, PageIndex)
- AI 분석: `keywords`, `keyword_aliases`(키워드의 한국어·영어 별칭), `relations`
- 출처: `office_sources`, `rtf_sources`, `image_sources`, `text_sources`, `markdown_sources`, `confluence_sources`, `jira_sources`, `ignored_sources`(영구 삭제한 출처), `writeback_backups`(원본 백업 기록)
- 프롬프트와 자동화: `prompts`, `runs`, `schedules`, `routines`, `routine_runs`
- 비서: `tasks`, `conversations`, `messages`
- `metadata`: 검색·대시보드 설정(`preferences`)과 색인 상태

가져온 이미지는 별도 파일 없이 HTML 안의 `data:` URI로 저장합니다. Office 그림을 내보낼 때 쓰는 임시 폴더(`private-agent-office-…`, `private-agent-pictures-…`)는 시스템 임시 폴더에 생기고, 백엔드가 시작할 때 10분 넘은 것을 지웁니다.

### 앱 설정 폴더

Electron의 `userData` 폴더입니다. Windows에서는 `%APPDATA%\Private Agent`, `npm run dev`에서는 `.local-data/dev-user-data`이며 `PRIVATE_AGENT_USER_DATA`로 바꿀 수 있습니다.

| 경로                                     | 내용                                                                                                                                                                                                             |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `storage.json`                           | 고른 Wiki 폴더                                                                                                                                                                                                   |
| `settings.json`                          | LLM 연결 설정. API Key(`encrypted_api_key`)와 헤더 값(`encrypted_headers`)은 암호화하고 헤더는 이름만 둡니다. 보안 저장소를 쓸 수 없으면 새 API Key는 실행 중 메모리에만 두고 헤더 값은 파일에 그대로 저장합니다 |
| `sites.json`                             | 사이트 계정, 로그인 방식, 고정한 로그인 주소. 비밀번호는 `encrypted_password`                                                                                                                                    |
| `api.json`                               | 로컬 API의 켜짐, 포트(기본 47615), 허용 범위(기본 `read`), 암호화한 토큰                                                                                                                                         |
| `window.json`                            | 화면 배율                                                                                                                                                                                                        |
| `logs/app.log`, `logs/backend.log`       | 메인 프로세스와 백엔드의 로그. 각각 1MB를 넘으면 `.1`로 하나 남깁니다                                                                                                                                            |
| `confluence-profiles/`, `jira-profiles/` | 사이트 주소(origin)마다 하나인 Chromium 프로필. 그 사이트의 마지막 계정을 지우면 함께 지웁니다                                                                                                                   |
| `mcp/private-agent-mcp.cjs`              | stdio로만 연결하는 AI 도구가 실행하는 MCP 연결 프로그램. 로컬 API를 켤 때 씁니다                                                                                                                                 |

화면 테마는 창의 localStorage(`private-agent:theme`)에 있습니다. 배포용 exe는 앱을 `%LOCALAPPDATA%\Private Agent\app-<버전>-<빌드 시각>`에 풀어 실행합니다.

## 모듈 지도

### backend

| 모듈                                                                                                                   | 역할                                                                                     |
| ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `main.py`                                                                                                              | 표준 입출력 JSON 줄 RPC 루프, 빠른·느린 줄, 이벤트 출력, Wiki를 열지 못할 때의 거절 응답 |
| `agent/service.py`                                                                                                     | `Service.dispatch`의 메서드 표, Wiki·프롬프트·휴지통·백업 처리, 보관 정책, 종료          |
| `agent/db.py`, `agent/migrations.py`                                                                                   | 스키마, 이전 형식의 Wiki 올리기, FTS5 색인, 스키마 버전과 번호 붙은 마이그레이션         |
| `agent/storage.py`                                                                                                     | 시작할 때 검사(`quick_check`, 스키마 버전), 이전 Wiki 복사, 백업과 예약된 복원           |
| `agent/search.py`, `agent/paging.py`                                                                                   | BM25·trigram 단어 검색, 키워드 별칭, LLM이 고르는 AI 검색, 목록·검색 결과의 나누어 받기  |
| `agent/llm.py`                                                                                                         | LLM 설정, OpenAI·Anthropic 형식, 재시도, 우선순위, 스트리밍, 중지                        |
| `agent/assistant.py`                                                                                                   | 문서를 근거로 한 대화, 할 일과 알림, 오늘의 브리핑                                       |
| `agent/pageindex.py`, `agent/batch_analysis.py`                                                                        | 긴 문서의 목차 색인, 여러 문서의 백그라운드 AI 분석                                      |
| `agent/routines.py`, `agent/schedules.py`                                                                              | 자동 작업과 예약 프롬프트를 하나의 대기열과 작업자 3개로 실행                            |
| `agent/sources.py`, `agent/parts.py`                                                                                   | 가져온 출처의 기록, 다시 가져오기와 이동한 파일 연결, 긴 자료를 여러 문서로 나누기       |
| `agent/file_import.py`, `office_import.py`, `rtf_import.py`, `image_import.py`, `text_import.py`, `markdown_import.py` | 파일·폴더 가져오기 작업과 종류별 변환                                                    |
| `agent/office_converter.py`, `office_session.py`, `office_processes.py`                                                | 매크로를 막은 일회용 Office COM 워커, 남은 Office 프로세스 정리                          |
| `agent/word_html.py`, `office_html.py`, `drawing.py`, `vml.py`                                                         | Word·Excel·PowerPoint·Visio 문서를 HTML 페이지와 SVG 그림으로                            |
| `agent/writeback.py`, `office_write.py`, `source_edits.py`                                                             | 고친 내용을 원본 Office 파일에 반영, 원본 백업                                           |
| `agent/browser_import.py`, `confluence_import.py`, `jira_import.py`                                                    | 사이트별 Chromium 프로필, 요청 속도 제한, Confluence 페이지와 JIRA 티켓 수집             |
| `agent/web_login.py`, `agent/web_assist.py`                                                                            | 로그인 방식에 따른 자동 로그인, 로그인·접속 실패 시 LLM 도움(기본 꺼짐)                  |
| `agent/diagnostics.py`                                                                                                 | `backend.log`와 비밀 값 가리기, 오류 ID가 붙은 사용자 메시지                             |

### electron

| 모듈                                                    | 역할                                                                                                          |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `main.cjs`                                              | 앱 시작과 종료, 창, `handle()`, 백엔드 실행, `settings.json`, 각 부분의 연결                                  |
| `protocol.cjs`                                          | `agent://bundle/` 프로토콜: `out/` 밖의 경로를 거절하고 파일 종류와 스크립트 해시 CSP를 붙여 정적 화면 제공   |
| `trusted-sender.cjs`                                    | IPC를 보낸 곳 확인(`trustedSender`: 메인 창의 메인 프레임과 화면 주소)과 화면이 부를 수 있는 메서드 `ALLOWED` |
| `preload.cjs`                                           | 화면에 여는 `window.privateAgent`: 요청 하나와 이벤트 구독                                                    |
| `backend-process.cjs`, `restarts.cjs`                   | 백엔드 프로세스 하나의 요청·응답·이벤트·제한 시간, 예기치 않게 끝난 백엔드의 재시작                           |
| `storage.cjs`                                           | Wiki 폴더 선택(`storage.json`), 이전 버전 Wiki 복사, 폴더 쓰기 확인                                           |
| `sites.cjs`, `login-methods.json`                       | 사이트 계정(`sites.json`, 암호화한 비밀번호)과 로그인 계획, 로그인 방식 목록                                  |
| `api-catalog.cjs`                                       | 로컬 API의 모든 메서드, 범위, REST 경로. REST·MCP·매뉴얼·OpenAPI가 모두 이 표에서 만들어집니다                |
| `api-server.cjs`, `mcp.cjs`, `mcp-stdio.cjs`            | 127.0.0.1 REST·MCP 서버, MCP 도구·리소스·프롬프트, stdio 연결 프로그램                                        |
| `api-manual.cjs`, `api-settings.cjs`, `api-methods.cjs` | 매뉴얼(JSON·Markdown·HTML·OpenAPI), `api.json`, AI 도구 전용 메서드와 경로 검사                               |
| `app-control.cjs`                                       | 로컬 API가 창과 화면을 다루는 명령                                                                            |
| `job-progress.cjs`, `routines.cjs`, `reminders.cjs`     | 작업 진행 알림, 자동 작업·예약 실행 시각 확인(30초), 할 일 알림(15초)                                         |
| `diagnostics.cjs`, `zoom.cjs`, `files.cjs`              | `app.log`와 진단 복사본, 화면 배율, 원자적 JSON 파일 쓰기                                                     |
| `demo.cjs`, `demo-plan.cjs`, `recorder.cjs`, `mp4.cjs`  | 데모 녹화(`record-demo.cmd`)                                                                                  |

### src

| 모듈                                                    | 역할                                                                                           |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `app/`                                                  | Next.js 진입점(정적 내보내기)                                                                  |
| `components/app.tsx`, `components/state.tsx`            | 시작 상태와 이벤트 연결, 따로 바뀌는 상태 조각과 선택자 훅(`useView`, `useQuery` 등)           |
| `components/workspace.tsx`, `dashboard.tsx`, `widgets/` | 사이드바와 화면 전환, 위젯 대시보드                                                            |
| `components/reader.tsx`, `search-panel.tsx`             | 문서·실행 기록 보기와 편집(충돌 비교 포함), 검색창과 AI 답변                                   |
| `components/admin.tsx`, `admin/`                        | 관리 화면: LLM 연결, 사이트 계정, 템플릿, AI 도구 연동, API 매뉴얼                             |
| `components/html-document.tsx`, `lib/html-document.ts`  | 가져온 HTML을 허용 목록으로 정제해 shadow DOM에 표시                                           |
| `lib/api.ts`, `lib/api/`                                | 요청 함수(`transport.ts`), 응답 타입(`types.ts`), 표시 형식(`format.ts`), 생성된 `Method` 타입 |
| `lib/query.ts`, `lib/store.ts`                          | 태그로 무효화하는 요청 캐시와 나누어 받는 목록, 선택자로 읽는 작은 상태 저장소                 |
| `components/windowing.ts`                               | 긴 목록에서 보이는 줄만 그리기(측정한 줄 높이)                                                 |

### scripts

| 파일                                                                                    | 역할                                                                              |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `dev.mjs`, `run-dev.mjs`                                                                | 개발 서버와 Electron, 파일 감시와 재시작 / 준비부터 실행까지 한 번에              |
| `setup-python.mjs`, `setup-browser.mjs`, `setup-electron.mjs`                           | 가상 환경, Chromium, Electron 준비                                                |
| `test-python.mjs`, `test-e2e.mjs`, `lint-python.mjs`                                    | pytest, Playwright(Linux에서는 xvfb-run), ruff 실행                               |
| `package.mjs`, `build-python.mjs`, `build-browser.mjs`, `build-exe.mjs`, `launcher.nsi` | 패키징(아래 참고)                                                                 |
| `generate-methods.mjs`, `export-api-docs.mjs`, `third-party-notices.mjs`                | 생성 파일: 화면의 `Method` 타입, `docs/api.md`·`docs/openapi.json`, 서드파티 고지 |

## 기능과 RPC 메서드 추가

새 메서드는 다음 순서로 연결합니다. 빠뜨리면 사용자가 그 기능을 처음 쓸 때 "허용되지 않은 요청입니다"나 "지원하지 않는 요청입니다"를 보게 됩니다.

1. 백엔드: `backend/agent/service.py`의 `dispatch` 표(또는 그 표에 합쳐지는 `assistant.py`·`schedules.py`·`routines.py`의 `methods`)에 처리 함수를 등록합니다. 사용자가 고칠 수 있는 문제는 `UserError`로 알립니다.
2. LLM이나 브라우저를 기다리는 메서드는 `backend/main.py`의 `SLOW`에, 사용자가 화면에서 기다리는 메서드는 `INTERACTIVE_METHODS`에 넣습니다.
3. 330초보다 오래 걸릴 수 있으면 `electron/main.cjs`의 `RPC_TIMEOUTS`에 제한 시간을 정합니다.
4. 화면이 부르는 메서드는 `electron/trusted-sender.cjs`의 `ALLOWED`에 넣습니다. 비밀 값, 대화 상자, 클립보드, 사이트 계정처럼 메인 프로세스만 할 수 있는 일은 `handle()`에서 처리합니다.
5. 응답 타입을 `src/lib/api/types.ts`에 적습니다. 화면이 캐시하는 목록이면 `src/lib/query.ts`의 `TAGS`와, 바꾼 뒤 무효로 할 태그를 정합니다.
6. AI 도구도 쓰게 하려면 `electron/api-catalog.cjs`의 `METHODS`에 범위와 설명을 넣고, 필요하면 `GROUPS`에 REST 경로와 매개변수를, 값 하나가 더 높은 범위를 요구하면 `FIELD_SCOPES`를 적습니다. 전용 MCP 도구가 필요하면 `electron/mcp.cjs`의 `TOOLS`에 추가합니다. 파괴적인 기능은 범위 `destructive`나 `admin`으로 둡니다.
7. 새 종류의 백그라운드 작업은 `<종류>.start`·`status`·`cancel`·`dismiss`를 갖추고 `electron/job-progress.cjs`의 `JOB_KINDS`에 넣어, 진행이 `job:progress`로 창에 가게 합니다.
8. `npm run generate:methods`와 `npm run docs:api`를 실행해 생성 파일을 함께 커밋합니다.
9. 백엔드 테스트(`backend/tests/`)와 메인 프로세스 테스트를 추가합니다. `tests/methods.test.cjs`는 화면이 부르는 모든 메서드가 `ALLOWED`에 있고 메인 프로세스나 백엔드가 처리하는지 확인합니다. 화면을 바꿨다면 E2E(`tests/*.spec.ts`)도 봅니다.
10. 사용자에게 보이는 변경은 README와 [CHANGELOG.md](CHANGELOG.md)에 적습니다.

## 보안 경계

자세한 위협 모델은 [SECURITY.md](SECURITY.md)에 있습니다. 구조와 관련된 규칙만 요약합니다.

- 렌더러는 샌드박스 안에서 요청 통로 하나만 씁니다. 메인 프로세스는 보낸 프레임과 `ALLOWED`를 확인하고, API Key·헤더 값·비밀번호·토큰을 화면에 보내지 않습니다(값이 있는지만 알림).
- 비밀 값은 메인 프로세스가 운영체제 보안 저장소(Electron `safeStorage`)로 암호화해 앱 설정 폴더에 둡니다. 사이트 비밀번호는 그 사이트의 로그인 계획에 담아 필요한 요청에만 백엔드로 넘깁니다.
- 백엔드는 포트를 열지 않습니다. 로컬 API는 기본으로 꺼져 있고, 켜도 `127.0.0.1`에서 토큰과 허용 범위로만 받습니다.
- 사이트 주소는 HTTPS가 기본이며 HTTP로 내려가지 않습니다. 브라우저 자동화는 Chromium 샌드박스를 켠 채 실행합니다.
- 문서 삭제는 휴지통으로 가고, 영구 삭제·백업 복원·파일 가져오기·설정 변경은 허용 범위 ‘전체’에서만 AI 도구가 할 수 있습니다.
- 로그와 진단 복사본은 키·토큰·비밀번호를 가립니다.

## 빌드와 패키징

배포 파일은 Windows에서 만듭니다. `npm run package:win`(`scripts/package.mjs`)은 다음을 차례로 실행합니다.

1. 빌드한 커밋을 `electron/build-info.json`에 적습니다(진단 복사본에 표시, Git에서 제외).
2. `next build`: 화면을 `out/`으로 정적 내보내기합니다(`NEXT_TELEMETRY_DISABLED=1`).
3. `scripts/build-python.mjs`: PyInstaller `--onedir`로 백엔드를 `dist/backend/private-agent-backend/`에 만듭니다. UTF-8 모드로 실행되게 하고, setuptools와 debugpy는 넣지 않습니다. Windows에서는 만든 백엔드가 모든 표준 확장 모듈을 불러오는지 확인합니다(`--check-build`).
4. `scripts/build-browser.mjs`: 백엔드의 Playwright가 쓰는 Chromium을 `dist/browsers`에 설치합니다. 패키징한 앱은 `PLAYWRIGHT_BROWSERS_PATH`를 이 폴더로 정합니다.
5. electron-builder(`package.json`의 `build`): `electron/`, `out/`, `package.json`을 `app.asar`에 넣고, `dist/browsers`와 `dist/backend`를 `resources/`에, `LICENSE`와 `THIRD_PARTY_NOTICES.md`를 앱 폴더에 넣으며 Electron fuses를 적용합니다. nsis 대상과 `scripts/launcher.nsi`로 `release/Private-Agent-<버전>-x64.exe` 하나를 만듭니다. 백신 검사 때문에 `EPERM`·`EBUSY`로 멈추면 세 번까지 다시 시도합니다.

이 exe는 설치 프로그램이 아니라 실행기입니다. 빌드마다 처음 실행할 때 앱을 `%LOCALAPPDATA%\Private Agent\app-<버전>-<빌드 시각>`에 풀고, 그 뒤에는 바로 실행합니다. 레지스트리, 바로 가기, 제거 프로그램을 만들지 않으며, 실행 중인 앱이 없을 때 더 오래된 빌드의 폴더를 지웁니다.

`npm run build:exe`(`build-exe.cmd`)는 `npm ci`, 세 가지 준비 스크립트, `npm run check`, `npm run package:win`을 실행한 뒤 패키징한 앱(`release/win-unpacked`)으로 E2E를 돌립니다. GitHub Actions의 `build.yml`이 `v*` 태그나 수동 실행에서 이를 실행하고, 서명 시크릿이 있으면 코드 서명하며, `SHA256SUMS`와 함께 아티팩트로 올립니다. 버전과 태그의 관계는 [CHANGELOG.md](CHANGELOG.md#버전-정책)를 보세요.
