const path = require('node:path');

// How to start the app again once this one has closed, for a start that came while it was closing (second-instance):
// as the program that start was (this app's, perhaps of another build in its own folder), or as this one when it names
// another program. The packaged app starts without arguments (a demo's ask for an app of their own and never come
// here); a source run keeps its own, the app's folder among them.
function relaunchOptions(started, { execPath, packaged }) {
  const own = path.win32.basename(execPath).toLowerCase();
  const program =
    typeof started?.execPath === 'string' &&
    (path.isAbsolute(started.execPath) || path.win32.isAbsolute(started.execPath)) &&
    path.win32.basename(started.execPath).toLowerCase() === own
      ? started.execPath
      : execPath;
  const args =
    !packaged &&
    Array.isArray(started?.args) &&
    started.args.every((value) => typeof value === 'string')
      ? started.args
      : [];
  return { execPath: program, args };
}

module.exports = { relaunchOptions };
