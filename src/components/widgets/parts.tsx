import { LoaderCircle } from 'lucide-react';
import { clock, dayLabel, localDay } from '@/lib/api';

// Pieces the formats share, so the same look means the same kind of reading in every widget.

export function Loading() {
  return (
    <div className="widget-loading">
      <LoaderCircle className="spin" size={20} />
    </div>
  );
}

// Numbers in tiles, as the briefing shows them: a label, the number and an optional note under it.
export function Metrics({
  items,
}: {
  items: { label: string; value: number | string; note?: string; alert?: boolean }[];
}) {
  return (
    <dl className={`metrics fit count-${items.length}`}>
      {items.map((item) => (
        <div key={item.label} className={item.alert ? 'alert' : undefined}>
          <dt>{item.label}</dt>
          <dd className={typeof item.value === 'string' ? 'text' : undefined}>{item.value}</dd>
          {item.note && <dd className="metric-note">{item.note}</dd>}
        </div>
      ))}
    </dl>
  );
}

// Horizontal bars in the given order, each with its value written beside it; with onPick each bar is a button.
export function Bars({
  label,
  items,
  onPick,
}: {
  label: string;
  items: { key: string; label: string; value: number; note?: string }[];
  onPick?: (key: string) => void;
}) {
  const max = Math.max(1, ...items.map((item) => item.value));
  return (
    <ul className="bar-list" aria-label={label}>
      {items.map((item) => {
        const row = (
          <>
            <span className="bar-label">{item.label}</span>
            <span className="bar-value">{item.note ?? item.value}</span>
            <span className="bar-track" aria-hidden="true">
              <span style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }} />
            </span>
          </>
        );
        return (
          <li key={item.key}>
            {onPick ? (
              <button type="button" className="bar-row" onClick={() => onPick(item.key)}>
                {row}
              </button>
            ) : (
              <div className="bar-row">{row}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// A ring filled by `value` of `total`, with the share in the middle. The filled part is an arc of its own: a dashed
// circle drew stray lines from its ends under Chromium's software rendering.
export function Ring({ value, total, label }: { value: number; total: number; label: string }) {
  const share = total ? Math.round((value / total) * 100) : 0;
  const radius = 42;
  const angle = (share / 100) * 2 * Math.PI;
  const end = `${(50 + radius * Math.sin(angle)).toFixed(2)} ${(50 - radius * Math.cos(angle)).toFixed(2)}`;
  return (
    <div className="ring" role="img" aria-label={`${label} ${share}%`}>
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle className="ring-track" cx="50" cy="50" r={radius} />
        {share >= 100 ? (
          <circle className="ring-fill" cx="50" cy="50" r={radius} />
        ) : (
          share > 0 && (
            <path
              className="ring-fill"
              d={`M50 ${50 - radius}A${radius} ${radius} 0 ${share > 50 ? 1 : 0} 1 ${end}`}
            />
          )
        )}
      </svg>
      <strong aria-hidden="true">
        {share}
        <small>%</small>
      </strong>
    </div>
  );
}

// A small column per day or group; screen readers hear each column's own sentence.
export function Columns({
  items,
}: {
  items: { key: string; label: string; value: number; text: string }[];
}) {
  const max = Math.max(1, ...items.map((item) => item.value));
  return (
    <ol className="column-chart">
      {items.map((item) => (
        <li key={item.key}>
          <span className="sr-only">{item.text}</span>
          <span className="column-area" aria-hidden="true">
            <span className="column-value">{item.value || ''}</span>
            <span
              className="column-bar"
              style={{ height: `${Math.max(3, (item.value / max) * 100)}%` }}
            />
          </span>
          <span className="column-label" aria-hidden="true">
            {item.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

export type AgendaItem = {
  id: string;
  at: string;
  title: string;
  meta?: string;
  // Shown under this name instead of its day, such as reminders that are due now.
  group?: string;
  alert?: boolean;
};

// Items under their day (오늘, 내일 or the date), in the given order, each with its time.
export function Agenda({ items }: { items: AgendaItem[] }) {
  const groups: [string, AgendaItem[]][] = [];
  for (const item of items) {
    const name = item.group ?? dayLabel(localDay(item.at));
    const last = groups[groups.length - 1];
    if (last?.[0] === name) last[1].push(item);
    else groups.push([name, [item]]);
  }
  return (
    <div className="agenda">
      {groups.map(([name, list], index) => (
        <section key={`${index}:${name}`} className="agenda-day">
          <h3>{name}</h3>
          <ul>
            {list.map((item) => (
              <li key={item.id} className={item.alert ? 'alert' : undefined}>
                <time dateTime={item.at}>{clock(item.at)}</time>
                <span>
                  <strong>{item.title}</strong>
                  {item.meta && <small>{item.meta}</small>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
