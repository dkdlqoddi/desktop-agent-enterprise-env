import { ArrowRight, BookOpen, FilePlus2, Import, Sparkles } from 'lucide-react';
import { useApp } from '../app';
import { useWidgetFrame, WidgetHead } from '../dashboard';

const SHORTCUTS = [
  { id: 'new', title: '새 문서', note: '떠오른 생각을 기록해요', icon: FilePlus2, tone: 'blue' },
  {
    id: 'import',
    title: '자료 가져오기',
    note: '파일과 링크를 한곳에',
    icon: Import,
    tone: 'violet',
  },
  {
    id: 'automation',
    title: 'AI 작업 시작',
    note: '반복되는 일을 간편하게',
    icon: Sparkles,
    tone: 'peach',
  },
  {
    id: 'library',
    title: '지식 둘러보기',
    note: '쌓아 둔 자료를 찾아요',
    icon: BookOpen,
    tone: 'mint',
  },
] as const;

function useShortcuts() {
  const { navigate, newDocument } = useApp();
  const run = (id: (typeof SHORTCUTS)[number]['id']) =>
    id === 'new' ? newDocument() : id === 'import' ? navigate('library', 'import') : navigate(id);
  return SHORTCUTS.map((shortcut) => ({ ...shortcut, run: () => run(shortcut.id) }));
}

// Four tiles in a row, as the home page always had them.
export function ShortcutTiles() {
  const { id } = useWidgetFrame('shortcuts');
  return (
    <>
      <h2 id={`widget-${id}`} className="sr-only">
        빠른 작업
      </h2>
      <div className="quick-actions">
        {useShortcuts().map(({ id: key, title, note, icon: Icon, tone, run }) => (
          <button key={key} onClick={run}>
            <span className={`quick-icon ${tone}`}>
              <Icon size={20} />
            </span>
            <span>
              <strong>{title}</strong>
              <small>{note}</small>
            </span>
            <ArrowRight size={16} />
          </button>
        ))}
      </div>
    </>
  );
}

// The same starting points one under another, to fit beside other widgets.
export function ShortcutList() {
  return (
    <>
      <WidgetHead />
      <ul className="shortcut-list">
        {useShortcuts().map(({ id, title, note, icon: Icon, tone, run }) => (
          <li key={id}>
            <button className="shortcut-row" onClick={run}>
              <span className={`quick-icon ${tone}`}>
                <Icon size={17} />
              </span>
              <span>
                <strong>{title}</strong>
                <small>{note}</small>
              </span>
              <ArrowRight size={14} />
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
