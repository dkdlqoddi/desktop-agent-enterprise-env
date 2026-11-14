<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# 프로젝트 규약

AI 코딩 에이전트가 이 저장소에서 지킬 규칙입니다. 위의 Next.js 안내는 `next dev`가 관리하니 고치지 말고, 이 절만 고칩니다. 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 명령과 검사는 [CONTRIBUTING.md](CONTRIBUTING.md), 보안 설계는 [SECURITY.md](SECURITY.md)에 있습니다.

## 언어와 표현

- 문서(README, SECURITY 등)와 화면·오류 메시지처럼 사용자에게 보이는 글은 한국어로, 짧고 구체적인 문장으로 씁니다.
- 코드 주석, 커밋 메시지, 테스트 이름은 영어로 씁니다.
- 누구나 자기 환경에 맞게 쓰는 공개 앱입니다. 특정 회사·조직의 환경을 가정하는 표현(회사 이름, 내부망, 내부 주소나 시스템 이름)을 화면 글, 프롬프트, 주석, 문서, 테스트 데이터에 쓰지 않습니다. 예시 주소는 `example.com`이나 `.test` 도메인을, 예시 이름은 자리표시자를 씁니다.
- `.env`, 인증서·키, 데이터베이스, 브라우저 프로필, 로그, 빌드 결과(`out/`, `dist/`, `release/`)를 커밋하지 않습니다.

## 보안 불변 조건

바꾸면 안 되는 규칙입니다. 바꿔야 할 이유가 있으면 코드보다 먼저 사용자에게 묻습니다.

- 사이트 주소는 HTTPS가 기본이고 HTTP로 내려가지 않습니다. 프로토콜 없이 입력한 주소는 `https://`로 저장하고, HTTP는 사용자가 `http://`를 직접 적은 사이트에만 씁니다(`electron/sites.cjs`, `backend/agent/browser_import.py`, `backend/agent/web_login.py`). Android 앱의 LLM 주소도 같습니다(`android/…/llm/Endpoint.kt`). Android는 실행 중에 입력한 호스트에만 평문 통신을 허용할 수 없으므로 앱 전체의 평문 통신은 꺼 둔 채로 두고(`android/…/res/xml/network_security_config.xml`, 앱 안 브라우저의 WebView와 플랫폼의 HTTP 스택이 따름), 사용자가 적은 `http://` LLM 주소만 LLM 클라이언트가 자기 소켓으로 보냅니다(`android/…/llm/PlainHttp.kt`). 앱 안 브라우저는 `http://` 페이지를 열지 않습니다(`android/…/web/BrowserUrls.kt`). 이 설정에서 평문 통신을 허용하지 마세요. WebView는 폼의 POST가 307·308 리다이렉트로 `http` 주소에 가는 것을 앱에 묻지 않아, 코드로는 막을 수 없습니다.
- 비밀 값(API Key, 사용자 지정 헤더 값, 사이트 비밀번호, 로컬 API 토큰, 채팅 에이전트의 서비스 키·봇 토큰)은 렌더러와 AI 도구로 보내지 않고 `has_value` 같은 표시만 보냅니다. 메인 프로세스가 `safeStorage`로 암호화해 저장하며(`electron/hermes-settings.cjs`), 비밀을 돌려주는 메서드·필드는 `ALLOWED`와 API 카탈로그에 넣지 않습니다. 채팅 에이전트의 키는 메인 프로세스가 백엔드 전용 파이프(`agent.configure`)로만 넘겨 워커 환경에 두며, 모델 호출은 언제나 LLM 연결로만 하므로 모델 제공자 키 이름은 거부합니다(`hermes-settings.cjs`의 `refusedName`). 메신저 봇 토큰은 게이트웨이를 켠 동안 게이트웨이 프로세스에만 넘기고 채팅의 워커에는 넘기지 않습니다. MCP 서버 설정의 값(env, headers, 인수, 주소)은 워커가 Wiki 폴더의 `hermes/config.yaml`에 쓰지 않고 환경 변수 참조로 넘깁니다(`runtime/hermes/worker.py`의 `mcp_servers`).
- 문서 삭제는 soft delete입니다(`documents.deleted_at`, 30일 휴지통). 문서를 읽는 쿼리는 `deleted_at IS NULL`(`service.py`의 `LIVE`)로 거르고, 영구 삭제(`wiki.purge`)와 백업 복원은 범위 `destructive`(허용 범위 ‘전체’)로 둡니다.
- 대화·실행 기록·할 일·프롬프트 템플릿·예약·자동 작업의 삭제도 바로 지우지 않고 `trash` 표로 옮겨 30일 보관합니다(`backend/agent/trash.py`의 `discard`). 채팅의 **대화 비우기**(`discard_chat`), 웹 기록 지우기, 보관 정책이 정리하는 실행 기록·대화(`Service.retire`)도 같습니다. 이 삭제 메서드들이 행을 그냥 지우게 바꾸지 말고, 영구 삭제(`trash.purge`)는 범위 `destructive`로 둡니다. AI 도구의 휴지통 요청에서는 메인 프로세스(`electron/main.cjs`의 `trashRequest`, `electron/trash.cjs`의 `trashValues`)가 API로 지울 수 있는 종류만 남기고(`only_kinds`: 비운 채팅과 지운 웹 기록은 사용자만 다룸) 웹 기록의 자동 작업을 빼며(`without_recipes`), 이 PC의 파일을 읽거나 등록하지 않은 사이트를 여는 자동 작업의 복원을 자동 작업을 켤 때와 같은 범위로 막습니다(백엔드는 `allow_files`로 다시 확인). 휴지통에 새 종류를 더해도 `only_kinds`(`api-catalog.cjs`의 `TRASH_KINDS`)에 넣기 전에는 AI 도구에 보이지 않습니다.
- Android 앱의 삭제(대화 지우기, 기록한 페이지, 기록 모두 지우기, 예약)도 `trash` 표로 옮겨 30일 보관합니다(`android/…/data/TrashStore.kt`의 `discard`·`discardChat`). 저장소의 삭제 메서드가 행을 그냥 지우게 바꾸지 말고, 영구 삭제는 휴지통 화면에서 묻고 하거나 30일 뒤 정리(`schedule/Upkeep.kt`)로만 합니다. 대화를 복원해도 ‘이 대화에서 허용’(`AgentGrants`의 `session`)은 되살리지 않습니다.
- 삭제·비우기를 실행하는 확인 버튼에는 `data-destructive`를 붙입니다. 로컬 API의 `app.click`은 허용 범위 ‘전체’에서만 이 버튼을 누릅니다.
- 채팅과 에이전트의 설정은 사용자만 다룹니다. 채팅의 입력란과 버튼(예시, 작업 카드, 위험한 명령의 확인, 질문의 답)과 **관리 → 채팅 에이전트**(서비스 키, MCP 서버, 메신저 게이트웨이의 계정, 묻지 않는 명령과 보호 구역, 에이전트의 기억), 비운 채팅과 지운 웹 기록이 있는 **관리 → 휴지통**은 `data-user-only` 영역 안에 두고, 로컬 API의 화면 제어(`app.click`·`app.fill`)는 어느 허용 범위에서도 이 영역을 거절합니다(`src/components/app-control.ts`의 `guard`). 채팅과 웹 기록의 메서드(`agent.*`, `web.*`)는 API 카탈로그에 넣지 않습니다. 넣으려면 먼저 사용자에게 묻습니다.
- 브라우저 자동화는 Chromium 렌더러 샌드박스를 켠 채 실행합니다(`chromium_sandbox=True`). 렌더러 창의 `sandbox`·`contextIsolation`, CSP, preload의 좁은 통로, `trustedSender`와 `ALLOWED` 검사도 유지합니다.
- 채팅 에이전트(Hermes)는 전체 도구(터미널·파일·코드·브라우저·MCP·컴퓨터 사용)로 **바로 실행**하고, 위험한 명령만 사용자 승인을 받습니다(`approvals.mode: smart`). Hermes 워커의 모델 호출은 모두 앱의 LLM 연결로 되돌려 보내므로(`runtime/hermes/worker.py`의 `build_keepalive_http_client` 패치) 키는 워커에 들어가지 않습니다. 이는 OS 샌드박스가 아니라 승인 기반 통제이니, 워커의 `isolate()`를 되살려 하위 프로세스·소켓을 막지 마세요(그러면 도구가 동작하지 않습니다). 모델 호출의 TLS 검증과 리다이렉트 금지, 승인 왕복(`agent.approve_command`)과 `agent.configure`가 비밀을 렌더러로 보내지 않는 점은 유지합니다. 워커는 앱이 넘긴 설정만 씁니다: Wiki의 `hermes` 폴더에 놓인 `.env`(Hermes는 이 파일을 환경 변수보다 먼저 읽고, 승인을 끄는 값도 여기서 읽음)와 이 PC의 `/etc/hermes` 설정을 읽지 않게 하는 `own_settings_only`를 지우지 마세요.
- 채팅 에이전트가 묻지 않고 실행하는 명령은 사용자가 고른 것뿐입니다. ‘항상 허용’은 글자까지 같은 한 줄 명령 하나이고(셸 연산자·와일드카드가 있거나, 비밀 값이 가려져 보이거나, 안전 검사가 거부한 명령에는 없음: `worker.py`의 `exact_command`), 그 목록은 메인 프로세스가 앱 설정의 `hermes.json`에 둡니다. 목록에 넣는 길은 백엔드가 물었던 명령을 돌려주는 `agent.approve_command`의 답 하나이며(`hermes-settings.cjs`의 `allow`), 화면이 보내는 설정(`hermes.update`)으로는 취소만 합니다. 이 목록을 Wiki 폴더의 파일이나 데이터베이스에 두지 말고(복원하거나 공유받은 Wiki가 허용 목록을 가져오게 됨), Hermes의 ‘같은 종류의 명령을 영구 허용’을 되살리지 마세요(`worker.py`의 `tell_grants`). ‘이 대화에서 허용’은 백엔드 메모리에만 두고 **대화 비우기**와 앱 종료로 끝나며, 메신저에서 고른 ‘항상 허용’은 그 대화에서만 유효합니다. Android 앱도 같은 규칙입니다: 목록은 백업·기기 이전에서 빠지는 앱 설정에 두고(`android/…/agent/AgentGrants.kt`), 넣는 길은 명령 확인의 답(`HermesChat.decide`) 하나이며 설정 화면은 취소만 합니다. 범위를 넓히려면 먼저 사용자에게 묻습니다.
- 보호 구역(`backend/agent/agent_guard.py`, `worker.py`의 `guard_files`·`denied_commands`)에서 앱의 설정 폴더와 Wiki의 데이터베이스·백업(`wiki.db`와 그 옆의 SQLite 파일, `backups`, `원본 백업`)은 사용자가 끌 수 없는 기본값입니다. Android 앱은 앱 자신의 데이터 폴더 전체를 에이전트의 홈만 남기고 끌 수 없는 보호 구역으로 넘깁니다(`ProtectedZones.kt`). 여기서 경로를 빼거나 자격 증명 위치의 기본값을 끄려면 먼저 사용자에게 묻습니다. 턴이 한 일(`agent_activity`)에 남기는 명령과 도구 인수는 비밀 값 가리기(`diagnostics.redact`)를 거칩니다.
- 메신저 게이트웨이(`backend/agent/hermes_gateway.py`, `worker.py`의 `run_gateway`)는 사용자가 켰을 때만, 사용자가 적은 계정에만 답합니다. 계정 검사(`only_listed_accounts`: Hermes의 페어링 승인·‘모두 허용’·채팅방·역할 허용을 다시 걸러 냄), 메신저에서 받는 명령의 목록(`GATEWAY_COMMANDS`: `/yolo`·`/approvals`·`/model`·`/update`·`/restart` 같은 명령은 넣지 않음), 연결하는 플랫폼(텔레그램·디스코드·슬랙)을 넓히려면 먼저 사용자에게 묻습니다. 접근을 넓히는 이름의 키는 저장도 거부합니다(`hermes-settings.cjs`의 `GATEWAY_STEERING`).
- 로그와 진단 정보에 요청의 매개변수, 문서 본문, 자격 증명을 쓰지 않고, 비밀 값 가리기(`electron/diagnostics.cjs`, `backend/agent/diagnostics.py`)를 거칩니다. 예기치 않은 예외는 `diagnostics.report`로 오류 ID와 함께 남기고, 스택은 화면에 보내지 않습니다.
- 백엔드는 포트를 열지 않습니다. 로컬 API는 기본으로 꺼져 있고 `127.0.0.1`에서만 받습니다. 메신저 게이트웨이도 포트를 열지 않습니다: Hermes의 메신저 어댑터만 실행하고(밖으로 여는 연결), 제어 소켓·로컬 API·대시보드·하트비트 소켓·`API_SERVER`·`WEBHOOK`·텔레그램 웹훅은 시작하지 않습니다(`backend/tests/test_hermes_gateway.py`가 프로세스에 듣는 소켓이 없음을 확인). 새 메서드에는 맞는 범위를 줍니다: 이 PC의 파일을 읽으면 `files`, 설정·계정이면 `admin`, 되돌릴 수 없으면 `destructive`.
- LLM 요청의 TLS 검증을 끄지 않고 리다이렉트를 따라가지 않습니다.

## RPC 메서드 추가

자세한 순서는 [ARCHITECTURE.md](ARCHITECTURE.md#기능과-rpc-메서드-추가)에 있습니다.

1. `backend/agent/service.py`의 `dispatch` 표(또는 `assistant.py`·`schedules.py`·`routines.py`의 `methods`)에 등록합니다.
2. LLM이나 브라우저를 기다리면 `backend/main.py`의 `SLOW`(사용자가 기다리면 `INTERACTIVE_METHODS`도)에, 330초를 넘을 수 있으면 `electron/main.cjs`의 `RPC_TIMEOUTS`에 넣습니다.
3. 화면이 부르면 `electron/trusted-sender.cjs`의 `ALLOWED`에, 응답 타입은 `src/lib/api/types.ts`에 넣습니다.
4. AI 도구도 쓰면 `electron/api-catalog.cjs`의 `METHODS`(필요하면 `GROUPS`의 REST 경로와 `FIELD_SCOPES`)에, 전용 MCP 도구는 `electron/mcp.cjs`의 `TOOLS`에 넣습니다.
5. `npm run generate:methods`와 `npm run docs:api`를 실행합니다.

## 생성 파일

손으로 고치지 않고 명령으로 다시 만들어 함께 커밋합니다. 최신이 아니면 테스트가 실패합니다.

- `src/lib/api/methods.generated.ts`: `npm run generate:methods`(`METHODS`나 `ALLOWED`를 바꾼 뒤)
- `docs/api.md`, `docs/openapi.json`: `npm run docs:api`(API 카탈로그, 매뉴얼, MCP 도구, `package.json`의 버전을 바꾼 뒤)
- `THIRD_PARTY_NOTICES.md`: `npm run notices`(의존성을 바꾼 뒤, `npm ci`와 `npm run setup:python` 다음에)

## 커밋 전에

```bash
npm run format          # 또는 npm run format:check
npm run lint            # ESLint(경고도 실패). 고칠 수 있는 것은 npm run lint:fix
npm run typecheck
npm run typecheck:electron   # electron/*.cjs, scripts/*.mjs(JSDoc 타입)
npm run test:main
npm run test:python     # 바꾼 부분만: npm run test:python -- backend/tests/test_x.py
npm run lint:python && npm run format:python:check   # 백엔드를 바꿨을 때
```

- 화면이나 메인 프로세스를 바꿨으면 관련 E2E를 실행합니다: `npm run test:e2e -- tests/<이름>.spec.ts`(`npm run build:web`이 필요하고, 백엔드는 `npm run build:python` 또는 `PRIVATE_AGENT_E2E_SOURCE_BACKEND=1`)
- 충돌 표시(`<<<<<<<`, `>>>>>>>`)가 남은 파일에 Prettier를 실행하지 않습니다. Markdown에서는 이를 인용문으로 바꿔 버립니다. 충돌을 먼저 풉니다.
- 커밋 메시지는 `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`, `style:` 접두사(필요하면 `feat(search):`처럼 범위)를 붙입니다. 사용자에게 보이는 변경은 [CHANGELOG.md](CHANGELOG.md)의 `Unreleased`에 적습니다.

## 화면(Next.js)

- 화면은 정적 내보내기(`output: 'export'`)입니다. Route Handler, Server Action, 미들웨어 같은 서버 기능은 쓸 수 없고, 데이터는 `api()`(`src/lib/api/transport.ts`)로만 받습니다.
- 상태는 `src/components/state.tsx`의 선택자 훅으로 읽고, 백엔드 응답의 캐시는 `src/lib/query.ts`의 태그로 무효화합니다.
