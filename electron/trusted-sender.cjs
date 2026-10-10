const { BUNDLE } = require('./protocol.cjs');

// Which IPC messages the main process takes from the screens. The screens reach it through the one request channel
// preload.cjs opens ('agent:request') and the replies to the local API's commands ('control:ready', 'control:result');
// each message must come from the app's own screen, and a request must name a method of ALLOWED. AI tools are checked
// apart, by their token and access level (api-catalog.cjs authorize()). Nothing here needs Electron, so
// tests/trusted-sender.test.cjs runs it under plain Node.js, and scripts/generate-methods.mjs reads ALLOWED from it.

// The methods the screens may ask for; a method left out is refused before it reaches handle() or the backend. A
// method that returns secrets (settings.secrets) is never one of them: the main process keeps those to itself.
const ALLOWED = new Set([
  'health',
  'stats',
  'prompts.list',
  'prompts.save',
  'prompts.delete',
  'prompts.run',
  'runs.list',
  'runs.get',
  'runs.delete',
  'runs.stats',
  'runs.active',
  'schedules.list',
  'schedules.save',
  'schedules.toggle',
  'schedules.delete',
  'schedules.run',
  // Automatic routines: ready-made jobs on a schedule. Due runs start from the main process (RoutineRunner).
  'routines.catalog',
  'routines.list',
  'routines.get',
  'routines.save',
  'routines.toggle',
  'routines.delete',
  'routines.run',
  'routines.cancel',
  'routines.runs',
  'wiki.list',
  'wiki.get',
  'wiki.save',
  // Read only: an edit wiki.save refused, compared with the document as it is now.
  'wiki.compare',
  'wiki.delete',
  'wiki.delete_many',
  'wiki.trash',
  'wiki.restore',
  'wiki.purge',
  // Backups of the Wiki in its backups folder, and the restore of one at the next start.
  'wiki.backup',
  'wiki.backups',
  'wiki.restore_backup',
  'wiki.analyze',
  'wiki.clear_analysis',
  'wiki.search',
  'wiki.graph',
  'analysis.start',
  'analysis.status',
  'analysis.cancel',
  'analysis.dismiss',
  // Every background job at once, read by the main process, which then follows them for the screens (job:progress).
  'jobs.status',
  'preferences.get',
  'preferences.save',
  'settings.get',
  'settings.update',
  'settings.test',
  'import.choose',
  'files.import_text',
  'clipboard.write',
  'storage.get',
  'storage.choose',
  'storage.initialize',
  'rtf.capabilities',
  'rtf.start',
  'rtf.status',
  'rtf.cancel',
  'rtf.dismiss',
  'office.capabilities',
  'office.start',
  'office.status',
  'office.cancel',
  'office.dismiss',
  'image.capabilities',
  'image.start',
  'image.status',
  'image.cancel',
  'image.dismiss',
  'text.capabilities',
  'text.start',
  'text.status',
  'text.cancel',
  'text.dismiss',
  'wiki.source_markdown',
  'wiki.source_html',
  'wiki.source_status',
  // Writing a document's changes back into the file it came from, and the copies of the file kept before each write.
  'writeback.start',
  'writeback.status',
  'writeback.cancel',
  'writeback.dismiss',
  'writeback.backups',
  'writeback.restore_backup',
  'confluence.start',
  'confluence.status',
  'confluence.cancel',
  'confluence.dismiss',
  'jira.start',
  'jira.status',
  'jira.cancel',
  'jira.dismiss',
  'sites.list',
  'sites.save',
  'sites.delete',
  'sites.match',
  'sites.check',
  'sites.check_cancel',
  'sites.login',
  'sites.login_close',
  'sites.status',
  'tasks.list',
  'tasks.save',
  'tasks.save_many',
  'tasks.delete',
  'tasks.extract',
  'tasks.snooze',
  'reminders.due',
  'reminders.ack',
  'reminders.status',
  'assistant.conversations',
  'assistant.history',
  'assistant.delete',
  'assistant.ask',
  // Stops an answer in progress; with stream, assistant.ask sends its parts on 'assistant:delta' (main.cjs
  // forwardEvent).
  'assistant.cancel',
  'briefing.get',
  'briefing.generate',
  // The admin page's own settings of the local API for AI tools; never reachable through that API.
  'api.get',
  'api.update',
  'api.rotate_token',
  'api.copy',
  'api.manual',
  'api.open_docs',
  // What to send when reporting a problem; also from the first-run screen, when the backend fails to start.
  'diagnostics.copy',
  'diagnostics.open_folder',
  // 지금 다시 시작 once a restore of a Wiki backup waits for the next start; the screens' own, never an AI tool's.
  'app.relaunch',
]);

// Whether an IPC message comes from the app's own screen: the main frame of the app's window (`window`, null once it
// has closed) while that frame shows the static export (agent://bundle/) or, in a source run, the development server
// at `devUrl`. A subframe, another window or webContents, a window or webContents already destroyed, a frame that has
// gone (Electron's senderFrame is then null) and a page of any other address are refused.
function trustedSender(event, { window, devUrl }) {
  if (!window || window.isDestroyed()) return false;
  const contents = window.webContents;
  if (event.sender !== contents || contents.isDestroyed()) return false;
  const url = event.senderFrame?.url;
  return (
    event.senderFrame === contents.mainFrame &&
    typeof url === 'string' &&
    url.startsWith(devUrl ? devUrl + '/' : BUNDLE)
  );
}

// Whether the screens may make this request: a message of their own, for a method of ALLOWED.
function allowedRequest(event, method, screen) {
  return trustedSender(event, screen) && ALLOWED.has(method);
}

module.exports = { ALLOWED, allowedRequest, trustedSender };
