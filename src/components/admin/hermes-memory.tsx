import { useCallback, useEffect, useState } from 'react';
import { Brain, Check, Pencil, Plus, Trash2, X } from 'lucide-react';
import { api, type AgentMemory } from '@/lib/api';
import Confirm from '../confirm';

type Target = 'memory' | 'user';
const LISTS: { target: Target; label: string; hint: string }[] = [
  {
    target: 'memory',
    label: '에이전트의 메모',
    hint: '일하면서 스스로 적어 둔 것입니다. 작업 환경, 자주 쓰는 방법 같은 내용입니다.',
  },
  {
    target: 'user',
    label: '사용자에 대해 기억하는 것',
    hint: '대화에서 알게 된 사용자의 선호와 정보입니다.',
  },
];

const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024)).toLocaleString()}KB`
    : `${(bytes / 1024 / 1024).toFixed(1)}MB`;

// What the chat agent remembers between turns, on its settings screen (inside the data-user-only form): the entries of
// its two memory files, which the user reads, corrects, adds to and deletes, and the records it keeps of the turns it
// ran, which the user deletes whole. The agent writes all of it by itself; here it is the user's to see and change.
export default function HermesMemory({
  notify,
}: {
  notify: (message: string, error?: boolean) => void;
}) {
  const [memory, setMemory] = useState<AgentMemory | null>(null);
  const [busy, setBusy] = useState(false);
  // The entry being edited, with its text; and what is typed to be added, by list.
  const [editing, setEditing] = useState<{ target: Target; index: number; text: string } | null>(
    null,
  );
  const [adding, setAdding] = useState<Record<Target, string>>({ memory: '', user: '' });

  const load = useCallback(async () => {
    try {
      setMemory(await api<AgentMemory>('agent.memory'));
    } catch {
      /* No Wiki is open yet: nothing is shown. */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const save = async (target: Target, entries: string[], done: string) => {
    setBusy(true);
    try {
      setMemory(await api<AgentMemory>('agent.memory_save', { target, entries }));
      notify(done);
      return true;
    } catch (error) {
      notify((error as Error).message, true);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    try {
      setMemory(await api<AgentMemory>('agent.memory_forget'));
      notify('에이전트의 작업 기록을 지웠습니다.');
    } catch (error) {
      notify((error as Error).message, true);
    }
  };

  if (!memory) return null;

  return (
    <fieldset className="admin-fieldset">
      <legend>
        <Brain size={14} /> 에이전트의 기억
      </legend>
      <p className="admin-hint">
        에이전트는 대화가 끝나도 남길 내용을 스스로 적어 두고, 다음 요청마다 읽습니다. 여기서 무엇을
        기억하는지 보고 고치거나 지울 수 있습니다. 바꾼 내용은 다음 요청부터 적용됩니다.
      </p>
      {LISTS.map(({ target, label, hint }) => {
        const list = memory[target];
        return (
          <div key={target} className="hermes-memory">
            <div className="hermes-messenger-head">
              <strong>{label}</strong>
              <span className={`hermes-list-note ${list.chars > list.limit ? 'over' : ''}`}>
                {list.chars.toLocaleString()} / {list.limit.toLocaleString()}자
              </span>
            </div>
            <p className="admin-hint">{hint}</p>
            {list.entries.length ? (
              <ul className="hermes-list hermes-memory-list" aria-label={label}>
                {list.entries.map((entry, index) =>
                  editing?.target === target && editing.index === index ? (
                    <li key={index}>
                      <textarea
                        className="hermes-memory-input"
                        aria-label={`${label} 항목 고치기`}
                        rows={3}
                        value={editing.text}
                        onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                      />
                      <button
                        type="button"
                        className="text-button"
                        disabled={busy || !editing.text.trim()}
                        onClick={() =>
                          void save(
                            target,
                            list.entries.map((item, at) => (at === index ? editing.text : item)),
                            '기억을 고쳤습니다.',
                          ).then((saved) => saved && setEditing(null))
                        }
                      >
                        <Check size={13} /> 저장
                      </button>
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => setEditing(null)}
                      >
                        <X size={13} /> 취소
                      </button>
                    </li>
                  ) : (
                    <li key={index}>
                      <span className="hermes-memory-entry">{entry}</span>
                      <button
                        type="button"
                        className="text-button"
                        disabled={busy}
                        onClick={() => setEditing({ target, index, text: entry })}
                      >
                        <Pencil size={13} /> 고치기
                      </button>
                      <Confirm
                        label="지우기"
                        question="이 기억을 지울까요? 되돌릴 수 없습니다."
                        icon={<Trash2 size={13} />}
                        disabled={busy}
                        onConfirm={async () => {
                          await save(
                            target,
                            list.entries.filter((_, at) => at !== index),
                            '기억을 지웠습니다.',
                          );
                        }}
                      />
                    </li>
                  ),
                )}
              </ul>
            ) : (
              <p className="admin-hint">아직 기억한 것이 없습니다.</p>
            )}
            <div className="hermes-add-key">
              <textarea
                className="hermes-memory-input"
                aria-label={`${label}에 추가`}
                rows={2}
                placeholder="직접 알려 줄 내용 (여러 줄을 붙여 넣으면 줄마다 한 항목)"
                value={adding[target]}
                onChange={(event) =>
                  setAdding((current) => ({ ...current, [target]: event.target.value }))
                }
              />
              <button
                type="button"
                className="button secondary small"
                disabled={busy || !adding[target].trim()}
                onClick={() =>
                  void save(
                    target,
                    [
                      ...list.entries,
                      ...adding[target]
                        .split('\n')
                        .map((line) => line.trim())
                        .filter(Boolean),
                    ],
                    '기억에 추가했습니다.',
                  ).then((saved) => saved && setAdding((current) => ({ ...current, [target]: '' })))
                }
              >
                <Plus size={14} /> 추가
              </button>
            </div>
          </div>
        );
      })}
      <div className="hermes-memory">
        <div className="hermes-messenger-head">
          <strong>작업 기록</strong>
          <span className="hermes-list-note">
            {memory.records.files
              ? `파일 ${memory.records.files.toLocaleString()}개 · ${size(memory.records.bytes)}`
              : '없음'}
          </span>
        </div>
        <p className="admin-hint">
          에이전트가 요청마다 남기는 내부 기록(주고받은 내용과 도구 결과)입니다. 이전 작업을 찾을 때
          씁니다. 채팅 화면의 대화와 위의 기억은 그대로 둡니다.
        </p>
        <div>
          <Confirm
            label="작업 기록 지우기"
            question="에이전트의 작업 기록을 모두 지울까요? 되돌릴 수 없습니다."
            icon={<Trash2 size={14} />}
            disabled={busy || !memory.records.files}
            onConfirm={forget}
          />
        </div>
      </div>
    </fieldset>
  );
}
