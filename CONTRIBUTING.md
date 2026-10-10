# 기여 안내

Private Agent에 코드나 문서를 보태는 방법입니다. 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 개발 중에 만나는 문제는 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)를 함께 보세요. 보안 문제는 공개 이슈 대신 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요.

## 준비

| 도구    | 버전                                                                 |
| ------- | -------------------------------------------------------------------- |
| Node.js | 22.13 이상(`.nvmrc`는 22). CI는 22와 24에서 검사합니다               |
| Python  | 3.10 이상. 배포 파일은 3.13(`.python-version`)으로 빌드합니다        |
| 그 밖에 | 데스크톱 실행에는 그래픽 환경, Office 관련 기능에는 Windows와 Office |

```bash
npm ci
npm run setup:python    # backend/.venv를 만들고 백엔드와 개발 도구(pytest, ruff, PyInstaller, coverage, debugpy)를 설치
npm run setup:browser   # 백엔드의 Playwright가 쓰는 Chromium 설치
npm run setup:electron  # Electron 실행 파일 내려받기
npm run dev
```

- `setup:python`은 `PYTHON` 환경변수의 Python을 먼저 쓰고, 없으면 `python3`·`python`(Windows는 `python`·`py -3`)에서 3.10 이상을 찾습니다. 예: `PYTHON=python3.13 npm run setup:python`
- `npm run dev:ready`(Windows는 `run-dev.cmd`)는 `npm ci`(바뀐 것이 없으면 건너뜀)와 세 가지 준비 스크립트를 실행한 뒤 `npm run dev`를 시작합니다.
- 개발용 LLM 초기값은 `.env.example`을 `.env`로 복사해 넣습니다. 소스로 실행한 백엔드만 `.env`를 읽습니다.

## 실행

- `npm run dev`: Next.js 개발 서버(`127.0.0.1`)와 Electron을 함께 띄웁니다. `electron/`, `backend/main.py`, `backend/agent/`의 파일을 고치면 Electron과 백엔드를 다시 시작하고, 화면 코드는 Next.js가 바로 반영합니다. 설치한 앱과 섞이지 않도록 앱 설정 폴더로 `.local-data/dev-user-data`를 씁니다.
- `npm run dev:web`: 웹 화면만 브라우저로 띄웁니다. 데스크톱 연결이 없어 목록은 빈 미리보기로 보이고, 저장과 AI 요청은 동작하지 않습니다.
- `npm start`: 빌드한 화면(`out/`, `npm run build:web`)으로 Electron을 실행합니다. 앱 설정 폴더는 설치한 앱과 같은 곳입니다.

## 테스트

`npm run check`는 ESLint 검사, TypeScript 검사(화면, Electron 메인 프로세스), Python 테스트, Electron 메인 프로세스 테스트를 차례로 실행합니다. 하나만 실행하려면 다음처럼 합니다.

```bash
# Python: 파일 하나, 테스트 하나, 이름으로 고르기(경로와 함께)
npm run test:python -- backend/tests/test_llm.py
npm run test:python -- backend/tests/test_llm.py::test_anthropic_messages_api
npm run test:python -- backend/tests -k header

# Electron 메인 프로세스(node --test): 파일 하나
node --test tests/api-docs.test.cjs

# Playwright E2E: 스펙 하나, 이름으로 고르기
npm run test:e2e -- tests/search.spec.ts
npm run test:e2e -- tests/search.spec.ts --grep "long documents"
```

- `npm run test:python -- <인수>`는 인수를 pytest에 넘기며, 인수가 있으면 기본 대상(`backend/tests`)을 대신합니다. `-k`만 줄 때도 경로를 함께 적습니다.
- `npm run test:main`은 `tests/*.test.cjs`를 모두 실행하고 인수로 거르지 않습니다. 파일 하나는 `node --test`로 직접 실행합니다.
- E2E는 빌드한 화면(`npm run build:web`)과 빌드한 백엔드(`npm run build:python`)를 씁니다. `PRIVATE_AGENT_E2E_SOURCE_BACKEND=1`이면 백엔드를 빌드하지 않고 `backend/.venv`의 Python으로 실행합니다. `npm run setup:electron`도 필요합니다.
- 브라우저 테스트(pytest 마커 `browser`)는 Chromium이 없으면 건너뜁니다. Office·Word·메모장을 실제로 쓰는 테스트는 Windows에서 아래 환경변수를 켤 때만 실행합니다.
- 커버리지: `npm run test:main:coverage`, `npm run test:python:coverage`

### Linux와 WSL

- Linux의 `npm run test:e2e`는 xvfb-run이 있으면 가상 디스플레이에서 창을 띄우지 않고 실행합니다. 설치: `sudo apt-get install -y xvfb`. 화면이 없는 Linux에서는 Chromium이 쓰는 시스템 라이브러리도 필요합니다: `backend/.venv/bin/python -m playwright install-deps chromium`
- `TMPDIR`이 긴 경로를 가리키면 그 아래에 만드는 Unix 소켓의 경로가 길이 제한(108바이트)을 넘어 테스트가 실패할 수 있습니다. 짧은 폴더를 지정합니다: `mkdir -p /tmp/pa && TMPDIR=/tmp/pa npm run test:python`
- `PYTHONPATH`는 비어 있어야 합니다. 다른 Python 도구가 전역으로 설정해 둔 `PYTHONPATH`는 가상 환경에 다른 패키지를 섞습니다. npm 스크립트와 앱은 이를 비우고 실행하지만, pytest를 직접 실행할 때는 직접 비웁니다: `PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUTF8=1 backend/.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_llm.py`
- 다른 worktree에서 `node_modules`를 심볼릭 링크로 공유해도 됩니다. `next.config.ts`가 Turbopack의 루트를 링크 대상까지 넓힙니다. `backend/.venv`도 링크로 공유할 수 있습니다.

## 환경변수

모두 선택 항목이며, 지정하지 않으면 기본 동작을 따릅니다. 이 가운데 패키징한 앱도 읽는 것은 `PRIVATE_AGENT_LOG_LEVEL`, `PRIVATE_AGENT_LOG_CONSOLE`, `PRIVATE_AGENT_USER_DATA`, `PRIVATE_AGENT_SCHEDULE_INTERVAL_MS`이고, 나머지는 개발과 테스트에서만 씁니다.

| 환경변수                             | 용도                                                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `PRIVATE_AGENT_LOG_LEVEL`            | `debug`·`info`(기본)·`warning`·`error`. `debug`이면 앱 로그에 요청별 메서드와 처리 시간을 남깁니다                 |
| `PRIVATE_AGENT_LOG_CONSOLE`          | `1`이면 로그와 백엔드의 오류 출력을 터미널에도 씁니다. `npm run dev`의 기본값이며 `0`으로 끕니다                   |
| `PRIVATE_AGENT_DEV_WATCH`            | `0`이면 `npm run dev`가 파일 변경을 감시하지 않습니다(변경 알림이 불안정한 네트워크·공유 폴더)                     |
| `PRIVATE_AGENT_DEBUGPY`              | 포트 번호. 소스로 실행한 백엔드가 `127.0.0.1`의 그 포트에서 debugpy 연결을 기다립니다. 1분 안에 연결합니다         |
| `PRIVATE_AGENT_USER_DATA`            | 앱 설정 폴더를 이 경로로 바꿉니다. E2E는 테스트마다 임시 폴더를 씁니다                                             |
| `PRIVATE_AGENT_DEV_SHARED`           | `1`이면 `npm run dev`가 `.local-data/dev-user-data` 대신 설치한 앱의 설정 폴더를 씁니다                            |
| `PRIVATE_AGENT_PYTHON`               | 소스 백엔드를 실행할 Python(기본 `backend/.venv`의 Python)                                                         |
| `PRIVATE_AGENT_USE_FROZEN`           | `1`이면 소스 실행에서도 빌드한 백엔드(`dist/backend`)를 씁니다                                                     |
| `PRIVATE_AGENT_E2E_SOURCE_BACKEND`   | `1`이면 E2E가 빌드한 백엔드 대신 `backend/main.py`를 실행합니다                                                    |
| `PRIVATE_AGENT_E2E_SHOW`             | `1`이면 Linux E2E를 가상 디스플레이 대신 실제 화면에 띄웁니다                                                      |
| `PRIVATE_AGENT_E2E_EXECUTABLE`       | E2E가 실행할 패키징한 exe. `npm run build:exe`가 `release/win-unpacked`의 exe로 설정합니다                         |
| `PRIVATE_AGENT_REQUIRE_BROWSER`      | `1`이면 Chromium이 없을 때 브라우저 테스트를 건너뛰지 않고 실패시킵니다(CI)                                        |
| `PRIVATE_AGENT_PERF`                 | `1`이면 문서 10,000개 검색 성능 테스트를 실행합니다: `npm run test:python -- backend/tests/test_search_perf.py -s` |
| `PRIVATE_AGENT_OFFICE_TEST`          | `1`이면 Windows에서 데스크톱 Office를 쓰는 테스트를 실행합니다                                                     |
| `PRIVATE_AGENT_WORD_TEST`            | `1`이면 Windows에서 Word로 RTF를 읽는 테스트를 실행합니다                                                          |
| `PRIVATE_AGENT_NOTEPAD_TEST`         | `1`이면 Windows에서 메모장을 쓰는 테스트를 실행합니다                                                              |
| `PRIVATE_AGENT_SCHEDULE_INTERVAL_MS` | 예약·자동 작업 확인 간격(1,000~60,000ms, 기본 30,000). 테스트가 줄여 씁니다                                        |

LLM 연결의 초기값은 `.env.example`의 이름(`BASE_URL`, `API_KEY`, `MODEL_NAME` 등)으로 줍니다. 패키징한 앱은 `PRIVATE_AGENT_` 접두사가 붙은 이름(`PRIVATE_AGENT_BASE_URL` 등)만 읽습니다. 앱에서 저장한 설정이 이 초기값보다 우선합니다.

## 형식과 검사

```bash
npm run format          # Prettier로 고치기
npm run format:check    # Prettier 검사: src, electron, scripts, tests, 루트의 *.json·*.ts·*.mjs·*.md, docs/*.md, 워크플로
npm run lint            # ESLint 검사(경고도 실패): 화면, Electron 메인 프로세스와 preload, 스크립트, 테스트
npm run lint:fix        # ESLint로 고칠 수 있는 것을 고치기
npm run typecheck       # 화면의 TypeScript 검사(tsconfig.json)
npm run typecheck:electron  # electron/*.cjs와 scripts/*.mjs의 TypeScript 검사(checkJs, tsconfig.electron.json)
npm run lint:python     # ruff 검사(npm run lint:python -- --fix는 고칠 수 있는 것을 고침)
npm run format:python   # ruff로 백엔드 형식 맞추기
npm run format:python:check
```

Prettier 설정은 `.prettierrc.json`, ESLint 설정은 `eslint.config.mjs`, Electron 코드의 타입 검사 설정은 `tsconfig.electron.json`, ruff 설정은 `backend/pyproject.toml`의 `[tool.ruff]`에 있습니다. ruff 버전은 `backend/constraints.txt`에, ESLint와 플러그인 버전은 `package.json`에 고정되어 있습니다.

- 형식은 Prettier만 맞추고, ESLint에는 배치에 관한 규칙이 없습니다.
- 이 코드에 맞지 않는 규칙은 `eslint.config.mjs`에서 이유와 함께 끄고(전체 또는 해당 파일만), 파일 전체를 끄는 인라인 주석은 쓰지 않습니다. 훅의 의존성 목록에서 값을 일부러 뺀 줄만 `// eslint-disable-next-line react-hooks/exhaustive-deps -- 이유`로 이유를 적습니다. 쓰이지 않게 된 비활성화 주석은 검사를 실패시킵니다.
- 메인 프로세스 코드는 JavaScript이므로 타입은 JSDoc으로 적습니다(`@param`, `@type`). 매개변수 타입이 없으면 `any`로 보고, 기본값 때문에 타입이 너무 좁게 추론되는 곳에만 JSDoc을 붙입니다. `tests/*.test.cjs`는 타입 검사에서 뺍니다.

## 생성 파일

다음 파일은 손으로 고치지 않고 명령으로 다시 만들어 함께 커밋합니다. 최신이 아니면 테스트나 CI가 실패합니다.

| 파일                               | 다시 만드는 명령           | 언제                                                                                         | 확인                      |
| ---------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------- | ------------------------- |
| `THIRD_PARTY_NOTICES.md`           | `npm run notices`          | npm·Python 의존성을 바꾼 뒤                                                                  | `npm run notices:check`   |
| `src/lib/api/methods.generated.ts` | `npm run generate:methods` | `electron/api-catalog.cjs`의 `METHODS`나 `electron/trusted-sender.cjs`의 `ALLOWED`를 바꾼 뒤 | `tests/methods.test.cjs`  |
| `docs/api.md`, `docs/openapi.json` | `npm run docs:api`         | `electron/api-catalog.cjs`·`api-manual.cjs`·`mcp.cjs`나 `package.json`의 버전을 바꾼 뒤      | `tests/api-docs.test.cjs` |

`npm run notices`는 `node_modules`와 `backend/.venv`를 읽으므로 `npm ci`와 `npm run setup:python` 뒤에 실행합니다. `npm run docs:api -- --check`는 파일을 쓰지 않고 최신인지만 확인합니다.

## git blame

코드의 배치만 바꾼 커밋(예: 백엔드 전체의 ruff 형식 적용)은 `.git-blame-ignore-revs`에 전체 해시로 적습니다. 한 번 설정하면 `git blame`이 그 커밋을 건너뜁니다.

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

GitHub의 blame 화면은 이 파일을 알아서 읽습니다. 리베이스나 스쿼시는 해시를 바꾸므로, 이 파일에 적을 커밋은 그대로 병합합니다.

## 커밋 메시지

`git log`처럼 Conventional Commits의 접두사를 쓰고, 제목은 영어로 무엇이 바뀌었는지 적습니다. 범위는 괄호에 넣을 수 있습니다.

| 접두사  | 쓰는 곳               | 예                                                                  |
| ------- | --------------------- | ------------------------------------------------------------------- |
| `feat`  | 기능 추가             | `feat(search): Korean/English keyword aliases from the AI analysis` |
| `fix`   | 버그 수정             | `fix(llm): a cancelled stream stops at once on Windows too`         |
| `docs`  | 문서                  | `docs: note npm run generate:methods beside the notices step`       |
| `test`  | 테스트만              | `test: pass the Python suite on Windows runners`                    |
| `ci`    | GitHub Actions        | `ci: run the E2E suite on a virtual display, nightly on Linux`      |
| `chore` | 도구·설정             | `chore: ruff configuration, lint fixes and CI step`                 |
| `style` | 동작이 같은 형식 변경 | `style: format the Python backend with ruff`                        |

본문에는 왜 바꾸었는지와 무엇이 달라지는지 적습니다. 사용자에게 보이는 변경은 [CHANGELOG.md](CHANGELOG.md)의 `Unreleased`에도 적습니다.

## PR에서 CI가 확인하는 것

`.github/workflows/check.yml`이 `main`으로의 푸시와 모든 PR에서 실행됩니다.

- Node.js 22·24: `npm ci`, `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run typecheck:electron`, `npm run test:main`(생성 파일이 최신인지 포함), `npm run build:web`
- Python 3.10·3.13(Ubuntu)과 3.13(Windows): `npm run setup:python`, `npm run setup:browser`, `npm run test:python`(`PRIVATE_AGENT_REQUIRE_BROWSER=1`이라 브라우저 테스트도 실행)
- Python 3.13(Ubuntu)에서 한 번: `npm run lint:python`, `npm run format:python:check`, `npm run notices:check`

`build.yml`(Windows exe 빌드와 패키징한 앱의 E2E)은 `v*` 태그나 수동 실행에서, `e2e-linux.yml`(Linux E2E)은 매일 밤과 수동 실행에서 돌아갑니다. PR 전에 위 명령을 직접 실행해 보고, 화면이나 메인 프로세스를 바꿨다면 관련 E2E 스펙도 실행합니다.

## 공개 앱 규약

- 누구나 자기 환경에 맞게 쓰는 공개 앱입니다. 특정 회사의 환경, 내부 주소, 시스템 이름을 가정하거나 언급하지 않습니다. 예시 주소는 `example.com`이나 `.test` 도메인을 씁니다.
- 문서(README, SECURITY 등)는 짧고 구체적인 한국어로, 코드 주석과 커밋 메시지는 영어로 씁니다.
- `.env`, 인증서·키, 데이터베이스, 브라우저 프로필, 로그, 빌드 결과(`out/`, `dist/`, `release/`)를 커밋하지 않습니다.
- 새 기능이나 RPC 메서드를 추가할 때는 [ARCHITECTURE.md](ARCHITECTURE.md#기능과-rpc-메서드-추가)의 확인 목록을 따르고, 보안 경계를 지킵니다([SECURITY.md](SECURITY.md)).
- 기여한 코드는 [MIT 라이선스](LICENSE)로 배포됩니다.
