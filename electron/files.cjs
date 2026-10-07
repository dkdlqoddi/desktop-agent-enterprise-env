const fs = require('node:fs/promises');

const LOCKED = new Set(['EPERM', 'EACCES', 'EBUSY']);

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Puts a fully written temporary file in place of `target`. Windows refuses to replace a file that
// another program (antivirus, data protection or the search indexer) holds for a moment, so such a
// refusal is tried again for about three seconds.
async function replaceFile(temporary, target, attempts = 10) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fs.rename(temporary, target);
    } catch (error) {
      if (attempt >= attempts || !LOCKED.has(error.code)) throw error;
      await pause(50 * attempt);
    }
  }
}

// Reads a file that another program may hold for a moment, trying again for about three seconds
// like replaceFile.
async function readFileRetrying(file, attempts = 10) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fs.readFile(file, 'utf8');
    } catch (error) {
      if (attempt >= attempts || !LOCKED.has(error.code)) throw error;
      await pause(50 * attempt);
    }
  }
}

// Moves a damaged settings file aside as "<name>.corrupt-<time>", where what may still be recovered
// from it stays; its new path is returned.
async function setAside(file, attempts = 10) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const aside = `${file}.corrupt-${stamp}`;
  await replaceFile(file, aside, attempts);
  return aside;
}

// One of the app's JSON settings files: its object, or null when there is none yet. A file that
// holds no JSON object (cut short by a power loss, say) is set aside (setAside), so that saving
// again cannot overwrite what may still be recovered from it, and null is returned. A file that
// cannot be read even after waiting is an error: the caller must not save over it.
async function readJsonFile(file, attempts = 10) {
  let text;
  try {
    text = await readFileRetrying(file, attempts);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
  try {
    const value = JSON.parse(text);
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  } catch {
    /* Set aside below. */
  }
  await setAside(file, attempts);
  return null;
}

// Writes `text` in place of `file` so that a crash or power loss leaves either the old contents or
// the new ones, never a file cut short: a temporary file is flushed to the disk, then put in place.
async function writeFileSafely(file, text) {
  const temporary = file + '.tmp';
  // One left by an earlier crash would keep its own permissions.
  await fs.rm(temporary, { force: true });
  const handle = await fs.open(temporary, 'w', 0o600);
  try {
    await handle.writeFile(text, 'utf8');
    await handle.sync();
  } finally {
    await handle.close();
  }
  await replaceFile(temporary, file);
}

module.exports = { replaceFile, readFileRetrying, readJsonFile, setAside, writeFileSafely };
