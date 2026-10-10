const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// The screens reach the backend through one channel the main process guards with its ALLOWED set
// (electron/trusted-sender.cjs), and a method left out of it fails only when a user meets it. These tests find every
// method the screens call and check that each is allowed, and that each allowed method is handled: by the main process
// itself, or by the backend's dispatch table.
const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const generator = import('../scripts/generate-methods.mjs');

// The methods of the backend's dispatch table, read from its source rather than by starting a backend: the keys of the
// dicts named `methods` (Service.dispatch, and the tables Assistant, Schedules and Routines merge into it) whose value
// is a handler (`self.…` or a lambda), as `"wiki.save": self.wiki_save`. The keys are plain string literals.
function backendMethods() {
  const names = new Set();
  const folder = path.join(root, 'backend', 'agent');
  for (const file of fs.readdirSync(folder).filter((name) => name.endsWith('.py'))) {
    const lines = fs.readFileSync(path.join(folder, file), 'utf8').split('\n');
    lines.forEach((line, start) => {
      const open = /^(\s*)(?:self\.)?methods = \{/.exec(line);
      if (!open) return;
      for (let index = start; index < lines.length; index++) {
        for (const match of lines[index].matchAll(
          /"([a-z_]+(?:\.[a-z_]+)?)"\s*:\s*(?:self\.|lambda\b)/g,
        ))
          names.add(match[1]);
        if (index > start && new RegExp(`^${open[1]}\\}`).test(lines[index])) break;
      }
    });
  }
  return names;
}

// The methods the main process answers itself before it hands a request to the backend (handle() and apiRequest()):
// those compared with `method`, listed for `.includes(method)`, or of a prefix it takes whole (`api.`, `app.`).
function mainMethods() {
  const source = read('electron/main.cjs');
  const names = new Set();
  for (const match of source.matchAll(/[^.\w]method === '([^']+)'/g)) names.add(match[1]);
  for (const match of source.matchAll(/\[([^\]]+)\]\.includes\(method\)/g))
    for (const name of match[1].matchAll(/'([^']+)'/g)) names.add(name[1]);
  const prefixes = [...source.matchAll(/method\.startsWith\('([^']+)'\)/g)].map(
    (match) => match[1],
  );
  return {
    has: (method) => names.has(method) || prefixes.some((prefix) => method.startsWith(prefix)),
  };
}

// Every method the screens ask for, as TypeScript checks the code: each string written where a Method is expected
// (api(), useQuery(), a list of methods), templates such as `${kind}.start` by the methods they make, and a variable
// narrowed to a few methods by those methods. A call that hands on any Method (the query cache asking for what it was
// given) names no method of its own.
function screenMethods() {
  const config = ts.readConfigFile(path.join(root, 'tsconfig.json'), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
  const inSrc = (file) => path.relative(path.join(root, 'src'), file).split(path.sep)[0] !== '..';
  const program = ts.createProgram(parsed.fileNames.filter(inSrc), {
    ...parsed.options,
    noEmit: true,
    incremental: false,
  });
  const checker = program.getTypeChecker();
  const generated = program.getSourceFile(
    path.join(root, 'src', 'lib', 'api', 'methods.generated.ts'),
  );
  const alias = checker
    .getExportsOfModule(checker.getSymbolAtLocation(generated))
    .find((symbol) => symbol.name === 'Method');
  const all = checker.getDeclaredTypeOfSymbol(alias);
  const methods = new Set(all.types.map((type) => type.value));
  const literals = (type) => {
    const members = type.isUnion() ? type.types : [type];
    return members.every((member) => member.isStringLiteral())
      ? members.map((member) => member.value)
      : null;
  };
  // A type that takes any Method: the parameter of api(), useQuery() and the like.
  const takesMethod = (type) => {
    const values = type && literals(checker.getNonNullableType(type));
    return (
      !!values && values.length >= methods.size && [...methods].every((m) => values.includes(m))
    );
  };
  const found = new Map();
  const add = (values, node) => {
    const source = node.getSourceFile();
    const line = source.getLineAndCharacterOfPosition(node.getStart()).line + 1;
    for (const value of values) found.set(value, `${path.relative(root, source.fileName)}:${line}`);
  };
  const visit = (node) => {
    if (
      (ts.isStringLiteral(node) ||
        ts.isNoSubstitutionTemplateLiteral(node) ||
        ts.isTemplateExpression(node)) &&
      takesMethod(checker.getContextualType(node))
    ) {
      const values = literals(checker.getTypeAtLocation(node));
      assert.ok(values, `${node.getText()}: not a known method`);
      add(values, node);
    }
    if (ts.isCallExpression(node) && node.arguments.length) {
      const parameter = checker.getResolvedSignature(node)?.parameters[0];
      if (parameter && takesMethod(checker.getTypeOfSymbolAtLocation(parameter, node))) {
        const values = literals(checker.getTypeAtLocation(node.arguments[0]));
        if (values && values.length < methods.size) add(values, node.arguments[0]);
      }
    }
    ts.forEachChild(node, visit);
  };
  for (const source of program.getSourceFiles()) if (inSrc(source.fileName)) visit(source);
  return found;
}

test('methods.generated.ts is what npm run generate:methods writes', async () => {
  const { render, OUTPUT } = await generator;
  const committed = fs.readFileSync(OUTPUT, 'utf8').replace(/\r\n/g, '\n');
  assert.equal(
    committed,
    await render(),
    'src/lib/api/methods.generated.ts가 최신이 아닙니다. npm run generate:methods를 실행하세요.',
  );
});

test('every method the screens call is allowed by the main process', async () => {
  const { allowedMethods } = await generator;
  const allowed = new Set(allowedMethods());
  const found = screenMethods();
  // The screens call far more than a handful of methods; finding few means the search above went wrong. Those asked
  // through a template, useQuery(), a list of methods or a narrowed variable must be among them.
  assert.ok(found.size > 80, `found only ${found.size} methods`);
  for (const method of [
    'office.start',
    'jira.dismiss',
    'image.capabilities',
    'wiki.graph',
    'prompts.list',
    'diagnostics.open_folder',
    'analysis.cancel',
    'jobs.status',
  ])
    assert.ok(found.has(method), `${method} was not found`);
  const missing = [...found].filter(([method]) => !allowed.has(method));
  assert.deepEqual(
    missing,
    [],
    'electron/trusted-sender.cjs ALLOWED에 없는 메서드를 화면이 호출합니다.',
  );
});

test('every allowed method is handled by the main process or the backend', async () => {
  const { allowedMethods } = await generator;
  const backend = backendMethods();
  const main = mainMethods();
  // The static reads must still see the tables they read.
  for (const known of ['wiki.save', 'tasks.list', 'schedules.run', 'routines.catalog'])
    assert.ok(backend.has(known), `the backend's ${known} was not found`);
  assert.ok(main.has('jobs.status') && main.has('api.get') && main.has('storage.choose'));
  const unhandled = allowedMethods().filter((method) => !backend.has(method) && !main.has(method));
  assert.deepEqual(
    unhandled,
    [],
    'ALLOWED에 있지만 메인 프로세스도 백엔드도 처리하지 않는 메서드입니다.',
  );
});
