import type { ComponentType } from 'react';
import {
  Bell,
  BookOpen,
  CalendarClock,
  CalendarRange,
  ChartBarBig,
  ChartColumn,
  CheckCheck,
  CircleGauge,
  Clock3,
  Cloud,
  FileText,
  Hash,
  History,
  Import,
  LayoutGrid,
  List,
  ListChecks,
  MessageSquareText,
  Network,
  PanelTop,
  Play,
  Rows2,
  Sparkles,
  Workflow,
  SquareKanban,
  Sun,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type { WidgetHeight, WidgetPlacement, WidgetSize } from '@/lib/api';
import BriefingWidget, { BriefingProgress, BriefingStats } from './briefing';
import ConversationsWidget from './conversations';
import { DocumentCards, DocumentSources, DocumentStats } from './document-views';
import DocumentsWidget from './documents';
import HistoryWidget, { HistoryChart, HistoryStats } from './history';
import ImportWidget from './importer';
import KnowledgeWidget, { KeywordCloud, KeywordRanking } from './knowledge';
import PromptWidget from './prompt-runner';
import RemindersWidget, { ReminderAgenda, ReminderStats } from './reminders';
import RoutinesWidget, { RoutineFeed } from './routines';
import { ScheduleAgenda } from './schedule-views';
import SchedulesWidget from './schedules';
import { ShortcutList, ShortcutTiles } from './shortcuts';
import { TaskBoard, TaskChart, TaskChecklist, TaskWeek } from './task-views';
import TasksWidget from './tasks';
import { WelcomeBanner, WelcomeStrip } from './welcome';

// One way of showing a content: its name in the widget list and the edit bar, the heading the widget shows when it is
// not the content's title, and the width it starts at. A frameless format draws its own surface instead of the card.
export type WidgetView = {
  label: string;
  title?: string;
  icon: LucideIcon;
  size: WidgetSize;
  frameless?: boolean;
  Component: ComponentType;
};
// What a widget shows, in every format it can take; the first is its default.
export type WidgetContent = {
  title: string;
  description: string;
  icon: LucideIcon;
  views: Record<string, WidgetView>;
};

export const WIDGETS: Record<string, WidgetContent> = {
  welcome: {
    title: '인사와 질문',
    description: '오늘 날짜와 인사, AI에게 묻는 입력창',
    icon: Sparkles,
    views: {
      banner: {
        label: '배너',
        icon: PanelTop,
        size: 'xl',
        frameless: true,
        Component: WelcomeBanner,
      },
      strip: { label: '한 줄', icon: Rows2, size: 'xl', frameless: true, Component: WelcomeStrip },
    },
  },
  shortcuts: {
    title: '빠른 작업',
    description: '새 문서·자료 가져오기·AI 작업·지식 둘러보기',
    icon: Zap,
    views: {
      tiles: {
        label: '타일',
        icon: LayoutGrid,
        size: 'xl',
        frameless: true,
        Component: ShortcutTiles,
      },
      list: { label: '목록', icon: List, size: 's', Component: ShortcutList },
    },
  },
  briefing: {
    title: '오늘의 브리핑',
    description: '마감·완료 현황과 AI 브리핑',
    icon: Sun,
    views: {
      full: { label: '브리핑', icon: Sun, size: 'l', Component: BriefingWidget },
      stats: { label: '숫자', title: '오늘 현황', icon: Hash, size: 'l', Component: BriefingStats },
      progress: {
        label: '진행률',
        title: '오늘의 진행률',
        icon: CircleGauge,
        size: 's',
        Component: BriefingProgress,
      },
    },
  },
  reminders: {
    title: '알림',
    description: '도래한 리마인더 확인·미루기',
    icon: Bell,
    views: {
      list: { label: '목록', icon: List, size: 's', Component: RemindersWidget },
      agenda: {
        label: '일정',
        title: '다가오는 알림',
        icon: CalendarClock,
        size: 's',
        Component: ReminderAgenda,
      },
      stats: { label: '숫자', title: '알림 현황', icon: Hash, size: 'm', Component: ReminderStats },
    },
  },
  tasks: {
    title: '할 일',
    description: '할 일 추가·완료·AI 추출',
    icon: CheckCheck,
    views: {
      list: { label: '목록', icon: List, size: 'm', Component: TasksWidget },
      checklist: {
        label: '체크리스트',
        title: '체크리스트',
        icon: ListChecks,
        size: 'm',
        Component: TaskChecklist,
      },
      board: {
        label: '보드',
        title: '할 일 보드',
        icon: SquareKanban,
        size: 'xl',
        Component: TaskBoard,
      },
      week: {
        label: '주간',
        title: '이번 주 마감',
        icon: CalendarRange,
        size: 'xl',
        Component: TaskWeek,
      },
      chart: {
        label: '차트',
        title: '할 일 현황',
        icon: ChartColumn,
        size: 's',
        Component: TaskChart,
      },
    },
  },
  import: {
    title: '가져오기',
    description: 'JIRA·Confluence 링크, Office·RTF·텍스트 파일',
    icon: Import,
    views: { full: { label: '기본', icon: Import, size: 'm', Component: ImportWidget } },
  },
  documents: {
    title: '문서',
    description: 'Wiki 문서 목록과 새 문서',
    icon: FileText,
    views: {
      list: { label: '목록', icon: List, size: 'm', Component: DocumentsWidget },
      cards: {
        label: '카드',
        title: '최근 문서',
        icon: LayoutGrid,
        size: 'xl',
        Component: DocumentCards,
      },
      stats: { label: '숫자', title: '문서 현황', icon: Hash, size: 'l', Component: DocumentStats },
      sources: {
        label: '출처별',
        title: '출처별 문서',
        icon: ChartBarBig,
        size: 's',
        Component: DocumentSources,
      },
    },
  },
  prompt: {
    title: 'Prompt 실행',
    description: '템플릿으로 요청을 실행하고 예약',
    icon: Play,
    views: { full: { label: '기본', icon: Play, size: 'm', Component: PromptWidget } },
  },
  schedules: {
    title: '예약 실행',
    description: '정해진 시간에 프롬프트 실행',
    icon: Clock3,
    views: {
      list: { label: '목록', icon: List, size: 'm', Component: SchedulesWidget },
      agenda: {
        label: '일정',
        title: '다음 예약 실행',
        icon: CalendarClock,
        size: 'm',
        Component: ScheduleAgenda,
      },
    },
  },
  routines: {
    title: '자동 작업',
    description: 'Confluence·JIRA 변경 확인·업무 보고 같은 주기 작업과 결과',
    icon: Workflow,
    views: {
      list: { label: '목록', icon: List, size: 'xl', Component: RoutinesWidget },
      feed: {
        label: '최근 결과',
        title: '자동 작업 결과',
        icon: History,
        size: 'm',
        Component: RoutineFeed,
      },
    },
  },
  conversations: {
    title: '최근 대화',
    description: '저장된 질문과 답변 다시 열기',
    icon: MessageSquareText,
    views: { list: { label: '목록', icon: List, size: 's', Component: ConversationsWidget } },
  },
  history: {
    title: '실행 기록',
    description: '실행 통계·일별 차트·기록 검색',
    icon: History,
    views: {
      full: { label: '기록', icon: History, size: 'xl', Component: HistoryWidget },
      chart: {
        label: '차트',
        title: '일별 실행',
        icon: ChartColumn,
        size: 'l',
        Component: HistoryChart,
      },
      stats: { label: '숫자', title: '실행 현황', icon: Hash, size: 'm', Component: HistoryStats },
    },
  },
  knowledge: {
    title: '지식 관계도',
    description: '키워드와 관계의 2D·3D 그래프',
    icon: BookOpen,
    views: {
      graph: { label: '관계도', icon: Network, size: 'xl', Component: KnowledgeWidget },
      ranking: {
        label: '순위',
        title: '자주 나온 키워드',
        icon: ChartBarBig,
        size: 'm',
        Component: KeywordRanking,
      },
      cloud: {
        label: '구름',
        title: '키워드 구름',
        icon: Cloud,
        size: 'xl',
        Component: KeywordCloud,
      },
    },
  },
};

export const SIZES: [WidgetSize, string][] = [
  ['s', '좁게 (1/3)'],
  ['m', '보통 (1/2)'],
  ['l', '넓게 (2/3)'],
  ['xl', '전체 너비'],
];
export const HEIGHTS: [WidgetHeight, string][] = [
  ['auto', '높이 자동'],
  ['s', '높이 낮게'],
  ['m', '높이 보통'],
  ['l', '높이 높게'],
];

// As many widgets as preferences keep (MAX_WIDGETS of backend/agent/service.py).
export const MAX_WIDGETS = 40;

// A placed widget with what it shows: its content and format, the content's first when the saved one is unknown.
export type Placed = {
  id: string;
  type: string;
  view: string;
  size: WidgetSize;
  height: WidgetHeight;
  content: WidgetContent;
  format: WidgetView;
};

export const defaultView = (content: WidgetContent) => Object.values(content.views)[0];

// Saved names are looked up among the registry's own entries only: every object also answers `constructor`.
const own = <T,>(entries: Record<string, T>, name: string) =>
  Object.hasOwn(entries, name) ? entries[name] : undefined;

export function place(item: WidgetPlacement): Placed | null {
  const type = item.type ?? item.id;
  const content = own(WIDGETS, type);
  // Unknown widgets (from a newer version) are skipped rather than breaking the page; edits keep them (keepHidden).
  if (!content) return null;
  // A format of a newer version shows as the first one and stays saved until the user picks another.
  const view = item.view || Object.keys(content.views)[0];
  const format = own(content.views, view) ?? defaultView(content);
  const { id, size, height = 'auto' } = item;
  return { id, type, view, size, height, content, format };
}

// An edited layout with the saved widgets this version cannot show (of a newer version) where they were, after the
// widget they followed, so that version finds them again.
export function keepHidden(saved: WidgetPlacement[] | null, next: WidgetPlacement[]) {
  const result = [...next];
  saved?.forEach((item, index) => {
    if (place(item) || result.some((entry) => entry.id === item.id)) return;
    let at = 0;
    for (let before = index - 1; before >= 0 && !at; before--)
      at = result.findIndex((entry) => entry.id === saved[before].id) + 1;
    result.splice(at, 0, item);
  });
  return result;
}

// What preferences keep of a widget; an auto height is the default and left out.
export const stored = (items: (WidgetPlacement | Placed)[]) =>
  items.map(({ id, type, view, size, height }) => {
    const item: WidgetPlacement = { id, type: type ?? id, size };
    if (view) item.view = view;
    if (height && height !== 'auto') item.height = height;
    return item;
  });

// A widget's id is its content's name, numbered from the second of the same content on.
export function nextId(type: string, layout: { id: string }[]) {
  const taken = new Set(layout.map((item) => item.id));
  let id = type;
  for (let n = 2; taken.has(id); n++) id = `${type}-${n}`;
  return id;
}

// Ready-made dashboards: each slot is a content, its format, width and height.
type Slot = [type: string, view: string, size: WidgetSize, height?: WidgetHeight];
export type WidgetTemplate = { id: string; title: string; description: string; slots: Slot[] };

export const TEMPLATES: WidgetTemplate[] = [
  {
    id: 'default',
    title: '기본',
    description: '인사와 빠른 작업, 브리핑·알림·할 일·문서',
    slots: [
      ['welcome', 'banner', 'xl'],
      ['shortcuts', 'tiles', 'xl'],
      ['briefing', 'full', 'l'],
      ['reminders', 'list', 's'],
      ['tasks', 'list', 'm'],
      ['documents', 'list', 'm'],
    ],
  },
  {
    id: 'focus',
    title: '오늘 집중',
    description: '오늘의 숫자와 진행률, 체크리스트와 이번 주 마감',
    slots: [
      ['welcome', 'strip', 'xl'],
      ['briefing', 'stats', 'l'],
      ['briefing', 'progress', 's'],
      ['tasks', 'checklist', 'm'],
      ['reminders', 'agenda', 'm'],
      ['tasks', 'week', 'xl'],
    ],
  },
  {
    id: 'knowledge',
    title: '지식 관리',
    description: '문서 현황과 출처, 자료 가져오기, 최근 문서와 키워드',
    slots: [
      ['welcome', 'strip', 'xl'],
      ['documents', 'stats', 'l'],
      ['documents', 'sources', 's'],
      ['import', 'full', 'm'],
      ['knowledge', 'ranking', 'm'],
      ['documents', 'cards', 'xl'],
      ['knowledge', 'cloud', 'xl'],
    ],
  },
  {
    id: 'automation',
    title: 'AI 자동화',
    description: 'Prompt 실행과 다음 예약, 자동 작업 결과, 실행 현황과 일별 차트',
    slots: [
      ['history', 'stats', 'xl'],
      ['prompt', 'full', 'm'],
      ['schedules', 'agenda', 'm'],
      ['routines', 'feed', 'l'],
      ['conversations', 'list', 's'],
      ['history', 'chart', 'xl'],
    ],
  },
  {
    id: 'overview',
    title: '한눈에 보기',
    description: '할 일·알림·문서·실행을 숫자와 차트로',
    slots: [
      ['briefing', 'stats', 'm'],
      ['documents', 'stats', 'm'],
      ['briefing', 'progress', 's'],
      ['tasks', 'chart', 's'],
      ['documents', 'sources', 's'],
      ['history', 'chart', 'l'],
      ['reminders', 'stats', 's'],
      ['knowledge', 'ranking', 'm'],
      ['history', 'stats', 'm'],
    ],
  },
  {
    id: 'simple',
    title: '간결하게',
    description: '질문 입력과 체크리스트, 알림만',
    slots: [
      ['welcome', 'strip', 'xl'],
      ['tasks', 'checklist', 'l'],
      ['reminders', 'list', 's'],
    ],
  },
];

export function fromTemplate(template: WidgetTemplate) {
  const widgets: WidgetPlacement[] = [];
  for (const [type, view, size, height] of template.slots)
    widgets.push(stored([{ id: nextId(type, widgets), type, view, size, height }])[0]);
  return widgets;
}

export const DEFAULT_LAYOUT = fromTemplate(TEMPLATES[0]);

// The greeting and the shortcuts were not widgets before; a layout saved then gets them back on top, where they were.
const EARLIER_TOP = DEFAULT_LAYOUT.slice(0, 2);

// The dashboard's widgets: the saved layout, or the default template's. One put back on top that the layout already
// has stays where it was saved.
export function layoutOf(saved: WidgetPlacement[] | null) {
  const items = !saved
    ? DEFAULT_LAYOUT
    : saved.length && saved.every((item) => !item.type)
      ? [...EARLIER_TOP.filter((top) => !saved.some((item) => item.id === top.id)), ...saved]
      : saved;
  return items.map(place).filter((item): item is Placed => !!item);
}

// The template a layout is, if it is one: the same formats, widths and heights in the same order.
const shape = (items: Placed[]) =>
  items.map((item) => `${item.type}.${item.view}.${item.size}.${item.height}`).join(' ');
export const templateOf = (layout: Placed[]) =>
  TEMPLATES.find((template) => shape(layoutOf(fromTemplate(template))) === shape(layout));
