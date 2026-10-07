import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agent import search
from agent.db import Database
from agent.llm import UserError
from agent.service import Service
from test_search import long_document


class FakeLLM:
    def __init__(self):
        self.responses = []
        self.calls = []

    def structured(self, system, content):
        self.calls.append((system, json.loads(content) if content.startswith('{') else content))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def complete(self, messages):
        self.calls.append(messages)
        return '오늘의 저장된 업무를 확인하세요.'


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / 'wiki.db'), FakeLLM())


def call(service, method, **params):
    return service.dispatch(method, params)


def task(service, **patch):
    return call(service, 'tasks.save', title='보고서 작성', **patch)


def past(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def document(service, content='개발팀이 캐시를 도입하기로 결정했습니다.'):
    return service.wiki_save({'title': '프로젝트 회의록', 'content': content})


def search_result(terms=None):
    return {'terms': terms or ['캐시'], 'keywords': [], 'explanation': '관련 자료 검색'}


def pick(*refs, sections=()):
    return {'thinking': '관련 문서 선택', 'next': None,
            'results': [{'ref': ref, 'reason': '질문과 관련', 'sections': list(sections)} for ref in refs]}


def test_tasks_persist_and_source_deletion_retains_task(service):
    doc = document(service)
    created = task(service, source_id=doc['id'], due_at='2026-10-01T09:00:00+09:00')
    assert created['due_at'] == '2026-10-01T00:00:00+00:00'
    restored = Service(Database(service.db.path), FakeLLM())
    assert call(restored, 'tasks.list')[0]['source_title'] == doc['title']
    restored.wiki_delete({'id': doc['id']})
    retained = call(restored, 'tasks.list')[0]
    assert retained['source_id'] is None
    assert retained['title'] == created['title']


@pytest.mark.parametrize('written, stored', [
    ('2026-10-01T09:00:00+0900', '2026-10-01T00:00:00+00:00'),
    ('2026-10-01T09:00:00.5+09:00', '2026-10-01T00:00:00.500000+00:00'),
    ('2026-10-01T00:00:00.123Z', '2026-10-01T00:00:00.123000+00:00'),
])
def test_task_times_as_an_llm_may_write_them_on_every_python(service, written, stored):
    # Python 3.10 reads only +09:00 offsets and 3- or 6-digit fractions.
    assert task(service, due_at=written)['due_at'] == stored


@pytest.mark.parametrize('patch', [
    {'due_at': '2026-09-30'}, {'due_at': '2026-02-30T10:00:00Z'}, {'remind_at': 10},
    {'status': 'unknown'}, {'repeat': 'hourly'}, {'repeat': 'daily'}, {'source_id': 'missing'},
])
def test_task_validation_rejects_invalid_records(service, patch):
    with pytest.raises(UserError):
        task(service, **patch)
    assert not call(service, 'tasks.list')


def test_bulk_review_saves_atomically_and_retry_is_idempotent(service):
    with pytest.raises(UserError):
        call(service, 'tasks.save_many', tasks=[{'title': '유효'}, {'title': ''}])
    assert not call(service, 'tasks.list')
    draft = {'id': 'candidate-1', 'title': '검토한 업무'}
    call(service, 'tasks.save_many', tasks=[draft])
    call(service, 'tasks.save_many', tasks=[draft])
    assert len(call(service, 'tasks.list')) == 1


def test_stale_editor_cannot_overwrite_snoozed_reminder(service):
    created = task(service, remind_at=past())
    call(service, 'tasks.snooze', id=created['id'], minutes=10)
    with pytest.raises(UserError, match='변경'):
        call(service, 'tasks.save', **created, expected_updated_at=created['updated_at'])
    assert not call(service, 'reminders.due')


def test_completed_paused_deleted_tasks_never_notify(service):
    created = task(service, remind_at=past())
    assert len(call(service, 'reminders.due')) == 1
    for state in ('done', 'paused'):
        call(service, 'tasks.save', id=created['id'], status=state)
        assert not call(service, 'reminders.due')
    call(service, 'tasks.save', id=created['id'], status='open')
    assert len(call(service, 'reminders.due')) == 1
    call(service, 'tasks.delete', id=created['id'])
    assert not call(service, 'reminders.due')


def test_ack_is_persistent_and_cannot_ack_newer_snooze(service):
    created = task(service, remind_at=past())
    assert call(service, 'reminders.ack', id=created['id'], remind_at=created['remind_at'])['acknowledged']
    assert not call(service, 'reminders.due')
    restored = Service(Database(service.db.path), FakeLLM())
    assert not call(restored, 'reminders.due')
    call(service, 'tasks.snooze', id=created['id'], minutes=10)
    assert not call(service, 'reminders.ack', id=created['id'], remind_at=created['remind_at'])['acknowledged']
    assert call(service, 'tasks.list')[0]['notified_at'] is None


@pytest.mark.parametrize('repeat,days', [('daily', 1), ('weekly', 7)])
def test_a_snoozed_repeating_reminder_keeps_the_time_its_repeats_count_from(service, repeat, days):
    anchor = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    created = task(service, remind_at=anchor.isoformat(), repeat=repeat)
    assert len(call(service, 'reminders.due')) == 1
    call(service, 'tasks.snooze', id=created['id'], minutes=10)
    snoozed = call(service, 'tasks.list')[0]
    assert snoozed['remind_at'] == created['remind_at'] and snoozed['snoozed_until'] > snoozed['remind_at']
    assert not call(service, 'reminders.due')
    # Ten minutes later the reminder is due again; acknowledged, the next one is a repeat of the old time.
    with service.db.connect() as db:
        db.execute('UPDATE tasks SET snoozed_until=? WHERE id=?', (past(0), created['id']))
    [due] = call(service, 'reminders.due')
    assert call(service, 'reminders.ack', id=due['id'], remind_at=due['remind_at'])['acknowledged']
    after = call(service, 'tasks.list')[0]
    assert datetime.fromisoformat(after['remind_at']) == anchor + timedelta(days=days)
    assert after['snoozed_until'] is None
    # A new time from the editor drops a snooze in progress.
    call(service, 'tasks.snooze', id=created['id'], minutes=60)
    moved = call(service, 'tasks.save', id=created['id'], remind_at=(anchor + timedelta(days=3)).isoformat())
    assert moved['snoozed_until'] is None


@pytest.mark.parametrize('repeat,days', [('daily', 1), ('weekly', 7)])
def test_missed_repeating_reminders_coalesce_and_concurrent_ack_advances_once(service, repeat, days):
    created = task(service, remind_at=past(30), repeat=repeat)
    def ack(_):
        return call(service, 'reminders.ack', id=created['id'], remind_at=created['remind_at'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ack, range(2)))
    assert sum(r['acknowledged'] for r in results) == 1
    updated = call(service, 'tasks.list')[0]
    remaining = datetime.fromisoformat(updated['remind_at']) - datetime.now(timezone.utc)
    assert timedelta(0) < remaining <= timedelta(days=days)
    assert not call(service, 'reminders.due')


def test_extract_only_previews_supported_tasks_with_offset(service):
    doc = document(service, '홍길동은 내일까지 보고서를 작성한다.')
    service.llm.responses = [{'tasks': [{'title': '보고서 작성', 'assignee': '홍길동',
        'due_at': '2026-09-29T18:00:00+09:00', 'evidence': doc['content']}]}]
    result = call(service, 'tasks.extract', source_id=doc['id'], timezone_offset=-540)
    candidate = result['tasks'][0]
    assert candidate['due_at'] == '2026-09-29T09:00:00+00:00'
    assert candidate['source_id'] == doc['id']
    assert candidate['remind_at'] is None
    assert service.llm.calls[0][1]['local_now'].endswith('+09:00')
    assert not call(service, 'tasks.list')
    call(service, 'tasks.save_many', tasks=result['tasks'])
    assert len(call(service, 'tasks.list')) == 1


def test_extract_leaves_out_invented_evidence_and_invalid_dates_and_says_so(service):
    # One candidate the text does not support costs only itself: the others stay, and the notice counts it.
    service.llm.responses = [{'tasks': [
        {'title': '새 업무', 'evidence': '없는 문장'},
        {'title': '검토', 'evidence': '검토하자', 'due_at': 'tomorrow'},
        {'title': '반복', 'evidence': '검토하자', 'repeat': 'daily'},
        'not a task',
        {'title': '검토하기', 'evidence': '검토하자'},
    ]}]
    result = call(service, 'tasks.extract', content='검토하자')
    assert [task['title'] for task in result['tasks']] == ['검토하기']
    assert result['notice'] == '근거를 원문에서 찾지 못했거나 날짜·반복 형식이 올바르지 않은 후보 4개는 뺐습니다.'
    service.llm.responses = [{'tasks': [{'title': '새 업무', 'evidence': '없는 문장'}]}]
    assert call(service, 'tasks.extract', content='검토하자')['tasks'] == []
    service.llm.responses = [{'tasks': 'none'}]
    with pytest.raises(UserError, match='형식'):
        call(service, 'tasks.extract', content='검토하자')
    assert not call(service, 'tasks.list')


def test_extract_compares_evidence_without_markdown_escapes_and_keeps_long_titles(service):
    doc = document(service, r'API\_SPEC\_v2.docx 검토는   R\&D 팀이 금요일까지' + '\n끝내기로 했다.')
    service.llm.responses = [{'tasks': [{'title': '사양 검토 ' + '가' * 300, 'assignee': 'R&D 팀',
                                         'evidence': 'API_SPEC_v2.docx 검토는 R&D 팀이 금요일까지 끝내기로 했다.'}]}]
    result = call(service, 'tasks.extract', source_id=doc['id'])
    assert result['notice'] == '' and len(result['tasks']) == 1
    assert len(result['tasks'][0]['title']) == 200 and result['tasks'][0]['assignee'] == 'R&D 팀'
    call(service, 'tasks.save_many', tasks=result['tasks'])


def test_a_document_longer_than_one_request_is_read_in_parts_at_once(service):
    """A Wiki document of 80,000 characters is read in three requests of at most 32,000 at the same time; their
    candidates are joined in document order without repeats."""
    paragraphs = [f'{index}번 문단: ' + '회의 내용 ' * 600 for index in range(24)]
    paragraphs[2] += ' 홍길동이 보고서를 쓴다.'
    paragraphs[20] += ' 김철수가 예산을 검토한다.'
    doc = document(service, '\n\n'.join(paragraphs)[:80000])
    asked = []

    def structured(system, content):
        payload = json.loads(content)
        asked.append(payload)
        found = [{'title': title, 'evidence': quote} for title, quote in
                 (('보고서 작성', '홍길동이 보고서를 쓴다.'), ('예산 검토', '김철수가 예산을 검토한다.'))
                 if quote in payload['text']]
        # Every part also finds the same standing task, which is kept once.
        return {'tasks': found + [{'title': '주간  일정 확인', 'evidence': payload['text'][:20]}]}

    service.llm.structured = structured
    result = call(service, 'tasks.extract', source_id=doc['id'])
    assert len(asked) == 3 and all(len(payload['text']) <= 32000 for payload in asked)
    assert sorted(payload['text_part'] for payload in asked) == ['1/3', '2/3', '3/3']
    assert [task['title'] for task in result['tasks']] == ['보고서 작성', '주간 일정 확인', '예산 검토']
    assert result['notice'] == ''

    def failing(system, content):
        if json.loads(content)['text_part'] == '1/3':
            raise UserError('LLM 응답 제한 시간을 초과했습니다.')
        return structured(system, content)

    # A part the LLM could not answer leaves the others' candidates, and says so.
    service.llm.structured = failing
    result = call(service, 'tasks.extract', source_id=doc['id'])
    assert [task['title'] for task in result['tasks']] == ['주간 일정 확인', '예산 검토']
    assert result['notice'] == '문서의 3부분 중 1부분은 LLM이 답하지 않아 나머지에서만 찾았습니다.'
    service.llm.structured = lambda system, content: (_ for _ in ()).throw(UserError('LLM API에 연결할 수 없습니다.'))
    with pytest.raises(UserError, match='연결할 수 없습니다'):
        call(service, 'tasks.extract', source_id=doc['id'])


@pytest.mark.parametrize('answer, listed', [
    ('캐시를 도입했습니다 [1, 2]. 2024년 계획 [2024]도 있습니다.', [1, 2]),
    ('캐시를 도입했습니다 [1][2].', []),
    ('캐시를 도입했습니다 [2].', [1, 7]),
])
def test_citations_are_the_documents_an_answer_marks_or_lists(service, answer, listed):
    first, second = document(service), document(service, '금요일에 배포합니다.')
    service.llm.responses = [{'answer': answer, 'citations': listed}]
    result = call(service, 'assistant.ask', question='결정 사항은?', document_ids=[first['id'], second['id']])
    message = result['messages'][-1]
    assert message['content'] == answer
    assert sorted(c['number'] for c in message['citations']) == [1, 2]


def test_grounded_chat_persists_valid_citations_and_current_followup_sources(service):
    doc = document(service)
    service.llm.responses = [{'answer': '캐시를 도입하기로 했습니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='결정 사항은?', document_ids=[doc['id']])
    citation = result['messages'][-1]['citations'][0]
    assert citation['id'] == doc['id']
    assert citation['excerpt'] == doc['content']
    restored = Service(Database(service.db.path), service.llm)
    history = call(restored, 'assistant.history', id=result['conversation_id'])
    assert len(history) == 2
    restored.wiki_save({'id': doc['id'], 'title': doc['title'], 'content': '캐시 담당자가 플랫폼팀으로 변경되었습니다.'})
    service.llm.responses = [search_result(), pick('D1'), {'answer': '플랫폼팀입니다. [1]', 'citations': [1]}]
    followup = call(restored, 'assistant.ask', question='담당자는?', conversation_id=result['conversation_id'])
    assert len(followup['messages']) == 4
    payload = service.llm.calls[-1][1]
    assert payload['history'][0]['content'] == '결정 사항은?'
    assert '플랫폼팀' in payload['documents'][0]['excerpt']
    assert payload['documents'][0]['revision'] == 2
    # The previous question resolves the follow-up during the analysis.
    assert service.llm.calls[-3][1]['previous_question'] == '결정 사항은?'
    # Prior answers preserve the exact evidence used before the document changed.
    assert followup['messages'][1]['citations'][0]['revision'] == 1
    assert followup['messages'][3]['citations'][0]['terms'] == ['캐시']
    assert [s['kind'] for s in followup['messages'][3]['trace']['steps']] == ['analyze', 'keyword', 'agent']
    assert followup['messages'][1]['trace'] == {'terms': ['결정', '사항'], 'steps': [],
                                                'sources': [{'title': '프로젝트 회의록', 'sections': []}]}


def test_automatic_search_lists_every_found_document_with_the_answer(service):
    chosen = document(service, '개발팀이 캐시를 도입하기로 결정했습니다.')
    other = service.wiki_save({'title': '캐시 교육', 'content': '캐시 개념 교육 자료입니다.'})
    # The title match ranks the course first (D1); the AI search picks the decision (D2).
    service.llm.responses = [search_result(), pick('D2'), {'answer': '캐시를 도입합니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='캐시 결정은?')
    found = result['search']
    assert found['ai'] is True and found['highlights'] == ['캐시']
    assert [(d['id'], d['match']['selected']) for d in found['documents']] == [(chosen['id'], True), (other['id'], False)]
    assert found['documents'][0]['match']['reason'] == '질문과 관련'
    assert 'content' not in found['documents'][0]
    assert [s['kind'] for s in found['steps']] == ['analyze', 'keyword', 'agent']
    # Picked documents answer without a search, so there is nothing to list.
    service.llm.responses = [{'answer': '캐시입니다. [1]', 'citations': [1]}]
    assert call(service, 'assistant.ask', question='결정은?', document_ids=[chosen['id']])['search'] is None


def test_no_matches_gives_explicit_gap_without_fabricating_answer(service):
    # The agent may try other words once; nothing found still means no answer request.
    service.llm.responses = [search_result(['존재하지않는키워드']), {'results': [], 'next': {'search': ['없는말']}}]
    result = call(service, 'assistant.ask', question='무슨 일이 있었어?')
    assert '찾지 못했습니다' in result['messages'][-1]['content']
    assert result['messages'][-1]['citations'] == []
    assert len(service.llm.calls) == 2


def test_followups_fall_back_to_cited_sources_only_when_the_agent_finds_nothing(service):
    decision = document(service, '캐시 도입을 결정했습니다.')
    service.wiki_save({'title': '배포 일정', 'content': '배포는 금요일입니다.'})
    service.llm.responses = [{'answer': '캐시입니다. [1]', 'citations': [1]}]
    conversation = call(service, 'assistant.ask', question='결정은?', document_ids=[decision['id']])['conversation_id']
    service.llm.responses = [search_result(['배포']), pick('D1'), {'answer': '금요일입니다. [1]', 'citations': [1]}]
    call(service, 'assistant.ask', question='배포는?', conversation_id=conversation)
    # The cited document is offered first; the agent's choice decides what the answer reads.
    agent = service.llm.calls[-2][1]
    assert [(c['title'], c.get('cited_before', False)) for c in agent['candidates']] == [
        ('프로젝트 회의록', True), ('배포 일정', False)]
    assert agent['candidates'][0]['ref'] == 'D2'
    service.llm.responses = [search_result(['배포']), pick('D1'), {'answer': '금요일입니다. [1]', 'citations': [1]}]
    call(service, 'assistant.ask', question='배포는 언제?', conversation_id=conversation)
    assert [d['title'] for d in service.llm.calls[-1][1]['documents']] == ['배포 일정']
    service.llm.responses = [search_result(['없는말']), {'results': [], 'next': None},
                             {'answer': '금요일로 정했습니다. [1]', 'citations': [1]}]
    call(service, 'assistant.ask', question='그건 왜?', conversation_id=conversation)
    assert [d['title'] for d in service.llm.calls[-1][1]['documents']] == ['배포 일정']


def test_agent_searches_again_before_answering(service):
    doc = document(service, 'API 지연 시간을 30% 줄였습니다.')
    service.llm.responses = [search_result(['속도']), {'results': [], 'next': {'search': ['지연']}}, pick('D1'),
                             {'answer': '지연 시간을 줄였습니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='속도 개선 결과는?')
    message = result['messages'][-1]
    assert message['citations'][0]['id'] == doc['id'] and message['citations'][0]['terms'] == ['속도', '지연']
    assert [s['kind'] for s in message['trace']['steps']] == ['analyze', 'keyword', 'agent', 'search', 'agent']
    assert len(service.llm.calls) == 4


def test_long_document_answers_from_its_chosen_sections(service):
    document(service, long_document())
    service.llm.responses = [search_result(['redis']), pick('D1', sections=['1.2']),
                             {'answer': 'Redis 클러스터입니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='캐시 전략은?')
    assert '1.2 캐시 전략' in service.llm.calls[1][1]['candidates'][0]['outline']
    source = service.llm.calls[-1][1]['documents'][0]
    assert source['sections'] == ['1.2 캐시 전략'] and source['partial']
    assert 'Redis 클러스터' in source['excerpt'] and len(source['excerpt']) <= search.SECTION_CHARS
    assert '배경 문단' not in source['excerpt'] and '운영 문단' not in source['excerpt']
    assert result['messages'][-1]['citations'][0]['sections'] == ['1.2 캐시 전략']


def test_long_chosen_section_is_narrowed_to_where_the_words_are(service):
    document(service, long_document())
    # Chapter 1 is too long to read whole; its part with the search word is read instead.
    service.llm.responses = [search_result(['redis']), pick('D1', sections=['1']),
                             {'answer': 'Redis 클러스터입니다. [1]', 'citations': [1]}]
    call(service, 'assistant.ask', question='캐시는 어떻게 했어?')
    source = service.llm.calls[-1][1]['documents'][0]
    assert source['sections'] == ['1.2 캐시 전략'] and 'Redis 클러스터' in source['excerpt']


def test_several_picked_long_documents_all_get_their_sections_chosen(service):
    ids = [service.wiki_save({'title': f'설계서 {i}', 'content': long_document()})['id'] for i in range(4)]
    service.llm.responses = [pick('D1', 'D2', 'D3', 'D4', sections=['1.3']),
                             {'answer': '운영 방식입니다. [1]', 'citations': [1]}]
    call(service, 'assistant.ask', question='운영은?', document_ids=ids)
    agent = service.llm.calls[0][1]
    assert [c['title'] for c in agent['candidates']] == [f'설계서 {i}' for i in range(4)]
    assert len(json.dumps(agent, ensure_ascii=False)) <= search.AGENT_CHARS + 2000
    documents = service.llm.calls[1][1]['documents']
    assert [d['sections'] for d in documents] == [['1.3 운영']] * 4


def test_picked_long_document_is_read_through_its_outline(service):
    doc = document(service, long_document())
    service.llm.responses = [pick('D1', sections=['1.3']), {'answer': '운영 방식입니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='운영은 어떻게 해?', document_ids=[doc['id']])
    agent = service.llm.calls[0][1]
    assert agent['steps_left'] == 0 and agent['candidates'][0]['outline'].count('\n') == 4
    source = service.llm.calls[1][1]['documents'][0]
    assert source['sections'] == ['1.3 운영'] and '운영 문단 0' in source['excerpt']
    assert '배경 문단' not in source['excerpt']
    assert result['messages'][-1]['trace']['steps'][0]['label'] == '목차 탐색'


def test_failed_ai_search_falls_back_to_keyword_matches(service):
    doc = document(service)
    service.llm.responses = [search_result(), UserError('LLM 응답 제한 시간을 초과했습니다.'),
                             {'answer': '캐시를 도입합니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='결정은?')
    message = result['messages'][-1]
    assert message['citations'][0]['id'] == doc['id']
    assert message['trace']['steps'][-1] == {'kind': 'fallback', 'label': 'AI 검색 실패',
                                             'detail': 'LLM 응답 제한 시간을 초과했습니다.'}


@pytest.mark.parametrize('answer, cited', [
    ({'answer': '근거입니다. [9]', 'citations': [9]}, []),
    ({'answer': '근거입니다. [1]', 'citations': []}, [1]),
    ({'answer': '근거입니다.', 'citations': [1]}, [1]),
    ({'answer': '근거입니다. [1]', 'citations': [True, '1']}, [1]),
])
def test_citation_numbers_that_fit_no_document_are_left_out_and_the_answer_kept(service, answer, cited):
    doc = document(service)
    service.llm.responses = [answer]
    message = call(service, 'assistant.ask', question='설명해줘', document_ids=[doc['id']])['messages'][-1]
    assert [c['number'] for c in message['citations']] == cited


@pytest.mark.parametrize('answer', [{'answer': '', 'citations': [1]}, {'citations': [1]}, {'answer': 3}])
def test_a_missing_answer_does_not_commit_a_partial_conversation(service, answer):
    doc = document(service)
    service.llm.responses = [answer]
    with pytest.raises(UserError, match='답변'):
        call(service, 'assistant.ask', question='설명해줘', document_ids=[doc['id']])
    assert not call(service, 'assistant.conversations')


def test_failed_followup_preserves_successful_history_and_deletion_cascades(service):
    doc = document(service)
    service.llm.responses = [{'answer': '캐시입니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='결정?', document_ids=[doc['id']])
    service.llm.responses = [UserError('연결 오류')]
    with pytest.raises(UserError):
        call(service, 'assistant.ask', question='후속 질문', conversation_id=result['conversation_id'], document_ids=[doc['id']])
    assert len(call(service, 'assistant.history', id=result['conversation_id'])) == 2
    call(service, 'assistant.delete', id=result['conversation_id'])
    assert not call(service, 'assistant.history', id=result['conversation_id'])
    with pytest.raises(UserError, match='삭제'):
        call(service, 'assistant.ask', question='다시', conversation_id=result['conversation_id'])


def test_long_document_keeps_relevant_tail_and_marks_partial(service):
    doc = document(service, '서론입니다. ' * 1500 + '꼬리키워드: 최종 담당자는 개발팀입니다.')
    service.llm.responses = [search_result(['꼬리키워드']), pick('D1'), {'answer': '개발팀입니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='꼬리키워드 담당자는?')
    citation = result['messages'][-1]['citations'][0]
    assert citation['partial']
    assert '최종 담당자는 개발팀' in citation['excerpt']
    assert len(citation['excerpt']) <= 4550


def test_briefing_respects_local_day_and_works_without_llm(service, monkeypatch):
    import agent.assistant as module
    fixed = datetime(2026, 9, 28, 23, 30, tzinfo=timezone(timedelta(hours=9)))
    monkeypatch.setattr(module, 'local_clock', lambda p: fixed)
    monkeypatch.setattr(module, 'now', lambda: fixed.astimezone(timezone.utc).isoformat())
    overdue = task(service, due_at='2026-09-28T22:00:00+09:00')
    today = task(service, due_at='2026-09-28T23:50:00+09:00')
    future = task(service, due_at='2026-09-29T00:10:00+09:00')
    unscheduled = task(service)
    snapshot = call(service, 'briefing.get', timezone_offset=-540)
    assert snapshot['local_date'] == '2026-09-28'
    assert [t['id'] for t in snapshot['overdue']] == [overdue['id']]
    assert [t['id'] for t in snapshot['today']] == [today['id']]
    assert [t['id'] for t in snapshot['upcoming']] == [future['id']]
    assert [t['id'] for t in snapshot['unscheduled']] == [unscheduled['id']]
    assert snapshot['counts']['open'] == 4
    assert not service.llm.calls
    result = call(service, 'briefing.generate')
    assert result['content']
    assert len(service.llm.calls) == 1


def test_a_briefing_longer_than_the_widget_keeps_is_cut_not_refused(service, monkeypatch):
    monkeypatch.setattr(service.llm, 'complete', lambda messages: '## 오늘\n' + '- 업무 확인\n' * 3000)
    content = call(service, 'briefing.generate')['content']
    assert len(content) == 20000 and content.startswith('## 오늘') and content.endswith('…')
    monkeypatch.setattr(service.llm, 'complete', lambda messages: '  ')
    with pytest.raises(UserError, match='브리핑'):
        call(service, 'briefing.generate')


def test_the_llm_reads_where_a_document_came_from_by_a_label_never_a_path_of_this_pc(service, tmp_path):
    note = tmp_path / '비밀 폴더' / '회의 메모.md'
    note.parent.mkdir()
    note.write_text('개발팀이 캐시를 도입하기로 결정했습니다.', encoding='utf-8')
    doc = call(service, 'markdown.import', path=str(note))
    service.llm.responses = [{'answer': '캐시를 도입합니다. [1]', 'citations': [1]}]
    result = call(service, 'assistant.ask', question='결정은?', document_ids=[doc['doc_id']])
    sent = service.llm.calls[-1][1]['documents'][0]
    assert sent['source'] == 'Markdown · 회의 메모.md' and str(tmp_path) not in json.dumps(service.llm.calls)
    # The citation keeps the path, which the screen copies as the original's address.
    assert result['messages'][-1]['citations'][0]['source']['source_path'] == str(note)
    assert search.source_label({'kind': 'confluence', 'source_url': 'https://wiki.example.com/x?pageId=1'}) == \
        'Confluence · wiki.example.com'
    assert search.source_label({'kind': 'office', 'source_path': 'C:\\업무\\설계.docx'}) == 'Office · 설계.docx'


def test_existing_wiki_migration_does_not_reseed_or_drop_data(service):
    doc = document(service)
    task(service)
    with service.db.connect() as db:
        db.execute('DELETE FROM prompts')
    restored = Service(Database(service.db.path), FakeLLM())
    assert restored.wiki_get({'id': doc['id']})['content'] == doc['content']
    assert len(call(restored, 'tasks.list')) == 1
    assert restored.prompts_list({}) == []


def test_editing_completed_task_does_not_change_completion_date(service, monkeypatch):
    import agent.assistant as module
    monkeypatch.setattr(module, 'now', lambda: '2026-09-27T00:00:00+00:00')
    created = task(service, status='done')
    monkeypatch.setattr(module, 'now', lambda: '2026-09-28T00:00:00+00:00')
    updated = call(service, 'tasks.save', id=created['id'], notes='완료 후 메모 수정')
    assert updated['completed_at'] == created['completed_at'] == '2026-09-27T00:00:00+00:00'
    reopened = call(service, 'tasks.save', id=created['id'], status='open')
    assert reopened['completed_at'] is None
    completed = call(service, 'tasks.save', id=created['id'], status='done')
    assert completed['completed_at'] == '2026-09-28T00:00:00+00:00'
