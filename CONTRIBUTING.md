# 기여 안내

Private Agent에 코드나 문서를 보태는 방법입니다. 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 개발 중에 만나는 문제는 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)를 함께 보세요. 보안 문제는 공개 이슈 대신 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요.

## 준비

| 도구    | 버전                                                                       |
| ------- | -------------------------------------------------------------------------- |
| Node.js | 22.13 이상(`.nvmrc`는 22). `npm run ci`는 설치된 Node.js 하나로 검사합니다 |
| Python  | 3.10 이상. 배포 파일은 3.13(`.python-version`)으로 빌드합니다              |
| 그 밖에 | 데스크톱 실행에는 그래픽 환경, Office 관련 기능에는 Windows와 Office       |
| Android | 앱(`android/`)에는 JDK 17 이상과 Android SDK. 없으면 `npm run ci`가 건너뜀 |

```bash
npm ci
npm run setup:python    # backend/.venv를 만들고 백엔드와 개발 도구(pytest, ruff, PyInstaller, coverage, debugpy)를 설치
npm run setup:browser   # 백엔드의 Playwright가 쓰는 Chromium 설치
npm run setup:electron  # Electron 실행 파일 내려받기
npm run build:hermes    # 채팅의 내장 Hermes를 dist/hermes에(선택. 없으면 채팅은 LLM의 JSON 답으로 계획)
npm run dev
```

- `setup:python`은 `PYTHON` 환경변수의 Python을 먼저 쓰고, 없으면 `python3`·`python`(Windows는 `python`·`py -3`)에서 3.10 이상을 찾습니다. 예: `PYTHON=python3.13 npm run setup:python`
- `npm run dev:ready`(Windows는 `run-dev.cmd`)는 `npm ci`(바뀐 것이 없으면 건너뜀)와 세 가지 준비 스크립트를 실행한 뒤 `npm run dev`를 시작합니다.
- 개발용 LLM 초기값은 `.env.example`을 `.env`로 복사해 넣습니다. 소스로 실행한 백엔드만 `.env`를 읽습니다.
- `build:hermes`는 Hermes 소스를 `build/hermes-source`에 받고(git), CPython 3.14.0과 의존성 wheel을 내려받아 해시를 확인합니다. `runtime/hermes/manifest.json`의 핀이 바뀌면 그 태그를 같은 폴더로 받아 와 바꾸고, 어느 경우에도 소스가 매니페스트의 커밋과 다르면 멈춥니다. 그 폴더에서 고친 파일이 있으면 바꾸지 않고 멈춥니다. 인터넷 없이 빌드하려면 같은 커밋의 소스(git 체크아웃)를 `build/hermes-source`에, 내려받은 압축 파일을 `build/`에 두거나, Linux에서는 독립 실행형 CPython 3.14.0을 `--python`(또는 `PRIVATE_AGENT_HERMES_BUILD_PYTHON`)으로 줍니다.
- `runtime/hermes/worker.py`를 고친 뒤에는 `npm run build:hermes`를 다시 실행합니다. `npm run ci`는 Python 테스트를 Hermes 빌드보다 먼저 실행하므로, 예전 워커가 든 `dist/hermes`가 남아 있으면 내장 Hermes 테스트가 실패합니다(`test_the_bundle_holds_the_worker_of_this_tree`가 그렇다고 알려 줍니다).

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
| `PRIVATE_AGENT_WEB_HEADLESS`         | `1`이면 웹 기록의 브라우저를 창 없이 실행합니다. 테스트만 씁니다                                                   |
| `PRIVATE_AGENT_HERMES_DIR`           | 내장 Hermes 폴더. 앱이 정하며(개발 중 `dist/hermes`, 배포 앱 `resources/hermes`), 백엔드 테스트가 바꿔 씁니다      |
| `PRIVATE_AGENT_HERMES_BUILD_PYTHON`  | Linux `build:hermes`가 내려받는 대신 복사할 독립 실행형 CPython 3.14.0                                             |

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

| 접두사  | 쓰는 곳               | 예                                                                     |
| ------- | --------------------- | ---------------------------------------------------------------------- |
| `feat`  | 기능 추가             | `feat(search): Korean/English keyword aliases from the AI analysis`    |
| `fix`   | 버그 수정             | `fix(llm): a cancelled stream stops at once on Windows too`            |
| `docs`  | 문서                  | `docs: note npm run generate:methods beside the notices step`          |
| `test`  | 테스트만              | `test: pass the Python suite on Windows runners`                       |
| `ci`    | 로컬 CI 러너          | `ci: a local runner for the checks that used to run on GitHub Actions` |
| `chore` | 도구·설정             | `chore: ruff configuration, lint fixes and CI step`                    |
| `style` | 동작이 같은 형식 변경 | `style: format the Python backend with ruff`                           |

본문에는 왜 바꾸었는지와 무엇이 달라지는지 적습니다. 사용자에게 보이는 변경은 [CHANGELOG.md](CHANGELOG.md)의 `Unreleased`에도 적습니다.

## 커밋 전 로컬 검사

GitHub Actions는 쓰지 않습니다. 대신 `npm run ci`가 설치된 Node.js·Python 하나로 아래를 한 번에 실행합니다(`scripts/ci-local.mjs`). 핵심 검사만 빠르게 돌리려면 `npm run ci:core`로 E2E를 건너뜁니다.

- 핵심 검사: `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run typecheck:electron`, `npm run test:main`(생성 파일이 최신인지 포함), `npm run build:web`, `npm run test:python`, `npm run lint:python`, `npm run format:python:check`, `npm run notices:check`, Android 앱 검사(`android/`에서 Gradle 래퍼로 `:app:testDebugUnitTest`, `:app:lintDebug`, `:app:compileDebugAndroidTestKotlin`)
- Android 앱 검사는 JDK 17 이상(`JAVA_HOME`이나 PATH의 `java`)과 Android SDK(`ANDROID_HOME`, `ANDROID_SDK_ROOT`나 `android/local.properties`의 `sdk.dir`)가 있을 때만 실행하고, 없으면 무엇이 없는지 알리고 건너뜁니다(`scripts/android-check.mjs`). `npm run ci -- --no-android`로 뺄 수 있습니다. 기기에서 도는 계측 테스트(`:app:connectedDebugAndroidTest`)는 에뮬레이터나 기기를 연결해 따로 실행합니다([android/README.md](android/README.md#빌드와-설치)).
- E2E: `npm run setup:electron`, `npm run build:python`(또는 `npm run ci -- --source-backend`로 건너뛰고 `backend/.venv`로 실행), `npm run build:hermes`, `npm run test:e2e`(Linux는 xvfb 가상 디스플레이)

핵심 검사가 하나라도 실패하면 E2E는 건너뛰고, 끝에 통과·실패를 정리해 보여 줍니다. 처음에는 `npm ci`와 `npm run setup:python`으로 의존성을 준비하세요. 화면이나 메인 프로세스를 바꿨다면 관련 E2E 스펙(`npm run test:e2e -- tests/<이름>.spec.ts`)도 실행합니다. Windows exe는 Windows에서 `npm run build:exe`로 따로 빌드합니다.

## 월간 의존성 갱신

Electron(Chromium 포함), Playwright의 Chromium, Python과 내장 Hermes는 보안 수정이 자주 나오므로 한 달에 한 번 올립니다. [Renovate](renovate.json) GitHub 앱을 설치하면 매달 1일에 나온 지 14일이 지난 릴리스로 PR이 올라오고, 설치하기 전에는 아래 순서를 직접 밟습니다.

### 무엇을 올리나

| 대상                                     | 고칠 곳                                                                                                                    | 누가                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Electron(Chromium), electron-builder     | `package.json`, `package-lock.json`                                                                                        | Renovate, PR 하나(`Electron`)                                                         |
| Playwright(E2E와 백엔드가 쓰는 Chromium) | `package.json`의 `@playwright/test`, `backend/pyproject.toml`과 `backend/constraints.txt`의 `playwright`                   | Renovate, 세 곳을 PR 하나로(`Playwright`). 백엔드는 자기 릴리스의 Chromium을 받습니다 |
| Python 패키지                            | `backend/constraints.txt`(고정 버전)와 `backend/pyproject.toml`(범위)                                                      | Renovate, PR 하나(`Python dependencies`). ruff는 따로(`Ruff`)                         |
| Next.js·React와 그 밖의 npm 패키지       | `package.json`, `package-lock.json`                                                                                        | Renovate                                                                              |
| Android(AGP, Kotlin, Compose, Gradle)    | `android/gradle/libs.versions.toml`, `android/gradle/wrapper/gradle-wrapper.properties`                                    | Renovate                                                                              |
| 백엔드를 빌드하는 Python                 | `.python-version`(지금 3.13)과 `backend/pyproject.toml`의 `requires-python`                                                | 직접                                                                                  |
| Node.js                                  | `.nvmrc`, `package.json`의 `engines`, `scripts/workflow.mjs`와 `scripts/ci-local.mjs`의 버전 확인                          | 직접                                                                                  |
| 내장 Hermes와 그 CPython                 | `runtime/hermes/manifest.json`(버전, 커밋, CPython 내려받기 주소와 SHA-256), `runtime/hermes/requirements*.txt`(해시 고정) | 직접. Hermes의 릴리스 노트를 읽고 올립니다                                            |
| Android의 내장 Hermes 실행 환경          | `android/hermes/pins.json`과 그 옆의 잠금 파일                                                                             | 직접([android/README.md](android/README.md))                                          |

### 브랜치에서 확인하기

PR 브랜치(직접 올릴 때는 새 브랜치)에서 차례로 실행합니다.

1. `npm ci`, `npm run setup:python`, `npm run setup:browser`, `npm run setup:electron`으로 새 버전을 설치합니다. 빌드용 Python을 올렸으면 `backend/.venv`를 지우고 새 Python으로 `npm run setup:python`을 실행합니다(`PYTHON` 환경변수로 고릅니다).
2. `npm run notices`로 `THIRD_PARTY_NOTICES.md`를 다시 만들어 커밋합니다. 의존성만 바뀐 PR에서 다시 만들 생성 파일은 이것 하나이고, `npm run notices:check`가 통과해야 합니다. 코드를 함께 고쳤다면 [생성 파일](#생성-파일)의 표를 따릅니다.
3. ruff가 바뀌었으면 `npm run format:python`과 `npm run lint:python -- --fix`로 백엔드를 고칩니다.
4. Hermes 핀을 바꿨으면 `npm run build:hermes`가 새 태그를 `build/hermes-source`로 받아 와 `dist/hermes`를 다시 만듭니다. Android 앱의 실행 환경은 그다음 `python3 android/hermes/build_runtime.py`로 다시 만듭니다.
5. `npm run ci`로 핵심 검사와 E2E를 모두 돌립니다. Electron이나 Playwright를 올린 PR에서는 E2E를 건너뛰지 않습니다(`ci:core`가 아니라 `ci`). Electron의 메이저 버전이 바뀌면 [Electron의 호환되지 않는 변경 목록](https://www.electronjs.org/docs/latest/breaking-changes)에서 `electron/`이 쓰는 API를 찾아봅니다.
6. Electron, electron-builder, 빌드용 Python이나 Hermes를 올렸으면 Windows에서 `npm run build:exe`로 exe를 만들고 [VERIFICATION.md](VERIFICATION.md)의 A1(설치본이 시작하는지)과 A5(설치본의 채팅 에이전트)를 확인합니다. exe의 구성은 `tests/package.test.cjs`가 확인합니다.
7. Android 쪽을 올렸으면 `android/`에서 `./gradlew :app:testDebugUnitTest :app:assembleDebug`를, 에뮬레이터가 있으면 `./gradlew :app:connectedDebugAndroidTest`도 실행합니다.

### 합치기

- 자동 CI 상태 검사가 없으므로, 위의 확인을 마친 사람이 실행한 명령과 결과(`npm run ci` 통과, exe·Android를 확인했는지)를 PR에 댓글로 적고 합칩니다.
- 묶음 PR(Electron, Playwright, Python dependencies)은 나누지 않고 한 번에 합칩니다. 일부만 올리면 다른 곳의 버전과 맞지 않습니다.
- 고칠 것이 생기면 같은 브랜치에 커밋을 더합니다. 당장 합치지 못하는 PR은 닫지 않고 남겨 두면 Renovate가 다음 달에 새 버전으로 고쳐 올립니다.
- 사용자가 알아차릴 변화(Electron·Hermes의 기능, 바뀐 지원 환경)가 있으면 [CHANGELOG.md](CHANGELOG.md)의 `Unreleased`에 적습니다.

## 공개 앱 규약

- 누구나 자기 환경에 맞게 쓰는 공개 앱입니다. 특정 회사의 환경, 내부 주소, 시스템 이름을 가정하거나 언급하지 않습니다. 예시 주소는 `example.com`이나 `.test` 도메인을 씁니다.
- 문서(README, SECURITY 등)는 짧고 구체적인 한국어로, 코드 주석과 커밋 메시지는 영어로 씁니다.
- `.env`, 인증서·키, 데이터베이스, 브라우저 프로필, 로그, 빌드 결과(`out/`, `dist/`, `release/`)를 커밋하지 않습니다.
- 새 기능이나 RPC 메서드를 추가할 때는 [ARCHITECTURE.md](ARCHITECTURE.md#기능과-rpc-메서드-추가)의 확인 목록을 따르고, 보안 경계를 지킵니다([SECURITY.md](SECURITY.md)).
- 기여한 코드는 [MIT 라이선스](LICENSE)로 배포됩니다.
