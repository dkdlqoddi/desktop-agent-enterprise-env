import { useId, useState } from 'react';
import { date, isoFromLocalInput, localInputValue, type Task, type WikiDocument } from '@/lib/api';

// Documents a source field offers while typing: the best matches, or the latest documents.
const OFFERED = 50;
const named = new WeakMap<
  WikiDocument[],
  { names: Map<string, string>; ids: Map<string, string> }
>();

// Each document's name in a source field, and the document of each name: its title, with its date when another
// document has the same title. Worked out once per document list, for all the forms that show it.
function sourceNames(documents: WikiDocument[]) {
  let found = named.get(documents);
  if (found) return found;
  const titles = new Map<string, number>();
  for (const d of documents) titles.set(d.title, (titles.get(d.title) ?? 0) + 1);
  found = { names: new Map(), ids: new Map() };
  for (const d of documents) {
    const base = titles.get(d.title)! > 1 ? `${d.title} · ${date(d.updated_at)}` : d.title;
    let name = base;
    for (let n = 2; found.ids.has(name); n++) name = `${base} (${n})`;
    found.names.set(d.id, name);
    found.ids.set(name, d.id);
  }
  named.set(documents, found);
  return found;
}

// The document a task came from, found by typing part of its title. A list of every document in every form (each of
// up to 20 extracted candidates, with thousands of documents) made the forms slow; this offers 50 at a time.
function SourceField({
  value,
  documents,
  onChange,
}: {
  value: string | null;
  documents: WikiDocument[];
  onChange: (id: string | null) => void;
}) {
  const list = useId();
  // What is typed, while the field is being edited; a name that matches no document is dropped on leaving it.
  const [typed, setTyped] = useState<string | null>(null);
  const { names, ids } = sourceNames(documents);
  const current = value ? (names.get(value) ?? '삭제된 문서 · 출처를 해제해 주세요') : '';
  const needle = (typed ?? '').trim().toLowerCase();
  const offered = (
    needle ? documents.filter((d) => names.get(d.id)!.toLowerCase().includes(needle)) : documents
  ).slice(0, OFFERED);
  return (
    <>
      <input
        list={list}
        value={typed ?? current}
        placeholder="직접 입력 · 제목으로 문서 찾기"
        onChange={(e) => {
          const text = e.target.value;
          setTyped(text);
          if (!text.trim()) onChange(null);
          else if (ids.has(text)) onChange(ids.get(text)!);
        }}
        onBlur={() => setTyped(null)}
      />
      <datalist id={list}>
        {offered.map((d) => (
          <option key={d.id} value={names.get(d.id)} />
        ))}
      </datalist>
    </>
  );
}

export const blankTask = (): Task => ({
  id: '',
  title: '',
  notes: '',
  assignee: '',
  status: 'open',
  due_at: null,
  remind_at: null,
  repeat: 'none',
  source_id: null,
});

export function TaskFields({
  task,
  onChange,
  documents,
}: {
  task: Task;
  onChange: (task: Task) => void;
  documents: WikiDocument[];
}) {
  const change = (patch: Partial<Task>) => onChange({ ...task, ...patch });
  return (
    <div className="task-fields">
      <label>
        할 일 제목
        <input
          required
          maxLength={200}
          value={task.title}
          onChange={(e) => change({ title: e.target.value })}
        />
      </label>
      <div className="form-grid">
        <label>
          담당자
          <input
            maxLength={120}
            value={task.assignee}
            placeholder="미지정"
            onChange={(e) => change({ assignee: e.target.value })}
          />
        </label>
        <label>
          상태
          <select
            value={task.status}
            onChange={(e) => change({ status: e.target.value as Task['status'] })}
          >
            <option value="open">진행 중</option>
            <option value="paused">보류</option>
            <option value="done">완료</option>
          </select>
        </label>
        <label>
          마감 시간
          <input
            type="datetime-local"
            value={localInputValue(task.due_at)}
            onChange={(e) => change({ due_at: isoFromLocalInput(e.target.value) })}
          />
        </label>
        <label>
          알림 시간
          <input
            type="datetime-local"
            required={task.repeat !== 'none'}
            value={localInputValue(task.remind_at)}
            onChange={(e) => change({ remind_at: isoFromLocalInput(e.target.value) })}
          />
        </label>
        <label>
          알림 반복
          <select
            value={task.repeat}
            onChange={(e) => change({ repeat: e.target.value as Task['repeat'] })}
          >
            <option value="none">반복 없음</option>
            <option value="daily">매일 (24시간)</option>
            <option value="weekly">매주 (7일)</option>
          </select>
        </label>
        <label>
          출처 문서
          <SourceField
            value={task.source_id}
            documents={documents}
            onChange={(source_id) => change({ source_id })}
          />
        </label>
      </div>
      <label>
        메모
        <textarea
          rows={3}
          maxLength={8000}
          value={task.notes}
          onChange={(e) => change({ notes: e.target.value })}
        />
      </label>
      <small>시간은 이 PC의 시간대 기준입니다. 완료·보류 상태에서는 알림이 멈춥니다.</small>
    </div>
  );
}
