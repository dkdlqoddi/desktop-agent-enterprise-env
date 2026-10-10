import { createContext, useContext, useLayoutEffect, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Check,
  GripVertical,
  LayoutDashboard,
  LayoutGrid,
  LayoutTemplate,
  Plus,
  Undo2,
  X,
} from 'lucide-react';
import type { WidgetHeight, WidgetPlacement, WidgetSize } from '@/lib/api';
import { useActions, usePreferences } from './state';
import ErrorBoundary from './error-boundary';
import {
  fromTemplate,
  HEIGHTS,
  keepHidden,
  layoutOf,
  MAX_WIDGETS,
  nextId,
  SIZES,
  stored,
  TEMPLATES,
  templateOf,
  WIDGETS,
  type Placed,
  type WidgetTemplate,
} from './widgets';

// The widget a component is drawn in: its id names its heading, its title is the heading's text and its width lets a
// format show more or fewer items. On a workspace page a widget stands alone at full width.
type Frame = { id: string; title: string; size: WidgetSize };
const FrameContext = createContext<Frame | null>(null);
export const useWidgetFrame = (id: string) =>
  useContext(FrameContext) ?? { id, title: '', size: 'xl' as WidgetSize };

const titleOf = (item: Placed) => item.format.title ?? item.content.title;

// What the edit controls call each widget: its heading, numbered when two widgets show the same one.
function namesOf(layout: Placed[]) {
  const titles = layout.map(titleOf);
  return new Map(
    layout.map((item, index) => {
      const same = titles.filter((title) => title === titles[index]);
      const nth = titles.slice(0, index + 1).filter((title) => title === titles[index]).length;
      return [item.id, same.length > 1 ? `${titles[index]} ${nth}` : titles[index]];
    }),
  );
}

// Home is a dashboard of widgets: the user picks a template, adds any content in any of its formats as often as they
// like, and saves each widget's order, format, width and height.
export default function Dashboard() {
  const preferences = usePreferences();
  const { savePreferences, notify } = useActions();
  const [editing, setEditing] = useState(false);
  const [dragged, setDragged] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ id: string; after: boolean } | null>(null);
  // The layouts before each change of this editing, for 되돌리기, and what the last change did.
  const [undo, setUndo] = useState<(WidgetPlacement[] | null)[]>([]);
  const [status, setStatus] = useState('');
  const [added, setAdded] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const layout = layoutOf(preferences.widgets);
  const template = templateOf(layout);
  const names = namesOf(layout);
  // Every saved widget counts toward the limit the backend keeps, those of a newer version too.
  const full = keepHidden(preferences.widgets, layout).length >= MAX_WIDGETS;
  const write = (widgets: WidgetPlacement[] | null) =>
    void savePreferences({ widgets }).catch((error) => notify(error.message, true));
  // An edit keeps the saved widgets this version cannot show; a template replaces the whole layout.
  const save = (next: (WidgetPlacement | Placed)[] | null, message = '', whole = false) => {
    setUndo((past) => [...past.slice(-29), preferences.widgets]);
    setStatus(message);
    write(next && (whole ? stored(next) : keepHidden(preferences.widgets, stored(next))));
  };
  // A widget moved with ↑·↓ keeps the keyboard on the button pressed (the other one at either end): React moves the
  // widget's section to reorder the grid, which takes the focus with it. This is a layout effect, which runs in the very
  // commit that moves the section. A passive effect would not be safe: the effect of an earlier commit (the answer to
  // the previous save, which changes the widgets too) can still be waiting when the next press comes. React runs that
  // effect first, before the section moves, so it would use up `moved`, and the moved widget would leave the focus
  // on the page.
  const moved = useRef<{ id: string; down: boolean } | null>(null);
  useLayoutEffect(() => {
    const target = moved.current;
    moved.current = null;
    if (!target) return;
    const buttons = [
      ...(root.current?.querySelectorAll<HTMLButtonElement>(
        `[data-widget="${CSS.escape(target.id)}"] button[data-move]`,
      ) ?? []),
    ];
    const pressed = buttons.find((button) => button.dataset.move === (target.down ? 'down' : 'up'));
    (pressed && !pressed.disabled ? pressed : buttons.find((button) => !button.disabled))?.focus();
  }, [preferences.widgets]);
  const move = (id: string, to: number) => {
    const next = layout.filter((item) => item.id !== id);
    next.splice(
      Math.max(0, Math.min(to, next.length)),
      0,
      layout.find((item) => item.id === id)!,
    );
    save(next);
  };
  const change = (id: string, patch: { view?: string; size?: WidgetSize; height?: WidgetHeight }) =>
    save(layout.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  const add = (type: string, view: string) => {
    if (full) return;
    // A new id is new among the hidden widgets too.
    const id = nextId(type, [...layout, ...(preferences.widgets ?? [])]);
    const format = WIDGETS[type].views[view];
    save(
      [...layout, { id, type, view, size: format.size }],
      `‘${format.title ?? WIDGETS[type].title}’ 위젯을 맨 아래에 놓았습니다.`,
    );
    setAdded(id);
  };
  // The default template is saved as no layout, so it follows the default of later versions.
  const apply = (chosen: WidgetTemplate) =>
    save(
      chosen === TEMPLATES[0] ? null : fromTemplate(chosen),
      `‘${chosen.title}’ 템플릿으로 바꿨습니다. 되돌리기로 이전 배치에 돌아갈 수 있습니다.`,
      true,
    );
  const back = () => {
    if (!undo.length) return;
    setUndo(undo.slice(0, -1));
    setStatus('직전 배치로 되돌렸습니다.');
    write(undo[undo.length - 1]);
  };
  const finish = () => {
    setEditing(false);
    setUndo([]);
    setStatus('');
  };
  return (
    <div ref={root} className={`dashboard${editing ? ' editing' : ''}`}>
      {!layout.some((item) => item.type === 'welcome') && <h1 className="sr-only">홈</h1>}
      <div className="dashboard-head">
        <p className="dashboard-caption">
          <LayoutDashboard size={15} aria-hidden="true" />
          <strong>{editing ? '대시보드 편집' : '나의 대시보드'}</strong>
          <span>
            {template ? `${template.title} 템플릿` : '직접 구성'} · 위젯 {layout.length}개
          </span>
        </p>
        {editing ? (
          <div className="edit-tools">
            <span>위젯을 끌어서 옮기고, 형식·너비·높이를 고르세요.</span>
            <button className="button secondary small" disabled={!undo.length} onClick={back}>
              <Undo2 size={14} />
              되돌리기
            </button>
            <button className="button primary small" onClick={finish}>
              <Check size={14} />
              편집 완료
            </button>
          </div>
        ) : (
          <button className="button secondary small" onClick={() => setEditing(true)}>
            <LayoutGrid size={14} />
            위젯 편집
          </button>
        )}
      </div>
      {editing && (
        <div className="dashboard-editor">
          <section className="template-picker" aria-label="대시보드 템플릿">
            <header>
              <h2>
                <LayoutTemplate size={15} aria-hidden="true" />
                템플릿
              </h2>
              <small>배치를 한 번에 바꾼 뒤, 위젯을 더하거나 빼서 다듬으세요.</small>
            </header>
            <div className="template-list">
              {TEMPLATES.map((item) => (
                <button
                  key={item.id}
                  className="template-card"
                  aria-current={template === item || undefined}
                  onClick={() => apply(item)}
                >
                  <TemplatePreview template={item} />
                  <strong>
                    {item.title}
                    {template === item && <span className="template-current">사용 중</span>}
                  </strong>
                  <small>{item.description}</small>
                </button>
              ))}
            </div>
          </section>
          <section className="widget-tray" aria-label="추가할 수 있는 위젯">
            <header>
              <h2>
                <Plus size={15} aria-hidden="true" />
                위젯 추가
              </h2>
              <small id="widget-tray-note" className={full ? 'tray-full' : undefined}>
                {full
                  ? `위젯은 ${MAX_WIDGETS}개까지 놓을 수 있습니다. 더 놓으려면 위젯을 하나 빼 주세요.`
                  : '같은 내용도 목록·숫자·차트처럼 다른 형식으로 여러 번 놓을 수 있습니다.'}
              </small>
            </header>
            <div className="tray-grid">
              {Object.entries(WIDGETS).map(([type, content]) => {
                const views = Object.entries(content.views);
                const Icon = content.icon;
                return (
                  <div key={type} className="tray-item" role="group" aria-label={content.title}>
                    <span className="tray-icon" aria-hidden="true">
                      <Icon size={16} />
                    </span>
                    <span className="tray-text">
                      <strong>{content.title}</strong>
                      <small>{content.description}</small>
                    </span>
                    <span className="tray-views">
                      {views.map(([view, format]) => {
                        const ViewIcon = format.icon;
                        const placed = layout.filter(
                          (item) => item.type === type && item.view === view,
                        ).length;
                        return (
                          <button
                            key={view}
                            type="button"
                            className="tray-view"
                            aria-label={
                              views.length > 1
                                ? `${content.title} · ${format.label} 추가`
                                : `${content.title} 추가`
                            }
                            title={placed ? `대시보드에 ${placed}개 있음` : undefined}
                            disabled={full}
                            aria-describedby={full ? 'widget-tray-note' : undefined}
                            onClick={() => add(type, view)}
                          >
                            <ViewIcon size={13} aria-hidden="true" />
                            {views.length > 1 ? format.label : '추가'}
                            {placed > 0 && (
                              <span className="tray-placed" aria-hidden="true">
                                {placed}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
          <p className="editor-status" aria-live="polite">
            {status}
          </p>
        </div>
      )}
      {layout.length === 0 && (
        <div className="dashboard-empty">
          <p>표시할 위젯이 없습니다.</p>
          {!editing && (
            <button className="button secondary" onClick={() => setEditing(true)}>
              <Plus size={15} />
              위젯 추가
            </button>
          )}
        </div>
      )}
      <div className="widget-grid">
        {layout.map((item, index) => {
          const name = names.get(item.id)!;
          const views = Object.entries(item.content.views);
          const fixed = item.height !== 'auto' && !item.format.frameless;
          const Widget = item.format.Component;
          return (
            <section
              key={item.id}
              className={`widget span-${item.size}${fixed ? ` fixed h-${item.height}` : ''}${
                item.format.frameless ? ' frameless' : ''
              }${dragged === item.id ? ' dragging' : ''}${
                drop?.id === item.id ? (drop.after ? ' drop-after' : ' drop-before') : ''
              }${added === item.id ? ' added' : ''}`}
              aria-labelledby={`widget-${item.id}`}
              data-widget={item.id}
              data-view={item.view}
              onAnimationEnd={(event) => {
                if (event.target === event.currentTarget) setAdded(null);
              }}
              onDragOver={(event) => {
                if (!dragged || dragged === item.id) return;
                event.preventDefault();
                const box = event.currentTarget.getBoundingClientRect();
                // Before or after this widget, by the half the pointer is over.
                const after =
                  box.width > box.height * 1.5
                    ? event.clientX > box.left + box.width / 2
                    : event.clientY > box.top + box.height / 2;
                setDrop({ id: item.id, after });
              }}
              onDragLeave={(event) => {
                // Moving between the widget's own children is not leaving it.
                if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
                setDrop((current) => (current?.id === item.id ? null : current));
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (!dragged || !drop) return;
                const target = layout.filter((entry) => entry.id !== dragged);
                const at = target.findIndex((entry) => entry.id === drop.id);
                move(dragged, drop.after ? at + 1 : at);
                setDragged(null);
                setDrop(null);
              }}
            >
              {editing && (
                <div
                  className="widget-edit"
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', item.id);
                    setDragged(item.id);
                  }}
                  onDragEnd={() => {
                    setDragged(null);
                    setDrop(null);
                  }}
                >
                  <GripVertical size={16} aria-hidden="true" />
                  <strong>{name}</strong>
                  <span className="widget-edit-options">
                    {views.length > 1 && (
                      <select
                        aria-label={`${name} 형식`}
                        title="형식"
                        value={item.view}
                        onChange={(event) => change(item.id, { view: event.target.value })}
                      >
                        {views.map(([view, format]) => (
                          <option key={view} value={view}>
                            {format.label}
                          </option>
                        ))}
                      </select>
                    )}
                    <select
                      aria-label={`${name} 크기`}
                      title="너비"
                      value={item.size}
                      onChange={(event) =>
                        change(item.id, { size: event.target.value as WidgetSize })
                      }
                    >
                      {SIZES.map(([size, label]) => (
                        <option key={size} value={size}>
                          {label}
                        </option>
                      ))}
                    </select>
                    {!item.format.frameless && (
                      <select
                        aria-label={`${name} 높이`}
                        title="높이"
                        value={item.height}
                        onChange={(event) =>
                          change(item.id, { height: event.target.value as WidgetHeight })
                        }
                      >
                        {HEIGHTS.map(([height, label]) => (
                          <option key={height} value={height}>
                            {label}
                          </option>
                        ))}
                      </select>
                    )}
                  </span>
                  <button
                    className="icon-button"
                    aria-label={`${name} 앞으로`}
                    data-move="up"
                    disabled={index === 0}
                    onClick={() => {
                      moved.current = { id: item.id, down: false };
                      move(item.id, index - 1);
                    }}
                  >
                    <ArrowUp size={15} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`${name} 뒤로`}
                    data-move="down"
                    disabled={index === layout.length - 1}
                    onClick={() => {
                      moved.current = { id: item.id, down: true };
                      move(item.id, index + 1);
                    }}
                  >
                    <ArrowDown size={15} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`${name} 빼기`}
                    onClick={() =>
                      save(
                        layout.filter((entry) => entry.id !== item.id),
                        `‘${name}’ 위젯을 뺐습니다.`,
                      )
                    }
                  >
                    <X size={15} />
                  </button>
                </div>
              )}
              <div className="widget-body" inert={editing || undefined}>
                <FrameContext.Provider
                  value={{ id: item.id, title: titleOf(item), size: item.size }}
                >
                  {/* Another format draws afresh. */}
                  <ErrorBoundary
                    key={item.view}
                    message="이 위젯을 표시하지 못했습니다."
                    heading={<WidgetHead />}
                  >
                    <Widget />
                  </ErrorBoundary>
                </FrameContext.Provider>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

// A template's arrangement in miniature: one block per widget, as wide as it is placed.
function TemplatePreview({ template }: { template: WidgetTemplate }) {
  return (
    <span className="template-preview" aria-hidden="true">
      {template.slots.map(([type, view, size], index) => (
        <span
          key={index}
          className={`t-${size}${WIDGETS[type]?.views[view]?.frameless ? ' bare' : ''}`}
        />
      ))}
    </span>
  );
}

// The header every widget starts with: its title (which names the widget's region), an optional
// count, and at most a few actions. On the dashboard the title and id come from the widget's format.
export function WidgetHead({
  id,
  title,
  count,
  children,
}: {
  id?: string;
  title?: string;
  count?: number;
  children?: React.ReactNode;
}) {
  const frame = useContext(FrameContext);
  return (
    <header className="widget-head">
      <h2 id={`widget-${frame?.id ?? id}`}>
        {title ?? frame?.title}
        {count !== undefined && <span className="count">{count}</span>}
      </h2>
      {children && <div className="widget-actions">{children}</div>}
    </header>
  );
}
