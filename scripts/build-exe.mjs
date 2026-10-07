import { fileURLToPath } from 'node:url';
import { npmCiSteps, runWorkflow } from './workflow.mjs';

if (process.platform !== 'win32') {
  console.error(
    'Windows .exe는 Windows에서 빌드해야 합니다. build-exe.cmd를 Windows에서 실행하세요.',
  );
  process.exit(1);
}

runWorkflow(
  [
    ...npmCiSteps(),
    ['run', 'setup:python'],
    ['run', 'setup:browser'],
    ['run', 'setup:electron'],
    ['run', 'check'],
    ['run', 'package:win'],
    ['run', 'test:e2e'],
  ],
  {
    CSC_IDENTITY_AUTO_DISCOVERY: process.env.CSC_IDENTITY_AUTO_DISCOVERY ?? 'false',
    NEXT_TELEMETRY_DISABLED: '1',
    // The E2E tests run the packaged app, with its own backend and Chromium, as users get it.
    PRIVATE_AGENT_E2E_EXECUTABLE: fileURLToPath(
      new URL('../release/win-unpacked/Private Agent.exe', import.meta.url),
    ),
  },
);

console.log(
  `\n빌드와 테스트가 완료되었습니다. 실행 파일: ${fileURLToPath(new URL('../release/', import.meta.url))}*.exe`,
);
