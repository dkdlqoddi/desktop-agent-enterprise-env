import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  timeout: 90_000,
  workers: 1,
  reporter: 'list',
  use: { trace: 'retain-on-failure', actionTimeout: 10_000 },
});
