import { useState } from 'react';
import { ArrowRight, ArrowUp, CalendarDays, ShieldCheck, Sparkles } from 'lucide-react';
import { localDay } from '@/lib/api';
import { useActions, useConfigured, useSearch } from '../state';
import { useWidgetFrame } from '../dashboard';

const greeting = (now: Date) =>
  now.getHours() < 12 ? '좋은 아침이에요' : now.getHours() < 18 ? '반가워요' : '오늘도 수고했어요';
const TODAY = new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'long' });

function Today({ now }: { now: Date }) {
  return (
    <div className="today-pill">
      <CalendarDays size={16} />
      <time dateTime={localDay(now)}>{TODAY.format(now)}</time>
    </div>
  );
}

// The question box of the greeting: it asks what the search box asks, and the answer opens above the dashboard.
function AskForm() {
  const search = useSearch();
  const { ask } = useActions();
  const [question, setQuestion] = useState('');
  return (
    <form
      className="ask-form"
      // A question not yet sent is no unsaved work, as in the search box (app-control.ts).
      data-no-draft=""
      onSubmit={(event) => {
        event.preventDefault();
        if (!question.trim() || search.state?.asking) return;
        ask(question.trim());
        setQuestion('');
        window.scrollTo({ top: 0, behavior: 'instant' });
      }}
    >
      <Sparkles size={20} aria-hidden="true" />
      <input
        aria-label="업무 질문"
        placeholder="어떤 일을 도와드릴까요? 문서를 찾거나 질문해 보세요."
        value={question}
        maxLength={2000}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && event.nativeEvent.isComposing) event.preventDefault();
        }}
      />
      <button aria-label="질문 보내기" disabled={!question.trim() || !!search.state?.asking}>
        <ArrowUp size={20} />
      </button>
    </form>
  );
}

// The greeting as the large banner the home page always had.
export function WelcomeBanner() {
  const configured = useConfigured();
  const { openAdmin } = useActions();
  const { id } = useWidgetFrame('welcome');
  const now = new Date();
  return (
    <>
      <header className="page-heading home-heading">
        <div>
          <p className="eyebrow">A LITTLE CLARITY, EVERY DAY</p>
          <h1 id={`widget-${id}`}>
            {greeting(now)}
            <span className="greeting-dot">.</span>
          </h1>
          <p>복잡한 업무는 가볍게, 오늘의 시작은 여유롭게.</p>
        </div>
        <Today now={now} />
      </header>
      <section className="welcome-card" aria-labelledby={`${id}-title`}>
        <div className="welcome-copy">
          <span className="assistant-label">
            <Sparkles size={14} /> 나만의 AI 업무 파트너
          </span>
          <h2 id={`${id}-title`}>
            생각을 정리하고,
            <br />
            다음 일에 집중하세요.
          </h2>
          <p>자료 찾기부터 답변까지. 필요한 순간에 함께할게요.</p>
        </div>
        <div className="glass-sculpture" aria-hidden="true">
          <div className="sculpture-halo" />
          <div className="glass-ring ring-back" />
          <div className="glass-ring ring-front" />
          <div className="sculpture-spark">
            <Sparkles size={25} strokeWidth={1.3} />
          </div>
          <span className="sculpture-orb" />
        </div>
        <div className="welcome-composer">
          <AskForm />
          <div className="composer-foot">
            <span>
              <ShieldCheck size={12} />
              {configured
                ? 'AI와 함께하는 나만의 지식 검색'
                : '문서 검색은 바로, AI 답변은 연결 후에'}
            </span>
            {!configured && (
              <button onClick={() => openAdmin('llm')}>
                AI 연결하기 <ArrowRight size={12} />
              </button>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

// The greeting in one line beside the question box, for dashboards that want the room for widgets.
export function WelcomeStrip() {
  const configured = useConfigured();
  const { openAdmin } = useActions();
  const { id } = useWidgetFrame('welcome');
  const now = new Date();
  return (
    <div className="welcome-strip">
      <div className="welcome-strip-copy">
        <h1 id={`widget-${id}`}>
          {greeting(now)}
          <span className="greeting-dot">.</span>
        </h1>
        <Today now={now} />
      </div>
      <AskForm />
      {!configured && (
        <button className="text-button" onClick={() => openAdmin('llm')}>
          AI 연결하기 <ArrowRight size={12} />
        </button>
      )}
    </div>
  );
}
