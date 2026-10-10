import { spawnSync } from 'node:child_process';
import { rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
// Read by electron/diagnostics.cjs, which puts the build on the diagnostics the user copies. Ignored by git.
export const buildInfoPath = path.join(projectRoot, 'electron', 'build-info.json');

function git(args) {
  const result = spawnSync('git', args, { cwd: projectRoot, encoding: 'utf8', windowsHide: true });
  return result.status === 0 ? result.stdout.trim() : '';
}

// The commit the package is built from and that commit's date, so the same commit always gives the same file; with
// -dirty when tracked files have changes that are not committed. Without git (a source archive) and outside GitHub
// Actions, which names the commit in GITHUB_SHA, nothing is written and the report has no build line.
export function writeBuildInfo() {
  const commit = git(['rev-parse', 'HEAD']) || process.env.GITHUB_SHA || '';
  if (!/^[0-9a-f]{40}$/.test(commit)) {
    removeBuildInfo();
    return null;
  }
  const date = git(['show', '-s', '--format=%cI', commit]);
  const dirty = git(['status', '--porcelain', '--untracked-files=no']) !== '';
  const info = { commit: dirty ? `${commit}-dirty` : commit, date };
  writeFileSync(buildInfoPath, `${JSON.stringify(info, null, 2)}\n`);
  return info;
}

export function removeBuildInfo() {
  rmSync(buildInfoPath, { force: true });
}
