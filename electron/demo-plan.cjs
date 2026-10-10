const fs = require('node:fs/promises');
const path = require('node:path');

// The list of a demo recording (record-demo.cmd): the files, folders and links to import, one a line, and settings
// written as "이름 = 값". Read, checked and turned into a plan before anything is recorded, so a typo shows at once
// instead of in the middle of a video.

const MAX_ITEMS = 100;
const MAX_LIST_BYTES = 1024 * 1024;
// A recording's captions (.srt) to make its video again: about 700 KB for each hour of progress captions.
const MAX_CAPTION_BYTES = 64 * 1024 * 1024;
// How many files of a folder are counted to tell which imports it needs; the backend still reads all of them.
const SCAN_LIMIT = 20_000;
// The file that marks a Wiki folder a demo made and may empty again.
const MARKER = '.private-agent-demo';
const DATABASE_FILES = ['wiki.db', 'wiki.db-wal', 'wiki.db-shm', 'wiki.db-journal'];
// The files of one recording, in its own folder under the video folder.
const FILES = {
  video: '시연 영상.mp4',
  raw: '원본 녹화.mp4',
  captions: '원본 녹화.srt',
  project: '편집 정보.json',
  report: '진행 기록.txt',
};
// A folder's files are imported one kind at a time, documents first.
const FOLDER_KINDS = ['office', 'rtf', 'text', 'image'];
// Office files by the app that reads them, as src/lib/links.ts.
const OFFICE_APPS = {
  doc: 'Word',
  docx: 'Word',
  docm: 'Word',
  xls: 'Excel',
  xlsx: 'Excel',
  xlsm: 'Excel',
  xlsb: 'Excel',
  ppt: 'PowerPoint',
  pptx: 'PowerPoint',
  pptm: 'PowerPoint',
  vsd: 'Visio',
  vsdx: 'Visio',
  vsdm: 'Visio',
};

const YES = new Set(['예', '네', '켜기', '사용', 'yes', 'y', 'true', 'on', '1', 'o']);
const NO = new Set([
  '아니오',
  '아니요',
  '아니',
  '끄기',
  '사용 안 함',
  'no',
  'n',
  'false',
  'off',
  '0',
  'x',
]);
const SETTINGS = {
  제목: ['title', 'text'],
  'ai 분석': ['analyze', 'switch'],
  '하위 폴더': ['subfolders', 'switch'],
  'confluence 하위 페이지': ['subpages', 'switch'],
  '빨리 감기': ['fastForward', 'switch'],
  '끝나면 닫기': ['quit', 'switch'],
  'wiki 폴더': ['wiki', 'path'],
  '영상 폴더': ['output', 'path'],
};

const TEMPLATE = `# Private Agent 시연 녹화 목록
#
# 녹화는 목록의 순서대로 가져오고, # 로 시작하는 줄은 읽지 않습니다.
# 경로나 링크 뒤에 " | 자막"을 붙이면 그 자료를 가져오는 동안 보일 자막을 직접 정할 수 있습니다.
#
# 예)
#   D:\\자료\\설계서.docx
#   D:\\자료\\회의록
#   https://confluence.example.com/display/SPACE/Page | 팀 위키 페이지를 Markdown 문서로 가져옵니다
#   https://jira.example.com/browse/ABC-123
#
# 설정: 줄 앞의 #을 지우고 값을 바꾸면 적용됩니다. 예·아니오로 적습니다.
# 제목 = Private Agent 시연
# AI 분석 = 아니오
# 하위 폴더 = 예
# Confluence 하위 페이지 = 아니오
# 빨리 감기 = 예
# 끝나면 닫기 = 예
#
# Wiki 폴더: 시연에 쓸 Wiki 저장소. 녹화를 시작할 때마다 비우고 새로 만듭니다. 평소 쓰는 Wiki는 건드리지 않습니다.
# 영상 폴더: 녹화마다 날짜·시각 이름의 폴더를 만들어 영상과 자막을 저장합니다.
# 비워 두면 이 파일 옆의 "시연 Wiki", "시연 영상" 폴더를 씁니다.
# Wiki 폴더 = D:\\시연\\시연 Wiki
# 영상 폴더 = D:\\시연\\시연 영상

`;

const fold = (value) => value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();

// Encrypted bytes show as control characters, private-use and unassigned code points and replacement characters; a
// tenth to a half of such text, next to nothing of a written one (looks_encrypted in backend/agent/notepad.py).
const ODD = /[\p{Cc}\p{Cs}\p{Co}\p{Cn}\uFFFD]/gu;
function looksEncrypted(text) {
  const odd = (text.replace(/[\t\n\r\f\v\x1b]/g, '').match(ODD) ?? []).length;
  return odd >= 8 && odd >= text.length * 0.02;
}

// A text file as Notepad saves it on a Korean Windows: UTF-8 (with or without BOM), UTF-16 with a BOM, or ANSI
// (cp949).
function decodeText(bytes) {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf)
    return new TextDecoder('utf-8').decode(bytes.subarray(3));
  if (bytes[0] === 0xff && bytes[1] === 0xfe)
    return new TextDecoder('utf-16le').decode(bytes.subarray(2));
  if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    const swapped = Buffer.from(bytes.subarray(2));
    swapped.swap16();
    return new TextDecoder('utf-16le').decode(swapped);
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder('euc-kr').decode(bytes);
  }
}

// A text file the user wrote, such as the list or captions edited in Notepad, of at most `limit` bytes. Bytes that are
// no text, as those of a file only Notepad can open, are read by `readProtected` (the backend's hidden Notepad, on
// Windows) as Notepad shows them.
/**
 * @param {string} file
 * @param {{ readProtected?: (file: string) => Promise<string>, what?: string, limit?: number }} [options]
 */
async function readUserText(file, { readProtected, what = '파일', limit = MAX_LIST_BYTES } = {}) {
  let bytes;
  try {
    const stat = await fs.stat(file);
    if (!stat.isFile()) throw Object.assign(new Error(), { code: 'EISDIR' });
    if (stat.size > limit)
      throw new Error(`${what}이 너무 큽니다(${Math.floor(limit / 1024 / 1024)}MB 이하): ${file}`);
    bytes = await fs.readFile(file);
  } catch (error) {
    if (error.code === 'ENOENT')
      throw new Error(`${what}을 찾을 수 없습니다: ${file}`, { cause: error });
    if (error.code) throw new Error(`${what}을 읽지 못했습니다: ${file}`, { cause: error });
    throw error;
  }
  const text = decodeText(bytes);
  if (!looksEncrypted(text)) return text;
  const encrypted = `${what}이 암호화되어 있어(DRM) 읽지 못했습니다: ${file}.`;
  if (!readProtected)
    throw new Error(
      `${encrypted} 메모장에서 열리는지 확인하거나, 내용을 새 파일에 옮겨 적어 주세요.`,
    );
  try {
    return await readProtected(file);
  } catch (error) {
    throw new Error(`${encrypted} 메모장으로도 읽지 못했습니다: ${error.message}`, {
      cause: error,
    });
  }
}

function setting(name, raw, line, base) {
  const [key, type] = SETTINGS[name];
  const value = raw.trim().replace(/^"(.*)"$/, '$1');
  if (type === 'switch') {
    const word = fold(value);
    if (YES.has(word)) return [key, true];
    if (NO.has(word)) return [key, false];
    throw new Error(`${line}번째 줄: ‘${name}’ 값은 예 또는 아니오로 적어 주세요.`);
  }
  if (!value) throw new Error(`${line}번째 줄: ‘${name}’ 값을 적어 주세요.`);
  if (type === 'text') return [key, value.slice(0, 120)];
  return [key, path.resolve(base, value)];
}

// The lines of a list as settings and items, with every mistake that keeps it from running named by its line.
function parseList(text, listFile) {
  const base = path.dirname(listFile);
  const settings = {
    title: 'Private Agent 시연',
    analyze: false,
    subfolders: true,
    subpages: false,
    fastForward: true,
    quit: true,
    wiki: path.join(base, '시연 Wiki'),
    output: path.join(base, '시연 영상'),
  };
  const items = [];
  const problems = [];
  text.split(/\r\n|\r|\n/).forEach((raw, index) => {
    const line = index + 1;
    const content = raw.replace(/^\uFEFF/, '').trim();
    if (!content || content.startsWith('#')) return;
    const [source, ...caption] = content.split(/\s+\|\s+/);
    const unquoted = source.trim().replace(/^"(.*)"$/, '$1');
    if (/^https?:\/\//i.test(unquoted) || path.isAbsolute(unquoted)) {
      items.push({ line, source: unquoted, caption: caption.join(' | ').trim().slice(0, 200) });
      return;
    }
    const assignment = /^([^=]+?)\s*=\s*(.*)$/.exec(content);
    const name = assignment && fold(assignment[1]);
    if (name && Object.hasOwn(SETTINGS, name)) {
      try {
        const [key, value] = setting(name, assignment[2], line, base);
        settings[key] = value;
      } catch (error) {
        problems.push(error.message);
      }
      return;
    }
    problems.push(
      `${line}번째 줄을 읽지 못했습니다: ‘${content.slice(0, 60)}’. 파일·폴더의 전체 경로(D:\\…)나 https:// 링크, 또는 ‘이름 = 값’ 설정을 적어 주세요.`,
    );
  });
  if (!items.length && !problems.length)
    problems.push('가져올 파일·폴더 경로나 링크가 목록에 없습니다. 한 줄에 하나씩 적어 주세요.');
  if (items.length > MAX_ITEMS)
    problems.push(`한 번에 ${MAX_ITEMS}개까지 녹화할 수 있습니다. 목록을 나눠 주세요.`);
  return { settings, items, problems };
}

const extensionOf = (file) => path.extname(file).toLowerCase().replace(/^\./, '');

// How many files of each kind a folder holds (its subfolders too when `recursive`), as the folder imports find
// them: Office temporary files (~$…) and links are left out, as are hidden folders.
async function folderCounts(folder, recursive, kindOf) {
  const counts = { office: 0, rtf: 0, image: 0, text: 0, markdown: 0 };
  let seen = 0;
  let truncated = false;
  const pending = [folder];
  while (pending.length && !truncated) {
    const current = pending.shift();
    let entries;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch (error) {
      if (current === folder) throw error;
      continue; // A folder inside that cannot be read is left out, as the imports leave it out.
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (recursive && !entry.name.startsWith('.')) pending.push(path.join(current, entry.name));
        continue;
      }
      if (!entry.isFile() || entry.name.startsWith('~$')) continue;
      const kind = kindOf(entry.name);
      if (kind) counts[kind]++;
      if (++seen >= SCAN_LIMIT) {
        truncated = true;
        break;
      }
    }
  }
  return { counts, truncated };
}

// Each item as the import box will take it: its kind and the name captions show, or why it cannot be imported.
// `linkKind` and `extensions` are the main process's own (the kinds of links and of files it routes).
async function classify(item, { linkKind, extensions, subfolders }) {
  const kindOf = (file) => {
    const extension = extensionOf(file);
    return Object.keys(extensions).find((kind) => extensions[kind].includes(extension)) ?? null;
  };
  if (/^https?:\/\//i.test(item.source)) {
    const kind = linkKind(item.source);
    if (!kind)
      throw new Error(
        `${item.line}번째 줄: 가져올 수 없는 링크입니다. JIRA 티켓(/browse/ABC-123), Confluence 페이지 링크를 적어 주세요.`,
      );
    const url = new URL(item.source);
    const key = kind === 'jira' ? /([A-Z][A-Z0-9_]*-\d+)\/?$/i.exec(url.pathname)?.[1] : '';
    const last = url.pathname.split('/').filter(Boolean).at(-1) ?? '';
    let page = last;
    try {
      page = decodeURIComponent(last).replace(/\+/g, ' ');
    } catch {
      /* A link with a broken %-escape keeps its name as written. */
    }
    const name = key?.toUpperCase() || page || url.hostname;
    return { ...item, type: 'link', kind, name };
  }
  let stat;
  try {
    stat = await fs.stat(item.source);
  } catch {
    throw new Error(`${item.line}번째 줄: 파일이나 폴더를 찾을 수 없습니다: ${item.source}`);
  }
  const name = path.basename(item.source.replace(/[\\/]+$/, '')) || item.source;
  if (stat.isDirectory()) {
    let found;
    try {
      found = await folderCounts(item.source, subfolders, kindOf);
    } catch {
      throw new Error(`${item.line}번째 줄: 폴더를 읽지 못했습니다: ${item.source}`);
    }
    const kinds = FOLDER_KINDS.filter((kind) => found.counts[kind] > 0);
    if (!kinds.length)
      throw new Error(
        `${item.line}번째 줄: 폴더에 가져올 수 있는 파일(Office 문서·.rtf·.txt·이미지)이 없습니다: ${item.source}`,
      );
    return { ...item, type: 'folder', kind: 'folder', name, kinds, ...found };
  }
  const kind = kindOf(item.source);
  if (!kind)
    throw new Error(
      `${item.line}번째 줄: 가져올 수 없는 파일입니다. .md·.txt, Word·Excel·PowerPoint·Visio, .rtf, 이미지 파일을 적어 주세요: ${item.source}`,
    );
  return {
    ...item,
    type: 'file',
    kind,
    name,
    application: kind === 'office' ? OFFICE_APPS[extensionOf(item.source)] : undefined,
  };
}

// The plan of a demo: its settings and classified items. Every problem of the list is reported at once.
async function readPlan(listFile, { linkKind, extensions, readProtected }) {
  const text = await readUserText(listFile, { readProtected, what: '목록 파일' });
  const { settings, items, problems } = parseList(text, listFile);
  const classified = [];
  for (const item of items)
    try {
      classified.push(
        await classify(item, { linkKind, extensions, subfolders: settings.subfolders }),
      );
    } catch (error) {
      problems.push(error.message);
    }
  if (problems.length)
    throw new Error(
      `목록 파일(${listFile})을 고쳐 주세요.\n\n${problems.slice(0, 12).join('\n')}${problems.length > 12 ? `\n… 외 ${problems.length - 12}건` : ''}`,
    );
  return { list: listFile, ...settings, items: classified };
}

// Writes a list to fill in where none is yet: UTF-8 with a BOM and Windows line breaks, as Notepad keeps them.
async function writeTemplate(file) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, '\uFEFF' + TEMPLATE.replace(/\n/g, '\r\n'), { flag: 'wx' });
}

// A folder as Windows finally finds it: a mapped drive (Z:) as its \\server\share, a subst drive, a junction or a
// short 8.3 name as the folder itself, which is how storage.json keeps the Wiki the app opens. A folder that does not
// exist yet is taken as written.
async function realFolder(folder) {
  try {
    return await fs.realpath(folder);
  } catch {
    return path.resolve(folder);
  }
}

async function same(a, b) {
  const [left, right] = await Promise.all([realFolder(a), realFolder(b)]);
  return process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
}

// Empties the demo's Wiki folder: only one a demo made (it holds the marker) or an empty one, never a Wiki of the
// user's own. `keep` are folders never to empty, such as the Wiki the app normally opens, however either is written.
async function prepareWiki(folder, keep = []) {
  for (const other of keep)
    if (other && (await same(other, folder)))
      throw new Error(
        `‘Wiki 폴더’가 평소 쓰는 Wiki와 같습니다. 시연에는 다른 폴더를 지정해 주세요: ${folder}`,
      );
  let entries;
  try {
    entries = await fs.readdir(folder);
  } catch (error) {
    if (error.code !== 'ENOENT')
      throw new Error(`시연 Wiki 폴더를 열지 못했습니다: ${folder}`, { cause: error });
    entries = null;
  }
  if (entries && !entries.includes(MARKER)) {
    if (entries.length)
      throw new Error(
        `시연 Wiki 폴더에 다른 파일이 있어 비우지 않았습니다. 빈 폴더나 새 폴더를 ‘Wiki 폴더’로 지정해 주세요: ${folder}`,
      );
  }
  try {
    await fs.mkdir(folder, { recursive: true });
    for (const name of DATABASE_FILES) await fs.rm(path.join(folder, name), { force: true });
    await fs.writeFile(
      path.join(folder, MARKER),
      'Private Agent 시연 녹화가 만든 Wiki 폴더입니다. 녹화를 시작할 때마다 이 폴더의 Wiki를 비웁니다.\r\n',
    );
  } catch {
    throw new Error(
      `시연 Wiki 폴더를 비우지 못했습니다. 다른 프로그램이 wiki.db를 쓰고 있지 않은지, 폴더 권한을 확인해 주세요: ${folder}`,
    );
  }
  return folder;
}

const stamp = (date) =>
  `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(date.getDate()).padStart(2, '0')}-${String(date.getHours()).padStart(2, '0')}${String(date.getMinutes()).padStart(2, '0')}${String(date.getSeconds()).padStart(2, '0')}`;

// A new folder for one recording under the video folder, named by its start time.
async function createRunFolder(output, date = new Date()) {
  try {
    await fs.mkdir(output, { recursive: true });
    for (let attempt = 1; ; attempt++) {
      const folder = path.join(output, attempt === 1 ? stamp(date) : `${stamp(date)}-${attempt}`);
      try {
        await fs.mkdir(folder);
        return folder;
      } catch (error) {
        if (error.code !== 'EEXIST' || attempt >= 50) throw error;
      }
    }
  } catch {
    throw new Error(
      `영상 폴더를 만들지 못했습니다. 폴더 권한과 디스크 공간을 확인해 주세요: ${output}`,
    );
  }
}

const hasRecording = async (folder) => {
  try {
    return (await fs.stat(path.join(folder, FILES.raw))).isFile();
  } catch {
    return false;
  }
};

// The recording to make again: a recording's folder, the newest one in a video folder, or the newest one of a list's
// video folder.
async function findRecording(target, { readList }) {
  let stat;
  try {
    stat = await fs.stat(target);
  } catch {
    throw new Error(`찾을 수 없습니다: ${target}`);
  }
  let output = target;
  if (stat.isDirectory()) {
    if (await hasRecording(target)) return target;
  } else output = (await readList(target)).output;
  let names = [];
  try {
    names = (await fs.readdir(output, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
      .reverse();
  } catch {
    /* Reported below. */
  }
  for (const name of names)
    if (await hasRecording(path.join(output, name))) return path.join(output, name);
  throw new Error(`다시 만들 녹화(${FILES.raw})를 찾지 못했습니다: ${output}`);
}

module.exports = {
  FILES,
  MARKER,
  MAX_CAPTION_BYTES,
  TEMPLATE,
  decodeText,
  looksEncrypted,
  readUserText,
  parseList,
  readPlan,
  writeTemplate,
  prepareWiki,
  createRunFolder,
  findRecording,
};
