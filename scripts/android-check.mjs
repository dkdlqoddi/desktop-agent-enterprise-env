import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

// The Android app's checks in the local runner (npm run ci): its JVM unit tests, Android lint and the compilation of
// its instrumented tests, through the Gradle wrapper in android/. They need a JDK 17 or later and the Android SDK; a
// machine without them skips the checks and says what is missing, as the app is not every contributor's.

/** The Gradle tasks the runner runs. The instrumented tests need a device and are run by hand (android/README.md). */
export const ANDROID_TASKS = [
  ':app:testDebugUnitTest',
  ':app:lintDebug',
  ':app:compileDebugAndroidTestKotlin',
];

/**
 * The major version `java -version` reports ("17.0.20.1" is 17, "1.8.0_402" is 8), or 0 when it says none.
 * @param {string} output
 * @returns {number}
 */
export function javaMajor(output) {
  const match = /version "(\d+)(?:\.(\d+))?/.exec(output);
  if (!match) return 0;
  const first = Number(match[1]);
  return first === 1 && match[2] ? Number(match[2]) : first;
}

/**
 * The SDK folder local.properties names (sdk.dir, as Android Studio writes it), or ''.
 * @param {string} text
 * @returns {string}
 */
export function sdkDirOf(text) {
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*sdk\.dir\s*[=:]\s*(.+?)\s*$/.exec(line);
    // Android Studio escapes the path's colons and backslashes (C\:\\Users\\...).
    if (match) return match[1].replace(/\\(.)/g, '$1');
  }
  return '';
}

/**
 * @typedef {object} AndroidTools
 * @property {boolean} ok whether the checks can run
 * @property {string[]} missing what is missing, in sentences for the runner's output
 * @property {string} androidDir the Gradle project
 */

/**
 * What the Android checks need, looked for as Gradle looks for it: the JDK of JAVA_HOME (or the java on the PATH),
 * and the SDK of ANDROID_HOME, ANDROID_SDK_ROOT or android/local.properties.
 * @param {{ root: string, env?: NodeJS.ProcessEnv, exists?: (file: string) => boolean,
 *   read?: (file: string) => string, javaVersion?: (java: string) => string }} options
 * @returns {AndroidTools}
 */
export function androidTools({
  root,
  env = process.env,
  exists = existsSync,
  read = (file) => readFileSync(file, 'utf8'),
  javaVersion = probeJava,
}) {
  const androidDir = path.join(root, 'android');
  const missing = [];
  if (!exists(path.join(androidDir, 'gradlew'))) {
    missing.push('android/gradlew가 없습니다.');
    return { ok: false, missing, androidDir };
  }

  const java = env.JAVA_HOME
    ? path.join(env.JAVA_HOME, 'bin', process.platform === 'win32' ? 'java.exe' : 'java')
    : 'java';
  const major = javaMajor(javaVersion(java));
  if (major === 0) {
    missing.push('JDK를 찾지 못했습니다. JDK 17 이상을 설치하고 JAVA_HOME을 지정하세요.');
  } else if (major < 17) {
    missing.push(`JDK ${major}입니다. Android 빌드에는 JDK 17 이상이 필요합니다(JAVA_HOME).`);
  }

  const properties = path.join(androidDir, 'local.properties');
  const sdk =
    env.ANDROID_HOME ||
    env.ANDROID_SDK_ROOT ||
    (exists(properties) ? sdkDirOf(read(properties)) : '');
  if (!sdk) {
    missing.push(
      'Android SDK를 찾지 못했습니다. ANDROID_HOME을 지정하거나 android/local.properties에 sdk.dir을 적으세요.',
    );
  } else if (!exists(sdk)) {
    missing.push(`Android SDK 폴더가 없습니다: ${sdk}`);
  }
  return { ok: missing.length === 0, missing, androidDir };
}

/**
 * What `java -version` writes (to stderr), or '' when it cannot be run.
 * @param {string} java
 * @returns {string}
 */
function probeJava(java) {
  const result = spawnSync(java, ['-version'], { encoding: 'utf8', shell: false, timeout: 30_000 });
  if (result.error) return '';
  return `${result.stderr ?? ''}${result.stdout ?? ''}`;
}

/**
 * The wrapper's command line for the checks: gradlew.bat through cmd.exe on Windows (Node.js does not start a batch
 * file directly), ./gradlew elsewhere.
 * @param {string} androidDir
 * @returns {{ command: string, args: string[] }}
 */
export function gradleCommand(androidDir) {
  const args = ['--console=plain', ...ANDROID_TASKS];
  if (process.platform === 'win32') {
    return { command: 'cmd.exe', args: ['/d', '/s', '/c', 'gradlew.bat', ...args] };
  }
  return { command: path.join(androidDir, 'gradlew'), args };
}
