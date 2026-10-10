import { useState } from 'react';
import {
  Bot,
  BookOpenText,
  ClipboardCopy,
  Database,
  FolderOpen,
  KeyRound,
  LayoutGrid,
  MessageSquareText,
  Unplug,
} from 'lucide-react';
import { api, type SiteKind } from '@/lib/api';
import { useActions, usePreferences, useStats, useStorageDirectory } from './state';
import ApiSection from './admin/api';
import ApiManualSection from './admin/api-manual';
import BackupsSection from './admin/backups';
import LlmSettings from './admin/llm';
import SitesSection from './admin/sites';
import TemplatesSection from './admin/templates';
import Confirm from './confirm';
import ErrorBoundary from './error-boundary';
import { rove } from './roving';
import { saveTheme, storedTheme, THEMES, type Theme } from './theme';
import { layoutOf, templateOf } from './widgets';

export type AdminSection =
  'llm' | 'sites' | 'templates' | 'preferences' | 'api' | 'manual' | 'about';
// A new site account the admin page opens with, e.g. for the link being imported.
export type SiteDraft = { url: string; kind?: SiteKind };
const SECTIONS: [AdminSection, string, typeof Unplug][] = [
  ['llm', 'LLM 연결', Unplug],
  ['sites', '사이트 계정', KeyRound],
  ['templates', '프롬프트 템플릿', MessageSquareText],
  ['preferences', '화면·검색·대시보드', LayoutGrid],
  ['api', 'AI 도구 연동', Bot],
  ['manual', 'API 매뉴얼', BookOpenText],
  ['about', '저장 위치·정보', Database],
];

// Every setting in one place, one section at a time. The app keeps which one (`onSection`), so being sent to the section
// already shown leaves it, and what is typed in it, as it is.
export default function Admin({
  section,
  draft,
  onSection: setSection,
}: {
  section: AdminSection;
  draft?: SiteDraft;
  onSection: (section: AdminSection) => void;
}) {
  const title = SECTIONS.find(([id]) => id === section)![1];
  return (
    <div className="admin">
      <nav className="admin-nav" role="tablist" aria-label="관리 항목" aria-orientation="vertical">
        <p className="admin-nav-title">관리 항목</p>
        {SECTIONS.map(([id, label, Icon]) => (
          <button
            key={id}
            role="tab"
            id={`admin-tab-${id}`}
            aria-controls="admin-panel"
            aria-selected={section === id}
            tabIndex={section === id ? 0 : -1}
            onClick={() => setSection(id)}
            onKeyDown={rove}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>
      <section
        className="admin-panel"
        role="tabpanel"
        id="admin-panel"
        aria-labelledby={`admin-tab-${section}`}
      >
        <h2>{title}</h2>
        {/* A section that fails to draw shows its error under the heading; the other sections stay a tab away, and
            coming back to it draws it afresh. */}
        <ErrorBoundary key={section} message="이 관리 항목을 표시하지 못했습니다.">
          {section === 'llm' && <LlmSettings />}
          {section === 'sites' && <SitesSection draft={draft} />}
          {section === 'templates' && <TemplatesSection />}
          {section === 'preferences' && <Preferences />}
          {section === 'api' && <ApiSection onManual={() => setSection('manual')} />}
          {section === 'manual' && <ApiManualSection />}
          {section === 'about' && <About />}
        </ErrorBoundary>
      </section>
    </div>
  );
}

function Preferences() {
  const preferences = usePreferences();
  const { savePreferences, notify } = useActions();
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const layout = layoutOf(preferences.widgets);
  const template = templateOf(layout);
  return (
    <div className="admin-form">
      <div className="setting">
        <span>
          <strong id="theme-setting">화면 테마</strong>
          <small>
            시스템 설정을 고르면 Windows의 밝은 모드·어두운 모드를 따릅니다. 이 PC의 앱에 저장되며
            Wiki 저장 위치를 바꿔도 그대로입니다.
          </small>
        </span>
        <div className="segmented" role="radiogroup" aria-labelledby="theme-setting">
          {THEMES.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={theme === value}
              tabIndex={theme === value ? 0 : -1}
              className={theme === value ? 'active' : ''}
              onClick={() => {
                saveTheme(value);
                setTheme(value);
              }}
              onKeyDown={rove}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="setting">
        <span>
          <strong>화면 배율</strong>
          <small>
            Ctrl+= 또는 Ctrl+마우스 휠로 크게, Ctrl+-로 작게 보고 Ctrl+0으로 원래 크기로 돌아갑니다
            (50%~200%). 바꾼 배율은 앱을 다시 열어도 그대로입니다.
          </small>
        </span>
      </div>
      <label className="setting">
        <span>
          <strong>검색할 때 AI 답변 만들기</strong>
          <small>
            켜 두면 위쪽 검색창에 입력할 때마다 LLM이 관련 문서를 골라 답변합니다(요청 최대 4번).
            끄면 PC 안에서 단어로만 찾고, 필요할 때 ‘AI 답변 받기’를 누릅니다.
          </small>
        </span>
        <span className="switch">
          <input
            type="checkbox"
            role="switch"
            checked={preferences.ai_answer}
            onChange={(e) =>
              void savePreferences({ ai_answer: e.target.checked }).catch((error) =>
                notify(error.message, true),
              )
            }
          />
          <span aria-hidden="true" />
        </span>
      </label>
      <div className="setting">
        <span>
          <strong>대시보드 위젯 배치</strong>
          <small>
            {template
              ? `‘${template.title}’ 템플릿을 쓰고 있습니다 (위젯 ${layout.length}개).`
              : `직접 구성한 배치를 쓰고 있습니다 (위젯 ${layout.length}개).`}{' '}
            대시보드의 ‘위젯 편집’에서 템플릿을 고르고 위젯과 형식을 바꿀 수 있습니다.
          </small>
        </span>
        <Confirm
          label="기본 배치로 되돌리기"
          question="위젯 배치를 기본값으로 되돌릴까요?"
          disabled={!preferences.widgets}
          onConfirm={async () => {
            try {
              await savePreferences({ widgets: null });
              notify('위젯 배치를 기본값으로 되돌렸습니다.');
            } catch (e) {
              notify((e as Error).message, true);
            }
          }}
        />
      </div>
    </div>
  );
}

function About() {
  const storageDirectory = useStorageDirectory();
  const stats = useStats();
  const { notify } = useActions();
  async function run(method: 'diagnostics.copy' | 'diagnostics.open_folder') {
    try {
      await api(method);
      if (method === 'diagnostics.copy') notify('진단 정보를 클립보드에 복사했습니다.');
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  return (
    <div className="about">
      <dl className="facts">
        <div>
          <dt>Wiki 저장 폴더</dt>
          <dd className="break-path">{storageDirectory}</dd>
        </div>
        <div>
          <dt>저장된 자료</dt>
          <dd>
            문서 {stats.documents} · 키워드 {stats.keywords} · 관계 {stats.relations} · 프롬프트{' '}
            {stats.prompts}
          </dd>
        </div>
      </dl>
      <p>
        문서·대화·할 일·실행 기록은 이 폴더의 <code>wiki.db</code>에 저장됩니다. 앱이 켜져 있으면
        하루에 한 번 이 폴더의 <code>backups</code> 폴더에 자동으로 백업하고 최근 7개를 남깁니다.
        연결 설정과 사이트 계정은 앱 설정 폴더에 따로 보관하며, API Key와 비밀번호는 운영체제 보안
        저장소로 암호화합니다.
      </p>
      <BackupsSection />
      <h3>쓰는 방법</h3>
      <ul className="guide">
        <li>
          <strong>검색·질문</strong> 위쪽 검색창(Ctrl+K)에 단어나 질문을 입력합니다. 일치하는 문서가
          바로 나오고, LLM이 연결되어 있으면 AI가 문서를 골라 근거와 함께 답합니다.
        </li>
        <li>
          <strong>자료 모으기</strong> 지식 보관함 → 자료 가져오기에 JIRA·Confluence 링크나
          Office·RTF·텍스트 파일 경로를 넣습니다. 로그인이 필요한 주소는 ‘사이트 계정’에 먼저
          등록합니다.
        </li>
        <li>
          <strong>대시보드 꾸미기</strong> ‘위젯 편집’에서 템플릿으로 배치를 한 번에 바꾸고, 같은
          내용도 목록·숫자·차트 같은 형식으로 골라 더합니다. 위젯을 끌어 옮기고 형식·너비·높이를
          바꾸거나 뺍니다.
        </li>
        <li>
          <strong>AI 도구 연동</strong> ‘AI 도구 연동’에서 로컬 API·MCP 서버를 켜고, Claude
          Code·Codex·OpenCode·Antigravity에 넣을 설정을 복사합니다. 쓸 수 있는 기능은 ‘API 매뉴얼’에
          있습니다.
        </li>
      </ul>
      <h3>문제 알리기</h3>
      <p>
        오류 메시지 끝의 ‘오류 ID’는 앱 설정 폴더의 로그에 남은 기록을 가리킵니다. 문제를 알릴 때
        진단 정보를 복사해 함께 보내 주세요. 로그에는 오류 내용과 파일 경로가 들어 있을 수 있지만,
        문서 본문과 비밀번호는 남기지 않습니다.
      </p>
      <div className="form-actions">
        <button className="button secondary" onClick={() => void run('diagnostics.copy')}>
          <ClipboardCopy size={15} />
          진단 정보 복사
        </button>
        <button className="button secondary" onClick={() => void run('diagnostics.open_folder')}>
          <FolderOpen size={15} />
          로그 폴더 열기
        </button>
      </div>
      <p className="muted">Private Agent v0.1.0</p>
    </div>
  );
}
