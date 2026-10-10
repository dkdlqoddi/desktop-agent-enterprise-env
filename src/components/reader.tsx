import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { catchError, type ErrorInfo } from 'next/error';
import {
  AlertCircle,
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileCode2,
  Hash,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  Network,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react';
import {
  api,
  clock,
  date,
  dayTime,
  seconds,
  type EditConflict,
  type ImportOutcome,
  type OutlineNode,
  type Run,
  type SkipReason,
  type SourceStatus,
  type TextDiff,
  type TextImport,
  type Deleted,
  type WikiDocument,
  type WritebackJob,
} from '@/lib/api';
import { readableSource } from '@/lib/html-document';
import { AnalysisLabel, analyzed } from './analysis';
import { useActions, useConfigured, useJobs } from './state';
import Confirm from './confirm';
import ErrorBoundary, { RenderError } from './error-boundary';
import { findAll, MatchNavigator, useMatches } from './highlight';
import HtmlDocument from './html-document';
import { activeJob, type ImportKind } from './import-jobs';
import Markdown from './markdown';
import Modal, { type ModalHandle } from './modal';
import { rove } from './roving';
import WritebackBackupsList from './writeback-backups';

// A document opened from search results shows where the search words and chosen sections are.
export type Focus = { terms: string[]; sections: string[] };
export type ReaderTarget =
  | { kind: 'document'; id: string; focus?: Focus | null }
  | { kind: 'new'; title?: string; content?: string }
  | { kind: 'run'; id: string; terms?: string[] };
// The document as the editor opened it.
type Started = { revision: number; title: string; content: string };

export default function Reader({ target, onClose }: { target: ReaderTarget; onClose: () => void }) {
  return (
    <Recovering title={target.kind === 'run' ? '실행 기록' : '문서'} onClose={onClose}>
      {target.kind === 'run' ? (
        <RunReader id={target.id} terms={target.terms} onClose={onClose} />
      ) : (
        <DocumentReader target={target} onClose={onClose} />
      )}
    </Recovering>
  );
}

// The window when drawing it failed: the panel stays with the error and 다시 시도, and closes as always.
const Recovering = catchError(function Failed(
  { title, onClose }: { title: string; onClose: () => void },
  { error, reset }: ErrorInfo,
) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="drawer-body">
        <RenderError message="이 창을 표시하지 못했습니다." error={error} onRetry={reset} />
      </div>
    </Modal>
  );
});

const SOURCE_LABEL = {
  confluence: () => '가져온 Confluence 페이지',
  jira: (source: { issue_key: string }) => `가져온 JIRA 티켓 · ${source.issue_key}`,
  office: (source: { application: string }) => `가져온 ${source.application} 문서`,
  rtf: () => '가져온 RTF 문서',
  image: () => '가져온 이미지',
  text: () => '가져온 텍스트 파일',
  markdown: () => '가져온 Markdown 파일',
};

function DocumentReader({
  target,
  onClose,
}: {
  target: Extract<ReaderTarget, { kind: 'document' | 'new' }>;
  onClose: () => void;
}) {
  const configured = useConfigured();
  const { notify, undoable, refresh, searchKeyword, askAbout, openAdmin, openDocument } =
    useActions();
  const [doc, setDoc] = useState<WikiDocument | null>(null);
  const [focus, setFocus] = useState(target.kind === 'document' ? (target.focus ?? null) : null);
  // source: the Markdown a Confluence or JIRA page became; html: the HTML page an imported file became; conflict: what
  // a save refused because the document changed meanwhile elsewhere, compared, with the ways to save it.
  const [mode, setMode] = useState<'view' | 'edit' | 'conflict' | 'source' | 'html'>(
    target.kind === 'new' ? 'edit' : 'view',
  );
  const [draft, setDraft] = useState({
    title: target.kind === 'new' ? (target.title ?? '') : '',
    content: target.kind === 'new' ? (target.content ?? '') : '',
  });
  // The text and revision the editor opened: a save never overwrites text changed meanwhile elsewhere, and one refused
  // for it compares this text with the editor's and the document's (wiki.compare), as no earlier text is stored.
  const base = useRef<Started | null>(null);
  // The comparison of a refused save, with the text the editor had started from then.
  const [conflict, setConflict] = useState<{ compared: EditConflict; started: Started } | null>(
    null,
  );
  // What changed elsewhere, beside the editor once the user goes back to writing from the document as it is now, to
  // bring into their text by hand.
  const [elsewhere, setElsewhere] = useState<TextDiff | null>(null);
  // The document 새 문서로 저장 made of the editor's text, which this one then links to.
  const [copy, setCopy] = useState<{ id: string; title: string } | null>(null);
  const [analyzeOnSave, setAnalyzeOnSave] = useState(false);
  const [source, setSource] = useState<{ markdown: string; filename: string } | null>(null);
  const [page, setPage] = useState<{ html: string; filename: string } | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const contentRef = useRef<HTMLDivElement>(null);
  const modal = useRef<ModalHandle>(null);
  const matches = useMatches(
    contentRef,
    doc && mode === 'view' ? `${doc.id}:${doc.revision}:${focus?.terms.join('\n') ?? ''}` : '',
  );
  useEffect(() => {
    if (target.kind !== 'document') return;
    let live = true;
    api<WikiDocument>('wiki.get', { id: target.id })
      .then((value) => live && setDoc(value))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [target]);
  // The document read again (an analysis, an update or a write back ended) while the user writes would change the
  // text and revision under the editor: it waits until the writing ends, and the user hears of it.
  const editingNow = useRef(false);
  editingNow.current = mode === 'edit' || mode === 'conflict';
  const behind = useRef(false);
  const show = (value: WikiDocument, what: string) => {
    if (!editingNow.current) return setDoc(value);
    behind.current = true;
    notify(
      `편집하는 동안 ${what} 쓰던 내용은 그대로 두었으며, 저장하거나 취소하면 바뀐 문서를 보여 드립니다.`,
    );
  };
  const reload = (id: string, what: string) =>
    api<WikiDocument>('wiki.get', { id }).then(
      (value) => show(value, what),
      (e) => setError(e.message),
    );
  // Back from the editor without saving: a document read again meanwhile shows now.
  const leaveEditor = () => {
    setMode('view');
    base.current = null;
    setElsewhere(null);
    if (!behind.current || !doc) return;
    behind.current = false;
    void api<WikiDocument>('wiki.get', { id: doc.id }).then(setDoc, (e) => setError(e.message));
  };
  // The analysis of documents chosen in the list may reach this one while it is open: show its result once done.
  // Only whether it waits draws the window again, not every step of the analysis.
  const waiting = useJobs(
    (jobs) => !!doc && (jobs.queued.has(doc.id) || jobs.analysis?.current?.id === doc.id),
  );
  const waited = useRef(false);
  useEffect(() => {
    const ended = waited.current && !waiting && doc;
    waited.current = waiting;
    if (!ended) return;
    let live = true;
    api<WikiDocument>('wiki.get', { id: doc.id })
      .then((value) => live && show(value, '이 문서의 AI 분석이 끝났습니다.'))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [waiting, doc]);
  // An update from the source or a write back into it, kept here while the window shows the editor or the raw text.
  const jobs = useSourceJobs(doc, (id) =>
    doc && id !== doc.id ? openDocument(id) : reload(id, '이 문서가 바뀌었습니다.'),
  );
  // Editing waits for them: the text they bring would land under the editor.
  const held = !!busy || jobs.running;
  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    setError('');
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  }
  const analyze = (id: string) =>
    act('analyze', async () => {
      try {
        setDoc(await api<WikiDocument>('wiki.analyze', { id }));
        notify('키워드와 관계를 분석했습니다.');
      } finally {
        setDoc(await api<WikiDocument>('wiki.get', { id }));
        await refresh();
      }
    });
  // The editor's text saved over revision `expected`: the one the editor opened, or the document's as the conflict view
  // showed it (현재 문서 덮어쓰기). A save refused because the document changed meanwhile shows the conflict view.
  async function save(expected?: number) {
    const revision = expected ?? base.current?.revision ?? doc?.revision;
    let saved: WikiDocument;
    try {
      saved = await api<WikiDocument>('wiki.save', {
        ...draft,
        id: doc?.id,
        ...(doc ? { expected_revision: revision } : {}),
      });
    } catch (error) {
      if (revision !== undefined && (await compare(revision))) return;
      throw error;
    }
    behind.current = false;
    base.current = null;
    setConflict(null);
    setElsewhere(null);
    setDoc(saved);
    setFocus(null);
    setMode('view');
    await refresh();
    notify(
      expected === undefined ? '문서를 저장했습니다.' : '현재 문서를 내 수정으로 덮어썼습니다.',
    );
    if (analyzeOnSave) await analyze(saved.id);
  }
  // Whether a save refused at revision `expected` met a change made elsewhere since, which the conflict view then
  // shows; any other failure (a document deleted meanwhile, a backend that went away) is told as it was.
  async function compare(expected: number) {
    const started = base.current;
    if (!doc || !started) return false;
    let found: EditConflict;
    try {
      found = await api<EditConflict>('wiki.compare', {
        id: doc.id,
        base_revision: started.revision,
        base: started.content,
        content: draft.content,
      });
    } catch {
      return false;
    }
    if (!found.changed || found.document.revision === expected) return false;
    setConflict({ compared: found, started });
    setMode('conflict');
    return true;
  }
  // 새 문서로 저장: the editor's text as a document of its own, named after the one it was written for, which the
  // window shows again as it is now, with a link to the copy.
  const saveCopy = () =>
    act('copy', async () => {
      if (!doc || !conflict) return;
      const suffix = ' (충돌 사본)';
      // Within the 200 characters of a title, counted as the backend counts them.
      const title = [...draft.title.trim()].slice(0, 200 - suffix.length).join('') + suffix;
      const saved = await api<WikiDocument>('wiki.save', { title, content: draft.content });
      const current = await api<WikiDocument>('wiki.get', { id: doc.id }).catch(
        () => conflict.compared.document,
      );
      behind.current = false;
      base.current = null;
      setConflict(null);
      setElsewhere(null);
      setCopy({ id: saved.id, title: saved.title });
      setDoc(current);
      setFocus(null);
      setMode('view');
      await refresh();
      notify(`쓰던 내용을 새 문서 ‘${saved.title}’(으)로 저장했습니다.`);
    });
  // 내 수정 유지하고 다시 열기: the editor goes on from the document as it is now, which the next save expects. What the
  // user changed stays; what they left as it was (the title, say) takes the document's new text, so a save does not
  // quietly take back the change made elsewhere.
  function reopen() {
    if (!conflict) return;
    const { compared, started } = conflict;
    const current = compared.document;
    const text = current.content ?? '';
    setDraft({
      title: draft.title === started.title ? current.title : draft.title,
      content: draft.content === started.content ? text : draft.content,
    });
    base.current = { revision: current.revision, title: current.title, content: text };
    setElsewhere(compared.diffs.theirs.hunks.length ? compared.diffs.theirs : null);
    // The comparison carries the document without its page: the window reads it whole once the editing ends.
    behind.current = true;
    setDoc(current);
    setConflict(null);
    setMode('edit');
    notify('지금 문서를 기준으로 다시 열었습니다. 쓰던 내용은 그대로 두었습니다.');
  }
  // Headings of the rendered document for its PageIndex table of contents.
  function showSection(node: OutlineNode) {
    const headings = findAll(contentRef.current, 'h1, h2, h3, h4, h5, h6');
    const text = (element: Element) => element.textContent?.trim() ?? '';
    const direct = node.heading === null ? undefined : headings[node.heading];
    const heading =
      direct && text(direct).startsWith(node.title)
        ? direct
        : headings.find((element) => text(element) === node.title);
    heading?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  // Writing: in the editor, or choosing how to save what a refused save held (the conflict view).
  const editing = mode === 'edit' || mode === 'conflict';
  const title =
    mode === 'conflict'
      ? '수정 충돌'
      : editing
        ? doc
          ? '문서 수정'
          : '새 문서'
        : (doc?.title ?? '문서');
  const subtitle = doc && !editing ? `마지막 수정 ${date(doc.updated_at)}` : undefined;
  // What closing the panel would throw away: a change to the opened document, or a new one with any text.
  const unsaved =
    editing &&
    (doc
      ? draft.title !== doc.title || draft.content !== (doc.content || '')
      : !!(draft.title.trim() || draft.content.trim()));
  // While the source is read or written, 수정·원문·삭제 wait and say why.
  const waitNote = jobs.running
    ? '원본에서 가져오거나 원본에 반영하는 동안에는 쓸 수 없습니다'
    : undefined;
  const actions =
    doc && mode === 'view' ? (
      <>
        <button
          className="button secondary small"
          disabled={held}
          title={waitNote}
          onClick={() => {
            const text = doc.content || '';
            setDraft({ title: doc.title, content: text });
            base.current = { revision: doc.revision, title: doc.title, content: text };
            setElsewhere(null);
            setAnalyzeOnSave(false);
            setMode('edit');
          }}
        >
          <Pencil size={14} />
          수정
        </button>
        <button
          className="button secondary small"
          disabled={!!busy}
          onClick={() => askAbout({ id: doc.id, title: doc.title })}
        >
          <MessageSquareText size={14} />이 문서에 질문
        </button>
        {doc.has_html ? (
          <button
            className="button secondary small"
            disabled={held}
            title={waitNote}
            onClick={() =>
              void act('html', async () => {
                setPage(await api('wiki.source_html', { id: doc.id }));
                setMode('html');
              })
            }
          >
            <FileCode2 size={14} />
            원문 HTML
          </button>
        ) : (
          doc.source && (
            <button
              className="button secondary small"
              disabled={held}
              title={waitNote}
              onClick={() =>
                void act('source', async () => {
                  setSource(await api('wiki.source_markdown', { id: doc.id }));
                  setMode('source');
                })
              }
            >
              <FileCode2 size={14} />
              원문 Markdown
            </button>
          )
        )}
        <Confirm
          label="삭제"
          icon={<Trash2 size={14} />}
          question="이 문서를 휴지통으로 옮길까요? 30일 안에 복원할 수 있습니다."
          disabled={held}
          onConfirm={() =>
            act('delete', async () => {
              const { ids } = await api<Deleted>('wiki.delete', { id: doc.id });
              await refresh();
              undoable('문서를 휴지통으로 옮겼습니다.', {
                label: '되돌리기',
                run: async () => {
                  await api('wiki.restore', { ids });
                  await refresh();
                },
              });
              onClose();
            })
          }
        />
      </>
    ) : null;
  return (
    <Modal
      ref={modal}
      title={title}
      subtitle={subtitle}
      actions={actions}
      onClose={onClose}
      locked={!!busy && busy !== 'analyze'}
      confirmClose={unsaved ? '저장하지 않은 내용이 있습니다. 버리고 닫을까요?' : undefined}
    >
      {error && (
        <p className="inline-error drawer-error" role="alert">
          {error}
        </p>
      )}
      {copy && mode === 'view' && (
        <p className="banner drawer-error" role="status">
          <Copy size={15} />
          <span>쓰던 내용은 새 문서 ‘{copy.title}’에 저장했습니다.</span>
          <button type="button" className="text-button" onClick={() => openDocument(copy.id)}>
            사본 열기
          </button>
        </p>
      )}
      {mode === 'conflict' && conflict ? (
        <ConflictView
          conflict={conflict.compared}
          startedTitle={conflict.started.title}
          busy={!!busy}
          unsaved={unsaved}
          onOverwrite={() => act('save', () => save(conflict.compared.document.revision))}
          onCopy={() => void saveCopy()}
          onReopen={reopen}
        />
      ) : editing ? (
        <form
          className="drawer-body document-form"
          // Unsaved writing, also after a save that failed: closing the window keeps it (app-control.ts changedForm).
          data-dirty={unsaved ? 'true' : undefined}
          onSubmit={(event) => {
            event.preventDefault();
            // The revision this editor opened: text changed meanwhile elsewhere is not overwritten.
            void act('save', () => save());
          }}
        >
          <label>
            문서 제목
            <input
              required
              maxLength={200}
              value={draft.title}
              autoFocus
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              placeholder="문서 제목"
            />
          </label>
          {elsewhere && (
            <details className="change-lines">
              <summary>
                다른 곳에서 바뀐 내용 보기 · +{elsewhere.added} −{elsewhere.removed}
              </summary>
              <DiffLines diff={elsewhere} from="편집 전" to="지금 문서" />
            </details>
          )}
          <label>
            내용
            <textarea
              required
              className="document-editor"
              rows={18}
              maxLength={80000}
              value={draft.content}
              onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              placeholder={'# 회의 메모\n\nMarkdown으로 작성할 수 있습니다.'}
            />
            <small>
              {draft.content.length.toLocaleString()} / 80,000자
              {doc ? ' · 내용을 바꾸면 이전 AI 분석은 지워집니다.' : ''}
            </small>
          </label>
          {doc?.format === 'html' && (
            <p className="drawer-note">
              파일에서 가져온 HTML 문서의 글입니다. 제목만 바꾸면 HTML 화면이 그대로이고, 내용을
              고치면 Markdown 문서로 저장되며 가져온 HTML은 ‘원문 HTML’에 남습니다.
            </p>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={analyzeOnSave}
              disabled={!configured}
              onChange={(e) => setAnalyzeOnSave(e.target.checked)}
            />
            저장 후 LLM으로 요약·키워드·관계 분석{configured ? '' : ' (LLM 연결 필요)'}
          </label>
          <div className="form-actions">
            {/* Unsaved writing is asked about in the panel first, as when closing it. */}
            <button
              type="button"
              className="button secondary"
              disabled={!!busy}
              onClick={() => modal.current?.requestClose(doc ? leaveEditor : undefined)}
            >
              취소
            </button>
            <button className="button primary" disabled={!!busy}>
              {busy === 'save' && <LoaderCircle size={15} className="spin" />}
              문서 저장
            </button>
          </div>
        </form>
      ) : mode === 'source' && source && doc ? (
        <div className="drawer-body">
          <p className="drawer-note">
            원본에서 가져올 때 변환한 Markdown 원문입니다. 본문을 수정해도 원문은 그대로 남고,
            원본에서 업데이트하면 새로 가져온 내용으로 바뀝니다.
          </p>
          <textarea
            aria-label="변환 Markdown 소스"
            className="document-editor"
            rows={22}
            readOnly
            value={source.markdown}
          />
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setMode('view')}>
              문서로 돌아가기
            </button>
            <a
              className="button primary"
              href={`data:text/markdown;charset=utf-8,${encodeURIComponent(source.markdown)}`}
              download={source.filename}
            >
              .md 다운로드
            </a>
          </div>
        </div>
      ) : mode === 'html' && page && doc ? (
        <HtmlSource page={page} onBack={() => setMode('view')} />
      ) : !doc ? (
        <div className="drawer-body loading-page small">
          <LoaderCircle className="spin" size={24} />
        </div>
      ) : (
        <div className="document-view">
          <div className="document-content" ref={contentRef}>
            {/* A text that cannot be drawn leaves the window, its 수정 and 원문 included, as it was. */}
            <ErrorBoundary
              key={`${doc.id}:${doc.revision}`}
              message="문서 내용을 표시하지 못했습니다."
            >
              {focus && <MatchNavigator matches={matches} terms={focus.terms} />}
              {doc.source && <DocumentSource doc={doc} jobs={jobs} />}
              {doc.format === 'html' && doc.html ? (
                <HtmlDocument html={doc.html} highlight={focus?.terms} />
              ) : (
                <Markdown highlight={focus?.terms}>{doc.content || ''}</Markdown>
              )}
            </ErrorBoundary>
          </div>
          <aside className="document-insight" aria-label="AI 분석">
            <div className="insight-head">
              <h3>
                <Sparkles size={15} />
                AI 분석
              </h3>
              <AnalysisLabel doc={doc} />
            </div>
            {doc.summary ? (
              <p className="document-summary">{doc.summary}</p>
            ) : (
              <p className="muted">요약·키워드·관계를 아직 추출하지 않았습니다.</p>
            )}
            {doc.error && (
              <p role="alert" className="inline-error">
                {doc.error}
              </p>
            )}
            <button
              className="button secondary full"
              disabled={!!busy}
              onClick={() => (configured ? void analyze(doc.id) : openAdmin('llm'))}
            >
              {busy === 'analyze' ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Sparkles size={15} />
              )}
              {!configured
                ? 'LLM 연결하기'
                : busy === 'analyze'
                  ? '분석하는 중…'
                  : analyzed(doc)
                    ? '다시 분석하기'
                    : 'AI 분석하기'}
            </button>
            <small>
              {doc.large
                ? '12,000자가 넘는 문서는 목차 색인(PageIndex)으로 섹션을 나누어 분석합니다.'
                : '분석할 때 문서 전문이 설정한 LLM API로 전송됩니다.'}
            </small>
            {!!doc.outline?.length && (
              <>
                <h4>
                  <ListTree size={14} /> 목차 색인 · 섹션 {doc.outline.length}개
                </h4>
                <ol className="outline-list" aria-label="문서 목차 색인">
                  {doc.outline.map((node) => (
                    <li
                      key={node.id}
                      style={{ paddingLeft: (node.depth - 1) * 12 }}
                      className={focus?.sections.includes(node.id) ? 'chosen' : undefined}
                    >
                      <button
                        type="button"
                        title={node.summary}
                        disabled={node.kind !== 'section'}
                        onClick={() => showSection(node)}
                      >
                        <span>{node.id}</span>
                        {node.title}
                      </button>
                      {focus?.sections.includes(node.id) && node.summary && (
                        <small>{node.summary}</small>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
            <h4>
              <Hash size={14} /> 키워드
            </h4>
            <div className="tags">
              {doc.keywords.length ? (
                doc.keywords.map((key) => (
                  <button
                    className="tag"
                    key={key}
                    onClick={() => {
                      // The window stays when an answer still being written keeps the search from starting.
                      if (searchKeyword(key)) onClose();
                    }}
                  >
                    {key}
                  </button>
                ))
              ) : (
                <span className="muted">분석 후 표시됩니다.</span>
              )}
            </div>
            <h4>
              <Network size={14} /> 발견한 관계
            </h4>
            <div className="relation-list">
              {doc.relations?.length ? (
                doc.relations.map((relation, i) => (
                  <div key={i}>
                    <strong>{relation.source}</strong>
                    <span>↳ {relation.label}</span>
                    <strong>{relation.target}</strong>
                  </div>
                ))
              ) : (
                <span className="muted">아직 추출된 관계가 없습니다.</span>
              )}
            </div>
          </aside>
        </div>
      )}
    </Modal>
  );
}

// The comparisons of a refused save, from wiki.compare: the text the editor started from against the document's and
// the editor's, and the document's against the editor's.
type Side = keyof EditConflict['diffs'];
const SIDES: { id: Side; label: string; note: string; from: string; to: string }[] = [
  {
    id: 'theirs',
    label: '다른 곳에서 바뀐 내용',
    note: '편집을 시작한 뒤 다른 창·AI 도구·가져오기에서 바뀐 줄입니다.',
    from: '편집 전',
    to: '지금 문서',
  },
  {
    id: 'ours',
    label: '내가 바꾼 내용',
    note: '편집을 시작한 뒤 내가 바꾼 줄입니다.',
    from: '편집 전',
    to: '내 수정',
  },
  {
    id: 'overwrite',
    label: '덮어쓰면 바뀌는 내용',
    note: '지금 문서를 덮어쓰면 − 줄이 사라지고 + 줄이 들어갑니다.',
    from: '지금 문서',
    to: '내 수정',
  },
];

// A save refused because the document changed elsewhere while it was edited: what changed on each side, and the ways
// to go on. The writing stays unsaved meanwhile (data-dirty), so neither closing the window nor an AI tool throws it
// away (app-control.ts editingDocument, changedForm).
function ConflictView({
  conflict,
  startedTitle,
  busy,
  unsaved,
  onOverwrite,
  onCopy,
  onReopen,
}: {
  conflict: EditConflict;
  startedTitle: string;
  busy: boolean;
  unsaved: boolean;
  onOverwrite: () => Promise<void>;
  onCopy: () => void;
  onReopen: () => void;
}) {
  const [side, setSide] = useState<Side>('theirs');
  const chosen = SIDES.find((item) => item.id === side)!;
  const diff = conflict.diffs[side];
  const current = conflict.document;
  return (
    <div className="drawer-body document-form" data-dirty={unsaved ? 'true' : undefined}>
      <p className="banner warn" role="alert">
        <AlertCircle size={15} />
        <span>
          편집하는 동안 다른 곳에서 문서가 바뀌어(마지막 수정 {date(current.updated_at)}) 저장하지
          않았습니다. 바뀐 내용을 비교하고 저장할 방법을 골라 주세요. 쓰던 내용은 그대로 있습니다.
        </span>
      </p>
      {current.title !== startedTitle && (
        <p className="drawer-note">
          제목도 ‘{startedTitle}’에서 ‘{current.title}’(으)로 바뀌었습니다.
        </p>
      )}
      {/* As wide as its choices, which wrap on a narrow window. */}
      <div
        className="segmented"
        role="radiogroup"
        aria-label="비교할 내용"
        style={{ alignSelf: 'flex-start', flexWrap: 'wrap' }}
      >
        {SIDES.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={side === item.id}
            tabIndex={side === item.id ? 0 : -1}
            onKeyDown={rove}
            className={side === item.id ? 'active' : ''}
            onClick={() => setSide(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <p className="drawer-note">
        +{diff.added} −{diff.removed} · {chosen.note}
      </p>
      <div className="change-lines">
        <DiffLines diff={diff} from={chosen.from} to={chosen.to} />
      </div>
      <div className="form-actions">
        <Confirm
          label="현재 문서 덮어쓰기"
          icon={<Save size={14} />}
          question="다른 곳에서 바뀐 내용은 사라지고 내 수정으로 저장됩니다. 덮어쓸까요?"
          disabled={busy}
          onConfirm={onOverwrite}
        />
        <button type="button" className="button secondary small" disabled={busy} onClick={onCopy}>
          <Copy size={14} />새 문서로 저장
        </button>
        <button type="button" className="button primary small" disabled={busy} onClick={onReopen}>
          <Pencil size={14} />내 수정 유지하고 다시 열기
        </button>
      </div>
    </div>
  );
}

// One comparison of two texts (wiki.compare): changed lines among a few unchanged ones, each run headed by where it
// starts in the two texts. − lines are only in the first text, + lines only in the second.
function DiffLines({ diff, from, to }: { diff: TextDiff; from: string; to: string }) {
  if (!diff.hunks.length) return <p className="muted">바뀐 줄이 없습니다.</p>;
  return (
    <ul aria-label={`${from} → ${to}`} style={{ maxHeight: 'min(52vh, 560px)' }}>
      {diff.hunks.map((hunk, index) => (
        <Fragment key={index}>
          <li className="more">
            {from} {hunk.old_start}행 · {to} {hunk.new_start}행
          </li>
          {hunk.lines.map((line, number) => (
            <li
              key={number}
              className={line.op === '+' ? 'added' : line.op === '-' ? 'removed' : undefined}
            >
              {line.op === ' ' ? (
                <span aria-hidden="true"> </span>
              ) : (
                <span aria-label={line.op === '+' ? '추가' : '삭제'}>
                  {line.op === '+' ? '+' : '−'}
                </span>
              )}
              {line.text}
            </li>
          ))}
        </Fragment>
      ))}
      {diff.more > 0 && <li className="more">바뀐 줄 {diff.more}개 더 있음</li>}
    </ul>
  );
}

// The importer that reads each kind of source again; a Markdown file is read at once (files.import_text).
const IMPORTERS: Record<string, ImportKind> = {
  rtf: 'rtf',
  office: 'office',
  image: 'image',
  text: 'text',
  confluence: 'confluence',
  jira: 'jira',
};
type Outcome = {
  status: ImportOutcome;
  message: string;
  doc_id?: string;
  doc_ids?: string[];
  reason?: SkipReason;
  analysis_error?: string;
};
// What a write to the original file asks first: Office files (Word, Excel, PowerPoint or Visio) are the ones written.
const WRITE_QUESTION = {
  office:
    '고친 내용을 원본 파일에 쓰고 저장합니다. 바뀌기 전 파일은 Wiki 폴더의 ‘원본 백업’에 남깁니다. 반영할까요?',
};
const FILE_STATE: Record<SourceStatus['state'], string> = {
  same: '원본 파일은 가져올 때 그대로입니다',
  changed: '가져온 뒤 원본 파일이 바뀌었습니다',
  missing: '원본 파일을 찾을 수 없습니다. 옮겨졌거나 지워졌습니다',
  unreadable: '원본 파일을 읽지 못했습니다. 권한과 네트워크 연결을 확인해 주세요',
  unknown: '',
};

const sourcePath = (doc: WikiDocument) =>
  doc.source && 'source_path' in doc.source ? doc.source.source_path : '';
// The document and the other parts of a long one, which an update or a write may concern.
const partsOf = (doc: WikiDocument) =>
  doc.parts?.length ? doc.parts.map((part) => part.id) : [doc.id];

// The update of an imported document from its source and the write of its changes back into its file, while they
// run. The document window keeps them rather than the source box, which is gone while the window shows the editor or
// the raw text, and offers neither meanwhile: what they bring never lands under an open editor, the spinner stays,
// and the window shows the result when they end.
function useSourceJobs(doc: WikiDocument | null, onUpdated: (id: string) => void) {
  const configured = useConfigured();
  const { notify, refresh, watchJobs } = useActions();
  // The job of the update with the document as it was when the update started.
  const [job, setJob] = useState<{ kind: ImportKind; id: string; doc: WikiDocument } | null>(null);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState('');
  // The write of this document's changes into its file while it runs, also one started before the window opened.
  const [writing, setWriting] = useState<WritebackJob | null>(null);
  // A result that comes after the window closed is only told: the window does not open again for it.
  const open = useRef(true);
  useEffect(() => {
    open.current = true;
    return () => {
      open.current = false;
    };
  }, []);
  const updated = (id: string) => {
    if (open.current) onUpdated(id);
  };
  const source = doc?.source;
  const writable = source?.kind === 'office';
  // How a write ended, said once: here, or when the document opens after it ended elsewhere (an AI tool, or this
  // window closed while it ran).
  async function ended(job: WritebackJob, at: WikiDocument) {
    void api('writeback.dismiss', { id: job.id }).catch(() => {});
    if (job.status === 'error') return setError(job.error);
    notify(job.message || '원본 반영을 마쳤습니다.', false, false);
    if (job.status === 'completed') {
      await refresh();
      updated(at.id);
    }
  }
  useEffect(() => {
    if (!doc || !writable) return;
    let live = true;
    api<WritebackJob | null>('writeback.status')
      .then((job) => {
        if (!live || !job || !partsOf(doc).includes(job.doc_id)) return;
        if (activeJob(job.status)) setWriting(job);
        else if (!job.dismissed) void ended(job, doc);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [doc?.id, writable]);
  // Followed every second until it ends: a file of this PC is then read into the document again.
  useEffect(() => {
    if (!doc || !writing || !activeJob(writing.status)) return;
    const timer = setTimeout(async () => {
      let job: WritebackJob | null;
      try {
        job = await api<WritebackJob | null>('writeback.status', { id: writing.id });
      } catch {
        return setWriting({ ...writing }); // The backend is busy; look again.
      }
      if (job && activeJob(job.status)) return setWriting(job);
      setWriting(null);
      if (job) await ended(job, doc);
    }, 1000);
    return () => clearTimeout(timer);
  }, [writing]);
  async function write() {
    if (!doc) return;
    setError('');
    try {
      setWriting(await api<WritebackJob>('writeback.start', { id: doc.id }));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function finish(result: Outcome, at: WikiDocument) {
    setUpdating(false);
    if (result.status === 'failed') return setError(result.message);
    const ids = result.doc_ids ?? (result.doc_id ? [result.doc_id] : []);
    // The import says what it kept: the document as it was, or also a page whose formatting or pictures changed.
    if (result.reason === 'same' && result.doc_id && partsOf(at).includes(result.doc_id))
      notify(result.message || '원본이 바뀌지 않아 문서를 그대로 두었습니다.');
    else
      notify(
        [result.message, result.analysis_error].filter(Boolean).join(' '),
        !!result.analysis_error,
      );
    // A long text that became shorter may have dropped this part: its first part opens instead.
    updated(ids.length && !ids.includes(at.id) ? ids[0] : at.id);
  }
  // The job of the update, until it ends.
  const watched = useJobs((jobs) => (job ? jobs.imports[job.kind] : null));
  useEffect(() => {
    if (!job || !watched || watched.id !== job.id || activeJob(watched.status)) return;
    setJob(null);
    if (watched.error) {
      setUpdating(false);
      setError(watched.error);
    } else if (watched.results[0]) finish(watched.results[0], job.doc);
    else {
      setUpdating(false);
      notify('원본에서 업데이트를 중지했습니다.');
    }
  }, [job, watched]);
  async function update(replace: boolean) {
    const at = doc;
    const source = at?.source;
    if (!at || !source) return;
    const path = sourcePath(at);
    setUpdating(true);
    setError('');
    // An analyzed document is analyzed again, so it never shows what the old text said.
    const options = { analyze: configured && analyzed(at), doc_id: at.id, replace };
    try {
      if (source.kind === 'markdown') {
        const result = await api<TextImport>('files.import_text', { path, ...options });
        await refresh();
        return finish(result, at);
      }
      const kind = IMPORTERS[source.kind];
      const params =
        'source_url' in source
          ? kind === 'jira'
            ? { url: source.source_url, scope: 'issue', keyword: '', user: '', role: 'any' }
            : kind === 'confluence'
              ? { url: source.source_url, recursive: false }
              : { url: source.source_url }
          : { path, mode: 'file', recursive: false };
      const started = await api<{ id: string } | { job: { id: string } }>(`${kind}.start`, {
        ...params,
        ...options,
      });
      setJob({ kind, id: 'job' in started ? started.job.id : started.id, doc: at });
      watchJobs();
    } catch (e) {
      setUpdating(false);
      setError((e as Error).message);
    }
  }
  function stop() {
    void api<WritebackJob | null>('writeback.cancel').then(
      (job) => job && setWriting(job),
      (e) => setError((e as Error).message),
    );
  }
  return {
    updating,
    writing,
    running: updating || !!writing,
    writable,
    error,
    update,
    write,
    stop,
  };
}
type SourceJobs = ReturnType<typeof useSourceJobs>;

// Where an imported document came from: the file or link, when the document took its text from there, whether the
// file has changed since, and the update that takes the source's text again. The update is an import of the same
// path or link, which replaces this document (every part of a long one) in place; the import box lists it too.
function DocumentSource({ doc, jobs }: { doc: WikiDocument; jobs: SourceJobs }) {
  const source = doc.source!;
  const path = sourcePath(doc);
  const [status, setStatus] = useState<SourceStatus | null>(null);
  // Grows when a backup of the file was put in its place: the file is then looked at again.
  const [restored, setRestored] = useState(0);
  const { updating, writing, writable, error, update, write, stop } = jobs;
  useEffect(() => {
    setStatus(null);
    if (!path) return;
    let live = true;
    api<SourceStatus>('wiki.source_status', { id: doc.id })
      .then((value) => live && setStatus(value))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [doc.id, doc.revision, path, restored]);
  const missing = status?.state === 'missing';
  const imported = source.imported_at > doc.created_at;
  return (
    <div className="document-source">
      <strong>
        {source.kind === 'jira'
          ? SOURCE_LABEL.jira(source)
          : source.kind === 'office'
            ? SOURCE_LABEL.office(source)
            : SOURCE_LABEL[source.kind]()}
      </strong>
      <span className="break-path">{'source_url' in source ? source.source_url : path}</span>
      <small>
        가져온 날짜 {date(doc.created_at)}
        {imported && ` · 원본에서 업데이트 ${date(source.imported_at)}`}
      </small>
      {status && FILE_STATE[status.state] && (
        <small className={`source-state ${status.state}`} role="status">
          {FILE_STATE[status.state]}
          {status.state === 'changed' &&
            status.modified_at &&
            ` · 수정 ${date(status.modified_at)}`}
        </small>
      )}
      {source.edited && <small className="source-state edited">가져온 뒤 본문을 고쳤습니다</small>}
      {/* Files on this PC are read with the user's own rights: AI tools may only update from them with
          the "full" access level, as with the import box (src/components/app-control.ts). */}
      {
        <div
          className="source-update"
          {...(source.kind === 'confluence' || source.kind === 'jira'
            ? {}
            : { 'data-requires-full': '' })}
        >
          {writing ? (
            <span className="source-busy" role="status">
              <LoaderCircle size={14} className="spin" />
              {writing.status === 'cancelling'
                ? '원본 반영을 멈추는 중…'
                : writing.waiting || writing.notice || '원본에 반영하는 중…'}
              <button
                type="button"
                className="text-button"
                disabled={writing.status === 'cancelling'}
                onClick={stop}
              >
                중지
              </button>
            </span>
          ) : updating ? (
            <span className="source-busy" role="status">
              <LoaderCircle size={14} className="spin" />
              원본에서 가져오는 중…
            </span>
          ) : (
            <>
              {source.edited ? (
                <Confirm
                  label="원본에서 업데이트"
                  icon={<RefreshCw size={14} />}
                  question="가져온 뒤 고친 본문이 원본의 내용으로 바뀝니다. 업데이트할까요?"
                  disabled={missing}
                  onConfirm={() => update(true)}
                />
              ) : (
                <button
                  type="button"
                  className="button secondary small"
                  disabled={missing}
                  title="같은 파일·링크를 다시 가져와 이 문서를 원본의 지금 내용으로 바꿉니다"
                  onClick={() => void update(false)}
                >
                  <RefreshCw size={14} />
                  원본에서 업데이트
                </button>
              )}
              {/* The changes made here, written into the file they came from; only once the text was changed. */}
              {source.edited && writable && (
                <Confirm
                  label="원본에 반영"
                  icon={<Upload size={14} />}
                  question={WRITE_QUESTION.office}
                  disabled={missing}
                  onConfirm={write}
                />
              )}
            </>
          )}
        </div>
      }
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {/* The copies of the file kept before each write-back, and 원본 백업에서 복원. */}
      {writable && (
        <WritebackBackupsList
          doc={doc}
          disabled={jobs.running}
          onRestored={() => setRestored((count) => count + 1)}
        />
      )}
      {source.parts > 1 && <PartLinks doc={doc} />}
    </div>
  );
}

// A document that is one part of an import too long for one document leads to the parts beside it.
function PartLinks({ doc }: { doc: WikiDocument }) {
  const { openDocument } = useActions();
  const parts = doc.parts ?? [];
  const index = parts.findIndex((part) => part.id === doc.id);
  const previous = index > 0 ? parts[index - 1] : undefined;
  const next = index >= 0 ? parts[index + 1] : undefined;
  return (
    <nav className="document-parts" aria-label="나누어 저장한 문서">
      <span>
        긴 글을 나누어 저장한 {doc.source!.parts}개 중 {doc.source!.part}번째
      </span>
      <button
        type="button"
        className="text-button"
        aria-label="이전 부분"
        disabled={!previous}
        onClick={() => previous && openDocument(previous.id)}
      >
        <ChevronLeft size={14} />
        이전
      </button>
      <button
        type="button"
        className="text-button"
        aria-label="다음 부분"
        disabled={!next}
        onClick={() => next && openDocument(next.id)}
      >
        다음
        <ChevronRight size={14} />
      </button>
    </nav>
  );
}

// What a downloaded page carries first. Opened from the disk in a browser, it is outside the app's sanitizer and CSP
// (src/lib/html-document.ts, electron/main.cjs): its own policy keeps any script of the imported file from running and
// anything from loading from elsewhere, while its embedded pictures and its own styles still show. The charset comes
// first as well, as the file is written in UTF-8 whatever the page says.
const DOWNLOAD_POLICY =
  "<meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">";
// The page with the policy before everything it holds but its doctype, which stays first so the page keeps the
// rendering mode it was made for.
function downloadPage(html: string) {
  const doctype = /^\uFEFF?\s*<!doctype[^>]*>/i.exec(html)?.[0].length ?? 0;
  return html.slice(0, doctype) + DOWNLOAD_POLICY + html.slice(doctype);
}

// The HTML page an imported file became, as text, with its download.
function HtmlSource({
  page,
  onBack,
}: {
  page: { html: string; filename: string };
  onBack: () => void;
}) {
  const text = useMemo(() => readableSource(page.html), [page.html]);
  const [link, setLink] = useState('');
  useEffect(() => {
    const url = URL.createObjectURL(
      new Blob([downloadPage(page.html)], { type: 'text/html;charset=utf-8' }),
    );
    setLink(url);
    return () => URL.revokeObjectURL(url);
  }, [page.html]);
  return (
    <div className="drawer-body">
      <p className="drawer-note">
        파일을 가져올 때(원본에서 업데이트했다면 그때) 만든 HTML 원문입니다. 읽기 쉽게 요소마다 줄을
        나누고, 문서 안에 base64로 들어 있는 그림 데이터는 앞부분만 보여 줍니다. .html 다운로드에는
        원문이 그대로 들어 있고, 브라우저로 열어도 스크립트가 실행되거나 외부 자료를 불러오지 않도록
        맨 앞에 보안 정책을 붙입니다. 본문을 수정해도 원문은 남습니다.
      </p>
      <textarea
        aria-label="HTML 원문"
        className="document-editor"
        rows={22}
        readOnly
        value={text}
      />
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={onBack}>
          문서로 돌아가기
        </button>
        <a className="button primary" href={link || undefined} download={page.filename}>
          .html 다운로드
        </a>
      </div>
    </div>
  );
}

const kindLabel = (run: Pick<Run, 'kind' | 'schedule_id' | 'schedule_name'>) =>
  `${run.kind === 'scheduled' ? '예약 실행' : '직접 실행'}${
    run.schedule_id ? ` · ${run.schedule_name || '삭제된 예약'}` : ''
  }`;

function RunReader({ id, terms, onClose }: { id: string; terms?: string[]; onClose: () => void }) {
  const { notify, refresh, bumpRuns } = useActions();
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    api<Run>('runs.get', { id })
      .then((value) => live && setRun(value))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [id]);
  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    setError('');
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  }
  const actions = run ? (
    <>
      {run.output && (
        <>
          <button
            className="button secondary small"
            disabled={!!busy}
            onClick={() =>
              void act('copy', async () => {
                await api('clipboard.write', { text: run.output });
                notify('응답을 복사했습니다.');
              })
            }
          >
            <Copy size={14} />
            응답 복사
          </button>
          <button
            className="button secondary small"
            disabled={!!busy}
            onClick={() =>
              void act('wiki', async () => {
                await api('wiki.save', {
                  title: `${run.title} · ${date(run.created_at)}`,
                  content: run.output,
                });
                await refresh();
                notify('응답을 Wiki 문서로 저장했습니다.');
              })
            }
          >
            <BookOpen size={14} />
            Wiki에 저장
          </button>
        </>
      )}
      <Confirm
        label="삭제"
        icon={<Trash2 size={14} />}
        question="이 실행 기록을 삭제할까요?"
        disabled={!!busy}
        onConfirm={() =>
          act('delete', async () => {
            await api('runs.delete', { id: run.id });
            bumpRuns();
            notify('실행 기록을 삭제했습니다.');
            onClose();
          })
        }
      />
    </>
  ) : null;
  return (
    <Modal
      title={run?.title ?? '실행 기록'}
      subtitle={run ? `${dayTime(run.created_at)} · ${kindLabel(run)}` : undefined}
      actions={actions}
      onClose={onClose}
      locked={!!busy}
    >
      <div className="drawer-body run-detail">
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {!run ? (
          <div className="loading-page small">
            <LoaderCircle className="spin" size={24} />
          </div>
        ) : (
          <>
            <div className="run-meta">
              <span className={`run-badge ${run.status}`}>
                {run.status === 'success' ? <Check size={12} /> : <AlertCircle size={12} />}
                {run.status === 'success' ? '성공' : '실패'}
              </span>
              <span>소요 시간 {seconds(run.duration_ms)}</span>
              {run.started_at && <span>시작 {clock(run.started_at)}</span>}
            </div>
            {run.status === 'error' && (
              <p className="inline-error" role="alert">
                {run.error}
              </p>
            )}
            {run.input && (
              <details className="run-input">
                <summary>보낸 프롬프트 보기</summary>
                <pre>{run.input}</pre>
              </details>
            )}
            {run.output && (
              <div className="run-output">
                <Markdown highlight={terms}>{run.output}</Markdown>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
