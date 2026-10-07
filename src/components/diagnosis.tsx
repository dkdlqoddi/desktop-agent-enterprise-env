import { Sparkles } from 'lucide-react';
import type { WebDiagnosis } from '@/lib/api';

// The LLM's analysis of why a site did not open or sign in: the cause, what the app tried
// after it, what the user can do, and on request what shows it.
export default function Diagnosis({ diagnosis }: { diagnosis: WebDiagnosis }) {
  if ('error' in diagnosis)
    return (
      <p className="diagnosis-missing">
        <Sparkles size={13} />
        AI 원인 분석을 하지 못했습니다: {diagnosis.error}
      </p>
    );
  return (
    <div className="diagnosis" role="note" aria-label="AI 원인 분석">
      <p className="diagnosis-head">
        <Sparkles size={14} />
        <strong>AI 원인 분석</strong>
        {diagnosis.label && <span className="diagnosis-label">{diagnosis.label}</span>}
      </p>
      <p>{diagnosis.cause}</p>
      {diagnosis.attempt && <p className="diagnosis-attempt">{diagnosis.attempt}</p>}
      {diagnosis.actions.length > 0 && (
        <ul>
          {diagnosis.actions.map((action, index) => (
            <li key={index}>{action}</li>
          ))}
        </ul>
      )}
      {diagnosis.evidence.length > 0 && (
        <details>
          <summary>근거 {diagnosis.evidence.length}개</summary>
          <ul>
            {diagnosis.evidence.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
