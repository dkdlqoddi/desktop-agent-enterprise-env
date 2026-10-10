import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { projectRoot, venvPython } from './python.mjs';

// THIRD_PARTY_NOTICES.md, written from what the app ships: the npm packages the static export can bundle
// (package-lock.json, with the license files in node_modules), the Python distributions the PyInstaller backend bundles
// (backend/.venv, through importlib.metadata), and fixed entries for what neither lock file lists (Electron, Chromium,
// Python, the PyInstaller bootloader) or what only Windows installs (pywin32). `--check` writes nothing and fails when
// the committed file differs from what it would write.
//
// The file must come out the same on Linux and Windows, from any checkout: entries are sorted, license texts get LF
// line breaks, Windows-only packages are written from fixed text, and everything else is read from files that are the
// same on every platform. The result goes through Prettier, which `npm run format` also runs over the file.

const require = createRequire(import.meta.url);
const OUTPUT = path.join(projectRoot, 'THIRD_PARTY_NOTICES.md');
const check = process.argv.includes('--check');

// Python distributions of the virtual environment that never reach the backend executable: the test runner and its
// plugins, PyInstaller with its own dependencies, the debugger and the coverage tool, the environment's installers
// (setuptools and packaging too: scripts/build-python.mjs keeps them out of the executable), and the backports and
// helpers some of them install only on Python 3.10, Windows or macOS. Their licenses are written here because the
// virtual environment of one platform lacks some of them.
const PYTHON_DEV = {
  altgraph: 'MIT',
  colorama: 'BSD-3-Clause',
  coverage: 'Apache-2.0',
  debugpy: 'MIT',
  exceptiongroup: 'MIT',
  iniconfig: 'MIT',
  macholib: 'MIT',
  packaging: 'Apache-2.0 OR BSD-2-Clause',
  pefile: 'MIT',
  pip: 'MIT',
  pluggy: 'MIT',
  pygments: 'BSD-2-Clause',
  pyinstaller: 'GPL-2.0-or-later WITH Bootloader-exception',
  'pyinstaller-hooks-contrib': 'GPL-2.0-or-later AND Apache-2.0',
  pytest: 'MIT',
  'pywin32-ctypes': 'BSD-3-Clause',
  setuptools: 'MIT',
  tomli: 'MIT',
};
// The backend's own distribution, installed in development mode.
const PROJECT = 'private-agent-backend';
// Runtime dependencies that only Windows installs (pyproject.toml's sys_platform markers): written from the license
// files of the parts the backend bundles (pythoncom, pywintypes, win32com, win32gui, win32con, win32timezone), so the
// notices read the same when generated on Linux. Check these texts when the pywin32 pin changes.
const PYWIN32_WIN32 = `Unless stated in the specific source file, this work is
Copyright (c) 1994-2008, Mark Hammond
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in
the documentation and/or other materials provided with the distribution.

Neither name of Mark Hammond nor the name of contributors may be used
to endorse or promote products derived from this software without
specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS \`\`AS
IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.`;
// com/License.txt differs from win32/License.txt only in its copyright line and in the names of its third clause.
const PYWIN32_COM = PYWIN32_WIN32.replace(
  '1994-2008, Mark Hammond',
  '1996-2008, Greg Stein and Mark Hammond.',
).replace('Neither name of Mark Hammond nor', 'Neither names of Greg Stein, Mark Hammond nor');
const WINDOWS_ONLY = {
  pywin32: {
    license: 'Python Software Foundation License',
    homepage: 'https://github.com/mhammond/pywin32',
    note: 'Windows 전용',
    texts: [PYWIN32_WIN32, PYWIN32_COM],
  },
};
// Pillow's wheels append the licenses of the image libraries they link in, and the Windows and Linux wheels link in
// different ones: only Pillow's own license, before the first appended section, reads the same everywhere. The Windows
// build ships the whole file (scripts/build-python.mjs copies Pillow's metadata), and the table says where.
const TRIM = {
  pillow: (text) => text.split(/\n(?=-{4,}\n|={5} .+ ={5}\n)/)[0],
};
const NOTES = {
  pillow:
    '포함한 이미지 라이브러리(libjpeg-turbo, libpng, zlib 등)의 라이선스는 백엔드 폴더의 `_internal/pillow-*.dist-info/licenses/LICENSE`에 있습니다',
};

// npm packages outside devDependencies that only `next build` runs: browser data for transpiling (caniuse-lite,
// baseline-browser-mapping), PostCSS with its dependencies, and Next's loader of .env files. None of them is in out/
// (checked against the export of Next 16.3); a package missing from this list is listed as shipped, which is the safe
// side.
const BUILD_ONLY = new Set([
  '@next/env',
  'baseline-browser-mapping',
  'caniuse-lite',
  'nanoid',
  'picocolors',
  'postcss',
  'source-map-js',
]);
// Type declarations without code.
const typesOnly = (name) => name.startsWith('@types/') || name === 'csstype';

function fail(message) {
  console.error(message);
  process.exit(1);
}

// pip's normalized form of a distribution name (PEP 503), as constraints.txt and the lists above write it.
const canonical = (name) => name.toLowerCase().replace(/[-_.]+/g, '-');

// LF line breaks, no trailing spaces, no blank lines around: the same text from any checkout or wheel.
function cleanText(text) {
  return text
    .replace(/^\uFEFF/, '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+$/, ''))
    .join('\n')
    .replace(/^\n+|\s+$/g, '');
}

const byName = (a, b) =>
  a.name.toLowerCase().localeCompare(b.name.toLowerCase(), 'en') ||
  a.version.localeCompare(b.version, 'en');

// --- npm ------------------------------------------------------------------------------------------------------------

const lock = JSON.parse(readFileSync(path.join(projectRoot, 'package-lock.json'), 'utf8'));

function homepageOf(manifest, name) {
  let url = manifest.homepage;
  if (!url && manifest.repository)
    url = typeof manifest.repository === 'string' ? manifest.repository : manifest.repository.url;
  if (!url) return `https://www.npmjs.com/package/${name}`;
  url = url
    .replace(/^git\+/, '')
    .replace(/^git:\/\//, 'https://')
    .replace(/^git@github\.com:/, 'https://github.com/')
    .replace(/^github:/, 'https://github.com/')
    .replace(/\.git$/, '')
    .replace(/#readme$/, '');
  return /^[\w.-]+\/[\w.-]+$/.test(url) ? `https://github.com/${url}` : url;
}

// The license files a package ships at its root: LICENSE, LICENCE, COPYING and NOTICE in any spelling.
function npmLicenseTexts(directory) {
  return readdirSync(directory)
    .filter((file) => /^(licen[cs]e|copying|notice)([-._].*)?$/i.test(file))
    .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase(), 'en'))
    .map((file) => cleanText(readFileSync(path.join(directory, file), 'utf8')))
    .filter(Boolean);
}

function npmPackages() {
  const shipped = [];
  const buildOnly = [];
  for (const [key, entry] of Object.entries(lock.packages)) {
    // The project itself, its development tools, and the optional packages: the SWC compiler and sharp with libvips,
    // which only `next build` runs and which npm installs only on their own platform.
    if (!key || entry.dev || entry.devOptional || entry.optional) continue;
    const name = key.slice(key.lastIndexOf('node_modules/') + 'node_modules/'.length);
    const directory = path.join(projectRoot, key);
    let manifest;
    try {
      manifest = JSON.parse(readFileSync(path.join(directory, 'package.json'), 'utf8'));
    } catch {
      fail(`node_modules에 ${name}이(가) 없습니다. npm ci를 실행한 뒤 다시 시도하세요.`);
    }
    if (manifest.version !== entry.version)
      fail(
        `node_modules의 ${name} ${manifest.version}이(가) package-lock.json의 ${entry.version}과 다릅니다. npm ci를 실행한 뒤 다시 시도하세요.`,
      );
    const item = {
      name,
      version: entry.version,
      license: entry.license ?? manifest.license ?? 'UNKNOWN',
      homepage: homepageOf(manifest, name),
      texts: npmLicenseTexts(directory),
    };
    if (BUILD_ONLY.has(name) || typesOnly(name)) buildOnly.push(item);
    else shipped.push(item);
  }
  for (const name of BUILD_ONLY)
    if (!buildOnly.some((item) => item.name === name))
      fail(
        `scripts/third-party-notices.mjs의 BUILD_ONLY에 있는 ${name}이(가) package-lock.json에 없습니다. 목록에서 빼세요.`,
      );
  return { shipped: shipped.sort(byName), buildOnly: buildOnly.sort(byName) };
}

function npmDevTools() {
  return Object.keys(lock.packages[''].devDependencies ?? {})
    .filter((name) => name !== 'electron') // Shipped: a runtime entry of its own.
    .map((name) => {
      const entry = lock.packages[`node_modules/${name}`];
      if (!entry)
        fail(`package-lock.json에 ${name}이(가) 없습니다. npm install로 잠금 파일을 갱신하세요.`);
      return { name, version: entry.version, license: entry.license ?? 'UNKNOWN' };
    })
    .sort(byName);
}

// --- Python ---------------------------------------------------------------------------------------------------------

// Every distribution of the virtual environment with its license metadata and license files, as JSON on one ASCII line,
// and the Chromium build the installed Playwright downloads. Run in isolated mode (-I): only the standard library.
const PYTHON = String.raw`
import importlib.metadata as md, json, sys

def texts(dist):
    found = []
    for entry in dist.metadata.get_all("License-File") or []:
        # Core metadata 2.4 keeps license files under licenses/, earlier versions beside METADATA.
        for name in ("licenses/" + entry, entry):
            text = dist.read_text(name)
            if text is not None:
                found.append(text)
                break
    if not found:
        for file in dist.files or []:
            parts = file.parts
            if len(parts) >= 2 and parts[0].endswith(".dist-info") and parts[-1].upper().startswith(("LICEN", "COPYING", "NOTICE")):
                found.append(file.read_text())
    return found

out = []
for dist in md.distributions():
    meta = dist.metadata
    out.append({
        "name": meta["Name"],
        "version": dist.version,
        "expression": meta.get("License-Expression"),
        "license": meta.get("License"),
        "classifiers": [c for c in meta.get_all("Classifier") or [] if c.startswith("License :: ")],
        "home": meta.get("Home-page"),
        "urls": meta.get_all("Project-URL") or [],
        "texts": texts(dist),
    })
browsers = json.loads(md.distribution("playwright").locate_file("playwright/driver/package/browsers.json").read_text(encoding="utf-8"))
chromium = next(item["browserVersion"] for item in browsers["browsers"] if item["name"] == "chromium")
json.dump({"distributions": out, "chromium": chromium}, sys.stdout)
`;

function venv() {
  if (!existsSync(venvPython))
    fail('Python 개발 환경이 없습니다. npm run setup:python을 먼저 실행하세요.');
  // The program goes in on the standard input, which spares it the quoting of a Windows command line.
  const result = spawnSync(venvPython, ['-I', '-'], {
    cwd: projectRoot,
    input: PYTHON,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.status !== 0)
    fail(
      `backend/.venv의 패키지 정보를 읽지 못했습니다.\n${result.error?.message ?? result.stderr}`,
    );
  return JSON.parse(result.stdout);
}

// constraints.txt as { canonical name: version }.
function constraints() {
  const pins = {};
  for (const line of readFileSync(
    path.join(projectRoot, 'backend', 'constraints.txt'),
    'utf8',
  ).split(/\r?\n/)) {
    const match = /^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)/.exec(line);
    if (match) pins[canonical(match[1])] = match[2];
  }
  return pins;
}

// An SPDX-like name from the metadata: License-Expression (core metadata 2.4), else a short License field, else the
// trove classifiers.
const CLASSIFIERS = {
  'License :: OSI Approved :: MIT License': 'MIT',
  'License :: OSI Approved :: Apache Software License': 'Apache-2.0',
  'License :: OSI Approved :: BSD License': 'BSD',
  'License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)': 'MPL-2.0',
  'License :: OSI Approved :: Python Software Foundation License': 'PSF-2.0',
  'License :: OSI Approved :: ISC License (ISCL)': 'ISC',
};
function pythonLicense(dist) {
  if (dist.expression) return dist.expression;
  const field = (dist.license ?? '').trim();
  if (field && !field.includes('\n') && field.length <= 40) return field.replace(/ License$/, '');
  const names = dist.classifiers.map(
    (classifier) => CLASSIFIERS[classifier] ?? classifier.split(' :: ').at(-1),
  );
  return names.length ? names.join(' AND ') : 'UNKNOWN';
}

const HOME_LABELS = ['homepage', 'home', 'repository', 'source', 'source code', 'code'];
function pythonHomepage(dist) {
  const urls = dist.urls.map((entry) => {
    const [label, ...rest] = entry.split(',');
    return { label: label.trim().toLowerCase(), url: rest.join(',').trim() };
  });
  for (const label of HOME_LABELS) {
    const found = urls.find((item) => item.label === label);
    if (found) return found.url;
  }
  return dist.home || urls[0]?.url || `https://pypi.org/project/${dist.name}/`;
}

function pythonPackages(installed, pins) {
  const shipped = [];
  for (const dist of installed.distributions) {
    const name = canonical(dist.name);
    if (name === PROJECT || Object.hasOwn(PYTHON_DEV, name) || Object.hasOwn(WINDOWS_ONLY, name))
      continue;
    if (!Object.hasOwn(pins, name))
      fail(
        `backend/.venv의 ${dist.name} ${dist.version}이(가) backend/constraints.txt에 없습니다. 런타임 의존성이면 고정하고, 아니면 가상 환경에서 지우거나 scripts/third-party-notices.mjs의 PYTHON_DEV에 넣으세요.`,
      );
    if (pins[name] !== dist.version)
      fail(
        `backend/.venv의 ${dist.name} ${dist.version}이(가) backend/constraints.txt의 ${pins[name]}과 다릅니다. npm run setup:python을 다시 실행하세요.`,
      );
    const trim = TRIM[name] ?? ((text) => text);
    shipped.push({
      name: dist.name,
      version: dist.version,
      license: pythonLicense(dist),
      homepage: pythonHomepage(dist),
      note: NOTES[name],
      texts: dist.texts.map((text) => cleanText(trim(cleanText(text)))).filter(Boolean),
    });
  }
  for (const [name, entry] of Object.entries(WINDOWS_ONLY)) {
    if (!pins[name]) fail(`backend/constraints.txt에 ${name}의 고정 버전이 없습니다.`);
    shipped.push({ name, version: pins[name], ...entry });
  }
  // Every pin is accounted for: shipped, a development tool, or Windows-only. A new runtime dependency whose
  // distribution is missing from the virtual environment stops here rather than being left out of the notices.
  for (const name of Object.keys(pins))
    if (!Object.hasOwn(PYTHON_DEV, name) && !shipped.some((item) => canonical(item.name) === name))
      fail(
        `backend/constraints.txt의 ${name}이(가) backend/.venv에 없습니다. npm run setup:python을 다시 실행하거나, 개발 도구라면 scripts/third-party-notices.mjs의 PYTHON_DEV에 넣으세요.`,
      );
  return shipped.sort(byName);
}

function pythonDevTools(pins) {
  return Object.keys(pins)
    .filter((name) => Object.hasOwn(PYTHON_DEV, name))
    .map((name) => ({ name, version: pins[name], license: PYTHON_DEV[name] }))
    .sort(byName);
}

// --- Runtimes -------------------------------------------------------------------------------------------------------

function runtimes(installed, pins) {
  const electron = lock.packages['node_modules/electron'];
  const pyinstaller = installed.distributions.find(
    (dist) => canonical(dist.name) === 'pyinstaller',
  );
  if (pyinstaller?.version !== pins.pyinstaller)
    fail(
      `backend/.venv의 PyInstaller(${pyinstaller?.version ?? '없음'})가 backend/constraints.txt의 ${pins.pyinstaller}과 다릅니다. npm run setup:python을 다시 실행하세요.`,
    );
  const playwright = pins.playwright;
  const python = readFileSync(path.join(projectRoot, '.python-version'), 'utf8').trim();
  return [
    {
      name: 'Electron',
      version: electron.version,
      license: 'MIT',
      homepage: 'https://www.electronjs.org/',
      note: '데스크톱 런타임. 앱 폴더의 `LICENSE.electron.txt`와 `LICENSES.chromium.html`에 Electron과 포함된 Chromium·Node.js 등의 고지가 있습니다',
      texts: npmLicenseTexts(path.join(projectRoot, 'node_modules', 'electron')),
    },
    {
      name: 'Chromium (Playwright 빌드)',
      version: installed.chromium,
      license: 'BSD-3-Clause 및 포함 구성 요소별 라이선스',
      homepage: 'https://www.chromium.org/',
      note: 'JIRA·Confluence 가져오기와 사이트 로그인에 쓰는 브라우저(앱의 `resources/browsers`). 브라우저의 `chrome://credits` 페이지에 전체 고지가 있습니다',
      texts: [],
    },
    {
      name: 'Node.js (Playwright 드라이버)',
      version: `Playwright ${playwright}에 포함`,
      license: 'MIT 및 포함 구성 요소별 라이선스',
      homepage: 'https://nodejs.org/',
      note: 'Playwright for Python이 브라우저를 제어할 때 실행합니다. 백엔드 폴더의 `_internal/playwright/driver/LICENSE`와 `_internal/playwright/driver/package/ThirdPartyNotices.txt`에 고지가 있습니다',
      texts: [],
    },
    {
      name: 'Python',
      version: python,
      license: 'PSF-2.0',
      homepage: 'https://www.python.org/',
      note: '백엔드 런타임(PyInstaller로 동봉). 라이선스 전문과 포함 구성 요소의 고지는 https://docs.python.org/3/license.html 에 있습니다',
      texts: [],
    },
    {
      name: 'PyInstaller 부트로더',
      version: pins.pyinstaller,
      license: 'GPL-2.0-or-later WITH Bootloader-exception',
      homepage: 'https://pyinstaller.org/',
      note: '백엔드 실행 파일. 예외 조항에 따라 동봉한 앱에는 GPL이 적용되지 않습니다. 함께 들어가는 런타임 훅은 Apache-2.0입니다',
      texts: pyinstaller.texts.map(cleanText).filter(Boolean),
    },
  ];
}

// --- Markdown -------------------------------------------------------------------------------------------------------

const cell = (text) =>
  String(text)
    .replace(/\|/g, '\\|')
    .replace(/\s*\n\s*/g, ' ');

function table(headers, rows) {
  return [
    `| ${headers.join(' | ')} |`,
    `| ${headers.map(() => '---').join(' | ')} |`,
    ...rows.map((row) => `| ${row.map(cell).join(' | ')} |`),
  ].join('\n');
}

function fence(text) {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
  const marks = '`'.repeat(Math.max(3, longest + 1));
  return `${marks}text\n${text}\n${marks}`;
}

// Each license text once, under the components it covers, in the order of the tables.
function licenseTexts(sections) {
  const groups = new Map();
  sections.forEach((items, section) =>
    items.forEach((item, index) => {
      const text = item.texts.join('\n\n---\n\n');
      if (!text) return;
      if (!groups.has(text)) groups.set(text, { order: [section, index], items: [] });
      groups.get(text).items.push(item);
    }),
  );
  return [...groups.entries()]
    .sort(([, a], [, b]) => a.order[0] - b.order[0] || a.order[1] - b.order[1])
    .map(([text, { items }]) => {
      const names = items.map((item) => `${item.name} ${item.version}`);
      const heading = names.length > 1 ? `${names[0]} 외 ${names.length - 1}개` : names[0];
      const covers = names.length > 1 ? `\n\n적용 대상: ${names.join(', ')}` : '';
      return `### ${heading}${covers}\n\n${fence(text)}`;
    })
    .join('\n\n');
}

function render() {
  const installed = venv();
  const pins = constraints();
  const runtime = runtimes(installed, pins);
  const python = pythonPackages(installed, pins);
  const npm = npmPackages();
  // The development dependencies and what only the build runs, each package once (@types/react is both).
  const npmTools = [...npmDevTools(), ...npm.buildOnly]
    .filter((item, index, all) => all.findIndex((other) => byName(other, item) === 0) === index)
    .sort(byName);
  const devTools = [
    ...npmTools.map((item) => ({ ...item, kind: 'npm' })),
    ...pythonDevTools(pins).map((item) => ({ ...item, kind: 'Python' })),
  ];

  return `# 서드파티 고지

Private Agent는 [MIT 라이선스](LICENSE)로 배포하며, 아래 오픈소스 구성 요소를 함께 배포하거나 빌드에 사용합니다. 각 구성 요소는 자신의 라이선스를 따릅니다. 고정된 버전은 \`package.json\`·\`package-lock.json\`과 \`backend/pyproject.toml\`·\`backend/constraints.txt\`에 있습니다.

<!-- 이 파일은 scripts/third-party-notices.mjs가 package-lock.json·node_modules와 backend/.venv에서 만듭니다. 직접 고치지 말고, 의존성을 바꾼 뒤 npm run notices를 실행하세요. CI는 npm run notices:check로 최신인지 확인합니다. -->

## 앱에 포함되는 구성 요소

### 런타임

<!-- 생성됨: 고정 항목(버전은 package-lock.json, backend/constraints.txt, .python-version과 Playwright에서 읽음) -->

${table(
  ['구성 요소', '버전', '라이선스', '설명'],
  runtime.map((item) => [
    `[${item.name}](${item.homepage})`,
    item.version,
    item.license,
    item.note,
  ]),
)}

### Python 패키지

백엔드 실행 파일(PyInstaller)에 들어가는 배포 패키지입니다.

<!-- 생성됨: backend/.venv의 배포 패키지에서 개발 도구를 뺀 목록과 Windows 전용 패키지 -->

${table(
  ['패키지', '버전', '라이선스', '홈페이지', '비고'],
  python.map((item) => [item.name, item.version, item.license, item.homepage, item.note ?? '']),
)}

### npm 패키지

화면(Next.js 정적 내보내기)에 번들될 수 있는 패키지입니다.

<!-- 생성됨: package-lock.json에서 개발용·선택 설치·빌드 전용 패키지를 뺀 목록 -->

${table(
  ['패키지', '버전', '라이선스', '홈페이지'],
  npm.shipped.map((item) => [item.name, item.version, item.license, item.homepage]),
)}

## 개발과 빌드에만 쓰는 도구

아래 도구는 개발·빌드·테스트에만 쓰이고 배포 파일에는 들어가지 않습니다. Next.js의 SWC 컴파일러(@next/swc-\\*)와 이미지 처리용 sharp·libvips(LGPL-3.0-or-later)처럼 플랫폼마다 따로 설치되는 빌드 도구도 마찬가지입니다.

<!-- 생성됨: package.json의 devDependencies, Next.js 빌드만 쓰는 패키지와 타입 선언, backend/constraints.txt의 개발 도구 -->

${table(
  ['도구', '종류', '버전', '라이선스'],
  devTools.map((item) => [item.name, item.kind, item.version, item.license]),
)}

## 라이선스 전문

앱에 포함되는 구성 요소의 라이선스 파일입니다. 같은 내용은 한 번만 싣고 적용 대상을 함께 적었습니다.

<!-- 생성됨: node_modules의 LICENSE·NOTICE 파일과 backend/.venv 배포 패키지의 License-File -->

${licenseTexts([runtime, python, npm.shipped])}
`;
}

const prettier = require('prettier');
const options = { ...(await prettier.resolveConfig(OUTPUT)), parser: 'markdown' };
const text = await prettier.format(render(), options);

if (check) {
  const current = existsSync(OUTPUT) ? readFileSync(OUTPUT, 'utf8').replace(/\r\n/g, '\n') : '';
  if (current !== text) {
    const before = current.split('\n');
    const after = text.split('\n');
    const line = after.findIndex((value, index) => value !== before[index]);
    fail(
      `THIRD_PARTY_NOTICES.md가 의존성과 맞지 않습니다(${line + 1}번째 줄부터 다름). npm run notices를 실행해 갱신한 뒤 커밋하세요.\n  현재: ${before[line] ?? '(없음)'}\n  생성: ${after[line] ?? '(없음)'}`,
    );
  }
  console.log('THIRD_PARTY_NOTICES.md가 최신입니다.');
} else {
  writeFileSync(OUTPUT, text);
  console.log(`THIRD_PARTY_NOTICES.md를 갱신했습니다.`);
}
