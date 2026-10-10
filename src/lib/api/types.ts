// The shapes of what the backend and the main process answer, as the screens read them. The requests go through
// ./transport.ts, and ./format.ts formats their dates and texts; '@/lib/api' gathers the three.
export type Prompt = {
  id: string;
  title: string;
  description: string;
  category: string;
  system_prompt: string;
  template: string;
  created_at: string;
  updated_at: string;
};
export type Run = {
  id: string;
  prompt_id: string;
  title: string;
  input: string;
  output: string;
  status: 'success' | 'error';
  error: string;
  kind: 'manual' | 'scheduled';
  schedule_id: string | null;
  schedule_name?: string | null;
  started_at: string | null;
  duration_ms: number | null;
  created_at: string;
};
export type RunItem = Omit<Run, 'input' | 'output'> & { preview: string };
export type ActiveRun = Pick<
  Run,
  'id' | 'prompt_id' | 'title' | 'kind' | 'schedule_id' | 'started_at'
>;
export type RunStats = {
  days: { date: string; success: number; error: number; scheduled: number }[];
  totals: {
    total: number;
    success: number;
    error: number;
    scheduled: number;
    average_ms: number | null;
  };
  active_schedules: number;
  running: ActiveRun[];
};
export type Frequency = 'once' | 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly';
export type Schedule = {
  id: string;
  prompt_id: string;
  prompt_title: string;
  name: string;
  variables: Record<string, string>;
  frequency: Frequency;
  run_time: string;
  weekday: number;
  month_day: number;
  run_at: string | null;
  enabled: boolean;
  notify: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: 'success' | 'error' | 'running' | null;
  last_error: string;
  created_at: string;
  updated_at: string;
};
export type ScheduleEvent =
  | {
      type: 'finished';
      schedule_id: string;
      name: string;
      notify: boolean;
      run_id: string;
      status: 'success' | 'error';
      error: string;
    }
  | { type: 'open'; run_id: string };
// Automatic routines (backend/agent/routines.py): ready-made jobs of a catalog that run on their own schedule while the
// app is open. A recipe's form is a list of fields; urls and paths hold one entry a line.
export type RoutineFieldType = 'text' | 'textarea' | 'url' | 'urls' | 'paths' | 'switch' | 'select';
export type RoutineField = {
  key: string;
  label: string;
  type: RoutineFieldType;
  required: boolean;
  default: unknown;
  help: string;
  options: { value: string | number; label: string }[];
  max: number | null;
  placeholder: string;
  group: string;
};
export type RoutineFrequency = 'interval' | 'daily' | 'weekdays' | 'weekly' | 'monthly';
export type RoutineSchedule = {
  frequency: RoutineFrequency;
  interval: number;
  run_time: string;
  weekday: number;
  month_day: number;
};
export type RoutineRecipe = {
  kind: string;
  title: string;
  summary: string;
  description: string;
  group: string;
  // Whether a run needs the LLM: not at all, for an option, or always.
  llm: 'none' | 'optional' | 'required';
  sites: SiteKind[];
  files: boolean;
  fields: RoutineField[];
  schedule: RoutineSchedule;
  notify: boolean;
};
export type RoutineStatus = 'queued' | 'running' | 'success' | 'error' | 'cancelled';
export type Routine = RoutineSchedule & {
  id: string;
  kind: string;
  name: string;
  // The recipe's name.
  title: string;
  config: Record<string, unknown>;
  enabled: boolean;
  notify: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: RoutineStatus | null;
  last_summary: string;
  last_error: string;
  // The last run found something new: a change, a new ticket, a report.
  last_changed: boolean;
  files: boolean;
  queued: boolean;
  running: boolean;
  // What a running run is doing.
  step: string;
  // The documents the routine keeps up to date (a project report, a work report).
  documents: { id: string; title: string }[];
  created_at: string;
  updated_at: string;
};
export type RoutineChange = {
  added: number;
  removed: number;
  lines: { op: '+' | '-'; text: string }[];
  more: number;
};
// What a run found: a page, ticket, file or document and what happened to it.
export type RoutineItem = {
  title: string;
  status: 'new' | 'found' | 'changed' | 'edited' | 'removed' | 'done' | 'failed' | 'missing';
  message: string;
  doc_id?: string | null;
  url?: string;
  change?: RoutineChange | null;
};
export type RoutineRun = {
  id: string;
  routine_id: string;
  name: string;
  kind: string;
  started_by: 'manual' | 'schedule';
  status: RoutineStatus;
  changed: boolean;
  summary: string;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  items: RoutineItem[];
  notes: string[];
  // What the LLM wrote of it (a summary of changes, a report), Markdown.
  text: string;
  metrics: { label: string; value: number | string; alert?: boolean }[];
  documents: { id: string; title: string }[];
  diagnosis?: WebDiagnosis | null;
};
export type RoutineEvent =
  | {
      type: 'finished';
      routine_id: string;
      run_id: string;
      name: string;
      notify: boolean;
      status: RoutineStatus;
      changed: boolean;
      summary: string;
      error: string;
    }
  | { type: 'open'; routine_id: string };
export type Relation = { source: string; target: string; label: string; count?: number };
// One section of a long document's PageIndex table of contents.
export type OutlineNode = {
  id: string;
  title: string;
  depth: number;
  kind: 'section' | 'page' | 'intro' | 'group';
  summary: string;
  // A section's position among the document's Markdown headings; null for pages and groups.
  heading: number | null;
};
export type WikiDocument = {
  id: string;
  title: string;
  // Set while the document waits in the trash (wiki.trash); lists and searches leave it out until it is restored.
  deleted_at?: string | null;
  // The Markdown text that search, PageIndex and the LLM read (and the editor edits).
  content?: string;
  excerpt?: string;
  // What the document window shows: the Markdown text, or the HTML page an imported file became.
  format?: 'markdown' | 'html';
  // Full documents only: the page while the format is html, and whether an imported page exists at all
  // (it stays as the original after the text is edited).
  html?: string;
  has_html?: boolean;
  summary: string;
  status: 'pending' | 'indexing' | 'ready' | 'error';
  error: string;
  revision: number;
  // How the last analysis indexed it: the whole text (LLM Wiki) or a table of contents (PageIndex).
  index_mode: '' | 'wiki' | 'pageindex';
  // Full documents only: whether analysis would build a PageIndex, and the stored table of contents.
  large?: boolean;
  outline?: OutlineNode[];
  keywords: string[];
  relations?: Relation[];
  // Full documents, and the entries of a page of the list or a search (DocumentPage): the documents an import too long
  // for one was saved as, in order (this one included). A page lists only the live ones and [] for other documents.
  parts?: { id: string; title: string; part: number }[];
  // Where an imported document came from, and when it last took its text from there (imported_at). An import too
  // long for one document is saved as `parts` documents; this one is number `part`. Full documents also say whether
  // the user changed the text of any of them since (an update from the source would replace it).
  source?:
    | ((
        | { kind: 'rtf'; source_path: string; imported_at: string }
        | { kind: 'image'; source_path: string; imported_at: string }
        | { kind: 'text'; source_path: string; imported_at: string }
        | { kind: 'markdown'; source_path: string; imported_at: string }
        | {
            kind: 'office';
            source_path: string;
            application: 'Word' | 'Excel' | 'PowerPoint' | 'Visio';
            imported_at: string;
          }
        | {
            kind: 'confluence';
            source_url: string;
            page_id: string;
            imported_at: string;
          }
        | {
            kind: 'jira';
            source_url: string;
            issue_key: string;
            imported_at: string;
          }
      ) & { part: number; parts: number; edited?: boolean })
    | null;
  created_at: string;
  updated_at: string;
};
export type LlmProvider = 'general';
// The request format; openai also covers OpenAI-compatible servers.
export type LlmFormat = 'openai' | 'anthropic';
// A custom header: {api_key} in its value becomes the API Key, {uuid} a new ID per request. The value is a secret of
// its own (a gateway's token): the screens get whether one is saved (has_value) and whether it takes the key, never
// the value; a header sent without `value` keeps the saved one.
export type LlmHeader = {
  name: string;
  value: string;
  has_value?: boolean;
  uses_api_key?: boolean;
};
export type Settings = {
  provider: LlmProvider;
  api_format: LlmFormat;
  base_url: string;
  model_name: string;
  headers: LlmHeader[];
  has_api_key: boolean;
  key_stored: boolean;
  temperature: number;
  // Seconds an answer may take, retries of failures that may pass (HTTP 429 and 5xx, a dropped connection) included.
  timeout: number;
  ca_bundle: string;
  // The cap of an answer in tokens: 0 leaves it automatic (the server's own; 16,000 for the Anthropic format).
  // Optional only for screens written before them: settings.get always sends these three.
  max_tokens?: number;
  // The tokens the model reads and writes in one request; a question's evidence and history scale with it.
  context_window?: number;
  // LLM requests at once (1~8); questions go before batch work such as analyses.
  concurrency?: number;
  // Whether the LLM in use has what a request needs, and its model.
  configured: boolean;
  active_model: string;
  secure_storage: boolean;
  os_user: string;
  // Why the saved settings could not be read this session; they cannot be changed until a restart.
  load_error: string;
};
export type Stats = { prompts: number; documents: number; keywords: number; relations: number };
// The trash (wiki.trash): the deleted documents, the last deleted first, and how many days each waits before it
// is purged by itself.
export type Trash = { documents: WikiDocument[]; days: number };
// What a deletion answers: the ids moved to the trash, for 되돌리기 (wiki.restore).
export type Deleted = { deleted: number; ids: string[] };
// Two texts compared line by line (backend/agent/changes.py diff): hunks of changed lines (+ added, - removed) among a
// few unchanged ones, each starting at those line numbers of the two texts; `more` counts changed lines left out.
export type DiffLine = { op: ' ' | '+' | '-'; text: string };
export type DiffHunk = { old_start: number; new_start: number; lines: DiffLine[] };
export type TextDiff = { added: number; removed: number; hunks: DiffHunk[]; more: number };
// What an edit that wiki.save refused meets (wiki.compare): the document as it is now (without its HTML page), whether
// it changed since the editor opened it, and the text the editor started from compared with the document's (theirs)
// and the editor's (ours), and the document's with the editor's (overwrite: what saving over it would change).
export type EditConflict = {
  document: WikiDocument;
  changed: boolean;
  diffs: { theirs: TextDiff; ours: TextDiff; overwrite: TextDiff };
};
// The Wiki's backups in its backups folder (wiki.backups), the newest first: made once a day while the app runs (auto)
// or when asked (manual), each kind keeping its newest `keep`, and the Wiki as it was before a restore
// (before_restore). created_at is UTC.
export type BackupKind = 'auto' | 'manual' | 'before_restore';
export type Backup = { file: string; kind: BackupKind; created_at: string; size: number };
export type Backups = {
  folder: string;
  keep: number;
  backups: Backup[];
  // The restore the next start of the app carries out (wiki.restore_backup).
  pending_restore: { file: string; requested_at: string } | null;
  // How the last restore went; `before` is the backup the Wiki it replaced became.
  last_restore: {
    file: string;
    status: 'restored' | 'failed';
    before: string | null;
    error: string;
    at: string;
  } | null;
};
// What wiki.backup answers: the backup made, and the older ones it pushed out (the oldest beyond `keep`).
export type BackupMade = Backup & { removed: string[] };
// What wiki.restore_backup answers: the restore happens when the app starts again (restart_required), or it was taken
// back (cancel).
export type RestoreScheduled = {
  restart_required: boolean;
  message: string;
  file?: string;
  requested_at?: string;
  cancelled?: boolean;
};
export type Graph = {
  // `aliases`: the other names the AI analysis gave the keyword (its Korean or English form, an abbreviation), by which
  // the keyword filter finds it; absent from a backend older than them.
  nodes: { id: string; label: string; count: number; docs: string[]; aliases?: string[] }[];
  edges: (Relation & { count: number; docs: string[] })[];
  documents: { id: string; title: string }[];
  total_keywords: number;
  total_relations: number;
};
// Where and why a document matched: passages around the search words (section path for long
// documents) and, for prompt search, the AI search's choice with its reason and sections.
export type SearchMatch = {
  score: number;
  matched: string[];
  snippets: { text: string; section?: string }[];
  sections: { id: string; title: string }[];
  reason: string;
  selected: boolean;
  // The keywords the document matched by an alias (another name the AI analysis gave the keyword: its Korean or
  // English form, an abbreviation), each with the alias the search named; they are in `matched` too. Absent otherwise.
  aliases?: { keyword: string; alias: string }[];
};
export type SearchStep = {
  kind: 'analyze' | 'keyword' | 'agent' | 'search' | 'outline' | 'fallback';
  label: string;
  detail?: string;
  count?: number;
  // AI search steps: how many candidates the LLM read.
  reviewed?: number;
  terms?: string[];
  documents?: string[];
};
export type SearchResult = {
  documents: (WikiDocument & { match: SearchMatch; size: number })[];
  plan: { terms: string[]; keywords: string[]; explanation: string; intent: string };
  highlights: string[];
  steps: SearchStep[];
  total: number;
  ai?: boolean;
  // Set when the search was asked for a page (DocumentQuery limit or cursor).
  next_cursor?: string | null;
};
// What a document list (wiki.list) or a search other than prompt (wiki.search) is asked for besides its query: with
// limit or cursor one page of it, whose next_cursor the next request sends back as cursor; and only the documents of
// one source (manual: written here), one state of AI analysis, or updated from updated_from up to updated_to (ISO 8601
// with a time zone; the end left out). Without limit and cursor the whole list comes as before. The document list also
// keeps only the documents holding `query` in their title, summary, keywords (or the keywords' aliases) or where they
// came from, and those with the keyword `keyword` or the keyword it is an alias of; a search's query is its own.
export type DocumentQuery = {
  limit?: number;
  cursor?: string;
  source?: DocumentSource;
  analysis?: DocumentAnalysis;
  updated_from?: string;
  updated_to?: string;
  query?: string;
  keyword?: string;
};
// Where a document came from, as a list filters by it (manual: written in the app).
export type DocumentSource = 'manual' | NonNullable<WikiDocument['source']>['kind'];
// A document's AI analysis, as a list filters by it (electron/api-catalog.cjs analysisState).
export type DocumentAnalysis = 'analyzed' | 'not_analyzed' | 'failed' | 'running';
// A page of the document list (wiki.list with limit or cursor): null next_cursor after the last page. `total` counts
// the documents the filters keep in all, each part of an import as one, as stats counts them.
export type DocumentPage = { documents: WikiDocument[]; next_cursor: string | null; total: number };

export type StorageInfo = {
  initialized: boolean;
  directory: string;
  suggested_path: string;
  has_legacy_data: boolean;
  legacy_path: string;
  error: string;
};
// What an import did with one file, page or ticket: saved new documents, updated the documents of the same path or
// link in place, skipped it or failed. A skipped one says why: the same content is saved (same, also a page or ticket
// a routine did not read again as the site said it had not changed), the user changed the text of its documents since
// they were imported (edited), a routine left its documents in the trash (trashed) or left out a source whose
// documents the user purged (ignored), or a moved or renamed file took its documents along (moved).
export type ImportOutcome = 'imported' | 'updated' | 'skipped' | 'failed';
export type SkipReason = 'same' | 'edited' | 'trashed' | 'ignored' | 'moved';
export type LocalImportJob = {
  id: string;
  status: 'scanning' | 'running' | 'cancelling' | 'completed' | 'cancelled' | 'error';
  total: number;
  processed: number;
  imported: number;
  updated: number;
  skipped: number;
  // Of the skipped: files whose documents' text the user changed (edited), and files a routine left out because the
  // user purged their documents from the trash (ignored).
  edited?: number;
  ignored?: number;
  failed: number;
  analysis_failed: number;
  // A folder of more files than a batch (5,000) is imported batch after batch: the batch being imported, of `batches`.
  batch: number;
  batches: number;
  current_file: string;
  // A caution raised once the job is about to read a file, such as a presentation in the user's PowerPoint.
  notice: string;
  // While the file waits for the Office app another import or write-back uses: what it waits for.
  waiting?: string;
  // The automatic routine the job runs for; '' for the user's own import.
  routine?: string;
  error: string;
  // The user cleared the finished job from the import box.
  dismissed?: boolean;
  // The results of up to 5,000 files, failures first, then the latest; the counts are of every file.
  results: {
    path: string;
    status: ImportOutcome;
    message: string;
    doc_id?: string;
    doc_ids?: string[];
    reason?: SkipReason;
    analysis_error?: string;
  }[];
};

// The LLM's analysis of why a site did not open or sign in (backend/agent/web_assist.py): the cause, its
// category, what shows it and what the user can do, and what the app tried after it (attempt); or only why there is
// none (error).
export type WebDiagnosis =
  | {
      category: string;
      label: string;
      cause: string;
      evidence: string[];
      actions: string[];
      attempt: string;
    }
  | { error: string };
// How a browser import signed in: the site account and the login method it used or detected, and the fields the LLM
// found when no method fitted the login screen (learned, method 'learned').
export type JobLogin = {
  site: string;
  method: string | null;
  label: string;
  detected: boolean;
  error: string;
  learned?: LearnedFields;
  // The methods tried before the one that signed in, and how each went.
  tried?: string[];
};
export type ConfluenceJob = {
  id: string;
  url: string;
  recursive: boolean;
  analyze: boolean;
  auto_login: boolean;
  login?: JobLogin;
  status: LocalImportJob['status'] | 'login';
  total: number;
  processed: number;
  imported: number;
  updated: number;
  skipped: number;
  failed: number;
  analysis_failed: number;
  discovery_failed: number;
  current_url: string;
  // The automatic routine the job runs for; '' for the user's own import.
  routine?: string;
  // What the LLM is doing while it helps with a login or a page that did not open.
  notice?: string;
  error: string;
  // The LLM's analysis of a failed login or first page, or of a page it opened again.
  diagnosis?: WebDiagnosis | null;
  dismissed?: boolean;
  results: {
    url: string;
    status: ImportOutcome;
    message: string;
    doc_id?: string;
    doc_ids?: string[];
    reason?: SkipReason;
    analysis_error?: string;
    discovery_error?: string;
  }[];
};
export type ConfluenceStatus = { job: ConfluenceJob | null };

// The import box offers issue, keyword and user; automatic routines also run list (watched tickets), report (a
// project's progress) and jql (a saved search).
export type JiraScope = 'issue' | 'keyword' | 'user' | 'list' | 'report' | 'jql';
export type JiraRole = 'any' | 'assignee' | 'reporter';
export type JiraJob = Omit<ConfluenceJob, 'recursive' | 'discovery_failed' | 'results'> & {
  scope: JiraScope;
  keyword: string;
  user: string;
  role: JiraRole;
  // The tickets of a routine's list scope.
  keys?: string[];
  project: string;
  jql: string;
  results: {
    key: string;
    url: string;
    status: ImportOutcome;
    message: string;
    doc_id?: string;
    doc_ids?: string[];
    reason?: SkipReason;
    analysis_error?: string;
  }[];
};
export type JiraStatus = { job: JiraJob | null };
// The AI analysis of the documents chosen in the document list, one after another in the background.
export type AnalysisJob = {
  id: string;
  status: 'running' | 'cancelling' | 'completed' | 'cancelled' | 'error';
  total: number;
  processed: number;
  analyzed: number;
  skipped: number;
  failed: number;
  // The document being analyzed, and while the job runs every document still to analyze (that one first).
  current: { id: string; title: string } | null;
  queue: string[];
  error: string;
  dismissed?: boolean;
  // The documents that were not analyzed and why; a deleted document has no title.
  results: { id: string; title: string; status: 'skipped' | 'failed'; message: string }[];
};
// The background imports, each kind with an importer of its own in the backend and a row in the import box.
export type ImportKind = 'office' | 'rtf' | 'image' | 'text' | 'confluence' | 'jira';
// The last job of each importer and of the AI analysis, null when there has been none.
export type Jobs = {
  office: LocalImportJob | null;
  rtf: LocalImportJob | null;
  image: LocalImportJob | null;
  text: LocalImportJob | null;
  confluence: ConfluenceJob | null;
  jira: JiraJob | null;
  analysis: AnalysisJob | null;
};
export type JobKind = keyof Jobs;
// What the main process read of the jobs (jobs.status), and each change it reads while one runs or an automatic
// routine's run is queued or running (job:progress, electron/job-progress.cjs). Every read is numbered as it is asked,
// so that a state read earlier never replaces one read later, whichever arrives first.
export type JobsSnapshot = { seq: number; jobs: Jobs };
export type JobProgressEvent =
  | { [K in JobKind]: { seq: number; kind: K; job: Jobs[K] } }[JobKind]
  | { seq: number; kind: 'routines'; routines: Routine[] };
// A Markdown file imported at once: saved, updated in place (the same path again) or skipped.
export type TextImport = {
  status: Exclude<ImportOutcome, 'failed'>;
  doc_id: string;
  doc_ids?: string[];
  reason?: SkipReason;
  title: string;
  path: string;
  message: string;
  analysis_error?: string;
};
// Whether the file a document came from is still as the document took it (wiki.source_status); pages and tickets
// are unknown until an import reads them.
export type SourceStatus = {
  state: 'same' | 'changed' | 'missing' | 'unreadable' | 'unknown';
  modified_at?: string;
};
// Writing what the user changed in an imported document back into its file (원본에 반영): an Office file of this PC,
// by a hidden Office application. changes: the places (lines, cells) it writes; backup: the copy of the file as it was.
export type WritebackJob = {
  id: string;
  doc_id: string;
  kind: 'office';
  application: 'Word' | 'Excel' | 'PowerPoint' | 'Visio';
  file: string;
  status: 'running' | 'cancelling' | 'completed' | 'error' | 'cancelled';
  changes: number;
  notice: string;
  // Another import or write still has the Office application: what this write waits for, until its turn comes.
  waiting?: string;
  message: string;
  error: string;
  backup: string;
  reread: boolean;
  // Its end was shown to the user (writeback.dismiss), so a document window does not tell it again.
  dismissed?: boolean;
};
// A copy of the file a document came from, kept before a write-back or a restore changed the file (writeback.backups):
// the newest five of each file stay. state: how the write it was made for went (writing while it runs, interrupted
// when the app was closed by force during it); current: the file holds these bytes now.
export type WritebackBackup = {
  id: string;
  name: string;
  created_at: string;
  size: number;
  kind: 'write' | 'restore';
  state: 'writing' | 'written' | 'failed' | 'interrupted' | 'done';
  current: boolean;
};
// What a write-back cut short by a forced close left of the file, as the next write-back tells it before writing:
// as it was (intact), missing or partly written (partial), or another whole file (unknown).
export type WritebackInterruption = {
  state: 'intact' | 'partial' | 'unknown';
  backup: string;
  name: string;
  message: string;
};
export type WritebackBackups = {
  file: string;
  keep: number;
  backups: WritebackBackup[];
  interrupted: WritebackInterruption | null;
};
// writeback.restore_backup: whether the file was replaced, and the copy of the file it replaced ('' when none).
export type WritebackRestore = { restored: boolean; backup: string; message: string };

// The kinds of site accounts (electron/sites.cjs).
export type SiteKind = 'jira' | 'confluence';
// One step of a login method: click an element, type the ID, the password or the user's own text into a field, or send
// the login (submit, the last step; without a target Enter in the field typed last). optional: only when it shows;
// unless: skipped when that element shows. Targets are XPaths (xpath=/html/…) or CSS selectors.
export type LoginStep = {
  action: 'click' | 'fill' | 'submit';
  target?: string;
  value?: 'id' | 'password' | 'text';
  text?: string;
  optional?: boolean;
  unless?: string;
};
export type LoginSteps = { steps: LoginStep[] };
// The fields the LLM found, as the backend reports them with a job's login.
export type LearnedFields = {
  before: string[];
  id: string;
  next: string;
  password: string;
  submit: string;
};
export type Site = {
  id: string;
  kind: SiteKind;
  name: string;
  // '' = every address of this kind (an account carried over from an earlier version).
  url: string;
  login_id: string;
  has_password: boolean;
  password_stored: boolean;
  // 'auto' tries every method and keeps the one that worked; 'session' never types the login. A chosen method is
  // tried first, then the others as in auto.
  method: string;
  method_label: string;
  detected_method: string | null;
  detected_label: string;
  // The user's own steps, used while method is 'custom'.
  custom: LoginSteps;
  // The fields the LLM found on a login screen no method fitted, tried while detected_method is 'learned'.
  learned: LoginSteps | null;
  // The hosts the account was typed into besides the site's own (its identity provider), kept by the first login
  // that signed in: the account goes to no other host until they are reset (sites.save reset_login_hosts).
  login_hosts: string[];
  verified_at: string | null;
  last_error: string;
  // The LLM's analysis of the last failed login.
  last_diagnosis: WebDiagnosis | null;
  updated_at: string;
};
// A method of the catalog: priority ones (standard SSO) are tried before the others; detect holds
// the elements that show its login screen.
export type LoginMethod = {
  id: string;
  label: string;
  description: string;
  priority: boolean;
  detect: string[];
  steps: LoginStep[];
};
export type SitesState = {
  sites: Site[];
  methods: LoginMethod[];
  secure_storage: boolean;
  // Why sites.json could not be read this session; the accounts cannot be changed until a restart.
  error: string;
};
export type SiteMatch = { kind: SiteKind | null; site: Site | null };
// Per kind: the site whose login window is open (or was last opened), and the site whose login check is running.
export type SiteWindows = Record<
  SiteKind,
  { site_id: string | null; open: boolean; error: string; checking: string | null }
>;
export type SiteCheck = {
  ok: boolean;
  message: string;
  // null when the site was deleted while it was checked.
  site: Site | null;
  diagnosis?: WebDiagnosis | null;
  // The user stopped the check (sites.check_cancel): nothing was recorded.
  stopped?: boolean;
};

export type WidgetSize = 's' | 'm' | 'l' | 'xl';
// As tall as its content (auto), or a fixed height the widget scrolls in.
export type WidgetHeight = 'auto' | 's' | 'm' | 'l';
// One widget of the dashboard: `type` is what it shows (tasks, documents…) and `view` the format it shows it in, so
// the same content can be placed more than once. Layouts saved before formats existed have neither: their id was the
// content, in its first format.
export type WidgetPlacement = {
  id: string;
  type?: string;
  view?: string;
  size: WidgetSize;
  height?: WidgetHeight;
};
// widgets null = the default dashboard.
// web_assist: the LLM helps when a site does not open or sign in, and says why.
export type Preferences = {
  widgets: WidgetPlacement[] | null;
  ai_answer: boolean;
  web_assist: boolean;
};

// The local API and MCP server for AI tools (electron/api-*.cjs). The token itself stays in the main process.
export type ApiAccess = 'read' | 'write' | 'full';
export type ApiLevel = { id: ApiAccess; label: string; description: string };
export type ApiClient = {
  id: string;
  name: string;
  snippets: { id: string; label: string; language: string; text: string }[];
  notes: string[];
};
export type ApiState = {
  enabled: boolean;
  running: boolean;
  port: number;
  access: ApiAccess;
  error: string;
  url: string;
  mcp_url: string;
  docs_url: string;
  token_hint: string;
  token_stored: boolean;
  // Why api.json could not be read this session; the server stays off until a restart.
  load_error: string;
  secure_storage: boolean;
  levels: ApiLevel[];
  clients: ApiClient[];
  recent: { at: string; transport: 'rest' | 'mcp'; name: string; ok: boolean; client: string }[];
};
export type ApiParam = {
  name: string;
  in: 'path' | 'query' | 'body' | 'argument';
  type: string;
  required: boolean;
  description: string;
  enum?: (string | number)[];
  default?: unknown;
};
export type ApiManual = {
  version: string;
  base_url: string;
  endpoints: Record<string, string>;
  authentication: {
    header: string;
    alternative: string;
    token: string;
    public: string[];
    network: string;
  };
  responses: { success: string; failure: string; notes: string[] };
  errors: { status: number; code: string; description: string }[];
  access: ApiAccess | null;
  access_levels: ApiLevel[];
  scopes: { id: string; label: string; levels: ApiAccess[] }[];
  groups: {
    id: string;
    title: string;
    description: string;
    routes: {
      verb: string;
      path: string;
      method: string | null;
      scope: string;
      slow: boolean;
      summary: string;
      params: ApiParam[];
    }[];
  }[];
  methods: { name: string; scope: string; slow: boolean; summary: string; routes: string[] }[];
  mcp: {
    endpoint: string;
    transports: { name: string; endpoint: string; note: string }[];
    protocol_versions: string[];
    // How the answers behave: the markers around the app's data, access changes, the user's unsaved work.
    notes: string[];
    tools: {
      name: string;
      title: string;
      scope: string;
      slow: boolean;
      description: string;
      params: ApiParam[];
    }[];
    resources: { uri: string; description: string }[];
    prompts: {
      name: string;
      title: string;
      description: string;
      arguments: { name: string; description: string; required: boolean }[];
    }[];
  };
  clients: ApiClient[];
};

export type Task = {
  id: string;
  title: string;
  notes: string;
  assignee: string;
  status: 'open' | 'done' | 'paused';
  due_at: string | null;
  remind_at: string | null;
  notified_at?: string | null;
  // A snoozed repeating reminder is due again then; remind_at stays the time its repeats count from.
  snoozed_until?: string | null;
  completed_at?: string | null;
  repeat: 'none' | 'daily' | 'weekly';
  source_id: string | null;
  source_title?: string | null;
  created_at?: string;
  updated_at?: string;
  evidence?: string;
};
export type Citation = {
  id: string;
  number: number;
  title: string;
  updated_at: string;
  revision: number;
  excerpt: string;
  partial: boolean;
  source: WikiDocument['source'];
  // Answers saved before search highlighting have neither.
  sections?: string[];
  terms?: string[];
};
export type Conversation = { id: string; title: string; updated_at: string };
// A page of the conversations (assistant.conversations with limit or cursor); see DocumentPage.
export type ConversationPage = { conversations: Conversation[]; next_cursor: string | null };
// An answer from the search box; an automatic search also lists every document it found. Asked with
// { stream: true, request_id }, the same answer also reaches onAssistantDelta while the LLM writes it. An answer the
// user stopped (assistant.cancel) or the LLM's output limit cut short keeps what came, with a note at its end.
export type Answer = {
  conversation_id: string;
  messages: ChatMessage[];
  search: SearchResult | null;
  request_id?: string;
  cancelled?: boolean;
  truncated?: boolean;
};
// What assistant.ask sends while the LLM writes an answer asked with stream, for its request_id: parts of the
// answer's text (delta; with reset, the text replaces what came before, as when a failed attempt starts over), then
// one end (done). A new conversation's ID comes with its first event, for assistant.cancel.
export type AssistantStreamEvent =
  | {
      type: 'delta';
      request_id: string;
      conversation_id: string;
      text: string;
      reset?: boolean;
    }
  | {
      type: 'done';
      request_id: string;
      conversation_id: string;
      status: 'completed' | 'cancelled' | 'error';
      // The LLM stopped at its output limit (max_tokens): the answer ends with a note saying so.
      truncated: boolean;
      error?: string;
    };
// assistant.cancel {conversation_id} or {request_id}: whether there was an answer to stop.
export type AssistantCancel = { ok: boolean; cancelled: boolean };
export type ChatMessage = {
  seq: number;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  // How the evidence was found, for assistant answers; outcome when the answer did not end by itself.
  trace?: {
    terms: string[];
    steps: SearchStep[];
    sources: { title: string; sections: string[] }[];
    outcome?: 'cancelled' | 'truncated';
  } | null;
  created_at: string;
};
export type Briefing = {
  generated_at: string;
  local_date: string;
  counts: { open: number; done: number; paused: number };
  overdue: Task[];
  today: Task[];
  upcoming: Task[];
  unscheduled: Task[];
  completed_today: Task[];
  reminders: Task[];
  recent_documents: Pick<WikiDocument, 'id' | 'title' | 'summary' | 'updated_at'>[];
};
