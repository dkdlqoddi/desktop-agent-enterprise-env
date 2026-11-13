const { allows } = require('./api-catalog.cjs');
const { WEB_ROUTINE } = require('./routines.cjs');

// The trash of what is deleted beside documents (backend/agent/trash.py) as the main process hands a request of it on
// to the backend: a page of the list, of one kind or all (trash.list), and the entries to restore or purge. Only these
// values go on, so that what decides which entries a caller meets there is the main process's, never a value of the
// request. The screens see and restore all of it. An AI tool (`access`) meets a routine there as it meets the routines
// themselves (main.cjs checkRoutineAccess): one of the web records is none of its, so the backend leaves those out of
// what it lists, restores and purges (without_recipes), and a routine that reads this PC's files comes back only at a
// level that may have them read (allow_files; main.cjs also asks trash.routines about the sites a routine opens).
// Nothing here needs Electron, so tests/trash-request.test.cjs runs it under plain Node.js.
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
  if (access) values.without_recipes = [WEB_ROUTINE];
  if (method === 'trash.restore') values.allow_files = !access || allows(access, 'files');
  return values;
}

module.exports = { trashValues };
