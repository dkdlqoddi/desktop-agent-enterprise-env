export type OfficeApplication = 'Word' | 'Excel' | 'PowerPoint' | 'Visio';
export type ImportTarget =
  | { kind: 'jira'; url: string; key: string }
  | { kind: 'confluence'; url: string }
  | { kind: 'office'; path: string; application: OfficeApplication }
  | { kind: 'markdown' | 'text' | 'rtf' | 'image' | 'folder'; path: string }
  | { kind: 'other-link' | 'other-file'; value: string };

// Same ticket link shapes as backend/agent/jira.py, also below a context path such as /jira.
const ISSUE_PATHS = [
  /\/browse\/([A-Z][A-Z0-9_]*-\d+)\/?$/i,
  /\/projects\/[^/]+\/issues\/([A-Z][A-Z0-9_]*-\d+)\/?$/i,
];
// Same page link shapes as backend/agent/confluence_import.py, also below /wiki or /confluence.
const PAGE_PATHS = [
  /\/display\/[^/]+\/[^/]+/,
  /\/spaces\/[^/]+\/pages\/\d+/,
  /\/x\/[A-Za-z0-9_-]+\/?$/,
];
const MARKDOWN = ['md', 'markdown'];
// Same image files as EXTENSIONS in backend/agent/images.py.
const IMAGES = [
  'png',
  'jpg',
  'jpeg',
  'jfif',
  'gif',
  'bmp',
  'webp',
  'avif',
  'svg',
  'ico',
  'tif',
  'tiff',
  'emf',
  'wmf',
];
// Same file kinds as FORMATS in backend/agent/office_converter.py: the app that reads each one.
const OFFICE = new Map<string, OfficeApplication>([
  ['doc', 'Word'],
  ['docx', 'Word'],
  ['docm', 'Word'],
  ['xls', 'Excel'],
  ['xlsx', 'Excel'],
  ['xlsm', 'Excel'],
  ['xlsb', 'Excel'],
  ['ppt', 'PowerPoint'],
  ['pptx', 'PowerPoint'],
  ['pptm', 'PowerPoint'],
  ['vsd', 'Visio'],
  ['vsdx', 'Visio'],
  ['vsdm', 'Visio'],
]);

// An address that names its scheme, and one without, as electron/sites.cjs takes them: a host (a name, an IPv4
// address or an [IPv6] one), its port, then its path, without spaces or backslashes.
const SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;
const BARE =
  /^(?:\/\/)?(?:[\p{L}\p{N}\p{M}_-]+(?:\.[\p{L}\p{N}\p{M}_-]+)*|\[[0-9a-f:.]+\])(?::\d{1,5})?(?:[/?#][^\s\\]*)?$/iu;

export const hasScheme = (value: string) => SCHEME.test(value.trim());

// An http(s) address as typed; one without its scheme is read as https, as the app opens a link typed so.
export function httpUrl(value: string) {
  const text = value.trim();
  const bare = !SCHEME.test(text);
  if (bare && !BARE.test(text)) return null;
  try {
    const url = new URL(bare ? `https://${text.replace(/^\/\//, '')}` : text);
    return ['http:', 'https:'].includes(url.protocol) &&
      url.hostname &&
      !url.username &&
      !url.password
      ? url
      : null;
  } catch {
    return null;
  }
}

// The ticket key of a JIRA ticket link, or '' for any other link.
export function jiraIssueKey(value: string) {
  const url = httpUrl(value);
  for (const pattern of url ? ISSUE_PATHS : []) {
    const match = url!.pathname.match(pattern);
    if (match) return match[1].toUpperCase();
  }
  return '';
}

export function isConfluencePageUrl(value: string) {
  const url = httpUrl(value);
  if (!url) return false;
  if (url.pathname.endsWith('/pages/viewpage.action'))
    return (
      !!url.searchParams.get('pageId') ||
      (!!url.searchParams.get('spaceKey') && !!url.searchParams.get('title'))
    );
  return PAGE_PATHS.some((pattern) => pattern.test(url.pathname));
}

// `folder` is true when the path came from the folder picker; a typed path without an extension is
// also taken as a folder.
export function importTarget(raw: string, folder = false): ImportTarget | null {
  // Explorer's "Copy as path" puts the path in double quotes, as the local API also accepts it.
  const value = raw.trim().replace(/^"(.*)"$/, '$1');
  if (!value) return null;
  // anything else without a scheme is a path, as is one that starts with a slash (//server/share/…).
  if (!folder && !/^[\\/]/.test(value)) {
    const key = jiraIssueKey(value);
    if (key) return { kind: 'jira', url: value, key };
    if (isConfluencePageUrl(value)) return { kind: 'confluence', url: value };
    if (SCHEME.test(value)) return { kind: 'other-link', value };
  }
  const name =
    value
      .replace(/[\\/]+$/, '')
      .split(/[\\/]/)
      .pop() ?? '';
  const extension = name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
  if (folder || !extension || /[\\/]$/.test(value)) return { kind: 'folder', path: value };
  if (MARKDOWN.includes(extension)) return { kind: 'markdown', path: value };
  // The backend reads it through a hidden Notepad, so it arrives as the text Notepad shows.
  if (extension === 'txt') return { kind: 'text', path: value };
  const application = OFFICE.get(extension);
  if (application) return { kind: 'office', path: value, application };
  if (extension === 'rtf') return { kind: 'rtf', path: value };
  if (IMAGES.includes(extension)) return { kind: 'image', path: value };
  return { kind: 'other-file', value };
}
