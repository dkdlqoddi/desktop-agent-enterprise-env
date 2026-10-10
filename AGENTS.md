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

- 사이트 주소는 HTTPS가 기본이고 HTTP로 내려가지 않습니다. 프로토콜 없이 입력한 주소는 `https://`로 저장하고, HTTP는 사용자가 `http://`를 직접 적은 사이트에만 씁니다(`electron/sites.cjs`, `backend/agent/browser_import.py`, `backend/agent/web_login.py`).
- 비밀 값(API Key, 사용자 지정 헤더 값, 사이트 비밀번호, 로컬 API 토큰)은 렌더러와 AI 도구로 보내지 않고 `has_value` 같은 표시만 보냅니다. 메인 프로세스가 `safeStorage`로 암호화해 저장하며, `settings.secrets`처럼 비밀을 돌려주는 메서드는 `ALLOWED`와 API 카탈로그에 넣지 않습니다.
- 문서 삭제는 soft delete입니다(`documents.deleted_at`, 30일 휴지통). 문서를 읽는 쿼리는 `deleted_at IS NULL`(`service.py`의 `LIVE`)로 거르고, 영구 삭제(`wiki.purge`)와 백업 복원은 범위 `destructive`(허용 범위 ‘전체’)로 둡니다.
- 삭제·비우기를 실행하는 확인 버튼에는 `data-destructive`를 붙입니다. 로컬 API의 `app.click`은 허용 범위 ‘전체’에서만 이 버튼을 누릅니다.
- 브라우저 자동화는 Chromium 렌더러 샌드박스를 켠 채 실행합니다(`chromium_sandbox=True`). 렌더러 창의 `sandbox`·`contextIsolation`, CSP, preload의 좁은 통로, `trustedSender`와 `ALLOWED` 검사도 유지합니다.
- 로그와 진단 정보에 요청의 매개변수, 문서 본문, 자격 증명을 쓰지 않고, 비밀 값 가리기(`electron/diagnostics.cjs`, `backend/agent/diagnostics.py`)를 거칩니다. 예기치 않은 예외는 `diagnostics.report`로 오류 ID와 함께 남기고, 스택은 화면에 보내지 않습니다.
- 백엔드는 포트를 열지 않습니다. 로컬 API는 기본으로 꺼져 있고 `127.0.0.1`에서만 받습니다. 새 메서드에는 맞는 범위를 줍니다: 이 PC의 파일을 읽으면 `files`, 설정·계정이면 `admin`, 되돌릴 수 없으면 `destructive`.
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
