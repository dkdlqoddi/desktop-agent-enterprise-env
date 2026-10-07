const fs = require('node:fs/promises');
const path = require('node:path');
const {
  ApiError,
  IMPORT_KINDS,
  SOURCE_KINDS,
  allows,
  analysisState,
  forbidden,
} = require('./api-catalog.cjs');

const ACTIVE = ['scanning', 'login', 'running', 'cancelling'];
const ANALYSIS = ['analyzed', 'not_analyzed', 'failed', 'running'];
const TASK_STATUS = ['open', 'done', 'paused', 'all'];
const FOLDER_KINDS = ['office', 'rtf', 'image', 'text'];
const JIRA_SCOPES = ['issue', 'keyword', 'user'];

function integer(value, name, fallback, min, max) {
  if (value === undefined || value === null) return fallback;
  if (!Number.isInteger(value) || value < min || value > max)
    throw new Error(`${name}: ${min}~${max.toLocaleString('en-US')} 사이의 정수로 입력해 주세요.`);
  return value;
}

function choice(value, name, allowed, fallback) {
  if (value === undefined || value === null || value === '') return fallback;
  if (!allowed.includes(value))
    throw new Error(`${name}: ${allowed.join(', ')} 중 하나로 입력해 주세요.`);
  return value;
}

function optionalText(value, name, limit) {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string' || value.length > limit)
    throw new Error(`${name}: ${limit.toLocaleString('en-US')}자 이내의 글로 입력해 주세요.`);
  return value.trim();
}

const fold = (value) => value.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
const LINK = /^[a-z][a-z0-9+.-]*:\/\//i;
const unquote = (value) => value.replace(/^"(.*)"$/, '$1');

// The computer a path is on: '' for this PC's drives, the server's name (lowercased) for a network path
// (\\server\share\…, //server/share/…, \\?\UNC\server\…), and null for a device path (\\.\…, \\?\Volume{…}\…).
function serverOf(file) {
  const value = String(file ?? '')
    .trim()
    .replace(/\//g, '\\');
  if (/^\\\\[.?]\\/.test(value)) {
    const rest = value.slice(4);
    if (/^[a-z]:(\\|$)/i.test(rest)) return '';
    const unc = /^unc\\([^\\]+)/i.exec(rest);
    return unc ? unc[1].toLowerCase() : null;
  }
  const unc = /^\\\\([^\\]+)/.exec(value);
  return unc ? unc[1].toLowerCase() : '';
}

// The paths of this PC a method of an AI tool would read: those of an import, and a routine's to bring up to date.
function pathsOf(method, params) {
  const text = (value) => (typeof value === 'string' ? [unquote(value.trim())] : []);
  if (method === 'import.start')
    return typeof params.source === 'string' && !LINK.test(params.source.trim())
      ? text(params.source)
      : [];
  if (method === 'files.import_text' || /^(office|rtf|image|text)\.start$/.test(method))
    return text(params.path);
  if (method === 'routines.save') {
    const paths = params.config?.paths;
    const lines = Array.isArray(paths)
      ? paths
      : typeof paths === 'string'
        ? paths.replace(/\r\n/g, '\n').split('\n')
        : [];
    return lines.flatMap(routinePath);
  }
  return [];
}

// A routine's path as the backend reads one (routines.py lines_of): the spaces and tabs around it go, then one pair of
// quotes around all of it. The backend refuses an entry that still holds a quote, a control character or a line
// separator, so what it saves is exactly what was checked here.
function routinePath(value) {
  if (typeof value !== 'string') return [];
  let item = value.replace(/^[ \t]+|[ \t]+$/g, '');
  if (item.length >= 2 && item.startsWith('"') && item.endsWith('"'))
    item = item.slice(1, -1).replace(/^[ \t]+|[ \t]+$/g, '');
  return item ? [item] : [];
}

// Reading a path on another computer makes Windows sign in to it with the user's own account, which hands that
// computer what it needs to pose as the user. A path an AI tool gives (say, one a document asked for) may reach only
// the computers the user already works with: the one the Wiki folder is on, or one a document was imported from.
// Checked at the "full" level only: a lower one is refused before any path is looked at (allowFiles), learning nothing
// of the servers either.
function createPathCheck({ handle, wikiDirectory }) {
  return async function checkPaths(method, params, access) {
    if (!allows(access, 'files') || !params || typeof params !== 'object') return;
    const servers = pathsOf(method, params)
      .map(serverOf)
      .filter((server) => server !== '');
    if (!servers.length) return;
    if (servers.includes(null))
      throw new Error(
        '장치 경로(\\\\.\\…)는 가져올 수 없습니다. 파일이나 폴더의 전체 경로를 입력해 주세요.',
      );
    const known = new Set([serverOf(wikiDirectory() ?? '')]);
    for (const doc of await handle('wiki.list', {})) known.add(serverOf(doc.source?.source_path));
    const unknown = servers.find((server) => !known.has(server));
    if (unknown)
      throw new ApiError(
        403,
        'forbidden',
        `다른 컴퓨터의 공유 폴더(\\\\${unknown}\\…)는 Wiki 폴더나 이미 가져온 문서가 있는 컴퓨터일 때만 AI 도구가 가져올 수 있습니다. 경로를 읽으면 Windows가 사용자 계정으로 그 컴퓨터에 로그인하기 때문입니다. 이 경로는 Private Agent의 가져오기 상자에서 직접 가져와 주세요.`,
      );
  };
}

// Importing this PC's files: only the access level that allows importing them ("full") may start that through the one
// import entry point, as with the methods of each kind. Checked before a path is even looked at, so a lower level
// learns nothing about the PC's files.
function allowFiles(access) {
  if (!allows(access, 'files')) throw forbidden(access, 'files');
}

// Methods only the local API offers, made of the screens' own requests (`handle`): one entry point for every kind of
// import, as the import box routes a link or path, document lists an AI tool can filter and page, and documents
// without their imported HTML page unless asked, which can be megabytes.
function createApiMethods({ handle, linkKind, extensions }) {
  const kindOfFile = (file) => {
    const extension = path.extname(file).toLowerCase().replace(/^\./, '');
    return Object.keys(extensions).find((kind) => extensions[kind].includes(extension)) ?? null;
  };
  const jobOf = (status) =>
    status && typeof status === 'object' && 'job' in status ? status.job : status;

  async function startImport(params, { access } = {}) {
    const source = optionalText(params.source, '가져올 링크나 경로', 32767);
    if (!source) throw new Error('가져올 링크나 파일·폴더의 전체 경로를 입력해 주세요.');
    const analyze = params.analyze ?? false;
    if (typeof analyze !== 'boolean') throw new Error('AI 분석 옵션(analyze)을 확인해 주세요.');
    if (params.recursive !== undefined && typeof params.recursive !== 'boolean')
      throw new Error('하위 항목 포함 옵션(recursive)을 확인해 주세요.');
    // A path or link imported before updates its document: which one (doc_id), and whether text the user changed is
    // replaced. Each importer checks them as the screens' requests.
    const update = {};
    const docId = optionalText(params.doc_id, '업데이트할 문서(doc_id)', 80);
    if (docId) update.doc_id = docId;
    if (params.replace !== undefined && typeof params.replace !== 'boolean')
      throw new Error('고친 본문을 원본으로 바꿀지(replace) 확인해 주세요.');
    if (params.replace) update.replace = true;
    // A JIRA or Confluence link typed without its scheme is still a link, as in the
    // import box; anything else without a scheme is a path, as is one that starts with a slash (//server/share/…).
    const linked = /^[\\/]/.test(source) ? null : linkKind(source);
    if (LINK.test(source) || linked) {
      const kind = linked;
      let result;
      if (kind === 'jira')
        result = await handle('jira.start', {
          url: source,
          scope: choice(params.jira_scope, 'JIRA 범위(jira_scope)', JIRA_SCOPES, 'issue'),
          keyword: optionalText(params.keyword, '검색 키워드', 200),
          user: optionalText(params.user, '사용자', 256),
          role: params.role ?? 'any',
          analyze,
          ...update,
        });
      else if (kind === 'confluence')
        result = await handle('confluence.start', {
          url: source,
          recursive: params.recursive ?? false,
          analyze,
          ...update,
        });
      else
        throw new Error(
          '가져올 수 없는 링크입니다. JIRA 티켓, Confluence 페이지 링크를 입력해 주세요.',
        );
      return { kind, job: jobOf(result) };
    }
    allowFiles(access);
    const file = unquote(source);
    if (!path.isAbsolute(file)) throw new Error('파일 또는 폴더의 전체 경로를 입력해 주세요.');
    let stat;
    try {
      stat = await fs.stat(file);
    } catch {
      throw new Error('파일이나 폴더를 찾을 수 없습니다. 경로를 확인해 주세요.');
    }
    if (stat.isDirectory()) {
      const kind = choice(
        params.folder_kind,
        '폴더의 파일 종류(folder_kind)',
        FOLDER_KINDS,
        'office',
      );
      const job = await handle(`${kind}.start`, {
        path: file,
        mode: 'directory',
        recursive: params.recursive ?? true,
        analyze,
        ...update,
      });
      return { kind, job: jobOf(job) };
    }
    const kind = kindOfFile(file);
    if (kind === 'markdown')
      return {
        kind,
        document: await handle('files.import_text', { path: file, analyze, ...update }),
      };
    if (!kind)
      throw new Error(
        '가져올 수 없는 파일입니다. .md·.txt, Word·Excel·PowerPoint·Visio, .rtf, 이미지 파일을 가져올 수 있습니다.',
      );
    const job = await handle(`${kind}.start`, {
      path: file,
      mode: 'file',
      recursive: false,
      analyze,
      ...update,
    });
    return { kind, job: jobOf(job) };
  }

  return {
    'documents.query': async (params) => {
      const limit = integer(params.limit, 'limit', 50, 1, 500);
      const offset = integer(params.offset, 'offset', 0, 0, 1_000_000);
      const query = fold(optionalText(params.query, '검색어', 200));
      const keyword = fold(optionalText(params.keyword, '키워드', 80));
      const source = choice(params.source, '출처(source)', SOURCE_KINDS, '');
      const analysis = choice(params.analysis, 'AI 분석 여부(analysis)', ANALYSIS, '');
      const found = (await handle('wiki.list', {})).filter(
        (doc) =>
          (!query ||
            fold(
              [doc.title, doc.summary, doc.excerpt, (doc.keywords || []).join(' ')].join(' '),
            ).includes(query)) &&
          (!keyword || (doc.keywords || []).includes(keyword)) &&
          (!source || (source === 'manual' ? !doc.source : doc.source?.kind === source)) &&
          (!analysis || analysisState(doc) === analysis),
      );
      return {
        total: found.length,
        offset,
        limit,
        documents: found
          .slice(offset, offset + limit)
          .map((doc) => ({ ...doc, analysis: analysisState(doc) })),
      };
    },
    'documents.get': async (params) => {
      const doc = await handle('wiki.get', { id: params.id });
      if (params.include_html !== true) delete doc.html;
      return { ...doc, analysis: analysisState(doc) };
    },
    'tasks.query': async (params) => {
      const status = choice(params.status, '상태(status)', TASK_STATUS, 'all');
      const tasks = await handle('tasks.list', {});
      return status === 'all' ? tasks : tasks.filter((task) => task.status === status);
    },
    'import.start': startImport,
    'import.status': async () => {
      const [statuses, analysis] = await Promise.all([
        Promise.all(IMPORT_KINDS.map((kind) => handle(`${kind}.status`, {}))),
        handle('analysis.status', {}),
      ]);
      const jobs = Object.fromEntries(IMPORT_KINDS.map((kind, i) => [kind, jobOf(statuses[i])]));
      return {
        jobs,
        analysis,
        active: IMPORT_KINDS.filter((kind) => ACTIVE.includes(jobs[kind]?.status)),
      };
    },
    'import.capabilities': async () => {
      const found = await Promise.all(
        FOLDER_KINDS.map((kind) =>
          handle(`${kind}.capabilities`, {}).catch((error) => ({
            available: false,
            reason: error.message,
          })),
        ),
      );
      return Object.fromEntries(FOLDER_KINDS.map((kind, i) => [kind, found[i]]));
    },
    'import.cancel': async (params) => {
      const kind = choice(params.kind, '가져오기 종류(kind)', IMPORT_KINDS, '');
      if (!kind) throw new Error('가져오기 종류(kind)를 입력해 주세요.');
      return jobOf(await handle(`${kind}.cancel`, {}));
    },
    'import.dismiss': async (params) => {
      const kind = choice(params.kind, '가져오기 종류(kind)', IMPORT_KINDS, '');
      if (!kind) throw new Error('가져오기 종류(kind)를 입력해 주세요.');
      return jobOf(await handle(`${kind}.dismiss`, { id: params.id }));
    },
  };
}

module.exports = { createApiMethods, createPathCheck, serverOf };
