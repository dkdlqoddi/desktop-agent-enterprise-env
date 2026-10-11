# 해야 할 일

2026-10-11 기준으로 남은 일입니다. 2026-10-10 리뷰 로드맵의 0~3단계는 구현을 마쳤고([CHANGELOG.md](CHANGELOG.md)의 Unreleased), 여기에는 결정이 필요한 것, 아직 확인하지 못한 것, 알려진 미완과 로드맵 밖 제안을 모읍니다. 끝낸 항목은 지우고 바뀐 내용은 CHANGELOG에 적습니다.

## 결정이 필요한 것

- [ ] 라이선스 확정. 지금은 MIT로 가정했습니다. 바꾸려면 `LICENSE`, `package.json`의 `license`, `backend/pyproject.toml`의 `license`, README의 라이선스 절, `THIRD_PARTY_NOTICES.md` 첫 줄을 함께 고칩니다.
- [ ] 코드 서명 인증서. 저장소 시크릿 `WIN_CSC_LINK`(base64로 적은 .pfx)와 `WIN_CSC_KEY_PASSWORD`를 넣으면 `build.yml`이 exe에 서명합니다. 없으면 SmartScreen 경고가 뜹니다.
- [ ] Renovate GitHub 앱 설치. `renovate.json`은 준비되어 있고, 설치하면 매달 의존성 PR이 올라옵니다(Playwright 3중 핀은 한 PR로).

## Windows에서 확인할 것

- [ ] `npm run build:exe`로 만든 설치본에서 fuses(asar 무결성 검증, `onlyLoadAppFromAsar`)가 켜진 exe가 시작되는지, 런처의 첫 실행 압축 해제와 그 뒤 E2E(`build.yml`이 자동 실행)가 통과하는지.
- [ ] `npm run test:main`의 glob이 cmd.exe에서 확장되는지, `PRIVATE_AGENT_E2E_SOURCE_BACKEND=1`에서 백엔드 프로세스를 찾는지.
- [ ] 원본 반영의 저장 저널(`office_write.edit`)이 실제 Office에서 동작하는지. 지금은 가짜 워커로만 테스트했습니다.
- [ ] HTTPS LLM 엔드포인트에서 답변 중지가 즉시 끝나는지. Windows CI는 평문 HTTP로만 확인했습니다.

## 알려진 미완과 개선

### 검색과 목록

- [ ] 휴지통 뷰의 페이지 처리와 윈도잉. 문서 목록은 되어 있습니다.
- [ ] 2글자 검색어는 트라이그램 인덱스를 못 써 전체를 읽습니다(1만 문서에 60~80ms). 절 요약은 아직 LIKE로 찾습니다.
- [ ] 텍스트 필터는 큰 Wiki에서 전체를 읽고, "모두 선택" 뒤의 새로고침은 로드한 전부(최대 1만)를 다시 받습니다.
- [ ] 포커스된 행이 마우스 스크롤로 화면 밖에 나가면 키보드 포커스가 끊깁니다.

### LLM과 어시스턴트

- [ ] 재시도와 PageIndex 재시도가 겹치는 최악의 경우 `wiki.analyze`가 Electron의 31분 한도(`RPC_TIMEOUTS`)를 넘을 수 있습니다.
- [ ] 답변 중인 대화에 들어온 새 질문은 거부합니다. 큐에 넣는 선택지가 없습니다.
- [ ] 편집 충돌은 필드 단위 규칙만 있고 줄 단위 자동 병합이 없습니다. MCP `save_document` 설명에 `wiki.compare`를 안내하면 좋습니다.

### 백업과 복원

- [ ] 복원 적용 중 두 번의 이름 변경 사이에서 멈추면 Electron이 Wiki가 없다고 보고합니다. 같은 폴더를 다시 고르면 복원이 끝나지만, `electron/storage.cjs`에서 바로 처리해야 합니다.
- [ ] 백엔드가 크래시한 뒤 자동 재시작해도 예약된 복원이 적용됩니다(다음 시작으로 칩니다).

### 동기화와 가져오기

- [ ] `web_login.py`의 로그인 페이지 로드는 호스트별 속도 제한 밖입니다. 가져오기 요청만 제한합니다.

### 화면과 접근성

- [ ] 창의 `backgroundColor`가 밝은 색이라 어두운 테마에서 크기를 조절하면 흰 번쩍임이 보일 수 있고, 전역 오류 페이지(`global-error.tsx`)는 밝은 테마로 고정입니다.
- [ ] 그래프의 확대 버튼은 스타일 없는 기본 버튼이고, `secret-input.tsx`에 12px 인라인 글자 크기가 남아 있으며, 그래프에서 Enter는 상세만 열고 문서를 열지 않습니다.
- [ ] 대시보드 편집 중에는 h1이 없습니다(axe moderate 1건).

### 데모 녹화

- [ ] 녹화 중 창 크기가 바뀌면 재생할 수 없는 영상이 만들어집니다. `electron/mp4.cjs`의 수정이 필요합니다.
- [ ] 영상 생성이 실패하면 오류 대화상자에서 기다립니다. 사용자에게는 맞지만 무인 실행에서는 멈춥니다.

### 품질과 운영

- [ ] 야간 Linux E2E(`e2e-linux.yml`)의 결과를 지켜봅니다. 첫 실행은 성공했습니다.
- [ ] main 브랜치 보호 규칙(필수 CI 검사, 강제 푸시 금지)을 검토합니다.
- [ ] Electron, Chromium, Python의 월간 갱신 절차를 정합니다. Renovate 설치 뒤 PR을 어떻게 검증해 합칠지가 핵심입니다.

## 로드맵 밖의 기능 제안

리뷰 문서의 '신규 기능 제안' 18건을 요약한 것입니다. 효과와 공수의 평가는 그 문서에 있습니다.

- 가져오기: PDF, HWP/HWPX, 메일(.eml/.msg), 범용 웹 페이지와 첨부, 이미지 OCR과 비전 캡션
- 문서: 버전 기록과 3-way 병합, 태그·즐겨찾기·백링크·여러 탭, 편집기 2.0(미리보기, Ctrl+S, 초안 자동 저장), 내보내기·인쇄·백업 UI 보강
- 자동화: 동적 프롬프트 자동화(`schedules.py` 이관), 아침 브리핑과 할 일 후보 루틴, 트레이 상주·알림 액션·드래그&드롭
- AI 도구: 토큰별 이름·등급·만료, 감사 로그, 앱 안 승인 큐
- 탐색: 딥링크, 명령 팔레트, 온보딩 체크리스트, AI 답변을 Wiki에 저장
- 검색과 분석: LLM 호출 관측(`llm_calls`), 임베딩 하이브리드 검색, Excel 구조화 표와 NL→SQL
- 플랫폼: 비-Windows 읽기 폴백(LibreOffice, python-docx)과 번들 축소
