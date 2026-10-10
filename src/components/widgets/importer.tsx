import { useEffect, useState } from 'react';
import {
  Check,
  CircleAlert,
  File,
  FolderOpen,
  Globe,
  KeyRound,
  LoaderCircle,
  Square,
  Upload,
  X,
} from 'lucide-react';
import {
  api,
  type ConfluenceJob,
  type JiraJob,
  type JiraRole,
  type JiraScope,
  type LocalImportJob,
  type SiteMatch,
  type TextImport,
} from '@/lib/api';
import { importTarget, type ImportTarget } from '@/lib/links';
import { useApp } from '../app';
import { loginSummary } from '../admin/sites';
import { WidgetHead } from '../dashboard';
import Diagnosis from '../diagnosis';
import { activeJob, type ImportKind } from '../import-jobs';
import { rove } from '../roving';

const KIND_LABEL: Record<ImportTarget['kind'], string> = {
  jira: 'JIRA 티켓',
  confluence: 'Confluence 페이지',
  markdown: 'Markdown 파일',
  text: '텍스트 파일',
  office: 'Office 문서',
  rtf: 'RTF 문서(.rtf)',
  image: '이미지 파일',
  folder: '폴더',
  'other-link': '지원하지 않는 링크',
  'other-file': '지원하지 않는 파일',
};
const ROLES: Record<JiraRole, string> = {
  any: '담당자 또는 보고자',
  assignee: '담당자',
  reporter: '보고자',
};

export default function ImportWidget() {
  const { configured, notify, refresh, imports, openAdmin } = useApp();
  const [value, setValue] = useState('');
  const [folderPicked, setFolderPicked] = useState(false);
  const [analyze, setAnalyze] = useState(false);
  // Confluence subpages start off; subfolders start on, as a folder import usually means all of it.
  const [recursive, setRecursive] = useState(false);
  const [subfolders, setSubfolders] = useState(true);
  const [folderKind, setFolderKind] = useState<'office' | 'rtf' | 'image' | 'text'>('office');
  const [scope, setScope] = useState<JiraScope>('issue');
  const [keyword, setKeyword] = useState('');
  const [user, setUser] = useState('');
  const [role, setRole] = useState<JiraRole>('any');
  const [match, setMatch] = useState<SiteMatch | null>(null);
  const [capability, setCapability] = useState<Capability | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const target = importTarget(value, folderPicked);
  const web = target?.kind === 'jira' || target?.kind === 'confluence';
  // Files are read by the Python backend, each kind by its own importer.
  const local =
    target?.kind === 'office' ||
    target?.kind === 'rtf' ||
    target?.kind === 'image' ||
    target?.kind === 'text' ||
    target?.kind === 'folder'
      ? target.kind === 'folder'
        ? folderKind
        : target.kind
      : null;
  // Office may be installed without the one app this file needs, such as Visio.
  const missing =
    target?.kind === 'office' &&
    capability?.available &&
    capability.applications?.[target.application] === false
      ? target.application
      : null;
  // A finished import may have saved the login method it detected; the account line reloads then.
  const settled = Object.values(imports.jobs)
    .filter((job) => job && !activeJob(job.status))
    .map((job) => job!.id)
    .join();
  // Which saved account the link will sign in with.
  useEffect(() => {
    if (!web) return setMatch(null);
    let live = true;
    const timer = setTimeout(() => {
      api<SiteMatch>('sites.match', { url: value.trim() })
        .then((result) => live && setMatch(result))
        .catch(() => live && setMatch({ kind: null, site: null }));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [value, web, settled]);
  useEffect(() => {
    setCapability(null);
    if (!local) return;
    let live = true;
    api<Capability>(`${local}.capabilities`)
      .then((result) => live && setCapability(result))
      .catch((e) => live && setCapability({ available: false, reason: e.message }));
    return () => {
      live = false;
    };
  }, [local]);
  async function choose(mode: 'file' | 'directory') {
    try {
      const path = await api<string | null>('import.choose', { mode });
      if (!path) return;
      setValue(path);
      setFolderPicked(mode === 'directory');
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function start() {
    if (!target) return;
    setBusy(true);
    setError('');
    try {
      if (target.kind === 'jira')
        await api('jira.start', { url: target.url, scope, keyword, user, role, analyze });
      else if (target.kind === 'confluence')
        await api('confluence.start', { url: target.url, recursive, analyze });
      else if (target.kind === 'markdown') {
        const result = await api<TextImport>('files.import_text', { path: target.path, analyze });
        await refresh();
        notify(
          [`‘${result.title}’: ${result.message}`, result.analysis_error].filter(Boolean).join(' '),
          !!result.analysis_error,
        );
        setValue('');
        return;
      } else if (local && 'path' in target)
        await api(`${local}.start`, {
          path: target.path,
          mode: target.kind === 'folder' ? 'directory' : 'file',
          recursive: target.kind === 'folder' && subfolders,
          analyze,
        });
      else throw new Error(unsupported(target));
      imports.watch();
    } catch (e) {
      setError((e as Error).message);
      // Refused as busy: the import box shows the job it waits for, such as an automatic routine's.
      imports.watch();
    } finally {
      setBusy(false);
    }
  }
  // A cleared job stays cleared on every screen: the importer that ran it remembers.
  const jobs = (Object.entries(imports.jobs) as [ImportKind, ImportJob | null][]).filter(
    ([, job]) => job && !job.dismissed,
  ) as [ImportKind, ImportJob][];
  return (
    <>
      <WidgetHead id="import" title="가져오기" />
      {/* Files on this PC are read with the user's own rights: AI tools may only work this box
          with the "full" access level (src/components/app-control.ts). */}
      <form
        className="import-form"
        data-requires-full=""
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <div className="import-input">
          <input
            aria-label="가져올 링크 또는 경로"
            value={value}
            maxLength={4096}
            placeholder="JIRA·Confluence 링크나 파일·폴더 경로"
            onChange={(e) => {
              setValue(e.target.value);
              setFolderPicked(false);
              setError('');
            }}
          />
          <button
            type="button"
            className="icon-button"
            aria-label="파일 선택"
            title="파일 선택 (.md .txt · Word·Excel·PowerPoint·Visio · .rtf · 이미지)"
            onClick={() => void choose('file')}
          >
            <File size={16} />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="폴더 선택"
            title="폴더 선택"
            onClick={() => void choose('directory')}
          >
            <FolderOpen size={16} />
          </button>
        </div>
        {target ? (
          <div className="import-options">
            <p className={`import-kind${target.kind.startsWith('other') ? ' danger' : ''}`}>
              {web ? <Globe size={14} /> : <File size={14} />}
              {KIND_LABEL[target.kind]}
              {target.kind === 'jira' && ` · ${target.key}`}
              {target.kind === 'office' && ` · ${target.application}`}
              {target.kind.startsWith('other') && ` · ${unsupported(target)}`}
            </p>
            {web && match && (
              <SiteLine match={match} onManage={() => openAdmin('sites', { url: value.trim() })} />
            )}
            {target.kind === 'jira' && (
              <>
                <div className="segmented" role="radiogroup" aria-label="가져올 범위">
                  {(
                    [
                      ['issue', '이 티켓만'],
                      ['keyword', '키워드 검색'],
                      ['user', '사용자 검색'],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      role="radio"
                      aria-checked={scope === id}
                      tabIndex={scope === id ? 0 : -1}
                      onKeyDown={rove}
                      className={scope === id ? 'active' : ''}
                      onClick={() => {
                        setScope(id);
                        // Searching one's own tickets is the common case.
                        if (id === 'user' && !user.trim()) setUser(match?.site?.login_id || '');
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {scope === 'keyword' && (
                  <label>
                    검색 키워드
                    <input
                      required
                      maxLength={200}
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      placeholder={`${target.key.split('-')[0]} 프로젝트에서 찾을 단어`}
                    />
                  </label>
                )}
                {scope === 'user' && (
                  <div className="form-grid">
                    <label>
                      사용자
                      <input
                        required
                        maxLength={256}
                        value={user}
                        onChange={(e) => setUser(e.target.value)}
                        placeholder="JIRA 사용자 ID 또는 이름"
                      />
                    </label>
                    <label>
                      검색 대상
                      <select value={role} onChange={(e) => setRole(e.target.value as JiraRole)}>
                        {Object.entries(ROLES).map(([id, label]) => (
                          <option key={id} value={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
              </>
            )}

            {target.kind === 'confluence' && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={recursive}
                  onChange={(e) => setRecursive(e.target.checked)}
                />
                하위 페이지까지 모두 가져오기
              </label>
            )}
            {target.kind === 'folder' && (
              <>
                <div className="segmented" role="radiogroup" aria-label="폴더에서 가져올 파일">
                  {(
                    [
                      ['office', 'Office 문서'],
                      ['rtf', 'RTF 문서(.rtf)'],
                      ['image', '이미지'],
                      ['text', '텍스트(.txt)'],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      role="radio"
                      aria-checked={folderKind === id}
                      tabIndex={folderKind === id ? 0 : -1}
                      onKeyDown={rove}
                      className={folderKind === id ? 'active' : ''}
                      onClick={() => setFolderKind(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={subfolders}
                    onChange={(e) => setSubfolders(e.target.checked)}
                  />
                  하위 폴더 포함
                </label>
              </>
            )}
            {local === 'text' && (
              <p className="widget-note">
                창 없이 띄운 메모장으로 파일을 열어 메모장에 보이는 글을 그대로 가져옵니다.
              </p>
            )}
            {local && capability && !capability.available && (
              <p className="inline-error">{capability.reason}</p>
            )}
            {missing && (
              <p className="inline-error">
                {missing} 데스크톱 앱이 설치된 Windows에서 이 파일을 가져올 수 있습니다.
              </p>
            )}
            {!target.kind.startsWith('other') && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={analyze}
                  disabled={!configured}
                  onChange={(e) => setAnalyze(e.target.checked)}
                />
                가져온 뒤 LLM으로 요약·키워드 분석{configured ? '' : ' (LLM 연결 필요)'}
              </label>
            )}
          </div>
        ) : (
          <p className="widget-note">
            JIRA 티켓·Confluence 페이지 링크, 또는 .md·.txt·Word·Excel·PowerPoint·Visio·.rtf·이미지
            파일이나 폴더를 넣으면 Wiki에 저장합니다. Office·RTF·이미지 파일은 서식과 그림을 담은
            HTML 문서가 되고, .txt 파일은 메모장으로 읽어 HTML 문서로 저장합니다. 이미 가져온
            파일·링크를 다시 넣으면 새 문서를 만들지 않고 그 문서를 원본의 바뀐 내용으로
            업데이트합니다.
          </p>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {target && !target.kind.startsWith('other') && (
          <button
            className="button primary"
            disabled={busy || (!!local && !capability?.available) || !!missing}
          >
            {busy ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
            Wiki로 가져오기
          </button>
        )}
      </form>
      {jobs.length > 0 && (
        <ul className="job-list" aria-label="가져오기 진행 상황">
          {jobs.map(([kind, job]) => (
            <JobView
              key={job.id}
              kind={kind}
              job={job}
              onDismiss={async () => {
                try {
                  await api(`${kind}.dismiss`, { id: job.id });
                  imports.watch();
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
              onCancel={async () => {
                try {
                  await api(`${kind}.cancel`);
                  imports.watch();
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            />
          ))}
        </ul>
      )}
    </>
  );
}

function unsupported(target: ImportTarget) {
  return target.kind === 'other-link'
    ? 'JIRA 티켓(/browse/PROJ-123), Confluence 페이지(/display/…, /pages/…, /spaces/…, /x/…) 링크를 넣어 주세요.'
    : '.md .markdown .txt, Word·Excel·PowerPoint·Visio 문서(.docx .xlsx .pptx .vsdx 등), .rtf, 이미지(.png .jpg .gif .bmp .webp .svg .tif .emf 등) 파일이나 폴더를 넣어 주세요.';
}

// Office imports report which Office apps this PC has; RTF imports only whether Word is there, text
// imports whether Notepad is. Images need neither.
type Capability = { available: boolean; reason: string; applications?: Record<string, boolean> };

function SiteLine({ match, onManage }: { match: SiteMatch; onManage: () => void }) {
  const site = match.site;
  return (
    <p className="site-line">
      <KeyRound size={14} />
      {site ? (
        <span>
          <strong>{site.name}</strong>
          {site.login_id && ` · ${site.login_id}`} · {loginSummary(site)}
          {site.method !== 'session' &&
            !site.has_password &&
            ' · 비밀번호가 없어 자동 로그인하지 않습니다'}
        </span>
      ) : (
        <span>등록된 계정이 없습니다. 브라우저에 남은 로그인으로 시도합니다.</span>
      )}
      <button type="button" className="text-button" onClick={onManage}>
        {site ? '계정 관리' : '계정 등록'}
      </button>
    </p>
  );
}

type ImportJob = LocalImportJob | ConfluenceJob | JiraJob;
const TITLES: Record<ImportKind, string> = {
  office: 'Office 문서',
  rtf: 'RTF 문서',
  image: '이미지',
  text: '텍스트',
  confluence: 'Confluence',
  jira: 'JIRA',
};

function statusText(kind: ImportKind, job: ImportJob) {
  const failed = job.failed || ('discovery_failed' in job && job.discovery_failed);
  switch (job.status) {
    case 'scanning':
      return kind === 'jira' || kind === 'confluence' ? '준비하는 중' : '파일을 찾는 중';
    case 'login':
      return '로그인하는 중';
    case 'running':
      return kind === 'jira' || kind === 'confluence'
        ? 'Markdown으로 바꿔 저장하는 중'
        : 'HTML 문서로 바꿔 저장하는 중';
    case 'cancelling':
      return '중지하는 중';
    case 'completed':
      return failed ? '끝났습니다 · 실패한 항목이 있습니다' : '가져오기가 끝났습니다';
    case 'cancelled':
      return '중지했습니다';
    default:
      return '가져오지 못했습니다';
  }
}

function JobView({
  kind,
  job,
  onCancel,
  onDismiss,
}: {
  kind: ImportKind;
  job: ImportJob;
  onCancel: () => void;
  onDismiss: () => void;
}) {
  const running = activeJob(job.status);
  const web = 'url' in job ? job : null;
  const scope =
    'scope' in job
      ? job.scope === 'keyword'
        ? `키워드 “${job.keyword}”`
        : job.scope === 'user'
          ? `${ROLES[job.role]} ${job.user}`
          : job.scope === 'list'
            ? `티켓 ${job.keys?.length ?? job.total}개`
            : job.scope === 'report'
              ? `${job.project} 진행 현황`
              : job.scope === 'jql'
                ? 'JQL 검색'
                : '이 티켓만'
      : 'recursive' in job
        ? job.recursive
          ? '하위 페이지 포함'
          : '이 페이지만'
        : '';
  const login = web && 'auto_login' in web ? web : null;
  // A folder's job lists the results of up to 5,000 files, failures first; its counts are of every file.
  const unlisted = 'batches' in job ? job.processed - job.results.length : 0;
  return (
    <li className={`job ${job.status}`}>
      {/* Only the state is live, so a screen reader hears it change, not every item counted. */}
      <div className="job-head">
        {running ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
        <strong>
          {TITLES[kind]}
          {job.routine ? ` · 자동 작업 ‘${job.routine}’` : ''}
        </strong>
        <span role="status">{statusText(kind, job)}</span>
        <span className="job-progress">
          {job.processed} / {job.total}
        </span>
        {running ? (
          <button
            className="icon-button"
            aria-label={`${TITLES[kind]} 가져오기 중지`}
            disabled={job.status === 'cancelling'}
            onClick={onCancel}
          >
            <Square size={14} />
          </button>
        ) : (
          <button
            className="icon-button"
            aria-label={`${TITLES[kind]} 결과 지우기`}
            onClick={onDismiss}
          >
            <X size={14} />
          </button>
        )}
      </div>
      <progress max={Math.max(job.total, 1)} value={job.processed} />
      <p className="job-counts">
        {scope && <span>범위 {scope}</span>}
        {'batches' in job && job.batches > 1 && (
          <span>
            묶음 {job.batch}/{job.batches}
          </span>
        )}

        <span>저장 {job.imported}</span>
        {job.updated > 0 && <span>업데이트 {job.updated}</span>}
        <span>중복 {job.skipped}</span>
        <span>실패 {job.failed}</span>
        {'discovery_failed' in job && job.discovery_failed > 0 && (
          <span>하위 목록 실패 {job.discovery_failed}</span>
        )}
        {job.analysis_failed > 0 && <span>AI 분석 실패 {job.analysis_failed}</span>}
      </p>
      {login && (
        <p className="job-login">
          로그인{' '}
          {login.auto_login
            ? `${login.login?.site || '저장한 계정'}${
                login.login?.label
                  ? ` · ${login.login.label}${login.login.detected ? ' (자동 감지)' : ''}`
                  : ''
              }`
            : '브라우저에 남은 로그인'}
          {/* Methods that did not fit or did not sign in before the one that did. */}
          {login.login?.method && login.login.tried?.length
            ? ` · 먼저 시도한 방식: ${login.login.tried.join(', ')}`
            : ''}
        </p>
      )}
      {'jql' in job && job.jql && (
        <p className="job-detail break-path">검색 조건(JQL): {job.jql}</p>
      )}
      {'current_url' in job && job.current_url && (
        <p className="job-detail break-path">{job.current_url}</p>
      )}
      {'current_file' in job && job.current_file && (
        <p className="job-detail break-path">{job.current_file}</p>
      )}
      {running && 'waiting' in job && job.waiting && <p className="job-detail">{job.waiting}</p>}
      {running && 'notice' in job && job.notice && (
        <p className="job-notice">
          <CircleAlert size={14} />
          {job.notice}
        </p>
      )}
      {job.error && (
        <p className="inline-error" role="alert">
          {job.error}
        </p>
      )}
      {web && 'diagnosis' in web && web.diagnosis && <Diagnosis diagnosis={web.diagnosis} />}

      {job.status === 'completed' && job.total === 0 && (
        <p className="job-detail">조건에 맞는 항목이 없습니다.</p>
      )}
      {job.results.length > 0 && (
        <details className="job-results">
          <summary>
            {unlisted > 0
              ? `결과 ${job.processed.toLocaleString()}건 중 실패와 최근 ${job.results.length.toLocaleString()}건`
              : `결과 ${job.results.length}건`}
          </summary>
          <ul>
            {job.results.map((item, index) => (
              <li
                key={index}
                className={
                  item.status === 'failed' || ('discovery_error' in item && item.discovery_error)
                    ? 'failed'
                    : ''
                }
              >
                <strong className="break-path">{'path' in item ? item.path : item.url}</strong>
                <span>{item.message}</span>
                {'discovery_error' in item && item.discovery_error && (
                  <span className="danger">{item.discovery_error}</span>
                )}
                {item.analysis_error && <span className="danger">{item.analysis_error}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}
