import { useEffect, useRef } from 'react';
import {
  ArrowUpRight,
  BookOpen,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Clock3,
  House,
  Layers3,
  Plus,
  Settings2,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { useActions, useSettings, useStats, useView, useWorkspaceTab } from './state';
import { WidgetHead } from './dashboard';
import ErrorBoundary from './error-boundary';
import { defaultView, WIDGETS } from './widgets';
import { useAssistantData } from './widgets/assistant-data';

export type WorkspaceView = 'home' | 'tasks' | 'library' | 'automation' | 'activity' | 'admin';

export const WORKSPACE_VIEWS = {
  home: { title: '홈', description: '나의 하루를 한눈에', icon: House },
  tasks: { title: '할 일', description: '지금 중요한 일에 집중하세요.', icon: CheckCheck },
  library: {
    title: '지식 보관함',
    description: '흩어진 자료를 모아, 나만의 지식으로.',
    icon: BookOpen,
  },
  automation: {
    title: 'AI 작업',
    description: '반복되는 업무는 맡기고, 중요한 일에 시간을 쓰세요.',
    icon: Sparkles,
  },
  activity: {
    title: '활동 기록',
    description: '지난 대화와 실행 결과를 이어서 살펴보세요.',
    icon: Clock3,
  },
  admin: {
    title: '관리',
    description: '나에게 맞는 워크스페이스를 만들어 보세요.',
    icon: Settings2,
  },
};

export function WorkspaceSidebar() {
  const view = useView();
  const settings = useSettings();
  const stats = useStats();
  const { navigate, newDocument, openAdmin } = useActions();
  const { tasks } = useAssistantData();
  const open = tasks.filter((task) => task.status === 'open').length;
  return (
    <aside className="sidebar">
      <button className="brand" onClick={() => navigate('home')} aria-label="대시보드로 이동">
        <span className="brand-mark" aria-hidden="true">
          <Layers3 size={21} />
        </span>
        <span>
          Private Agent<small>YOUR PERSONAL WORKSPACE</small>
        </span>
      </button>
      <button
        className="sidebar-create"
        aria-label="새 문서 작성"
        title="새 문서 작성"
        onClick={() => newDocument()}
      >
        <Plus size={17} /> 새 문서 작성 <span>↗</span>
      </button>
      <nav className="workspace-nav" aria-label="화면">
        <p className="nav-caption">워크스페이스</p>
        {(['home', 'tasks', 'library', 'automation', 'activity'] as const).map((id) => {
          const { title, icon: Icon } = WORKSPACE_VIEWS[id];
          const count = id === 'tasks' ? open : id === 'library' ? stats.documents : 0;
          return (
            <button
              key={id}
              aria-label={title}
              title={title}
              aria-current={view === id ? 'page' : undefined}
              onClick={() => navigate(id)}
            >
              <Icon size={19} strokeWidth={1.7} />
              <span>{title}</span>
              {count > 0 && <span className="nav-count">{count}</span>}
              {view === id && !count && <span className="nav-active-dot" />}
            </button>
          );
        })}
        <div className="nav-bottom">
          <p className="nav-caption">환경 설정</p>
          <button
            aria-label="관리"
            title="관리"
            aria-current={view === 'admin' ? 'page' : undefined}
            onClick={() => openAdmin()}
          >
            <Settings2 size={19} strokeWidth={1.7} />
            <span>관리</span>
          </button>
          <button aria-label="사용 가이드" title="사용 가이드" onClick={() => openAdmin('about')}>
            <CircleHelp size={19} strokeWidth={1.7} />
            <span>사용 가이드</span>
            <ArrowUpRight size={14} />
          </button>
        </div>
      </nav>
      <div className="sidebar-foot">
        <div className="privacy-note">
          <ShieldCheck size={17} />
          <div>
            <strong>내 PC에 안전하게</strong>
            <small>나의 자료, 나만의 공간</small>
          </div>
        </div>
        <button className="profile-button" onClick={() => openAdmin('llm')}>
          <span className="profile-avatar">
            {(settings.os_user || 'P').slice(0, 1).toUpperCase()}
          </span>
          <span>
            <strong>{settings.os_user || '나의 워크스페이스'}</strong>
            <small>Private workspace</small>
          </span>
          <ChevronRight size={15} />
        </button>
      </div>
    </aside>
  );
}

export function WorkspaceWidget({
  id,
  size = 'xl',
}: {
  id: string;
  size?: 's' | 'm' | 'l' | 'xl';
}) {
  // A workspace page shows each content in its first, fullest format.
  const Widget = defaultView(WIDGETS[id]).Component;
  return (
    <section className={`widget span-${size}`} data-widget={id} aria-labelledby={`widget-${id}`}>
      <div className="widget-body">
        <ErrorBoundary
          message="이 위젯을 표시하지 못했습니다."
          heading={<WidgetHead id={id} title={WIDGETS[id].title} />}
        >
          <Widget />
        </ErrorBoundary>
      </div>
    </section>
  );
}

const TABS = {
  library: [
    ['documents', '문서'],
    ['import', '자료 가져오기'],
    ['knowledge', '지식 관계도'],
  ],
  automation: [
    ['prompt', '프롬프트 실행'],
    ['schedules', '예약 실행'],
    ['routines', '자동 작업'],
  ],
  activity: [
    ['history', '실행 기록'],
    ['conversations', '최근 대화'],
  ],
} as const;

export default function WorkspacePage({
  view,
}: {
  view: Exclude<WorkspaceView, 'home' | 'admin'>;
}) {
  const workspaceTab = useWorkspaceTab();
  const { navigate, newDocument } = useActions();
  const { title, description } = WORKSPACE_VIEWS[view];
  const tabs = view === 'tasks' ? null : TABS[view];
  const selected = tabs?.find(([id]) => id === workspaceTab)?.[0] ?? tabs?.[0][0];
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [view]);
  useEffect(() => {
    if (view === 'tasks' && workspaceTab === 'reminders') {
      document
        .querySelector('[data-widget="reminders"]')
        ?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }, [view, workspaceTab]);
  return (
    <div className="workspace-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">MY WORKSPACE</p>
          <h1 ref={heading} tabIndex={-1}>
            {title}
          </h1>
          <p>{description}</p>
        </div>
        {view === 'library' && (
          <button className="button primary" onClick={() => newDocument()}>
            <Plus size={16} /> 새 문서
          </button>
        )}
      </header>
      {tabs && (
        <div className="workspace-tabs" role="tablist" aria-label={`${title} 보기`}>
          {tabs.map(([id, label], index) => (
            <button
              key={id}
              id={`view-tab-${id}`}
              role="tab"
              aria-selected={selected === id}
              aria-controls="workspace-panel"
              tabIndex={selected === id ? 0 : -1}
              onClick={() => navigate(view, id)}
              onKeyDown={(event) => {
                let next = index;
                if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = tabs.length - 1;
                else return;
                event.preventDefault();
                navigate(view, tabs[next][0]);
                document.getElementById(`view-tab-${tabs[next][0]}`)?.focus();
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {view === 'tasks' ? (
        <div className="widget-grid">
          <WorkspaceWidget id="tasks" size="l" />
          <WorkspaceWidget id="reminders" size="s" />
          <WorkspaceWidget id="briefing" />
        </div>
      ) : (
        <div
          id="workspace-panel"
          role="tabpanel"
          aria-labelledby={`view-tab-${selected}`}
          className="widget-grid"
          key={selected}
        >
          <WorkspaceWidget id={selected!} />
        </div>
      )}
    </div>
  );
}
