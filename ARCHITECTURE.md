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
   │  stdin/stdout JSON 줄(agent/hermes.py)
   ▼
Hermes 워커(resources/hermes, 내장 CPython) — 채팅의 에이전트: 자기 도구로 실행하고, 모델 호출과 앱의 작업은 백엔드에 맡김
```

| 프로세스      | 맡는 일                                                                                                                                                               | 경계                                                                                                                                                                         |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 렌더러        | 화면. Next.js 16(React 19)의 정적 내보내기(`output: 'export'`)를 메인 프로세스가 `agent://bundle/` 프로토콜로 제공합니다                                              | `sandbox`·`contextIsolation`, Node.js 없음, 스크립트 해시 기반 CSP. 외부 이동·새 창·webview·권한 요청을 막습니다. 요청 통로는 preload가 연 하나뿐입니다                      |
| Electron 메인 | 창, IPC 허용 목록, 설정 파일과 비밀 값(운영체제 보안 저장소), 사이트 계정과 로그인 계획, 로컬 API·MCP 서버, 알림·자동 작업의 시각 확인, 로그·진단, 백엔드 시작·재시작 | 렌더러의 요청은 보낸 프레임과 `ALLOWED`로, AI 도구의 요청은 토큰과 허용 범위로 거릅니다                                                                                      |
| Python 백엔드 | `wiki.db`, 검색, LLM 호출, 가져오기(Office COM 워커, Playwright Chromium), 원본 반영, 자동 작업 실행, 백업, 채팅 작업 실행과 웹 기록(보이는 Chromium 창)              | 포트를 열지 않고 메인 프로세스와 표준 입출력으로만 통신합니다. LLM 설정(API Key 포함)은 시작할 때 받아 메모리에만 두고, 사이트 비밀번호는 로그인이 필요한 요청에 담겨 옵니다 |

메인 프로세스는 앱이 시작할 때 저장된 Wiki 폴더로 백엔드를 띄우고(`electron/storage.cjs`), 처음 실행이면 화면이 폴더를 고르게 합니다. 백엔드가 예기치 않게 끝나면 1초, 5초, 15초 뒤에 다시 시작합니다. 2분 안에 세 번 다시 시작하고도 또 끝나면 더 시작하지 않고 사용자에게 알립니다(`electron/restarts.cjs`). 이렇게 다시 시작한 백엔드는 예약된 백업 복원을 앱의 다음 시작까지 그대로 둡니다(환경 변수 `PRIVATE_AGENT_DEFER_RESTORE`, `agent/storage.py`의 `apply_restore`).

## 요청 경로

1. 화면이 `api(method, params)`를 부릅니다(`src/lib/api/transport.ts`). `method`는 `src/lib/api/methods.generated.ts`의 `Method` 타입이어야 합니다.
2. preload(`electron/preload.cjs`)가 `ipcRenderer.invoke('agent:request', method, params)`로 보냅니다.
3. 메인 프로세스의 `ipcMain.handle('agent:request')`는 `allowedRequest()`(`electron/trusted-sender.cjs`)로 보낸 곳이 메인 창의 메인 프레임이고 주소가 `agent://bundle/` 또는 개발 서버인지(`trustedSender`), 메서드가 `ALLOWED`에 있는지 확인하고, 매개변수가 객체이며 JSON으로 900,000자 이하인지 봅니다. 문서의 글을 싣는 `wiki.save`와 `wiki.compare`만 문서 한도(800만 자)에 맞춰 더 크게 받고(`DOCUMENT_PARAMS`), 백엔드도 그 두 메서드의 줄만 길게 읽습니다(`backend/main.py`의 `DOCUMENT_LINES`). AI 도구의 요청은 로컬 API의 본문 1MiB 상한을 거치므로 900,000자 그대로입니다.
4. `api.*`는 로컬 API 설정(`apiRequest`), `app.relaunch`(예약한 백업 복원을 적용하는 ‘지금 다시 시작’)는 앱을 닫고 같은 프로그램으로 다시 실행하며, `app.theme`(화면 테마 설정)은 창의 바탕색과 Electron의 테마를 그 설정에 맞추고(`window-theme.cjs`), 나머지는 `handle()`로 갑니다. `handle()`은 메인 프로세스만 할 수 있는 일(저장 위치, 진단, 앱 창 제어, 사이트 계정, LLM 비밀 값, 클립보드, 파일 선택 대화 상자, JIRA·Confluence 가져오기에 로그인 계획 붙이기)을 직접 처리하고, 나머지는 `rpc()`로 백엔드에 넘깁니다.
5. `rpc()`는 백엔드가 (다시) 시작하는 동안 기다렸다가 `{"id", "method", "params", "limit_ms"}` 한 줄을 표준 입력에 씁니다. 응답 제한 시간 `limit_ms`는 `RPC_TIMEOUTS`(`electron/main.cjs`)에 있으면 그 값, 없으면 330초입니다. 시간이 지나면 메인 프로세스 쪽만 실패로 끝나고 백엔드의 작업은 계속됩니다. 그래서 오래 걸릴 수 있는 작업은 줄에 실린 제한 시간 안에 스스로 끝냅니다. AI 분석(`wiki.analyze`)은 그 1분 전까지 LLM 요청을 마치고, 시간이 모자라 요약하지 못한 섹션은 문서의 안내에 적습니다(`service.py`의 `wiki_analyze`).
6. 백엔드(`backend/main.py`)는 줄마다 처리 줄을 고릅니다. `SLOW`의 메서드와 `mode`가 `prompt`인 `wiki.search`는 느린 줄(4개), 나머지는 빠른 줄(24개)입니다. 줄이 차 있으면 기다리지 않고 바로 "진행 중인 요청이 많습니다"로 답합니다. `INTERACTIVE_METHODS`의 LLM 요청은 일괄 작업보다 먼저 갑니다(`agent/llm.py`의 `Slots`).
7. `Service.dispatch`(`agent/service.py`)가 처리 함수를 부르고, 백엔드는 `{"id", "result"}`나 `{"id", "error"}`로 답합니다. `UserError`는 그 메시지를, 예기치 않은 예외는 로그에 남긴 뒤 오류 ID가 붙은 일반 메시지를 돌려줍니다. 스택이나 비밀 값은 화면으로 가지 않습니다.
8. 메인 프로세스가 `{ok: true, result}`나 `{ok: false, error}`로 답하고, `api()`는 실패를 `Error`로 던집니다.

AI 도구의 요청은 `electron/api-server.cjs`(127.0.0.1, Host·Origin 검사, 토큰)에서 `callApi()`로 옵니다. `authorize()`(`electron/api-catalog.cjs`)가 메서드의 범위와 사용자가 고른 허용 범위를 맞춰 보고, 파일 경로 검사(`electron/api-methods.cjs`)를 거친 뒤 화면과 같은 `handle()`로 처리합니다. 무엇을 바꾼 요청이면 창에 `api:changed`를 보내 화면이 다시 읽게 합니다.

## 이벤트

- 백엔드가 스스로 보내는 이벤트는 `id` 없는 줄 `{"event", "params"}`입니다. 메인 프로세스는 `BACKEND_EVENTS`에 있는 `assistant.delta`(LLM이 쓰는 답변의 조각)와 `assistant.done`(끝)만 창의 `assistant:delta` 채널로 넘기고, 나머지는 버립니다.
- 화면은 `stream`과 자기 `request_id`를 붙여 물은 답변의 이벤트만 상태 조각 하나(`src/components/answers.ts`)에 모읍니다. 검색창은 그 글을 쓰는 대로(150ms마다) 그리고, 최근 대화 목록은 답변 중인 대화와 아직 저장되지 않은 새 대화를 보여 줍니다. ‘답변 중지’는 `assistant.cancel {request_id}`를 보냅니다.
- 메인 프로세스가 보내는 이벤트: `job:progress`(가져오기·AI 분석·자동 작업의 진행, `electron/job-progress.cjs`가 따라가며 번호를 붙임), `schedule:event`, `routine:event`, `import:notice`, `assistant:open`, `api:changed`, `control:command`(로컬 API의 화면 명령, 화면은 `control:result`로 답함), `chat:focus`(빠른 호출의 바로 가기 키와 트레이의 ‘채팅 열기’: 채팅을 열고 입력란에 커서를 둠. 실어 보내는 내용 없음)
- 화면은 백엔드의 응답을 메서드와 매개변수별로 캐시합니다(`src/lib/query.ts`). 변경, 끝난 작업, 메인 프로세스의 알림이 태그(`documents`, `prompts`, `runs`, `routines`, `tasks`, `conversations`)를 무효로 하면 그 태그를 가진 응답만 다시 읽습니다. 보관함의 문서 목록은 같은 캐시의 나누어 받는 목록(`QueryCache.list`, `useDocumentPages`)으로 100개씩 불러오고, 무효가 되면 보이던 만큼을 첫 쪽부터 다시 읽으며, 보이는 줄만 그립니다(`src/components/windowing.ts`).

## 데이터 위치

### Wiki 폴더

사용자가 처음 실행할 때 고른 폴더입니다(기본 제안은 문서 폴더의 `Private Agent`).

| 경로                    | 내용                                                                                                                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wiki.db`               | SQLite(WAL). 스키마 버전은 `PRAGMA user_version`이고 지금은 1입니다(`backend/agent/migrations.py`)                                                                            |
| `backups/`              | `wiki-YYYYMMDD-HHMMSS.db`(하루 한 번 자동, 최근 7개), `wiki-manual-….db`(직접 백업, 최근 7개), `wiki-before-restore-….db`(복원으로 바뀐 Wiki, 지우지 않음)                    |
| `backups/restore.json`  | 예약된 복원. 함께 있는 `restore-<id>.db`를 앱의 다음 시작 때 `wiki.db` 자리에 넣고, 결과를 `last-restore.json`에 남깁니다                                                     |
| `원본 백업/`            | 원본 반영 전에 복사한 Office 파일(파일마다 최근 5개)과 저장 단계를 적는 `.writing` 파일                                                                                       |
| `web-captures/YYYY-MM/` | 웹 기록 때 찍은 페이지 스크린샷(`<기록 id>.jpg`)                                                                                                                              |
| `exports/`              | 웹 기록의 CSV 내보내기(`web-records-YYYYMMDD-HHMMSS.csv`, UTF-8 BOM)                                                                                                          |
| `hermes/`               | 내장 Hermes의 홈(`HERMES_HOME`): 메모리·스킬·세션 기록(`state.db`), 턴마다 다시 쓰는 `config.yaml`, 임시·캐시 폴더. API Key와 헤더 값, MCP 서버 설정의 값은 들어가지 않습니다 |

`wiki.db`의 테이블은 다음과 같이 묶입니다(`backend/agent/db.py`).

- 문서: `documents`(삭제하면 `deleted_at`을 적고 30일 휴지통에 둠), `document_html`(가져온 파일의 HTML 페이지), `document_search`와 FTS5 색인 `document_fts`(trigram), `doc_nodes`(긴 문서의 목차 색인, PageIndex)
- AI 분석: `keywords`, `keyword_aliases`(키워드의 한국어·영어 별칭), `relations`
- 출처: `office_sources`, `rtf_sources`, `image_sources`, `text_sources`, `markdown_sources`, `confluence_sources`, `jira_sources`, `ignored_sources`(영구 삭제한 출처), `writeback_backups`(원본 백업 기록)
- 프롬프트와 자동화: `prompts`, `runs`, `schedules`, `routines`, `routine_runs`
- 비서: `tasks`, `conversations`, `messages`
- 휴지통: `trash`(지운 대화·실행 기록·할 일·프롬프트 템플릿·예약·자동 작업의 행과 함께 지워진 행을 JSON으로 30일 보관. 문서는 `documents.deleted_at`)
- 채팅과 웹 기록: `agent_messages`(채팅 대화와 작업 카드, 최근 1,000개), `agent_activity`(답마다 그 턴이 한 일: 쓴 도구, 앱 작업, 확인을 받은 명령과 결정), `web_captures`(기록한 페이지), `web_items`(광고·상품·검색 결과, 직접 누른 광고의 도착 페이지)
- `metadata`: 검색·대시보드 설정(`preferences`)과 색인 상태

가져온 이미지는 별도 파일 없이 HTML 안의 `data:` URI로 저장합니다. Office 그림을 내보낼 때 쓰는 임시 폴더(`private-agent-office-…`, `private-agent-pictures-…`)는 시스템 임시 폴더에 생기고, 백엔드가 시작할 때 10분 넘은 것을 지웁니다.

### 앱 설정 폴더

Electron의 `userData` 폴더입니다. Windows에서는 `%APPDATA%\Private Agent`, `npm run dev`에서는 `.local-data/dev-user-data`이며 `PRIVATE_AGENT_USER_DATA`로 바꿀 수 있습니다.

| 경로                                     | 내용                                                                                                                                                                                                                             |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `storage.json`                           | 고른 Wiki 폴더                                                                                                                                                                                                                   |
| `settings.json`                          | LLM 연결 설정. API Key(`encrypted_api_key`)와 헤더 값(`encrypted_headers`)은 암호화하고 헤더는 이름만 둡니다. 보안 저장소를 쓸 수 없으면 새 API Key는 실행 중 메모리에만 두고 헤더 값은 파일에 그대로 저장합니다                 |
| `sites.json`                             | 사이트 계정, 로그인 방식, 고정한 로그인 주소. 비밀번호는 `encrypted_password`                                                                                                                                                    |
| `api.json`                               | 로컬 API의 켜짐, 포트(기본 47615), 허용 범위(기본 `read`), 암호화한 토큰                                                                                                                                                         |
| `hermes.json`                            | 채팅 에이전트 설정: 작업 폴더, 컴퓨터 사용, 항상 허용한 명령(`allowed`), 보호할 폴더와 실행하지 않을 명령, 암호화한 서비스 키와 MCP 서버 설정(`encrypted`). 보안 저장소를 쓸 수 없으면 키와 MCP 설정은 실행 중 메모리에만 둡니다 |
| `window.json`                            | 화면 배율                                                                                                                                                                                                                        |
| `quick-access.json`                      | 빠른 호출: 채팅 바로 가기 키(정해 둔 키 가운데 하나, 기본은 꺼진 빈 값), 트레이 아이콘, 창을 닫아도 계속 실행(기본 꺼짐)                                                                                                         |
| `logs/app.log`, `logs/backend.log`       | 메인 프로세스와 백엔드의 로그. 각각 1MB를 넘으면 `.1`로 하나 남깁니다                                                                                                                                                            |
| `confluence-profiles/`, `jira-profiles/` | 사이트 주소(origin)마다 하나인 Chromium 프로필. 그 사이트의 마지막 계정을 지우면 함께 지웁니다                                                                                                                                   |
| `web-profiles/default/`                  | 웹 기록용 보이는 브라우저의 Chromium 프로필(쿠키와 로그인 상태). 사용자가 그 창에서 로그인한 사이트의 세션이 남습니다                                                                                                            |
| `mcp/private-agent-mcp.cjs`              | stdio로만 연결하는 AI 도구가 실행하는 MCP 연결 프로그램. 로컬 API를 켤 때 씁니다                                                                                                                                                 |

화면 테마는 창의 localStorage(`private-agent:theme`)에 있습니다. 화면이 열릴 때와 테마를 바꿀 때 메인 프로세스에 알리고(`app.theme`), 창 크기를 바꾸는 동안 보이는 창의 바탕색과 Electron이 그리는 메뉴가 그 테마를 따릅니다. 배포용 exe는 앱을 `%LOCALAPPDATA%\Private Agent\app-<버전>-<빌드 시각>`에 풀어 실행합니다.

## 모듈 지도

### backend

| 모듈                                                                                                                   | 역할                                                                                                     |
| ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `main.py`                                                                                                              | 표준 입출력 JSON 줄 RPC 루프, 빠른·느린 줄, 이벤트 출력, Wiki를 열지 못할 때의 거절 응답                 |
| `agent/service.py`                                                                                                     | `Service.dispatch`의 메서드 표, Wiki·프롬프트·휴지통·백업 처리, 보관 정책, 종료                          |
| `agent/db.py`, `agent/migrations.py`                                                                                   | 스키마, 이전 형식의 Wiki 올리기, FTS5 색인, 스키마 버전과 번호 붙은 마이그레이션                         |
| `agent/storage.py`                                                                                                     | 시작할 때 검사(`quick_check`, 스키마 버전), 이전 Wiki 복사, 백업과 예약된 복원                           |
| `agent/search.py`, `agent/paging.py`                                                                                   | BM25·trigram 단어 검색, 키워드 별칭, LLM이 고르는 AI 검색, 목록·검색 결과의 나누어 받기                  |
| `agent/llm.py`                                                                                                         | LLM 설정, OpenAI·Anthropic 형식, 재시도, 우선순위, 스트리밍, 중지                                        |
| `agent/assistant.py`                                                                                                   | 문서를 근거로 한 대화, 할 일과 알림, 오늘의 브리핑                                                       |
| `agent/trash.py`                                                                                                       | 지운 대화·실행 기록·할 일·템플릿·예약·자동 작업의 휴지통: 보관, 복원, 영구 삭제                          |
| `agent/pageindex.py`, `agent/batch_analysis.py`                                                                        | 긴 문서의 목차 색인, 여러 문서의 백그라운드 AI 분석                                                      |
| `agent/routines.py`, `agent/schedules.py`                                                                              | 자동 작업과 예약 프롬프트를 하나의 대기열과 작업자 3개로 실행                                            |
| `agent/sources.py`, `agent/parts.py`                                                                                   | 가져온 출처의 기록, 다시 가져오기와 이동한 파일 연결, 문서 한도를 넘는 글 줄이기와 예전 분할 문서 합치기 |
| `agent/file_import.py`, `office_import.py`, `rtf_import.py`, `image_import.py`, `text_import.py`, `markdown_import.py` | 파일·폴더 가져오기 작업과 종류별 변환                                                                    |
| `agent/office_converter.py`, `office_session.py`, `office_processes.py`                                                | 매크로를 막은 일회용 Office COM 워커, 남은 Office 프로세스 정리                                          |
| `agent/word_html.py`, `office_html.py`, `drawing.py`, `vml.py`                                                         | Word·Excel·PowerPoint·Visio 문서를 HTML 페이지와 SVG 그림으로                                            |
| `agent/writeback.py`, `office_write.py`, `source_edits.py`                                                             | 고친 내용을 원본 Office 파일에 반영, 원본 백업                                                           |
| `agent/browser_import.py`, `confluence_import.py`, `jira_import.py`                                                    | 사이트별 Chromium 프로필, 요청 속도 제한, Confluence 페이지와 JIRA 티켓 수집                             |
| `agent/web_login.py`, `agent/web_assist.py`                                                                            | 로그인 방식에 따른 자동 로그인, 로그인·접속 실패 시 LLM 도움(기본 꺼짐)                                  |
| `agent/diagnostics.py`                                                                                                 | `backend.log`와 비밀 값 가리기, 오류 ID가 붙은 사용자 메시지                                             |
| `agent/agent_chat.py`                                                                                                  | 채팅: 대화 보관, 에이전트가 부른 앱 작업의 검사·실행과 카드, 진행 중인 턴(`agent.*`)                     |
| `agent/hermes.py`, `agent/hermes_llm.py`                                                                               | 내장 Hermes 워커 실행·중지와 턴의 프레임 처리(승인·질문 포함), 모델 호출의 LLM 연결 중계                 |
| `agent/agent_guard.py`, `agent/agent_memory.py`                                                                        | 보호 구역의 경로 계산, 에이전트의 기억(메모·사용자 정보) 읽기·저장과 작업 기록 지우기                    |
| `agent/hermes_gateway.py`                                                                                              | 메신저 게이트웨이: 두 번째 워커 프로세스의 시작·재시작·종료, 모델 호출과 앱 작업의 중계                  |
| `agent/web_capture.py`                                                                                                 | 웹 기록: 보이는 Chromium 창, 페이지 읽기, 직접 누른 광고와 도착 페이지, CSV(`web.*`)                     |

### electron

| 모듈                                                    | 역할                                                                                                                                     |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `main.cjs`                                              | 앱 시작과 종료, 창, `handle()`, 백엔드 실행, `settings.json`, 각 부분의 연결                                                             |
| `protocol.cjs`                                          | `agent://bundle/` 프로토콜: `out/` 밖의 경로를 거절하고 파일 종류와 스크립트 해시 CSP를 붙여 정적 화면 제공                              |
| `trusted-sender.cjs`                                    | IPC를 보낸 곳 확인(`trustedSender`: 메인 창의 메인 프레임과 화면 주소)과 화면이 부를 수 있는 메서드 `ALLOWED`                            |
| `preload.cjs`                                           | 화면에 여는 `window.privateAgent`: 요청 하나와 이벤트 구독                                                                               |
| `backend-process.cjs`, `restarts.cjs`                   | 백엔드 프로세스 하나의 요청·응답·이벤트·제한 시간, 예기치 않게 끝난 백엔드의 재시작                                                      |
| `storage.cjs`                                           | Wiki 폴더 선택(`storage.json`), 이전 버전 Wiki 복사, 폴더 쓰기 확인                                                                      |
| `sites.cjs`, `login-methods.json`                       | 사이트 계정(`sites.json`, 암호화한 비밀번호)과 로그인 계획, 로그인 방식 목록                                                             |
| `hermes-settings.cjs`                                   | 채팅 에이전트 설정(`hermes.json`): 작업 폴더, 컴퓨터 사용, 암호화한 서비스 키·봇 토큰·MCP 서버, 메신저 계정, 항상 허용한 명령, 보호 구역 |
| `api-catalog.cjs`                                       | 로컬 API의 모든 메서드, 범위, REST 경로. REST·MCP·매뉴얼·OpenAPI가 모두 이 표에서 만들어집니다                                           |
| `api-server.cjs`, `mcp.cjs`, `mcp-stdio.cjs`            | 127.0.0.1 REST·MCP 서버, MCP 도구·리소스·프롬프트, stdio 연결 프로그램                                                                   |
| `api-manual.cjs`, `api-settings.cjs`, `api-methods.cjs` | 매뉴얼(JSON·Markdown·HTML·OpenAPI), `api.json`, AI 도구 전용 메서드와 경로 검사                                                          |
| `app-control.cjs`                                       | 로컬 API가 창과 화면을 다루는 명령                                                                                                       |
| `job-progress.cjs`, `routines.cjs`, `reminders.cjs`     | 작업 진행 알림, 자동 작업·예약 실행 시각 확인(30초), 할 일 알림(15초)                                                                    |
| `diagnostics.cjs`, `zoom.cjs`, `files.cjs`              | `app.log`와 진단 복사본, 화면 배율, 원자적 JSON 파일 쓰기                                                                                |
| `window-theme.cjs`                                      | 창의 바탕색과 Electron의 테마(`nativeTheme.themeSource`)를 화면 테마 설정에 맞춤                                                         |
| `quick-access.cjs`                                      | 빠른 호출: 시스템 전체 바로 가기 키, 트레이 아이콘과 메뉴, 창을 닫아도 트레이에서 계속 실행(`quick-access.json`)                         |
| `demo.cjs`, `demo-plan.cjs`, `recorder.cjs`, `mp4.cjs`  | 데모 녹화(`record-demo.cmd`)                                                                                                             |

### src

| 모듈                                                    | 역할                                                                                           |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `app/`                                                  | Next.js 진입점(정적 내보내기)                                                                  |
| `components/app.tsx`, `components/state.tsx`            | 시작 상태와 이벤트 연결, 따로 바뀌는 상태 조각과 선택자 훅(`useView`, `useQuery` 등)           |
| `components/workspace.tsx`, `dashboard.tsx`, `widgets/` | 사이드바와 화면 전환, 위젯 대시보드                                                            |
| `components/reader.tsx`, `search-panel.tsx`             | 문서·실행 기록 보기와 편집(충돌 비교 포함), 검색창과 AI 답변                                   |
| `components/admin.tsx`, `admin/`                        | 관리 화면: LLM 연결, 채팅 에이전트, 사이트 계정, 템플릿, AI 도구 연동, API 매뉴얼, 휴지통      |
| `components/html-document.tsx`, `lib/html-document.ts`  | 가져온 HTML을 허용 목록으로 정제해 shadow DOM에 표시                                           |
| `lib/api.ts`, `lib/api/`                                | 요청 함수(`transport.ts`), 응답 타입(`types.ts`), 표시 형식(`format.ts`), 생성된 `Method` 타입 |
| `lib/query.ts`, `lib/store.ts`                          | 태그로 무효화하는 요청 캐시와 나누어 받는 목록, 선택자로 읽는 작은 상태 저장소                 |
| `components/windowing.ts`                               | 긴 목록에서 보이는 줄만 그리기(측정한 줄 높이)                                                 |
| `components/agent-chat.tsx`, `agent-turn.ts`            | 첫 화면인 채팅(진행·명령 확인·질문·작업 카드·한 일), 화면을 옮겨도 이어지는 진행 중인 턴       |
| `components/admin/hermes*.tsx`                          | 채팅 에이전트 설정: 키·도구·게이트웨이, 묻지 않는 명령과 보호 구역, 에이전트의 기억            |
| `components/web-records.tsx`                            | 웹 기록 표와 CSV 내보내기                                                                      |

### scripts

| 파일                                                                                    | 역할                                                                              |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `dev.mjs`, `run-dev.mjs`                                                                | 개발 서버와 Electron, 파일 감시와 재시작 / 준비부터 실행까지 한 번에              |
| `setup-python.mjs`, `setup-browser.mjs`, `setup-electron.mjs`                           | 가상 환경, Chromium, Electron 준비                                                |
| `test-python.mjs`, `test-e2e.mjs`, `lint-python.mjs`                                    | pytest, Playwright(Linux에서는 xvfb-run), ruff 실행                               |
| `package.mjs`, `build-python.mjs`, `build-browser.mjs`, `build-exe.mjs`, `launcher.nsi` | 패키징(아래 참고)                                                                 |
| `build-hermes.mjs`, `build-hermes.py`                                                   | 내장 Hermes 준비: 고정한 소스·CPython·해시 고정 wheel을 `dist/hermes`에           |
| `generate-methods.mjs`, `export-api-docs.mjs`, `third-party-notices.mjs`                | 생성 파일: 화면의 `Method` 타입, `docs/api.md`·`docs/openapi.json`, 서드파티 고지 |

## 기능과 RPC 메서드 추가

새 메서드는 다음 순서로 연결합니다. 빠뜨리면 사용자가 그 기능을 처음 쓸 때 "허용되지 않은 요청입니다"나 "지원하지 않는 요청입니다"를 보게 됩니다.

1. 백엔드: `backend/agent/service.py`의 `dispatch` 표(또는 그 표에 합쳐지는 `assistant.py`·`schedules.py`·`routines.py`의 `methods`)에 처리 함수를 등록합니다. 사용자가 고칠 수 있는 문제는 `UserError`로 알립니다.
2. LLM이나 브라우저를 기다리는 메서드는 `backend/main.py`의 `SLOW`에, 사용자가 화면에서 기다리는 메서드는 `INTERACTIVE_METHODS`에 넣습니다.
3. 330초보다 오래 걸릴 수 있으면 `electron/main.cjs`의 `RPC_TIMEOUTS`에 제한 시간을 정합니다. 처리 함수는 그 제한 시간에서 남은 초를 `Service.dispatch`의 `within`으로 받으므로, 시간 안에 스스로 끝내야 하는 작업은 이 값을 씁니다(`wiki.analyze`).
4. 화면이 부르는 메서드는 `electron/trusted-sender.cjs`의 `ALLOWED`에 넣습니다. 비밀 값, 대화 상자, 클립보드, 사이트 계정처럼 메인 프로세스만 할 수 있는 일은 `handle()`에서 처리합니다.
5. 응답 타입을 `src/lib/api/types.ts`에 적습니다. 화면이 캐시하는 목록이면 `src/lib/query.ts`의 `TAGS`와, 바꾼 뒤 무효로 할 태그를 정합니다.
6. AI 도구도 쓰게 하려면 `electron/api-catalog.cjs`의 `METHODS`에 범위와 설명을 넣고, 필요하면 `GROUPS`에 REST 경로와 매개변수를, 값 하나가 더 높은 범위를 요구하면 `FIELD_SCOPES`를 적습니다. 전용 MCP 도구가 필요하면 `electron/mcp.cjs`의 `TOOLS`에 추가합니다. 파괴적인 기능은 범위 `destructive`나 `admin`으로 둡니다.
7. 새 종류의 백그라운드 작업은 `<종류>.start`·`status`·`cancel`·`dismiss`를 갖추고 `electron/job-progress.cjs`의 `JOB_KINDS`에 넣어, 진행이 `job:progress`로 창에 가게 합니다.
8. `npm run generate:methods`와 `npm run docs:api`를 실행해 생성 파일을 함께 커밋합니다.
9. 백엔드 테스트(`backend/tests/`)와 메인 프로세스 테스트를 추가합니다. `tests/methods.test.cjs`는 화면이 부르는 모든 메서드가 `ALLOWED`에 있고 메인 프로세스나 백엔드가 처리하는지 확인합니다. 화면을 바꿨다면 E2E(`tests/*.spec.ts`)도 봅니다.
10. 사용자에게 보이는 변경은 README와 [CHANGELOG.md](CHANGELOG.md)에 적습니다.

## 채팅과 웹 기록

채팅은 첫 화면입니다. 메시지를 보내면(`agent.send`) 백엔드가 최근 대화와 함께 내장 Hermes 에이전트에 넘기고, 에이전트가 턴 하나를 끝낼 때까지 실행합니다.

1. `agent/hermes.py`가 `resources/hermes`(개발 중에는 `dist/hermes`)의 CPython으로 `worker.py`를 띄웁니다(`-I`). 실행 전에 `manifest.json`의 `worker_sha256`을 확인하고, 환경 변수는 사용자의 Hermes·프록시·Python 설정을 버린 최소한에 사용자가 넣은 서비스 키만 더해 넘깁니다(`child_environment`). 워커는 Wiki마다 하나이며 다음 턴에 다시 씁니다. 끝까지 가지 못한 턴의 워커는 끝내고 새로 띄웁니다.
2. 워커와 백엔드는 표준 입출력으로 JSON 줄(프레임)을 주고받습니다. 워커가 보내는 것은 `inference`(모델 요청), `app_tool`(앱의 작업), `approval`(위험한 명령의 확인), `grant`(‘이 대화에서 허용’이 덮는 명령 종류), `clarify`(사용자에게 묻는 질문), `progress`·`draft`(진행과 작성 중인 답), `result`·`error`이고, 백엔드는 `turn`, `completion`, `app_tool_reply`, `approval_reply`, `clarify_reply`, `cancel`, `shutdown`을 보냅니다.
3. Hermes는 자기 도구(터미널, 파일, 코드 실행, 웹 검색, 브라우저, 메모리, 스킬, 컴퓨터 사용, MCP 서버)를 워커 안에서 바로 실행합니다. 모델을 부를 때마다(보조 모델과 하위 에이전트 포함) 요청이 `inference`로 백엔드에 오고, `agent/hermes_llm.py`가 앱의 LLM 연결(키, 헤더, CA)로 보냅니다. 키는 워커에 들어가지 않습니다. 모델 요청 하나는 120초(연결의 응답 제한 시간이 더 짧으면 그 시간), 턴 전체는 그 제한 시간의 4배(2분~15분)까지이며, 사용자의 답을 기다린 시간은 세지 않습니다.
4. 앱의 작업(문서 찾기·읽기·새 문서 저장, 웹 기록 요약, 사이트 열기, 페이지 기록, 할 일 보기·추가·완료, 자동 작업 보기, 예약, 오늘 현황)은 Hermes가 도구로 부르면 `agent/agent_chat.py`가 이름과 인수를 검사해(`card`) 실행하고(`run`), 결과를 카드로 남깁니다.
5. Hermes의 검사가 위험하다고 본 명령은 `approval` 프레임으로 멈춥니다. 화면이 `agent.status`로 이를 읽어 묻고, 사용자의 결정(`agent.approve_command`)이나 질문의 답(`agent.answer_clarify`)을 돌려보냅니다. 5분 안에 답이 없으면 거부·취소로 처리하고, **중지**(`agent.stop`)는 기다리는 중에도 턴을 끝냅니다. 명령의 결정은 네 가지입니다. ‘이번만 실행’은 그 한 번, ‘이 대화에서 허용’은 Hermes가 분류한 같은 종류의 명령을 대화를 비우거나 앱을 닫을 때까지(백엔드 메모리의 `session_allowed`, 턴마다 워커에 다시 넘김), ‘항상 허용’은 글자까지 같은 그 명령(메인 프로세스가 `hermes.json`의 `allowed`에 두고 `agent.configure`로 넘기면 워커가 턴마다 `config.yaml`의 `command_allowlist`로 씀), ‘거부’는 실행하지 않습니다. 턴이 한 일(쓴 도구, 앱 작업, 확인을 받은 명령과 결정, 질문)은 답과 함께 `agent_activity`에 남습니다.
6. 진행 중인 턴은 화면 밖에 둡니다(`src/components/agent-turn.ts`). 다른 화면으로 옮겨도 요청과 진행 확인이 이어지고, 에이전트가 답을 기다리거나 끝나면 알림으로 알립니다. 화면을 다시 불러오면 `agent.active`로 진행 중인 턴을 찾아 이어서 보여 줍니다.
7. 사이트 작업은 `agent/web_capture.py`가 맡습니다. Chromium 창을 사용자에게 보이게 열고(샌드박스 켬), 검색 결과를 읽어 광고·상품·검색 결과와 스크린샷을 남깁니다. 페이지의 링크나 광고를 누르지 않고, 로봇 확인(CAPTCHA)이 나오면 멈추고 사용자에게 넘깁니다. 사용자가 직접 누른 광고(`isTrusted` 클릭)만 도착 페이지까지 기록합니다. 사이트별 규칙은 `shared/web-capture/`에 있어 Android 앱도 같은 스크립트를 씁니다.

에이전트의 설정(**관리 → 채팅 에이전트**)은 메인 프로세스가 `hermes.json`에 두고(`electron/hermes-settings.cjs`), 시작할 때와 바뀔 때 `agent.configure`로 백엔드에 넘깁니다: 작업 폴더(터미널과 파일 도구가 시작하는 곳), 컴퓨터 사용, MCP 서버, 서비스 키, 메신저 게이트웨이(켜기, 봇마다 답할 계정, 봇 토큰). MCP 서버 설정의 값(env, headers, 인수, 주소)은 워커가 `config.yaml`에 쓰지 않고 환경 변수 참조로 바꿔 넘깁니다(`worker.py`의 `mcp_servers`). 워커는 시작할 때 받은 설정만 씁니다: Wiki의 `hermes` 폴더에 놓인 `.env`와 이 PC의 `/etc/hermes` 설정은 읽지 않습니다(`own_settings_only`).

보호 구역은 턴마다 백엔드가 경로 목록으로 만들어(`agent/agent_guard.py`: 앱의 설정 폴더, Wiki의 데이터베이스와 백업, 자격 증명이 있는 곳, 사용자가 더한 폴더. 에이전트의 홈과 작업 폴더는 그 안에 있어도 열어 둠) 워커에 넘깁니다. 워커는 그 경로를 Hermes 파일 도구의 읽기·쓰기 거절 규칙에 더하고(`guard_files`), 경로를 적은 명령과 사용자가 적은 패턴을 Hermes의 `approvals.deny`로 넘겨 승인과 관계없이 거절하게 합니다(`denied_commands`). 에이전트의 기억은 `hermes/memories`의 두 파일(`MEMORY.md`, `USER.md`)이고, 턴이 돌지 않을 때 `agent/agent_memory.py`가 읽고 저장합니다.

메신저 게이트웨이(`agent/hermes_gateway.py`)는 Hermes의 게이트웨이를 같은 `worker.py`의 두 번째 프로세스로 실행합니다(`run_gateway`). 사용자가 켜고, 봇 토큰과 답할 계정을 모두 넣은 메신저가 하나라도 있을 때만 띄웁니다.

1. 백엔드가 `gateway` 프레임으로 채팅과 같은 지시문·도구·설정을 넘기고, 봇 토큰은 그 프로세스의 환경 변수로만 넘깁니다. 채팅의 워커에는 봇 토큰을 주지 않습니다.
2. 워커는 Hermes의 메신저 어댑터만 실행합니다. 텔레그램은 롱 폴링, 디스코드와 슬랙은 밖으로 여는 소켓이라 포트를 열지 않습니다. Hermes가 게이트웨이와 함께 띄우는 것(제어 소켓, 로컬 API, 대시보드, 예약 실행, 작업 보드, 하트비트 소켓)은 시작하지 않고, 세 메신저가 아닌 플랫폼은 환경 변수에 키가 있어도 연결하지 않습니다.
3. 메신저에서 온 글이 턴이 되면 모델 요청은 `inference`로, 앱의 작업은 `app_tool`로 백엔드에 오고, 채팅과 같은 방식으로 처리합니다. 모델 요청은 그때의 LLM 연결로 보내며, 실패하면 그 사유를 워커에 돌려주어 메신저에 알립니다.
4. 답할 사람은 사용자가 적은 계정뿐입니다(`only_listed_accounts`). Hermes가 따로 허용하는 길(페어링 승인, 모두 허용 스위치, 채팅방·역할 허용)은 그 뒤에서 다시 걸러 냅니다. 메신저에서 쓸 수 있는 명령은 자기 대화를 다루는 것뿐이고(`GATEWAY_COMMANDS`), 승인을 끄거나(`/yolo`, `/approvals`) 모델·게이트웨이를 바꾸는 명령은 Hermes의 명령 등급으로 거절합니다. 이 제한은 Hermes의 관리 설정 층(`hermes/gateway/config.yaml`)에 둡니다.
5. 위험한 명령은 온 메신저에서 묻고, 적은 계정의 답(`/approve`·`/deny`나 버튼)을 5분까지 기다립니다.
6. 상태는 `gateway` 프레임으로 올라오고 화면이 `agent.gateway`로 읽습니다. Hermes의 오류 문장은 넘기지 않고 상태와 오류 코드만 넘깁니다(문장에 봇 토큰이 들어갈 수 있음). 설정이 바뀌면 다시 띄우고, 스스로 끝나면 간격을 늘려 가며 다섯 번까지 다시 띄웁니다. 앱을 닫으면 함께 끝납니다.

Android 앱(`android/`)은 같은 `shared/web-capture`를 빌드 때마다 assets로 복사해 WebView에서 쓰고, 채팅·기록·설정을 휴대폰 안에서 처리합니다. 내장 Hermes 실행 환경을 넣은 빌드에서는 같은 `worker.py`를 휴대폰에서 실행하고(터미널·파일·코드·브라우저 자동화·컴퓨터 사용은 끔), 넣지 않은 빌드에서는 설정한 LLM의 도구 호출로 계획해 카드를 확인받습니다. 채팅의 통제(명령 확인의 범위, 턴이 한 일, 보호 구역, 기억 관리)는 데스크톱의 `agent_chat.py`·`agent_guard.py`·`agent_memory.py`를 Kotlin으로 옮긴 `agent/`의 `ApprovalRules`·`AgentGrants`·`TurnActivity`·`ProtectedZones`·`AgentMemory`가 맡고, 워커에는 데스크톱과 같은 턴 프레임 필드(`allowed`·`session_allowed`·`protected`)로 넘깁니다. 구조와 빌드는 [android/README.md](android/README.md)에 있습니다.

`npm run build:hermes`를 하지 않은 소스 실행에는 도구가 없습니다. 연결한 LLM이 JSON으로 계획하고(`engine: direct`), 읽기만 하는 작업은 바로, 나머지는 사용자가 카드의 **확인 후 실행**(`agent.approve`)을 눌러야 실행하며, 화면의 배지가 이를 알립니다. 배포 앱은 항상 내장 Hermes를 씁니다.

## 보안 경계

자세한 위협 모델은 [SECURITY.md](SECURITY.md)에 있습니다. 구조와 관련된 규칙만 요약합니다.

- 렌더러는 샌드박스 안에서 요청 통로 하나만 씁니다. 메인 프로세스는 보낸 프레임과 `ALLOWED`를 확인하고, API Key·헤더 값·비밀번호·토큰을 화면에 보내지 않습니다(값이 있는지만 알림).
- 비밀 값은 메인 프로세스가 운영체제 보안 저장소(Electron `safeStorage`)로 암호화해 앱 설정 폴더에 둡니다. 사이트 비밀번호는 그 사이트의 로그인 계획에 담아 필요한 요청에만 백엔드로 넘깁니다.
- 백엔드는 포트를 열지 않습니다. 로컬 API는 기본으로 꺼져 있고, 켜도 `127.0.0.1`에서 토큰과 허용 범위로만 받습니다.
- 사이트 주소는 HTTPS가 기본이며 HTTP로 내려가지 않습니다. 브라우저 자동화는 Chromium 샌드박스를 켠 채 실행합니다.
- 문서와 대화·실행 기록·할 일·프롬프트 템플릿·예약·자동 작업의 삭제는 휴지통으로 가고, 영구 삭제·백업 복원·파일 가져오기·설정 변경은 허용 범위 ‘전체’에서만 AI 도구가 할 수 있습니다.
- 로그와 진단 복사본은 키·토큰·비밀번호를 가립니다.
- 채팅의 에이전트(Hermes)는 자기 도구로 바로 실행하고, 위험한 명령만 사용자의 확인을 받습니다. 운영체제 샌드박스가 아니라 승인 기반 통제입니다. 모델 호출은 모두 앱의 LLM 연결로 보내고, 앱의 웹 기록은 페이지의 광고나 링크를 누르지 않습니다.
- 채팅 화면과 에이전트의 설정(**관리 → 채팅 에이전트**)은 사용자만 다룹니다(`data-user-only`). 로컬 API의 화면 제어는 허용 범위와 관계없이 그곳에 입력하거나 그 버튼(명령 확인, 메신저 계정 포함)을 누르지 못합니다.
- 메신저 게이트웨이는 포트를 열지 않고, 사용자가 적은 계정에만 답합니다.

## 빌드와 패키징

배포 파일은 Windows에서 만듭니다. `npm run package:win`(`scripts/package.mjs`)은 다음을 차례로 실행합니다.

1. 빌드한 커밋을 `electron/build-info.json`에 적습니다(진단 복사본에 표시, Git에서 제외).
2. `next build`: 화면을 `out/`으로 정적 내보내기합니다(`NEXT_TELEMETRY_DISABLED=1`).
3. `scripts/build-python.mjs`: PyInstaller `--onedir`로 백엔드를 `dist/backend/private-agent-backend/`에 만듭니다. UTF-8 모드로 실행되게 하고, setuptools와 debugpy는 넣지 않습니다. Windows에서는 만든 백엔드가 모든 표준 확장 모듈을 불러오는지 확인합니다(`--check-build`).
4. `scripts/build-browser.mjs`: 백엔드의 Playwright가 쓰는 Chromium을 `dist/browsers`에 설치합니다. 패키징한 앱은 `PLAYWRIGHT_BROWSERS_PATH`를 이 폴더로 정합니다.
5. `scripts/build-hermes.mjs`: `runtime/hermes/manifest.json`에 고정한 Hermes 소스(태그와 커밋 확인), CPython 3.14.0(Windows는 python.org embeddable package, Linux는 python-build-standalone, 둘 다 SHA-256 확인), `runtime/hermes/requirements-desktop.txt`의 해시 고정 wheel(Android와 함께 쓰는 핵심 `requirements.txt`에 데스크톱 기능을 더한 것)과 Hermes의 번들 플러그인·스킬을 `dist/hermes`에 모읍니다.
6. electron-builder(`package.json`의 `build`): `electron/`, `out/`, `assets/icon.png`(트레이 아이콘), `package.json`을 `app.asar`에 넣고, `dist/browsers`, `dist/backend`, `dist/hermes`를 `resources/`에, `LICENSE`와 `THIRD_PARTY_NOTICES.md`를 앱 폴더에 넣으며 Electron fuses를 적용합니다. nsis 대상과 `scripts/launcher.nsi`로 `release/Private-Agent-<버전>-x64.exe` 하나를 만듭니다. 백신 검사 때문에 `EPERM`·`EBUSY`로 멈추면 세 번까지 다시 시도합니다.

이 exe는 설치 프로그램이 아니라 실행기입니다. 빌드마다 처음 실행할 때 앱을 `%LOCALAPPDATA%\Private Agent\app-<버전>-<빌드 시각>`에 풀고, 그 뒤에는 바로 실행합니다. 레지스트리, 바로 가기, 제거 프로그램을 만들지 않으며, 실행 중인 앱이 없을 때 더 오래된 빌드의 폴더를 지웁니다.

`npm run build:exe`(`build-exe.cmd`)는 `npm ci`, 세 가지 준비 스크립트, `npm run check`, `npm run package:win`을 실행한 뒤 패키징한 앱(`release/win-unpacked`)으로 E2E를 돌립니다. Windows에서 직접 실행하며, 서명 인증서(환경 변수 `WIN_CSC_LINK`·`WIN_CSC_KEY_PASSWORD`)가 있으면 코드 서명합니다. 버전과 태그의 관계는 [CHANGELOG.md](CHANGELOG.md#버전-정책)를 보세요.
