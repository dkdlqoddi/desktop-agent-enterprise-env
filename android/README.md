# Private Agent for Android

데스크톱 Private Agent의 휴대폰용 짝 앱입니다. 채팅으로 명령하면 앱이 쇼핑·검색 사이트를 **앱 안의 보이는 브라우저**에서 열고, 화면에 보이는 광고와 상품을 휴대폰 안의 데이터베이스에 기록합니다. 사용자가 직접 누른 광고는 도착 페이지와 함께 기록합니다. 설정한 LLM에 보내는 요청 말고는 아무것도 휴대폰 밖으로 내보내지 않습니다.

Kotlin과 Jetpack Compose(Material 3)로 만들었고 Android 8.0(API 26) 이상에서 동작합니다.

## 화면

| 탭     | 하는 일                                                                                                                                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 채팅   | 대화 하나를 이어 갑니다(기기의 SQLite에 저장). "쿠팡에서 노트북 광고 기록해 줘", "오늘 기록한 광고 요약해 줘", "매일 9시에 쿠팡 노트북 광고 기록해 줘"처럼 적습니다. 내장 Hermes가 있으면 Hermes가 바로 처리하고(배지 **Hermes**), 없는 빌드에서는 작업을 카드로 먼저 보여 주고 **확인 후 실행**을 눌러야 실행합니다(배지 **기본 계획기**). |
| 브라우저 | 앱 안의 WebView입니다. 주소창, 뒤로·앞으로·새로고침, **이 페이지 기록** 버튼이 있습니다. 사용자가 직접 로그인하고 둘러볼 수 있고, 쿠키는 남습니다.                                                                                                  |
| 기록   | 기록한 페이지(시각, 사이트, 검색어, 개수, 출처)와 항목(최신순)을 봅니다. 사이트, 종류(광고·상품·검색 결과·누른 광고), 검색어로 거르고, 항목을 누르면 제목·가격·표시 URL·링크·도착 페이지를 봅니다. **CSV 내보내기**로 거른 항목을 공유합니다. |
| 설정   | LLM 연결(API 형식, 전체 API 엔드포인트, 모델 이름, API Key, **연결 확인**), 예약한 기록, 브라우저 데이터, 대화·기록 지우기.                                                                                                                 |

### 채팅의 작업

내장 Hermes 실행 환경(아래 「내장 Hermes 실행 환경」)을 넣은 빌드에서는 데스크톱 앱처럼 Hermes 에이전트가 채팅을 처리합니다.

- Hermes는 자기 도구(웹 검색·페이지 읽기, 메모리, 기술, 할 일 목록, 지난 대화 찾기, 하위 에이전트, 이미지 분석, 질문하기)와 앱의 도구(아래 표와 `capture_page`)를 직접 부릅니다. 앱의 도구는 Hermes가 부를 때 바로 실행되고, 앱이 인자를 확인한 뒤 실행해 무엇을 했는지 카드로 보여 줍니다. 터미널, 코드 실행, 파일, 브라우저 자동화, 컴퓨터 사용은 휴대폰에서 켜지 않습니다.
- Hermes가 모델을 부를 때마다 요청이 앱으로 돌아오고, 앱이 설정한 LLM 연결로 보냅니다. API Key는 Hermes 프로세스에 들어가지 않습니다.
- Hermes가 묻는 질문(선택지나 짧은 답)과 Hermes의 안전 검사가 올린 명령 확인은 채팅의 답 자리에 나타나고, 답할 때까지 턴이 기다립니다. 5분 안에 답하지 않으면 질문은 취소, 명령은 거부로 처리합니다. 진행한 도구와 작성 중인 답도 그 자리에 보입니다.
- **요청 중지**는 Hermes 프로세스를 끝냅니다. 다음 요청은 새 프로세스로 시작합니다(몇 초). 쓰지 않는 Hermes 프로세스는 5분 뒤 끝납니다.

내장 Hermes가 없는 빌드에서는 앱이 대화의 최근 메시지와 도구 목록을 설정한 LLM에 보냅니다. LLM이 쓸 수 있는 도구는 넷입니다.

| 도구               | 하는 일                                                                       | 실행                          |
| ------------------ | ----------------------------------------------------------------------------- | ----------------------------- |
| `web_capture`      | 사이트(쿠팡·아마존·네이버·구글)의 검색 결과를 열고 보이는 광고와 상품을 기록 | 카드에서 **확인 후 실행**     |
| `open_site`        | 사이트 홈이나 https 주소를 열어 사용자가 둘러보게 함                          | 카드에서 **확인 후 실행**     |
| `list_records`     | 기기의 기록을 찾아 개수와 최근 항목을 돌려줌(읽기만)                          | 바로 실행, 결과로 LLM이 답함  |
| `schedule_capture` | 정한 시각(매일 또는 평일)에 기록을 알리는 예약을 만듦                         | 카드에서 **확인 후 실행**     |

카드는 승인 대기 → 실행 중 → 완료·실패·취소로 바뀌고, "광고 5건, 상품 32건을 기록했습니다."처럼 실제 결과를 보여 줍니다. 실행되기 전에는 실행했다고 말하지 않습니다. LLM이 보낸 도구 이름과 인자는 앱이 엄격히 확인합니다. 모르는 사이트, 빈 검색어, https가 아닌 주소, 이해할 수 없는 시각은 실행하지 않고 사용자에게 다시 묻습니다. 도구 호출을 지원하지 않는 서버를 위해 `{"message": "...", "actions": [{"tool": "...", "args": {...}}]}` 형식의 JSON 답도 읽습니다.

## 보이는 브라우저와 자동 클릭 금지

- 기록은 사용자가 보는 브라우저 탭에서 진행합니다. 백그라운드에 숨은 브라우저는 쓰지 않습니다. 예약도 시각이 되면 알림만 보내고, 사용자가 알림을 눌러야 브라우저에서 기록합니다.
- `web_capture`는 검색 페이지를 열고, 페이지가 다 열린 뒤 1.5초를 기다리고, 화면 높이만큼씩 네 번 천천히 내려 늦게 뜨는 카드를 보이게 한 다음, 데스크톱 앱과 같은 `capture.js`로 페이지를 읽습니다.
- 앱은 페이지의 어떤 것도 누르거나 입력하거나 제출하지 않습니다. 광고를 자동으로 누르는 일은 광고 사기이고 사이트 약관에 어긋나기 때문입니다.
- 로봇 확인(CAPTCHA)이 나오면 기록을 멈추고, 사용자가 브라우저에서 직접 확인을 마친 뒤 다시 요청하도록 알립니다. 우회하거나 대신 풀지 않고, 봇 탐지를 피하는 설정도 하지 않습니다. User-Agent는 WebView 기본값 그대로입니다.
- 페이지를 열 때마다 `click-watch.js`를 넣고, 페이지에는 `PaBridge.reportClick` 하나만 엽니다. 사람이 직접 누른 클릭(`isTrusted`)만 보고되며, 앱은 그 항목에 누른 시각과 링크를 적고 다음에 열린 페이지의 주소와 제목을 도착 페이지로 기록합니다. 사용자가 둘러보는 페이지의 광고도 표시해 두었다가, 그중 하나를 누르면 그 페이지와 누른 광고를 함께 저장합니다(설정의 **직접 누른 광고 기록**으로 끕니다).
- WebView 설정: JavaScript 켬(스크립트 실행에 필요), DOM 저장소 켬, 파일·콘텐츠 접근 끔, 혼합 콘텐츠 차단, Safe Browsing 켬, 새 창 열기 끔. `intent:`·`market:` 같은 다른 앱 링크는 열지 않습니다(`intent:` 링크에 https 대체 주소가 있으면 그 주소를 앱 안에서 엽니다). 인증서 오류가 난 페이지는 열지 않습니다.

## 휴대폰에 남는 것과 LLM으로 가는 것

| 위치                               | 내용                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------ |
| 앱 저장소의 `private-agent.db`      | 채팅 메시지와 작업 카드, 기록한 페이지와 항목(제목·가격·링크·이미지 주소·카드 글), 누른 광고와 도착 페이지, 예약 |
| 앱 저장소의 설정 파일               | API 형식, 엔드포인트, 모델 이름, 암호화한 API Key(입력한 서버의 주소와 함께)                  |
| WebView 데이터                     | 사용자가 브라우저 탭에서 로그인하거나 둘러본 사이트의 쿠키와 캐시                            |
| 캐시의 `exports/`                  | 마지막으로 내보낸 CSV(새로 내보내면 지워짐)                                                  |
| 앱 저장소의 `hermes/`               | Hermes의 메모리, 기술, 지난 대화(세션), 설정(키 없음). 대화·기록 지우기로는 지워지지 않습니다 |
| code cache의 `hermes-runtime/`      | 풀어 둔 Hermes 실행 환경(앱을 업데이트하면 Android가 지움)                                   |

- **LLM으로 가는 것(Hermes)**: Hermes가 만드는 요청 그대로입니다. 시스템 프롬프트, 최근 대화(6,000자까지)와 작업 카드의 결과, Hermes의 메모리와 기술, 도구 목록과 도구 결과(앱의 기록, 웹 검색 결과와 읽은 페이지 내용).
- **LLM으로 가는 것(기본 계획기)**: 채팅의 최근 메시지(30개, 12,000자까지)와 작업 카드의 한 줄 요약, 앱의 규칙과 도구 목록, 현재 시각. `list_records`의 결과는 개수와 최근 항목(최대 50개)의 사이트·검색어·종류·제목(80자)·가격·기록 시각·누름 여부만 보냅니다. 링크와 페이지 본문, API Key는 프롬프트에 넣지 않습니다.
- **사이트로 가는 것**: 앱의 브라우저는 작업(Hermes가 부른 것이나 사용자가 승인한 것)이나 사용자가 직접 연 페이지만 엽니다. 일반 브라우저처럼 그 페이지와 페이지가 부르는 자원(광고 포함)을 받습니다. Hermes의 웹 검색과 페이지 읽기는 휴대폰에서 검색 엔진(DuckDuckGo 등, ddgs)과 그 페이지에 직접 요청합니다.
- 원격 측정, 사용 통계, 오류 보고가 없습니다. WebView의 사용 통계 보내기도 끕니다(`MetricsOptOut`). 백업과 기기 간 이전에서 앱 데이터를 모두 뺍니다.
- 로그에 요청 본문, 키, 헤더를 쓰지 않습니다. 오류는 한국어 문장으로만 보여 주고 스택은 보여 주지 않습니다.

## LLM 연결

**설정 → LLM 연결**에서 입력합니다.

1. API 형식: **OpenAI 호환**(Chat Completions, `choices[0].message.tool_calls`) 또는 **Anthropic**(Messages API, `x-api-key`, `anthropic-version: 2023-06-01`)
2. 전체 API 엔드포인트: 요청을 보낼 주소 그대로. 예: `https://api.example.com/v1/chat/completions`
3. 모델 이름과 필요하면 API Key

- API Key는 Android Keystore의 AES-GCM 키로 암호화해 저장하고 화면에 다시 보여 주지 않습니다("저장됨"만 표시). 엔드포인트를 다른 서버로 바꾸면 저장된 키를 지우고 새로 입력하게 합니다.
- **연결 확인**은 저장한 뒤 짧은 요청 하나를 보내 응답 형식까지 확인합니다.
- 요청은 60초가 지나면 끝냅니다. TLS 인증서 검증은 끄지 않고, 리다이렉트(HTTP 3xx)는 키가 다른 곳으로 가지 않도록 따라가지 않습니다.
- **HTTPS만 씁니다.** `https://`를 빼고 입력하면 `https://`로 저장하고, `http://` 주소는 오류로 막습니다. Android의 네트워크 보안 설정은 앱 전체에 적용되고, 사용자가 실행 중에 입력한 호스트 하나에만 평문 HTTP를 허용할 수 없기 때문에 평문 트래픽을 앱 전체에서 꺼 두었습니다(`res/xml/network_security_config.xml`). 그래서 같은 네트워크의 로컬 LLM 서버도 HTTPS(시스템이 신뢰하는 인증서)로 열어야 합니다. 브라우저 탭에서도 `http://` 페이지는 열지 않고, 원하면 다른 브라우저로 열 수 있게 묻습니다.

## 예약

채팅에서 "매일 9시에 쿠팡 노트북 광고 기록해 줘"처럼 요청하고 카드를 승인하면 예약이 만들어집니다. **설정 → 예약한 기록**에서 켜고 끄거나 지웁니다.

- WorkManager가 예약한 시각에 "예약한 광고 기록: 쿠팡 '노트북'" 알림을 보냅니다. 알림을 누르면 앱이 열리고 브라우저 탭에서 기록합니다.
- Android 13 이상에서는 예약을 승인할 때 알림 권한을 묻습니다. 권한이 없으면 예약은 만들어지지만 알림이 오지 않습니다.
- 휴대폰이 절전(Doze) 상태면 알림이 몇 분 늦을 수 있습니다.

## 빌드와 설치

JDK 17 이상과 Android SDK(플랫폼 `android-36`)가 필요합니다. `local.properties`에 `sdk.dir`을 적거나 `ANDROID_HOME`을 지정합니다.

```bash
cd android
./gradlew :app:assembleDebug            # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest        # JVM 단위 테스트
./gradlew :app:connectedDebugAndroidTest   # 연결한 기기·에뮬레이터의 계측 테스트
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

- Gradle 8.14.3(래퍼), Android Gradle Plugin 8.13.0, Kotlin 2.3, Compose BOM, compileSdk·targetSdk 36, minSdk 26입니다.
- 페이지 스크립트와 사이트 목록은 데스크톱 앱과 같은 `../shared/web-capture/`(`capture.js`, `click-watch.js`, `sites.json`)를 씁니다. 손으로 복사하지 않습니다. 빌드할 때마다 `copy<Variant>WebCaptureAssets` 작업이 `app/build/generated/assets/`로 복사하고 그 디렉터리를 assets 소스로 등록하므로, APK에는 항상 현재 파일이 들어갑니다. 파일이 없으면 빌드가 실패합니다. 단위 테스트도 `sites.json` 원본을 직접 읽습니다.
- 계측 테스트는 테스트 assets의 가짜 검색 페이지(`capture-fixture.html`, `shop.example.com` 링크)를 씁니다. 실제 쇼핑 사이트에는 접속하지 않고, 이 예시 호스트는 주소가 없어 링크를 눌러도 어느 서버에도 닿지 않습니다.
  - `CaptureScriptTest`: 앱과 같은 설정의 WebView에서 복사된 `capture.js`가 광고 3건·상품 4건을 세는지, 일반 규칙이 광고 표시 글자로 3건을 찾는지, 스크립트의 클릭은 무시하고 화면을 실제로 누른 탭만 `PaBridge.reportClick`에 닿는지 확인합니다.
  - `BrowserControllerTest`: 앱 화면의 브라우저에서 **이 페이지 기록**과 같은 기록을 실행해 데이터베이스에 저장되는지, 기록한 광고를 실제로 누르면 누른 시각·링크와 도착 페이지가 기록되는지 확인합니다. 테스트가 만든 기록만 지웁니다.
  - `StoresTest`(SQLite 거르기·누른 광고·한 번만 실행되는 카드·CSV 파일), `SecretBoxTest`(Keystore 암호화), `ActionCardTest`(카드의 승인 대기·완료·실패 표시).
- 릴리스 빌드는 서명 설정이 없습니다. 배포하려면 서명 키를 따로 만들어 `signingConfigs`에 지정합니다(키 파일은 커밋하지 않습니다).

## 내장 Hermes 실행 환경

데스크톱 앱과 같은 고정 버전의 Hermes Agent(`runtime/hermes/manifest.json`)를 휴대폰에서 실행하는 Python 실행 환경입니다. 채팅이 이 환경으로 Hermes를 실행합니다(앱 코드의 `hermes` 패키지: `HermesRuntime`, `HermesProcess`, `HermesSession`, `HermesModel`).

- `hermes/build_runtime.py`가 python.org의 CPython 3.14.0 Android 패키지, Hermes 소스(`build/hermes-source`), `runtime/hermes/requirements.txt`(데스크톱과 같은 핵심 의존성)와 `hermes/requirements-android.txt`(웹 검색용 ddgs)의 wheel, `runtime/hermes/worker.py`를 모아 `hermes/build/runtime/`(Git 제외)에 씁니다. Gradle은 이 폴더가 있으면 APK에 넣고, 없으면 Hermes 없이 빌드합니다. 넣으면 APK가 ABI 하나면 약 113 MB, 둘이면 약 134 MB 커지고, 처음 실행할 때 약 285 MB(파일 약 1만 7천 개)를 풉니다. 앱은 시작할 때 미리 풀어 둡니다.
- PyPI에 Android wheel이 없는 cffi, cryptography, jiter, lxml, Pillow, primp, psutil, pydantic-core, ruamel.yaml.clib는 sdist에서 cibuildwheel로 컴파일하고, wheel의 SHA-256을 `hermes/wheels.lock.json`에 적습니다. psutil 7.2.2에는 Android를 인식하게 하는 상위 프로젝트의 수정을, lxml에는 교차 빌드용 수정(`hermes/patches/`)을 적용합니다. 모든 다운로드는 고정 버전과 SHA-256으로 확인합니다(`hermes/pins.json`, `toolchain.txt`, `build-requirements.txt`).
- Android는 앱 데이터 폴더의 파일을 실행하지 못하게 합니다. 그래서 Python 실행 파일(`hermes/launcher/hermes_python.c`)을 `lib/<abi>/libhermes_python.so`로 넣고, 설치할 때 네이티브 라이브러리 폴더에 풀리게 합니다(`useLegacyPackaging`). Python 파일은 미리 컴파일한 바이트코드와 함께 처음 실행할 때 code cache 폴더에 한 번 풀고(앱을 업데이트하면 Android가 지움), Hermes의 데이터(`HERMES_HOME`)는 앱 파일 폴더의 `hermes/`에 둡니다.
- 워커는 데스크톱처럼 별도 프로세스(`-I -B`)로 실행하고 stdin·stdout으로 JSON 줄을 주고받습니다(`HermesSession`이 데스크톱의 `hermes.py`처럼 처리). 중지하면 프로세스를 끝냅니다. 환경 변수는 데스크톱의 `child_environment`처럼 최소한만 넘깁니다.

```bash
python3 android/hermes/build_runtime.py --setup-toolchain   # 처음 한 번: NDK·CMake·Rust를 설치하고 빌드
python3 android/hermes/build_runtime.py                     # 다시 빌드(입력이 바뀐 wheel만 다시 컴파일)
python3 android/hermes/build_runtime.py --abi x86_64        # 에뮬레이터용 ABI만
cd android && ./gradlew :app:assembleDebug -PhermesAbis=x86_64   # 한 ABI만 넣은 APK
```

- JDK 17, Android SDK(cmdline-tools), git, patch가 필요합니다. NDK 27.3.13750724, CMake 3.31.6, Rust 1.99.1(Android 대상 2개)은 `--setup-toolchain`이 사용자 폴더에 설치하고, 나머지 작업 파일은 `build/android-hermes/`에 둡니다.
- wheel 빌드는 비트 단위로 같게 나온다는 보장이 없으므로, 다시 컴파일한 wheel은 잠금 파일의 해시를 바꿉니다. `--frozen`은 다시 컴파일하지 않고 잠금 파일과 맞는 캐시만 씁니다. 같은 입력이면 실행 환경 압축 파일은 같게 나오므로, 앱은 무언가 바뀌었을 때만 다시 풉니다.
- 계측 테스트 `HermesRuntimeTest`는 실행 환경을 풀고, `hermes/smoke/hermes_smoke.py`로 Hermes 한 턴(도구 호출 1번)을 네트워크 없이 실행한 뒤 확장 모듈을 모두 불러 봅니다. `HermesChatTest`는 실제 워커로 채팅 한 턴을 실행합니다: 대신 답하는 모델이 `list_records`를 부르게 하고, 앱이 실행한 카드와 Hermes의 답을 확인합니다. 질문에 답하기와 중지도 확인합니다. 실제 웹 검색은 `am instrument -e hermesNetwork true`로 따로 실행합니다. 실행 환경이 없는 APK에서는 모두 건너뜁니다.

## 코드 구조

```text
app/src/main/java/dev/privateagent/android/
  agent/     채팅 흐름(ChatAgent, Hermes는 HermesChat), 앱의 작업(AppActions), 도구 정의와 인자 검사, 시스템 프롬프트, 화면 문구
  data/      SQLite(messages, captures, items, schedules)와 저장소
  llm/       엔드포인트 규칙, 요청·응답 형식(OpenAI 호환·Anthropic, JSON 계획), HTTPS 클라이언트
  web/       보이는 브라우저(BrowserController), WebView 설정, PaBridge, 사이트 목록, capture.js 결과 읽기
  schedule/  다음 실행 시각, WorkManager 작업, 알림
  security/  Keystore 암호화(SecretBox), 설정 저장
  export/    CSV(UTF-8 BOM, RFC 4180, 수식 무력화)와 공유
  hermes/    내장 Hermes: 풀기(HermesRuntime), Python 프로세스(HermesProcess), 워커 프로토콜(HermesSession), LLM 연결(HermesModel)
  ui/        Compose 화면(채팅, 브라우저, 기록, 설정)과 테마
```

## 한계

- 사이트의 화면 구조가 바뀌면 `capture.js`의 선택자가 맞지 않아 항목을 못 찾을 수 있습니다. 광고 표시 글자("광고", "Sponsored" 등)로 찾는 일반 규칙이 그다음을 맡지만, 상품은 사이트 규칙이 맞아야 찾습니다. 일반 규칙은 카드의 링크 글자에서 따로 감싼 광고 표시와 가격을 빼고 제목으로 쓰지만, 한 글자 덩어리로 붙어 있으면 섞일 수 있습니다. WebView는 휴대폰용 페이지를 받으므로 데스크톱 페이지에 맞춘 선택자가 덜 맞을 수 있습니다.
- 같은 페이지를 다시 기록하면 `capture.js`가 지난 표시(`data-pa-capture`)를 지우고 새 토큰으로 다시 표시합니다. 그 뒤의 클릭은 나중 기록에 붙습니다.
- 예약한 기록은 알림을 사용자가 눌러야 진행됩니다. 휴대폰을 쓰지 않는 동안 자동으로 기록하지 않습니다.
- 페이지에 보이는 것만 기록합니다. 네 번 스크롤한 뒤에도 나오지 않은 카드나 iframe 안의 광고는 기록하지 않습니다.
- `PaBridge`는 페이지의 모든 프레임이 부를 수 있습니다. 앱은 보고 형식과 기록의 토큰을 확인하지만, 페이지가 표시된 항목의 토큰을 읽어 가짜 클릭을 보고하는 것까지 막지는 못합니다(그 경우에도 기록에 "누름" 표시가 붙을 뿐 다른 일은 일어나지 않습니다).
- 도착 페이지는 누른 뒤 30초 안에 열린 첫 페이지와, 그 뒤 3초 안에 이어진 리다이렉트로 정합니다. 페이지가 자바스크립트로만 처리하는 클릭은 보고되지 않을 수 있습니다.
- 로봇 확인은 확인 위젯(reCAPTCHA·hCaptcha·Turnstile)이나 Google·Amazon의 확인 페이지로 판정하고, "captcha" 같은 글자는 아는 사이트의 페이지에 기록할 항목이 하나도 없을 때만 봅니다. 위젯 없이 글자로만 막는 다른 사이트의 확인 페이지는 빈 기록으로 남을 수 있습니다.
- `http://` LLM 서버와 `http://` 페이지는 쓸 수 없습니다(위의 HTTPS 설명).
