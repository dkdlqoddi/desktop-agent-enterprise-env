const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

// The local runner's Android checks (scripts/android-check.mjs) run only where Gradle can: with a JDK 17 or later and
// the Android SDK. Elsewhere they are skipped, and the runner says what is missing instead of failing.
const check = import('../scripts/android-check.mjs');
const root = path.join(path.sep, 'repo');
const android = path.join(root, 'android');

/** The files of a machine, as `exists` and `read` see them. */
function machine(files) {
  return {
    exists: (file) => Object.prototype.hasOwnProperty.call(files, file),
    read: (file) => files[file],
  };
}

const java17 = () => 'openjdk version "17.0.20.1" 2025-10-21\nOpenJDK Runtime Environment';

test('java -version is read for its major version', async () => {
  const { javaMajor } = await check;
  assert.equal(javaMajor('openjdk version "17.0.20.1" 2025-10-21'), 17);
  assert.equal(javaMajor('java version "21" 2023-09-19'), 21);
  assert.equal(javaMajor('java version "1.8.0_402"'), 8);
  assert.equal(javaMajor(''), 0);
});

test("local.properties' sdk.dir is read as Android Studio writes it", async () => {
  const { sdkDirOf } = await check;
  assert.equal(sdkDirOf('# a comment\nsdk.dir=/home/user/Android/Sdk\n'), '/home/user/Android/Sdk');
  assert.equal(
    sdkDirOf('sdk.dir=C\\:\\\\Users\\\\user\\\\Android\\\\Sdk'),
    'C:\\Users\\user\\Android\\Sdk',
  );
  assert.equal(sdkDirOf('ndk.dir=/x\n'), '');
});

test('with a JDK 17 and the SDK the checks run', async () => {
  const { androidTools } = await check;
  const sdk = path.join(path.sep, 'sdk');
  const files = machine({ [path.join(android, 'gradlew')]: '', [sdk]: '' });
  const tools = androidTools({ root, env: { ANDROID_HOME: sdk }, javaVersion: java17, ...files });
  assert.deepEqual(tools, { ok: true, missing: [], androidDir: android });

  // The SDK of local.properties serves as well as ANDROID_HOME.
  const properties = path.join(android, 'local.properties');
  const local = machine({
    [path.join(android, 'gradlew')]: '',
    [properties]: `sdk.dir=${sdk}\n`,
    [sdk]: '',
  });
  assert.equal(androidTools({ root, env: {}, javaVersion: java17, ...local }).ok, true);
});

test('without a JDK 17 or the SDK the checks are skipped, saying what is missing', async () => {
  const { androidTools } = await check;
  const gradlew = { [path.join(android, 'gradlew')]: '' };
  const none = androidTools({ root, env: {}, javaVersion: () => '', ...machine(gradlew) });
  assert.equal(none.ok, false);
  assert.equal(none.missing.length, 2);
  assert.match(none.missing[0], /JDK를 찾지 못했습니다/);
  assert.match(none.missing[1], /Android SDK를 찾지 못했습니다/);

  const old = androidTools({
    root,
    env: { ANDROID_HOME: path.join(path.sep, 'gone') },
    javaVersion: () => 'java version "1.8.0_402"',
    ...machine(gradlew),
  });
  assert.deepEqual(
    old.missing.map((reason) => reason.split('.')[0]),
    ['JDK 8입니다', `Android SDK 폴더가 없습니다: ${path.join(path.sep, 'gone')}`],
  );
});

test('the JDK is the one JAVA_HOME names', async () => {
  const { androidTools } = await check;
  const asked = [];
  const sdk = path.join(path.sep, 'sdk');
  androidTools({
    root,
    env: { JAVA_HOME: path.join(path.sep, 'jdk'), ANDROID_HOME: sdk },
    javaVersion: (java) => {
      asked.push(java);
      return java17();
    },
    ...machine({ [path.join(android, 'gradlew')]: '', [sdk]: '' }),
  });
  assert.equal(asked.length, 1);
  assert.equal(path.dirname(asked[0]), path.join(path.sep, 'jdk', 'bin'));
});

test("the runner runs the unit tests, lint and the instrumented tests' compilation", async () => {
  const { ANDROID_TASKS, gradleCommand } = await check;
  assert.deepEqual(ANDROID_TASKS, [
    ':app:testDebugUnitTest',
    ':app:lintDebug',
    ':app:compileDebugAndroidTestKotlin',
  ]);
  const { command, args } = gradleCommand(android);
  if (process.platform === 'win32') {
    assert.equal(command, 'cmd.exe');
    assert.deepEqual(args.slice(0, 4), ['/d', '/s', '/c', 'gradlew.bat']);
  } else {
    assert.equal(command, path.join(android, 'gradlew'));
  }
  assert.deepEqual(args.slice(-3), ANDROID_TASKS);
});
