import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
try {
  // The electron package downloads its binary on first require, not during npm ci.
  require('electron');
} catch (error) {
  console.error(error.message);
  console.error(
    'Electron 실행 파일을 준비하지 못했습니다. 위 오류를 확인하세요. GitHub에서 내려받을 수 없는 네트워크라면 ELECTRON_MIRROR(미러) 또는 ELECTRON_GET_USE_PROXY=1과 HTTPS_PROXY(프록시)를 설정한 뒤 다시 실행하세요.',
  );
  process.exit(1);
}
