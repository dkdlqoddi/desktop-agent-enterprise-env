import { memo, useCallback, useLayoutEffect, useRef, useState } from 'react';
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
  Undo2,
} from 'lucide-react';
import { api, date, plain, type Deleted, type Trash, type WikiDocument } from '@/lib/api';
import { AnalysisJobView, AnalysisLabel, analyzed } from '../analysis';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import EmptyState from '../empty-state';
import { activeJob } from '../import-jobs';
import { rove } from '../roving';
import { useActions, useConfigured, useDocuments, useJobs, useQuery, useView } from '../state';

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
  const documents = useDocuments();
  const view = useView();
  const { openDocument, newDocument, navigate, refresh } = useActions();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  // Documents chosen for the library's bulk actions; the actions take those the filters show.
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const library = view === 'library';
  // The trash, shown in place of the list in the library; loaded again with every reload of the documents (its tag).
  const [trash, setTrash] = useState(false);
  const bin = useQuery<Trash>('wiki.trash', library ? {} : null).data ?? null;
  const binned = bin?.documents.length ?? 0;
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
  // The rows take one toggle that stays the same, so a row draws again only when its document or its check changed.
  const latest = useRef(toggle);
  useLayoutEffect(() => {
    latest.current = toggle;
  });
  const onToggle = useCallback(
    (doc: WikiDocument, on: boolean, range: boolean) => latest.current(doc, on, range),
    [],
  );
  return (
    <>
      <WidgetHead id="documents" title="문서" count={documents.length}>
        <button className="button secondary small" onClick={() => newDocument()}>
          <Plus size={14} />새 문서
        </button>
      </WidgetHead>
      {(documents.length > 0 || binned > 0) && library && (
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
          <button
            type="button"
            className={`button secondary small${trash ? ' active' : ''}`}
            aria-pressed={trash}
            onClick={() => setTrash(!trash)}
          >
            <Trash2 size={14} />
            휴지통{binned ? ` ${binned}` : ''}
          </button>
        </div>
      )}
      {trash && library && <TrashView bin={bin} onChanged={refresh} />}
      {!(trash && library) && <AnalysisJobView />}
      {!(trash && library) && !documents.length && (
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
      {!(trash && library) && !!documents.length && !filtered.length && (
        <p className="widget-note">
          {query
            ? '일치하는 문서가 없습니다. 다른 검색어를 입력해 보세요.'
            : filter === 'analyzed'
              ? 'AI로 분석한 문서가 없습니다. 문서를 골라 AI 분석을 실행해 보세요.'
              : '모든 문서를 AI로 분석했습니다.'}
        </p>
      )}
      {library && !trash && filtered.length > 0 && (
        <BulkActions
          chosen={chosen}
          all={chosen.length === filtered.length}
          onSelectAll={(on) => select(filtered, on)}
          onClear={() => setSelected(new Set())}
        />
      )}
      <ul className="document-list" hidden={trash && library}>
        {visible.map((doc) => (
          <DocumentRow
            key={doc.id}
            doc={doc}
            onOpen={openDocument}
            checked={library ? selected.has(doc.id) : undefined}
            onToggle={onToggle}
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

// The trash: the deleted documents wait `days` days to be restored, or to be deleted for good by the user.
function TrashView({ bin, onChanged }: { bin: Trash | null; onChanged: () => Promise<void> }) {
  const { notify } = useActions();
  const [busy, setBusy] = useState('');
  async function run(key: string, action: () => Promise<string>) {
    setBusy(key);
    try {
      const message = await action();
      await onChanged();
      notify(message);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy('');
    }
  }
  if (!bin) return <p className="widget-note">휴지통을 불러오는 중입니다.</p>;
  if (!bin.documents.length) return <p className="widget-note">휴지통이 비어 있습니다.</p>;
  return (
    <>
      <p className="trash-head">
        <span>
          삭제한 문서는 {bin.days}일 뒤 자동으로 지워집니다. 복원하면 AI 분석 결과와 할 일의 출처도
          돌아옵니다.
        </span>
        <Confirm
          label="휴지통 비우기"
          icon={<Trash2 size={14} />}
          question={`휴지통의 문서 ${bin.documents.length}개를 영구 삭제할까요? 되돌릴 수 없습니다.`}
          disabled={!!busy}
          onConfirm={() =>
            run('all', async () => {
              const { purged } = await api<{ purged: number }>('wiki.purge', { all: true });
              return `문서 ${purged}개를 영구 삭제했습니다.`;
            })
          }
        />
      </p>
      <ul className="document-list" aria-label="휴지통">
        {bin.documents.map((doc) => {
          const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
          return (
            <li key={doc.id} className="trash-row">
              <Icon size={15} aria-hidden="true" />
              <span className="document-title">
                <strong>{doc.title}</strong>
                <small>{doc.summary || plain(doc.excerpt ?? '')}</small>
              </span>
              <time dateTime={doc.deleted_at ?? undefined}>
                {doc.deleted_at ? date(doc.deleted_at) : ''}
              </time>
              <span className="trash-actions">
                <button
                  type="button"
                  className="button secondary small"
                  disabled={!!busy}
                  onClick={() =>
                    void run(doc.id, async () => {
                      await api('wiki.restore', { ids: [doc.id] });
                      return `‘${doc.title}’ 문서를 복원했습니다.`;
                    })
                  }
                >
                  <Undo2 size={14} />
                  복원
                </button>
                <Confirm
                  label="영구 삭제"
                  icon={<Trash2 size={14} />}
                  iconOnly
                  name={`${doc.title} 영구 삭제`}
                  question="영구 삭제할까요? 되돌릴 수 없습니다."
                  disabled={!!busy}
                  onConfirm={() =>
                    run(doc.id, async () => {
                      await api('wiki.purge', { ids: [doc.id] });
                      return `‘${doc.title}’ 문서를 영구 삭제했습니다.`;
                    })
                  }
                />
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}

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
  const configured = useConfigured();
  const { openAdmin, notify, undoable, refresh, watchJobs } = useActions();
  const [busy, setBusy] = useState(false);
  const ids = chosen.map((doc) => doc.id);
  const running = useJobs((jobs) => !!jobs.analysis && activeJob(jobs.analysis.status));
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
                    watchJobs();
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
            question={`선택한 문서 ${chosen.length}개를 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다.`}
            disabled={busy}
            onConfirm={() =>
              run(async () => {
                const moved = await api<Deleted>('wiki.delete_many', { ids });
                await refresh();
                undoable(`문서 ${moved.deleted}개를 휴지통으로 옮겼습니다.`, {
                  label: '되돌리기',
                  run: async () => {
                    await api('wiki.restore', { ids: moved.ids });
                    await refresh();
                  },
                });
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

// A row of the list, drawn again only when its document (kept as the same object while it does not change, see
// keepEqual in src/lib/query.ts) or its check changed. Without `checked` it has no checkbox.
const DocumentRow = memo(function DocumentRow({
  doc,
  onOpen,
  checked,
  onToggle,
}: {
  doc: WikiDocument;
  onOpen: (id: string) => void;
  checked?: boolean;
  onToggle: (doc: WikiDocument, on: boolean, range: boolean) => void;
}) {
  const Icon = doc.source ? ICONS[doc.source.kind] : FileText;
  return (
    <li className={checked === undefined ? undefined : `selectable${checked ? ' selected' : ''}`}>
      {checked !== undefined && (
        <input
          type="checkbox"
          className="document-check"
          aria-label={`${doc.title} 선택`}
          checked={checked}
          // React reports a checkbox change with the click that made it, so Shift can be read.
          onChange={(event) =>
            onToggle(doc, event.target.checked, !!(event.nativeEvent as MouseEvent).shiftKey)
          }
        />
      )}
      <button className="document-row" onClick={() => onOpen(doc.id)}>
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
});
