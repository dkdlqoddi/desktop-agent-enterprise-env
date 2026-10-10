const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { readFileRetrying, setAside, writeFileSafely } = require('./files.cjs');

// How long the check of the saved Wiki folder may take. Windows can spend a minute on a network drive that is not
// connected before it gives up; the first-run screen says so sooner instead of the window waiting on it.
const CHECK_LIMIT = 10_000;

const MISSING =
  '이전에 사용한 Wiki 저장소를 찾을 수 없습니다. 드라이브 연결과 폴더 위치를 확인한 뒤 저장 위치를 선택해 주세요.';
const DAMAGED = 'Wiki 저장 위치 설정 파일(storage.json)이 손상되었습니다.';
const KEPT = '손상된 파일은 저장 위치를 고를 때 storage.json.corrupt-<시각>으로 옮겨 보관합니다.';

// Settles as `promise` does, or fails with the code ETIMEDOUT after `limit` ms; the operation itself goes on unseen.
function within(promise, limit) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' })),
      limit,
    );
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

async function exists(file, limit = CHECK_LIMIT) {
  try {
    return (await within(fs.stat(file), limit)).isFile();
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return false;
    throw error;
  }
}

const usable = (value) =>
  typeof value === 'string' && !value.includes('\0') && path.isAbsolute(value);

// What a damaged storage.json still says of the Wiki folder: `paths`, the whole path it names (as JSON wrote it, or
// typed by hand with single backslashes, as Explorer shows a path), and `partial`, the start of a path cut short.
function remains(text) {
  const whole = /"directory"\s*:\s*"((?:[^"\\\r\n]|\\.)*)"/.exec(text);
  if (whole) {
    let decoded = null;
    try {
      decoded = JSON.parse(`"${whole[1]}"`);
    } catch {
      /* Not JSON's escapes: a path typed by hand. */
    }
    const named = [decoded, whole[1]].find(usable);
    return { paths: named ? [named] : [], partial: '' };
  }
  const cut = /"directory"\s*:\s*"((?:[^"\\\r\n]|\\.)*)/.exec(text);
  return { paths: [], partial: cut ? cut[1].replace(/\\\\/g, '\\').slice(0, 260) : '' };
}

class StorageManager {
  // `remember: false` (a demo recording) opens a Wiki for this run only: nothing is read from or written to
  // storage.json, and no earlier version's data is offered for copying.
  constructor({
    configDirectory,
    suggestedPath,
    start,
    stop,
    migrate,
    remember = true,
    checkLimit = CHECK_LIMIT,
  }) {
    this.remember = remember;
    this.configDirectory = configDirectory;
    this.file = path.join(configDirectory, 'storage.json');
    this.legacyFile = path.join(configDirectory, 'wiki.db');
    this.suggestedPath = suggestedPath;
    this.start = start;
    this.stop = stop;
    this.migrate = migrate;
    this.checkLimit = checkLimit;
    this.initialized = false;
    this.directory = '';
    this.error = '';
    this.busy = false;
    this.hasLegacy = false;
    // storage.json holds no usable folder: it stays as it is until a folder is chosen, and is then set aside.
    this.damaged = false;
    // storage.json could not be read even after waiting: it is never written over in this run.
    this.unreadable = '';
  }

  async load() {
    if (!this.remember) return this.info();
    this.hasLegacy = await exists(this.legacyFile, this.checkLimit).catch(() => false);
    if (this.hasLegacy) this.suggestedPath = this.configDirectory;
    try {
      const directory = await this.saved();
      if (directory === null) return this.info(); // The first run.
      this.directory = directory;
      this.suggestedPath = directory;
      await this.check(directory);
      await this.start(directory);
      this.initialized = true;
    } catch (error) {
      this.error = error.message;
      if (!this.initialized) await this.stop();
    }
    return this.info();
  }

  // The Wiki folder storage.json names, or null before the first choice. A file that cannot be read, or names no
  // usable folder, is an error that the first-run screen shows.
  async saved() {
    let text;
    try {
      // Antivirus may hold the file for a moment.
      text = await readFileRetrying(this.file);
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      this.unreadable = `Wiki 저장 위치 설정 파일(storage.json)을 읽지 못했습니다(${error.code || error.message}). 백신 등 다른 프로그램이 파일을 잡고 있을 수 있으니 잠시 후 앱을 다시 실행해 주세요. 저장된 위치를 덮어쓰지 않도록 이번 실행에서는 저장 위치를 고를 수 없습니다.`;
      throw new Error(this.unreadable, { cause: error });
    }
    let record = null;
    try {
      // A byte order mark, as Notepad may write when the folder is changed by hand.
      record = JSON.parse(text.replace(/^\uFEFF/, ''));
    } catch {
      /* Damaged: below. */
    }
    if (record && typeof record === 'object' && usable(record.directory)) return record.directory;
    this.damaged = true;
    throw new Error(await this.recover(text));
  }

  // What the first-run screen offers after a damaged storage.json, rather than the default folder unasked: the folder
  // the file still names, a Wiki in the suggested folder, or nothing, so that the user chooses.
  async recover(text) {
    const { paths, partial } = remains(text);
    for (const candidate of paths)
      if (await this.holdsWiki(candidate)) {
        this.suggestedPath = candidate;
        return `${DAMAGED} 남은 내용에서 이전에 쓰던 Wiki 폴더를 찾아 아래에 넣었습니다. 맞으면 ‘이 위치에 저장하고 시작’을 눌러 주세요. ${KEPT}`;
      }
    if (paths.length) {
      this.suggestedPath = paths[0];
      return `${DAMAGED} 남은 내용의 폴더를 아래에 넣었지만 그 폴더에서 Wiki(wiki.db)를 찾지 못했습니다. 드라이브 연결을 확인하거나 이전에 쓰던 Wiki 폴더를 선택해 주세요. ${KEPT}`;
    }
    if (await this.holdsWiki(this.suggestedPath))
      return `${DAMAGED} 기본 저장 위치에 있는 Wiki를 아래에 넣었습니다. 이전에 다른 폴더를 썼다면 그 폴더를 선택해 주세요. ${KEPT}`;
    this.suggestedPath = '';
    return `${DAMAGED} 이전에 쓰던 Wiki 폴더를 알 수 없습니다${partial ? `(남은 경로의 앞부분: ${partial}…)` : ''}. 그 폴더(wiki.db가 있는 폴더)를 선택하거나, 새로 시작하려면 빈 폴더를 선택해 주세요. ${KEPT}`;
  }

  holdsWiki(directory) {
    return exists(path.join(directory, 'wiki.db'), this.checkLimit).catch(() => false);
  }

  // Whether the saved folder still holds the Wiki, as far as can be told in time.
  async check(directory) {
    let found;
    try {
      found = await exists(path.join(directory, 'wiki.db'), this.checkLimit);
    } catch (error) {
      throw new Error(
        error.code === 'ETIMEDOUT'
          ? `저장된 Wiki 폴더가 ${Math.round(this.checkLimit / 1000)}초 안에 응답하지 않습니다. 네트워크 드라이브 연결을 확인한 뒤 앱을 다시 실행하거나 ‘이 위치에 저장하고 시작’을 눌러 주세요.`
          : `저장된 Wiki 폴더를 확인하지 못했습니다(${error.code || error.message}). 드라이브 연결과 폴더 권한을 확인한 뒤 저장 위치를 선택해 주세요.`,
        { cause: error },
      );
    }
    if (!found) throw new Error(MISSING);
  }

  info() {
    return {
      initialized: this.initialized,
      directory: this.directory,
      suggested_path: this.suggestedPath,
      has_legacy_data: this.hasLegacy,
      legacy_path: this.hasLegacy ? this.configDirectory : '',
      error: this.error,
    };
  }

  async initialize(value) {
    if (this.initialized) throw new Error('이미 저장소가 설정되어 있습니다.');
    if (this.busy) throw new Error('저장소를 준비하고 있습니다. 잠시 기다려 주세요.');
    if (this.unreadable) throw new Error(this.unreadable);
    if (typeof value !== 'string' || value.length > 32767 || value.includes('\0'))
      throw new Error('저장할 폴더의 전체 경로를 입력해 주세요.');
    const selected = value.trim().replace(/^"(.*)"$/, '$1');
    if (!path.isAbsolute(selected)) throw new Error('저장할 폴더의 전체 경로를 입력해 주세요.');
    this.busy = true;
    try {
      await fs.mkdir(selected, { recursive: true });
      const directory = await fs.realpath(selected);
      const probe = path.join(directory, `.private-agent-write-${randomUUID()}`);
      await fs.writeFile(probe, '', { flag: 'wx', mode: 0o600 });
      await fs.unlink(probe);
      const database = path.join(directory, 'wiki.db');
      if (this.hasLegacy && path.resolve(this.legacyFile) !== path.resolve(database)) {
        // Resolving the legacy directory avoids copying onto itself through a symlink.
        const legacyDirectory = await fs.realpath(this.configDirectory);
        if (legacyDirectory !== directory) {
          if (await exists(database))
            throw new Error(
              '선택한 폴더에 wiki.db가 이미 있습니다. 기존 데이터와 병합하지 않습니다. 비어 있는 폴더 또는 이전 저장 위치를 선택해 주세요.',
            );
          await this.migrate(this.legacyFile, database);
        }
      }
      await this.start(directory);
      if (this.remember) await this.persist(directory);
      this.directory = directory;
      this.initialized = true;
      this.error = '';
      return this.info();
    } catch (error) {
      await this.stop();
      if (error.code)
        throw new Error(
          '저장 폴더를 준비하지 못했습니다. 폴더 권한과 디스크 공간을 확인해 주세요.',
          { cause: error },
        );
      throw error;
    } finally {
      this.busy = false;
    }
  }

  // storage.json names the chosen folder from now on, written whole or not at all. A damaged one is set aside first,
  // so what it held stays.
  async persist(directory) {
    try {
      if (this.damaged)
        await setAside(this.file).catch((error) => {
          if (error.code !== 'ENOENT') throw error;
        });
      this.damaged = false;
      await writeFileSafely(this.file, JSON.stringify({ version: 1, directory }, null, 2));
    } catch {
      throw new Error(
        '선택한 Wiki 폴더를 앱 설정 폴더의 storage.json에 저장하지 못했습니다. 백신 등이 파일을 잡고 있을 수 있으니 잠시 후 다시 시도해 주세요.',
      );
    }
  }
}

module.exports = { StorageManager, CHECK_LIMIT };
