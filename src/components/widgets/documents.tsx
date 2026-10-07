import { useRef, useState } from 'react';
import {
  ArrowRight,
  Eraser,
  FileCode2,
  FileText,
  Globe,
  Image as ImageIcon,
  ListTree,
  NotepadText,
  Plus,
  Search,
  Sparkles,
  Ticket,
  Trash2,
} from 'lucide-react';
import { api, date, plain, type WikiDocument } from '@/lib/api';
import { AnalysisJobView, AnalysisLabel, analyzed } from '../analysis';
import { useApp } from '../app';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { activeJob } from '../import-jobs';
import { rove } from '../roving';

export const ICONS = {
  confluence: Globe,
  jira: Ticket,
  rtf: FileText,
  office: FileText,
  image: ImageIcon,
  text: NotepadText,
  markdown: FileCode2,
};

// The library shows every document, or only those the LLM has or has not analyzed.
const FILTERS = { all: '전체', analyzed: 'AI 분석 완료', plain: '분석 전' };
type Filter = keyof typeof FILTERS;

export default function DocumentsWidget() {
  const { documents, openDocument, newDocument, navigate, view } = useApp();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  // Documents chosen for the library's bulk actions; the actions take those the filters show.
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const library = view === 'library';
  const done = documents.filter(analyzed).length;
  const counts = { all: documents.length, analyzed: done, plain: documents.length - done };
  // The filter also finds documents by where they came from: a folder, a file name, a JIRA key or a page address.
  const filtered = documents.filter(
    (doc) =>
      (filter === 'all' || analyzed(doc) === (filter === 'analyzed')) &&
      `${doc.title} ${doc.summary} ${doc.keywords.join(' ')} ${sourceOf(doc)}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const visible = library ? filtered : filtered.slice(0, 5);
  const chosen = library ? filtered.filter((doc) => selected.has(doc.id)) : [];
  const select = (docs: WikiDocument[], on: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      for (const doc of docs) {
        if (on) next.add(doc.id);
        else next.delete(doc.id);
      }
      return next;
    });
  // Shift-click selects every document between the last one clicked and this one.
  function toggle(doc: WikiDocument, on: boolean, range: boolean) {
    const from = range ? filtered.findIndex((item) => item.id === anchor.current) : -1;
    const to = filtered.indexOf(doc);
    select(from < 0 ? [doc] : filtered.slice(Math.min(from, to), Math.max(from, to) + 1), on);
    anchor.current = doc.id;
  }
  return (
    <>
      <WidgetHead id="documents" title="문서" count={documents.length}>
        <button className="button secondary small" onClick={() => newDocument()}>
          <Plus size={14} />새 문서
        </button>
      </WidgetHead>
      {documents.length > 0 && library && (
        <div className="document-tools">
          <label className="document-filter">
            <Search size={16} />
            <input
              aria-label="보관함 문서 필터"
              placeholder="제목, 요약, 키워드, 출처로 찾기"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <small>{filtered.length}개</small>
          </label>
          <div className="segmented" role="radiogroup" aria-label="AI 분석 여부">
            {(Object.keys(FILTERS) as Filter[]).map((id) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={filter === id}
                tabIndex={filter === id ? 0 : -1}
                onKeyDown={rove}
                className={filter === id ? 'active' : ''}
                onClick={() => setFilter(id)}
              >
                {FILTERS[id]}
                <span className="count">{counts[id]}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <AnalysisJobView />
      {!documents.length && (
        <EmptyState
          icon={FileText}
          title="첫 번째 지식을 담아 보세요"
          action={
            <button className="text-button" onClick={() => navigate('library', 'import')}>
              자료 가져오기 <ArrowRight size={13} />
            </button>
          }
        >
          메모를 작성하거나 파일과 링크를 가져오세요.
        </EmptyState>
      )}
      {!!documents.length && !filtered.length && (
        <p className="widget-note">
          {query
            ? '일치하는 문서가 없습니다. 다른 검색어를 입력해 보세요.'
            : filter === 'analyzed'
              ? 'AI로 분석한 문서가 없습니다. 문서를 골라 AI 분석을 실행해 보세요.'
              : '모든 문서를 AI로 분석했습니다.'}
        </p>
      )}
      {library && filtered.length > 0 && (
        <BulkActions
          chosen={chosen}
          all={chosen.length === filtered.length}
          onSelectAll={(on) => select(filtered, on)}
          onClear={() => setSelected(new Set())}
        />
      )}
      <ul className="document-list">
        {visible.map((doc) => (
          <DocumentRow
            key={doc.id}
            doc={doc}
            onOpen={() => openDocument(doc.id)}
            selection={
              library
                ? {
                    checked: selected.has(doc.id),
                    onToggle: (on, range) => toggle(doc, on, range),
                  }
                : undefined
            }
          />
        ))}
      </ul>
      {view === 'home' && documents.length > 5 && (
        <button className="text-button" onClick={() => navigate('library')}>
          문서 {documents.length}개 모두 보기 <ArrowRight size={13} />
        </button>
      )}
    </>
  );
}

const sourceOf = (doc: WikiDocument) =>
  !doc.source ? '' : 'source_url' in doc.source ? doc.source.source_url : doc.source.source_path;

// Select all shown documents, and act on the chosen ones: analyze them with the LLM, take back their analysis
// or delete them.
function BulkActions({
  chosen,
  all,
  onSelectAll,
  onClear,
}: {
  chosen: WikiDocument[];
  all: boolean;
  onSelectAll: (on: boolean) => void;
  onClear: () => void;
}) {
  const { configured, openAdmin, notify, refresh, analysis } = useApp();
  const [busy, setBusy] = useState(false);
  const ids = chosen.map((doc) => doc.id);
  const running = !!analysis.job && activeJob(analysis.job.status);
  async function run(action: () => Promise<string | void>) {
    setBusy(true);
    try {
      const message = await action();
      onClear();
      if (message) notify(message);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="document-bulk" role="group" aria-label="선택한 문서 작업">
      <label className="check" title="지금 목록에 보이는 문서를 모두 선택합니다">
        <input
          type="checkbox"
          checked={all}
          ref={(box) => {
            if (box) box.indeterminate = !!chosen.length && !all;
          }}
          onChange={(event) => onSelectAll(event.target.checked)}
        />
        모두 선택
      </label>
      {chosen.length > 0 && (
        <>
          <strong className="selection-count" aria-live="polite">
            {chosen.length}개 선택
          </strong>
          <button
            type="button"
            className="button secondary small"
            disabled={busy || running}
            title={
              running
                ? '진행 중인 AI 분석이 끝나면 다시 실행할 수 있습니다'
                : 'LLM으로 차례로 분석합니다. 이미 분석한 문서는 다시 분석합니다'
            }
            onClick={() =>
              configured
                ? void run(async () => {
                    await api('analysis.start', { ids });
                    analysis.watch();
                  })
                : openAdmin('llm')
            }
          >
            <Sparkles size={14} />
            AI 분석
          </button>
          {chosen.some((doc) => analyzed(doc) || doc.status === 'error') && (
            <Confirm
              label="분석 결과 지우기"
              icon={<Eraser size={14} />}
              question={`선택한 문서 ${chosen.length}개의 AI 분석 결과(요약·키워드·관계)를 지울까요?`}
              disabled={busy}
              onConfirm={() =>
                run(async () => {
                  const result = await api<{ cleared: number; busy: number }>(
                    'wiki.clear_analysis',
                    { ids },
                  );
                  await refresh();
                  const kept = result.busy
                    ? `분석 중인 문서 ${result.busy}개는 그대로 두었습니다.`
                    : '';
                  return result.cleared
                    ? `문서 ${result.cleared}개의 AI 분석 결과를 지웠습니다. ${kept}`.trim()
                    : kept || '지울 AI 분석 결과가 없습니다.';
                })
              }
            />
          )}
          <Confirm
            label="삭제"
            icon={<Trash2 size={14} />}
            question={`선택한 문서 ${chosen.length}개와 분석 결과를 삭제할까요?`}
            disabled={busy}
            onConfirm={() =>
              run(async () => {
                const { deleted } = await api<{ deleted: number }>('wiki.delete_many', { ids });
                await refresh();
                return `문서 ${deleted}개를 삭제했습니다.`;
              })
            }
          />
          <button type="button" className="text-button" onClick={onClear}>
            선택 해제
          </button>
        </>
      )}
    </div>
  );
}

function DocumentRow({
  doc,
  onOpen,
  selection,
}: {
  doc: WikiDocument;
  onOpen: () => void;
  selection?: { checked: boolean; onToggle: (on: boolean, range: boolean) => void };
}) {
  const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
  return (
    <li className={selection ? `selectable${selection.checked ? ' selected' : ''}` : undefined}>
      {selection && (
        <input
          type="checkbox"
          className="document-check"
          aria-label={`${doc.title} 선택`}
          checked={selection.checked}
          // React reports a checkbox change with the click that made it, so Shift can be read.
          onChange={(event) =>
            selection.onToggle(event.target.checked, !!(event.nativeEvent as MouseEvent).shiftKey)
          }
        />
      )}
      <button className="document-row" onClick={onOpen}>
        <Icon size={15} aria-hidden="true" />
        <span className="document-title">
          <strong>{doc.title}</strong>
          <small>{doc.summary || plain(doc.excerpt ?? '')}</small>
        </span>
        {doc.index_mode === 'pageindex' && (
          <span className="index-badge" title="긴 문서를 목차 단위로 색인했습니다 (PageIndex)">
            <ListTree size={11} />
            목차 색인
          </span>
        )}
        <AnalysisLabel doc={doc} />
        <time dateTime={doc.updated_at}>{date(doc.updated_at)}</time>
      </button>
    </li>
  );
}
