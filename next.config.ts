import { realpathSync } from 'node:fs';
import path from 'node:path';
import type { NextConfig } from 'next';

// A second git worktree may link its node_modules to the main checkout's. Turbopack resolves nothing outside its root,
// which Next puts beside package-lock.json, and refuses such a link: the root is then the nearest folder that holds both
// the project and the linked node_modules. Next runs in the project folder (npm scripts start it there). Anywhere else,
// as in the main checkout and CI, the configuration stays as it was.
function linkedRoot(): string | undefined {
  const project = process.cwd();
  let modules: string;
  try {
    modules = realpathSync(path.join(project, 'node_modules'));
  } catch {
    return undefined;
  }
  const holds = (folder: string) => {
    const relative = path.relative(folder, modules);
    return !relative.startsWith('..') && !path.isAbsolute(relative);
  };
  if (holds(project)) return undefined;
  let root = path.dirname(project);
  while (!holds(root) && path.dirname(root) !== root) root = path.dirname(root);
  return root;
}

const root = linkedRoot();
const config: NextConfig = {
  output: 'export',
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  devIndicators: false,
  ...(root ? { turbopack: { root } } : {}),
};
export default config;
