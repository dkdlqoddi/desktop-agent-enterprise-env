const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JobProgress, JOB_KINDS } = require('../electron/job-progress.cjs');

const job = (status, processed = 0, extra = {}) => ({
  id: 'job-1',
  status,
  total: 3,
  processed,
  results: [],
  ...extra,
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Waits until `condition` holds, failing after a while: the follower's timers run on their own.
async function waitFor(condition, message = 'condition') {
  for (const start = Date.now(); !condition(); await sleep(2))
    if (Date.now() - start > 3000) assert.fail(`timed out waiting for ${message}`);
}

// A backend whose jobs and routines a test changes as it goes: `state[kind]` is what `${kind}.status` answers (null
// when absent; Confluence and JIRA wrap it in { job }), `state.routines` what routines.list answers.
function setup(state = {}, options = {}) {
  const calls = [];
  const sent = [];
  const rpc = async (method) => {
    calls.push(method);
    if (state.fail) throw new Error('busy');
    if (method === 'routines.list') return state.routines ?? [];
    const kind = method.split('.')[0];
    const value = state[kind] ?? null;
    return kind === 'confluence' || kind === 'jira' ? { job: value } : value;
  };
  const progress = new JobProgress({
    rpc,
    send: (event) => sent.push(event),
    interval: 5,
    routineInterval: 10,
    retry: 5,
    retries: 3,
    ...options,
  });
  // The events of jobs alone: once no job runs the routines are looked at too.
  const jobs = () => sent.filter((event) => event.kind !== 'routines');
  return { progress, calls, sent, jobs, state };
}

test('a started job is followed until it ends, and only its changes are sent', async () => {
  const { progress, jobs, state, calls } = setup({ office: job('running', 0) });
  progress.observe('office.start');
  await waitFor(() => jobs().length === 1, 'the first event');
  assert.equal(jobs()[0].kind, 'office');
  assert.equal(jobs()[0].job.processed, 0);
  // Reads that find the job as it was send nothing.
  const reads = calls.length;
  await waitFor(() => calls.length >= reads + 14, 'two more reads');
  assert.equal(jobs().length, 1);
  state.office = job('running', 1, { results: [{ path: 'a.docx' }] });
  await waitFor(() => jobs().length === 2, 'the change');
  assert.equal(jobs()[1].job.processed, 1);
  assert.ok(jobs()[1].seq > jobs()[0].seq);
  state.office = job('completed', 3, { results: [{ path: 'a.docx' }] });
  await waitFor(() => !progress.following, 'the end of the following');
  assert.equal(jobs().length, 3);
  assert.equal(jobs()[2].job.status, 'completed');
  // Nothing runs any more: no more reads are asked.
  const asked = calls.length;
  await sleep(40);
  assert.equal(calls.length, asked);
  progress.stop();
});

test('every kind is read, Confluence and JIRA unwrapped, and a request that changes no job is ignored', async () => {
  const { progress, calls, jobs, state } = setup({ jira: job('login') });
  progress.observe('wiki.save');
  progress.observe('office.status');
  await sleep(20);
  assert.deepEqual(calls, []);
  progress.observe('jira.cancel');
  await waitFor(() => jobs().length === 1, 'the JIRA job');
  for (const kind of JOB_KINDS) assert.ok(calls.includes(`${kind}.status`), kind);
  // The screens knew of no job yet: only the JIRA job, unwrapped, is news to them.
  assert.equal(jobs()[0].kind, 'jira');
  assert.deepEqual(jobs()[0].job, job('login'));
  // Cleared, it is sent as gone.
  state.jira = null;
  await waitFor(() => jobs().length === 2, 'the cleared job');
  assert.equal(jobs()[1].job, null);
  progress.stop();
});

test('a snapshot is numbered, and a later read sends only what changed since', async () => {
  const { progress, jobs, calls, state } = setup({
    analysis: job('running', 1, { queue: ['b'] }),
  });
  const snapshot = await progress.snapshot();
  assert.equal(snapshot.jobs.analysis.processed, 1);
  assert.equal(snapshot.jobs.office, null);
  // The snapshot follows the running analysis; reads that find it as it was send no job.
  await waitFor(() => calls.length >= JOB_KINDS.length * 3, 'two more reads');
  assert.deepEqual(jobs(), []);
  state.analysis = job('completed', 3, { queue: [] });
  await waitFor(() => jobs().length === 1, 'the change');
  assert.equal(jobs()[0].kind, 'analysis');
  assert.ok(jobs()[0].seq > snapshot.seq);
  progress.stop();
});

test('a state read earlier never replaces one read later, whichever arrives first', async () => {
  const pending = [];
  // Every read waits until the test answers it.
  const progress = new JobProgress({
    rpc: (method) => new Promise((resolve) => pending.push({ method, resolve })),
    send: () => {},
    interval: 60_000,
  });
  const answer = (read, office) =>
    pending
      .slice(read * JOB_KINDS.length, (read + 1) * JOB_KINDS.length)
      .forEach(({ method, resolve }) =>
        resolve(method === 'office.status' ? office : method.startsWith('confluence') ? {} : null),
      );
  const first = progress.snapshot();
  const second = progress.snapshot();
  answer(1, job('running', 2));
  answer(0, job('running', 1));
  const [older, newer] = await Promise.all([first, second]);
  assert.ok(newer.seq > older.seq);
  assert.equal(progress.latest.get('office').seq, newer.seq);
  assert.match(progress.latest.get('office').text, /"processed":2/);
  progress.stop();
});

test("while a routine's run is queued or running, its imports are looked for and the routines sent", async () => {
  const routine = { id: 'r1', name: '매일 확인', queued: true, running: false, step: '' };
  const { progress, sent, state } = setup({ routines: [routine] });
  const routines = () => sent.filter((event) => event.kind === 'routines');
  progress.observe('routines.run');
  await waitFor(() => routines().length === 1, 'the queued run');
  assert.equal(routines()[0].routines[0].queued, true);
  // The run starts and its first import appears: both are sent as they come.
  state.routines = [{ ...routine, queued: false, running: true, step: '페이지를 읽는 중' }];
  state.confluence = job('running', 0);
  await waitFor(
    () => sent.some((event) => event.kind === 'confluence' && event.job?.status === 'running'),
    'the import',
  );
  await waitFor(() => routines().at(-1).routines[0].step === '페이지를 읽는 중', 'the step');
  state.confluence = job('completed', 1);
  state.routines = [{ ...routine, queued: false, running: false, step: '' }];
  await waitFor(() => !progress.following, 'the end of the run');
  assert.equal(routines().at(-1).routines[0].running, false);
  progress.stop();
});

test('a backend that keeps failing is given up after the retries; a later request follows again', async () => {
  const { progress, calls, state } = setup({ fail: true, office: job('running') });
  progress.follow();
  await waitFor(() => !progress.following, 'giving up');
  const asked = calls.length;
  await sleep(30);
  assert.equal(calls.length, asked);
  state.fail = false;
  progress.observe('office.cancel');
  await waitFor(() => calls.length > asked, 'a new read');
  progress.stop();
});

test('a check that is not ready waits, and stop() ends the following', async () => {
  let ready = false;
  const { progress, calls } = setup(
    { office: job('running') },
    { ready: () => ready, retries: 1000 },
  );
  progress.follow();
  await sleep(20);
  assert.deepEqual(calls, []);
  ready = true;
  await waitFor(() => calls.length > 0, 'a read');
  progress.stop();
  await sleep(10);
  const asked = calls.length;
  await sleep(30);
  assert.equal(calls.length, asked);
});
