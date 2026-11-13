const { TRASH_KINDS, allows } = require('./api-catalog.cjs');
const { WEB_ROUTINE } = require('./routines.cjs');

// The trash of what is deleted beside documents (backend/agent/trash.py) as the main process hands a request of it on
// to the backend: a page of the list, of one kind or all (trash.list), and the entries to restore or purge. Only these
// values go on, so that what decides which entries a caller meets there is the main process's, never a value of the
// request. The screens see and restore all of it. An AI tool (`access`) meets only the kinds the API deletes into the
// trash (only_kinds: TRASH_KINDS): what the chat's 대화 비우기 and the web records put there is the user's alone, as
// the chat and the web records are, so the backend leaves it out of what a tool lists, counts, restores and purges,
// one by one or all at once. A routine the tool meets as it meets the routines themselves (main.cjs
// checkRoutineAccess): one of the web records is none of its either (without_recipes), and one that reads this PC's
// files comes back only at a level that may have them read (allow_files; main.cjs also asks trash.routines about the
// sites a routine opens). Nothing here needs Electron, so tests/trash-request.test.cjs runs it under plain Node.js.
const LISTED = ['kind', 'limit', 'cursor'];

/**
 * The parameters the backend gets for a request of the trash.
 * @param {string} method trash.list, trash.restore or trash.purge
 * @param {Record<string, any>} params the request's own parameters
 * @param {string} [access] the access level of an AI tool; none for the screens
 */
function trashValues(method, params, access) {
  /** @type {Record<string, any>} */
  const values = {};
  if (method === 'trash.list') {
    for (const key of LISTED) if (params[key] !== undefined) values[key] = params[key];
  } else values.ids = params.ids;
  if (method === 'trash.purge') values.all = params.all;
  if (access) {
    values.only_kinds = [...TRASH_KINDS];
    values.without_recipes = [WEB_ROUTINE];
  }
  if (method === 'trash.restore') values.allow_files = !access || allows(access, 'files');
  return values;
}

module.exports = { trashValues };
