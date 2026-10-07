import { useState } from 'react';
import { ArrowRight, Database, FolderOpen, LoaderCircle, ShieldCheck } from 'lucide-react';
import { api, type StorageInfo } from '@/lib/api';

export default function StorageSetup({
  info,
  onReady,
}: {
  info: StorageInfo;
  onReady: () => Promise<void>;
}) {
  const [directory, setDirectory] = useState(info.suggested_path);
  const [error, setError] = useState(info.error);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState('');
  // A start that fails here is what most needs reporting; the admin page is out of reach until it works.
  async function copyDiagnostics() {
    try {
      await api('diagnostics.copy');
      setCopied('진단 정보를 클립보드에 복사했습니다. 문제를 알릴 때 붙여 넣어 주세요.');
    } catch (error) {
      setCopied((error as Error).message);
    }
  }
  async function choose() {
    try {
      const selected = await api<string | null>('storage.choose');
      if (selected) setDirectory(selected);
    } catch (error) {
      setError((error as Error).message);
    }
  }
  async function initialize() {
    setBusy(true);
    setError('');
    try {
      await api<StorageInfo>('storage.initialize', { directory });
      await onReady();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="storage-setup">
      <section className="storage-card">
        <h1>
          <span className="brand-mark" aria-hidden="true" />
          Wiki를 저장할 폴더를 정해 주세요
        </h1>
        <p>
          문서·가져온 자료·키워드와 관계도·프롬프트와 실행 기록·할 일을 이 폴더에 저장합니다. 다음
          실행부터 같은 폴더를 씁니다.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void initialize();
          }}
        >
          <label>
            Wiki 저장 폴더
            <div className="path-input">
              <input
                aria-label="Wiki 저장 폴더"
                value={directory}
                onChange={(e) => setDirectory(e.target.value)}
                disabled={busy}
                required
                placeholder="C:\Users\사용자\Documents\Private Agent"
              />
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => void choose()}
              >
                <FolderOpen size={16} />
                폴더 선택
              </button>
            </div>
          </label>
          {info.has_legacy_data && (
            <div className="info-box storage-notice">
              <Database size={18} />
              <p>
                이전 데이터가 있습니다. 다른 빈 폴더를 선택하면 기존 데이터를 복사합니다. 이전
                원본은 그대로 보관됩니다.
                <br />
                <span className="break-path">{info.legacy_path}</span>
              </p>
            </div>
          )}
          {error && (
            <div className="inline-error" role="alert">
              {error}{' '}
              <button type="button" className="text-button" onClick={() => void copyDiagnostics()}>
                진단 정보 복사
              </button>
              {copied && (
                <>
                  <br />
                  <small>{copied}</small>
                </>
              )}
            </div>
          )}
          <button className="button primary full" disabled={busy || !directory.trim()}>
            {busy ? <LoaderCircle className="spin" size={17} /> : <ArrowRight size={17} />}
            {busy ? '저장소를 준비하고 있어요…' : '이 위치에 저장하고 시작'}
          </button>
        </form>
        <div className="storage-footnote">
          <ShieldCheck size={15} />
          <span>연결 설정과 사이트 계정은 앱 설정 폴더에 따로 보관합니다.</span>
        </div>
      </section>
    </main>
  );
}
