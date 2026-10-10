import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.db import Database
from agent.llm import LLM, UserError
from agent.service import Service


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.result = {"summary": "Python API 성능 개선 기록", "keywords": ["Python", "API", "캐시"],
                       "relations": [{"source": "Python", "target": "API", "label": "구현"}, {"source": "캐시", "target": "API", "label": "성능 개선"}]}

    def structured(self, system, content):
        self.calls.append((system, content))
        return self.result

    def complete(self, messages):
        self.calls.append(messages)
        return "검토 결과입니다."


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "test.db"), FakeLLM())


def document(service, title="API 성능 개선", content="Python API에 캐시를 도입해 100% 개선했습니다."):
    return service.wiki_save({"title": title, "content": content})


def test_analyze_search_and_graph(service):
    doc = document(service)
    analyzed = service.wiki_analyze({"id": doc["id"]})
    assert analyzed["keywords"] == ["api", "python", "캐시"]
    assert analyzed["status"] == "ready"
    assert len(analyzed["relations"]) == 2
    graph = service.wiki_graph({})
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    assert all(node["docs"] == [doc["id"]] for node in graph["nodes"])
    assert graph["documents"] == [{"id": doc["id"], "title": doc["title"]}]
    assert len(service.wiki_search({"query": "ＰＹＴＨＯＮ", "mode": "keyword"})["documents"]) == 1
    assert not service.wiki_search({"query": "py", "mode": "keyword"})["documents"]
    assert len(service.wiki_search({"query": "unknown, 캐시", "mode": "keyword"})["documents"]) == 1
    # A relation found in two documents is one discovered relation with both sources.
    second = service.wiki_analyze({"id": document(service, "캐시 회고")["id"]})
    graph = service.wiki_graph({})
    assert [edge["count"] for edge in graph["edges"]] == [2, 2]
    assert set(graph["edges"][0]["docs"]) == {doc["id"], second["id"]}
    assert graph["total_relations"] == service.stats({})["relations"] == 2


def test_literal_search_escapes_wildcards_and_sql(service):
    document(service)
    document(service, "다른 문서", "성능 개선 기록입니다.")
    assert len(service.wiki_search({"query": "%", "mode": "word"})["documents"]) == 1
    assert not service.wiki_search({"query": "_", "mode": "word"})["documents"]
    assert not service.wiki_search({"query": "' OR 1=1 --", "mode": "word"})["documents"]
    assert not service.llm.calls


def test_an_analysis_loads_the_html_page_only_for_the_document_window(service):
    """Imports and batch analysis analyze one document after another and drop what it returns; only the document
    window's request shows the page, which may hold megabytes of pictures."""
    doc = document(service)
    with service.db.connect() as db:
        db.execute("UPDATE documents SET format='html' WHERE id=?", (doc["id"],))
        db.execute("INSERT INTO document_html VALUES (?, '<p>페이지</p>')", (doc["id"],))
    analyzed = service.wiki_analyze({"id": doc["id"]})
    assert "html" not in analyzed and analyzed["status"] == "ready"
    shown = service.dispatch("wiki.analyze", {"id": doc["id"]})
    assert (shown["html"], shown["status"], shown["keywords"]) == ("<p>페이지</p>", "ready", ["api", "python", "캐시"])


def test_prompt_search_analyzes_only_the_question_and_keeps_keyword_results(service):
    doc = document(service)
    service.llm.result = {"terms": ["캐시"], "keywords": ["API"], "explanation": "API 성능 개선 기록을 검색합니다."}
    result = service.wiki_search({"query": "속도를 높인 기록을 찾아줘", "mode": "prompt"})
    assert result["documents"][0]["id"] == doc["id"]
    # The analysis gets the question (and existing keywords), never documents. "API" is not a Wiki
    # keyword yet, so it is searched as a word. The unusable AI search answer keeps the keyword ranking.
    assert json.loads(service.llm.calls[0][1]) == {"question": "속도를 높인 기록을 찾아줘", "wiki_keywords": []}
    assert result["plan"]["terms"] == ["캐시", "api"] and not result["ai"]
    assert len(service.llm.calls) == 2


def test_edit_invalidates_metadata_and_delete_cascades(service):
    doc = document(service)
    service.wiki_analyze({"id": doc["id"]})
    edited = service.wiki_save({"id": doc["id"], "title": "다른 주제", "content": "새 문서입니다."})
    assert edited["status"] == "pending"
    assert edited["summary"] == ""
    assert edited["keywords"] == edited["relations"] == []
    service.wiki_analyze({"id": doc["id"]})
    service.wiki_delete({"id": doc["id"]})
    assert service.wiki_graph({})["nodes"] == []
    assert service.stats({})["relations"] == 0


def test_delete_many_removes_the_chosen_documents_together(service):
    first, second, kept = document(service), document(service, "회고"), document(service, "남길 문서")
    service.wiki_analyze({"id": first["id"]})
    # Repeated and unknown IDs are no error; only the documents that were there count.
    moved = service.wiki_delete_many({"ids": [first["id"], second["id"], first["id"], "gone"]})
    assert (moved["deleted"], sorted(moved["ids"])) == (2, sorted([first["id"], second["id"]]))
    assert [doc["id"] for doc in service.wiki_list({})] == [kept["id"]]
    assert service.wiki_graph({})["nodes"] == []
    for bad in (None, [], kept["id"], ["x"] * 10001):
        with pytest.raises(UserError, match="문서를 1~10,000개 선택해 주세요"):
            service.wiki_delete_many({"ids": bad})
    with pytest.raises(UserError, match="ID"):
        service.wiki_delete_many({"ids": [kept["id"], 1]})
    assert len(service.wiki_list({})) == 1


def test_a_deleted_document_waits_in_the_trash_until_it_is_restored_or_purged(service):
    doc, other = document(service), document(service, "남는 문서", "다른 내용")
    service.wiki_analyze({"id": doc["id"]})
    service.dispatch("tasks.save", {"title": "확인", "source_id": doc["id"]})
    assert service.wiki_delete({"id": doc["id"]}) == {"ok": True, "deleted": 1, "ids": [doc["id"]]}
    # Gone from everything that shows documents, but not from the database.
    assert [d["id"] for d in service.wiki_list({})] == [other["id"]]
    assert not service.wiki_search({"query": "캐시", "mode": "word"})["documents"]
    assert service.stats({}) == {"prompts": 4, "documents": 1, "keywords": 0, "relations": 0}
    assert service.wiki_graph({})["nodes"] == []
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        service.wiki_get({"id": doc["id"]})
    trash = service.dispatch("wiki.trash", {})
    assert [d["id"] for d in trash["documents"]] == [doc["id"]] and trash["days"] == 30
    assert trash["documents"][0]["deleted_at"] and trash["documents"][0]["keywords"] == ["api", "python", "캐시"]
    # Deleting it again, or restoring a document that is not in the trash, changes nothing.
    assert service.wiki_delete({"id": doc["id"]})["deleted"] == 0
    assert service.dispatch("wiki.restore", {"ids": [other["id"]]}) == {"restored": 0}
    # Restored with its analysis, keywords and task as they were.
    assert service.dispatch("wiki.restore", {"ids": [doc["id"]]}) == {"restored": 1}
    back = service.wiki_get({"id": doc["id"]})
    assert (back["keywords"], back["status"]) == (["api", "python", "캐시"], "ready")
    assert service.wiki_search({"query": "캐시", "mode": "word"})["documents"][0]["id"] == doc["id"]
    assert service.dispatch("tasks.list", {})[0]["source_title"] == doc["title"]
    # Purged: nothing brings it back.
    service.wiki_delete({"id": doc["id"]})
    assert service.dispatch("wiki.purge", {"ids": [doc["id"]]}) == {"purged": 1}
    assert service.dispatch("wiki.trash", {})["documents"] == []
    assert service.dispatch("wiki.restore", {"ids": [doc["id"]]}) == {"restored": 0}
    with pytest.raises(UserError, match="all"):
        service.dispatch("wiki.purge", {"all": "yes"})
    # The whole trash at once; what has waited 30 days goes by itself at the next start.
    service.wiki_delete({"id": other["id"]})
    assert service.dispatch("wiki.purge", {"all": True}) == {"purged": 1}
    old = document(service, "오래된 문서")
    service.wiki_delete({"id": old["id"]})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET deleted_at='2020-01-01T00:00:00+00:00' WHERE id=?", (old["id"],))
    assert Service(Database(service.db.path), FakeLLM()).dispatch("wiki.trash", {})["documents"] == []


def test_clear_analysis_returns_documents_to_before_their_analysis(tmp_path):
    from test_search import RoutingLLM, long_document

    service = Service(Database(tmp_path / "wiki.db"), RoutingLLM())
    short, long, plain, failed, busy = (service.wiki_save({"title": title, "content": content})["id"] for title, content in (
        ("짧은 문서", "캐시 전략을 정리했습니다."), ("긴 문서", long_document()), ("원문", "분석하지 않은 글"),
        ("실패한 문서", "분석이 실패한 글"), ("분석 중인 문서", "지금 분석하는 글")))
    service.wiki_analyze({"id": short})
    service.wiki_analyze({"id": long})
    service.llm.extract = UserError("LLM 응답 형식이 올바르지 않습니다.")
    with pytest.raises(UserError):
        service.wiki_analyze({"id": failed})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET status='indexing' WHERE id=?", (busy,))
    before = service.wiki_get({"id": long})
    assert before["index_mode"] == "pageindex" and before["outline"]
    # The unanalyzed document has nothing to take back; the one being analyzed is left alone.
    assert service.wiki_clear_analysis({"ids": [short, long, plain, failed, busy]}) == {"cleared": 3, "busy": 1}
    for ident in (short, long, failed):
        doc = service.wiki_get({"id": ident})
        assert (doc["status"], doc["summary"], doc["error"], doc["index_mode"]) == ("pending", "", "", "")
        assert doc["keywords"] == doc["relations"] == doc["outline"] == []
    after = service.wiki_get({"id": long})
    assert (after["content"], after["revision"], after["updated_at"]) == \
        (before["content"], before["revision"], before["updated_at"])
    assert service.wiki_get({"id": busy})["status"] == "indexing"
    assert service.wiki_graph({})["nodes"] == []
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM doc_nodes").fetchone()[0] == 0
    # Cleared documents can be analyzed again.
    service.llm.extract = {"summary": "다시 분석", "keywords": ["cache"], "relations": []}
    assert service.wiki_analyze({"id": short})["index_mode"] == "wiki"


@pytest.mark.parametrize("bad", [None, [], "wrong", [123, "", " "]])
def test_invalid_metadata_keeps_source_document(service, bad):
    doc = document(service)
    service.llm.result["keywords"] = bad
    with pytest.raises(UserError):
        service.wiki_analyze({"id": doc["id"]})
    saved = service.wiki_get({"id": doc["id"]})
    assert saved["content"] == doc["content"]
    assert saved["status"] == "error"
    assert saved["keywords"] == []


def test_what_the_llm_got_wrong_is_left_out_and_the_rest_of_the_analysis_kept(service):
    doc = document(service)
    service.llm.result["keywords"] += [123, "", "x" * 81]
    service.llm.result["relations"] += [{"source": "hallucinated", "target": "API", "label": "가짜"},
                                        {"source": "Python", "target": "캐시", "label": " "}, "not a relation"]
    service.llm.result["summary"] = "요" * 3000
    analyzed = service.wiki_analyze({"id": doc["id"]})
    assert analyzed["keywords"] == ["api", "python", "캐시"]
    assert sorted((r["source"], r["target"]) for r in analyzed["relations"]) == [("python", "api"), ("캐시", "api")]
    assert len(analyzed["summary"]) == 2000
    service.llm.result["summary"] = None
    with pytest.raises(UserError, match="요약"):
        service.wiki_analyze({"id": document(service, "다른 문서")["id"]})


def aliases(service):
    with service.db.connect() as db:
        return [tuple(row) for row in db.execute(
            "SELECT keyword,alias,normalized FROM keyword_aliases ORDER BY keyword,normalized")]


def test_the_analysis_keeps_up_to_five_aliases_of_each_keyword(service):
    """The analysis asks for other names of each keyword (Korean and English forms, abbreviations) and keeps them as
    written and as search compares words. Names of keywords the answer does not have, a keyword's own name, repeats in
    another form, names too long and those beyond five a keyword are left out."""
    doc = document(service)
    service.llm.result["aliases"] = {
        "Python": ["파이썬", "PY", "py", "Ｐｙｔｈｏｎ", "  파이썬  ", "python3"],
        "API": ["에이피아이", "Application  Programming Interface", "a", "b", "c", "d"],
        "캐시": ["cache", "x" * 81, ""], "없는키워드": ["무시"]}
    analyzed = service.wiki_analyze({"id": doc["id"]})
    system = service.llm.calls[-1][0]
    assert '"aliases"' in system and "at most 5" in system and "Use lowercase English keywords" in system
    assert analyzed["keywords"] == ["api", "python", "캐시"]
    assert aliases(service) == [
        ("api", "a", "a"), ("api", "Application Programming Interface", "application programming interface"),
        ("api", "b", "b"), ("api", "c", "c"), ("api", "에이피아이", "에이피아이"),
        ("python", "PY", "py"), ("python", "python3", "python3"), ("python", "파이썬", "파이썬"), ("캐시", "cache", "cache")]
    assert [d["id"] for d in service.wiki_search({"query": "파이썬", "mode": "keyword"})["documents"]] == [doc["id"]]
    # Analyzed again, the document keeps only the new answer's aliases.
    service.llm.result["aliases"] = {"python": ["파이선"]}
    service.wiki_analyze({"id": doc["id"]})
    assert aliases(service) == [("python", "파이선", "파이선")]


@pytest.mark.parametrize("bad", [None, "파이썬", ["파이썬"], {"python": "파이썬"}, {"": ["x"], "python": None},
                                 {"python": [1, None, {"x": 1}, ["파이썬"], " ", "x" * 81]}])
def test_aliases_in_another_form_are_left_out_and_the_analysis_kept(service, bad):
    doc = document(service)
    service.llm.result["aliases"] = bad
    analyzed = service.wiki_analyze({"id": doc["id"]})
    assert (analyzed["status"], analyzed["keywords"], len(analyzed["relations"])) == ("ready", ["api", "python", "캐시"], 2)
    assert aliases(service) == []


def test_saving_the_same_text_keeps_the_ai_analysis(service):
    doc = document(service)
    analyzed = service.wiki_analyze({"id": doc["id"]})
    kept = ("summary", "keywords", "relations", "status", "index_mode", "revision", "format")
    # Saved without a change, or with only a new title: what the analysis found still holds.
    same = service.wiki_save({"id": doc["id"], "title": doc["title"], "content": doc["content"] + "\n",
                              "expected_revision": analyzed["revision"]})
    assert {key: same[key] for key in kept} == {key: analyzed[key] for key in kept}
    renamed = service.wiki_save({"id": doc["id"], "title": "새 제목", "content": doc["content"],
                                 "expected_revision": analyzed["revision"]})
    assert renamed["title"] == "새 제목" and {key: renamed[key] for key in kept} == {key: analyzed[key] for key in kept}
    # New text clears it, in the same save.
    edited = service.wiki_save({"id": doc["id"], "title": "새 제목", "content": "다른 내용",
                                "expected_revision": analyzed["revision"]})
    assert (edited["summary"], edited["keywords"], edited["status"], edited["index_mode"]) == ("", [], "pending", "")
    assert edited["revision"] == analyzed["revision"] + 1


def test_an_edit_never_overwrites_text_that_changed_while_it_was_open(service):
    doc = document(service)
    # Another window, an AI tool or an import changed the text after the editor opened it.
    service.wiki_save({"id": doc["id"], "title": doc["title"], "content": "다른 곳에서 고친 내용"})
    with pytest.raises(UserError, match="다른 곳에서 문서가 바뀌었습니다"):
        service.wiki_save({"id": doc["id"], "title": doc["title"], "content": "내가 쓴 내용",
                           "expected_revision": doc["revision"]})
    assert service.wiki_get({"id": doc["id"]})["content"] == "다른 곳에서 고친 내용"
    service.wiki_delete({"id": doc["id"]})
    with pytest.raises(UserError, match="휴지통으로 옮겨졌습니다"):
        service.wiki_save({"id": doc["id"], "title": "t", "content": "c", "expected_revision": doc["revision"]})
    with pytest.raises(UserError, match="휴지통에 있는 문서"):
        service.wiki_save({"id": doc["id"], "title": "t", "content": "c"})
    service.wiki_purge({"ids": [doc["id"]]})
    with pytest.raises(UserError, match="삭제되었습니다"):
        service.wiki_save({"id": doc["id"], "title": "t", "content": "c", "expected_revision": doc["revision"]})
    for bad in ("1", 1.5, True):
        with pytest.raises(UserError, match="expected_revision"):
            service.wiki_save({"title": "t", "content": "c", "expected_revision": bad})
    # Without it, as before, the last save wins.
    assert service.wiki_save({"id": doc["id"], "title": "새 문서", "content": "다시"})["content"] == "다시"


def test_a_refused_edit_compares_the_text_it_started_from_with_both_sides(service):
    """The document window keeps the text its editor opened (no earlier text is stored): wiki.compare sets it beside the
    editor's text and the document's, so the user sees what changed elsewhere, what they changed and what saving over
    the document would do, and nothing changes until they choose."""
    base = "# 배포 계획\n\n- 월요일 준비\n- 화요일 배포\n- 금요일 회고"
    doc = document(service, "배포 계획", base)
    service.wiki_analyze({"id": doc["id"]})
    theirs = service.wiki_save({"id": doc["id"], "title": "배포 계획",
                                "content": "# 배포 계획\n\n- 월요일 준비\n- 목요일 배포\n- 금요일 회고"})
    mine = base + "\n- 다음 주 점검"
    with pytest.raises(UserError, match="다른 곳에서 문서가 바뀌었습니다"):
        service.wiki_save({"id": doc["id"], "title": "배포 계획", "content": mine, "expected_revision": doc["revision"]})
    stored = service.wiki_get({"id": doc["id"]})
    conflict = service.dispatch("wiki.compare", {"id": doc["id"], "base_revision": doc["revision"], "base": base,
                                                 "content": mine})
    assert conflict["changed"] is True
    assert {key: conflict["document"][key] for key in ("id", "title", "content", "revision")} == \
        {key: theirs[key] for key in ("id", "title", "content", "revision")}
    assert "html" not in conflict["document"]

    def lines(change):
        return [(line["op"], line["text"]) for hunk in change["hunks"] for line in hunk["lines"]]

    elsewhere = conflict["diffs"]["theirs"]
    assert (elsewhere["added"], elsewhere["removed"], elsewhere["more"]) == (1, 1, 0)
    assert [(hunk["old_start"], hunk["new_start"]) for hunk in elsewhere["hunks"]] == [(1, 1)]
    assert lines(elsewhere) == [(" ", "# 배포 계획"), (" ", ""), (" ", "- 월요일 준비"), ("-", "- 화요일 배포"),
                                ("+", "- 목요일 배포"), (" ", "- 금요일 회고")]
    assert [line for line in lines(conflict["diffs"]["ours"]) if line[0] != " "] == [("+", "- 다음 주 점검")]
    # Saving over the document would take back what changed elsewhere and add what the user wrote.
    assert [line for line in lines(conflict["diffs"]["overwrite"]) if line[0] != " "] == [
        ("-", "- 목요일 배포"), ("+", "- 화요일 배포"), ("+", "- 다음 주 점검")]
    # Only read: the document, its analysis state and its revision are as they were.
    assert service.wiki_get({"id": doc["id"]}) == stored
    # Saved over the revision the comparison showed, the user's text wins; compared again, nothing changed since.
    saved = service.wiki_save({"id": doc["id"], "title": "배포 계획", "content": mine,
                               "expected_revision": conflict["document"]["revision"]})
    assert saved["content"] == mine and saved["revision"] == theirs["revision"] + 1
    again = service.dispatch("wiki.compare", {"id": doc["id"], "base_revision": saved["revision"], "base": mine,
                                              "content": mine})
    assert again["changed"] is False
    assert all(change == {"added": 0, "removed": 0, "hunks": [], "more": 0} for change in again["diffs"].values())
    # Without a revision, a text that differs from the one the editor started from is a change.
    assert service.dispatch("wiki.compare", {"id": doc["id"], "base": base, "content": mine})["changed"] is True
    for bad in ({"base_revision": "1"}, {"base_revision": True}, {"base_revision": 1.5}):
        with pytest.raises(UserError, match="base_revision"):
            service.dispatch("wiki.compare", {"id": doc["id"], "base": base, "content": mine, **bad})
    with pytest.raises(UserError, match="편집을 시작한 문서"):
        service.dispatch("wiki.compare", {"id": doc["id"], "base": ["not text"], "content": mine})
    with pytest.raises(UserError, match="문서"):
        service.dispatch("wiki.compare", {"id": doc["id"], "base": base, "content": "x" * 80001})
    # A document moved to the trash meanwhile is no conflict to compare: the window tells wiki.save's refusal.
    service.wiki_delete({"id": doc["id"]})
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        service.dispatch("wiki.compare", {"id": doc["id"], "base_revision": doc["revision"], "base": base,
                                          "content": mine})


def test_a_comparison_shows_changes_among_a_few_unchanged_lines_and_stays_bounded():
    from agent.changes import diff
    old = "\n".join(f"줄 {n}" for n in range(1, 31))
    new = old.replace("줄 5", "다섯째 줄").replace("줄 9", "아홉째 줄").replace("줄 25", "스물다섯째 줄")
    change = diff(old, new)
    # Changes close together share a hunk; each hunk starts three unchanged lines before its first change.
    assert [(hunk["old_start"], hunk["new_start"], len(hunk["lines"])) for hunk in change["hunks"]] == \
        [(2, 2, 13), (22, 22, 8)]
    assert (change["added"], change["removed"], change["more"]) == (3, 3, 0)
    # Lines past the limit are counted, not sent; a hunk cut before its first change is left out whole.
    cut = diff(old, new, limit=9)
    assert [line["op"] for line in cut["hunks"][0]["lines"]] == [" ", " ", " ", "-", "+", " ", " ", " ", "-"]
    assert len(cut["hunks"]) == 1 and cut["more"] == 3
    assert diff(old, new, limit=2) == {"added": 3, "removed": 3, "hunks": [], "more": 6}
    # Blank lines count, line breaks of any kind and trailing spaces do not.
    assert diff("가\r\n\r\n나\r\n", "가\n\n나") == {"added": 0, "removed": 0, "hunks": [], "more": 0}
    assert diff("가  \n나", "가\n나")["hunks"] == []
    assert diff("가\n나", "가\n\n나")["hunks"][0]["lines"] == [
        {"op": " ", "text": "가"}, {"op": "+", "text": ""}, {"op": " ", "text": "나"}]
    assert diff("", "새 글")["hunks"] == [{"old_start": 1, "new_start": 1, "lines": [{"op": "+", "text": "새 글"}]}]


def test_concurrent_edit_cannot_receive_old_analysis(service):
    doc = document(service)
    started, release = threading.Event(), threading.Event()
    original = service.llm.structured

    def slow(system, content):
        started.set()
        assert release.wait(5)
        return original(system, content)

    service.llm.structured = slow
    with ThreadPoolExecutor() as pool:
        job = pool.submit(service.wiki_analyze, {"id": doc["id"]})
        assert started.wait(5)
        service.wiki_save({"id": doc["id"], "title": "최신", "content": "업데이트된 내용"})
        release.set()
        with pytest.raises(UserError, match="수정 또는 삭제"):
            job.result()
    current = service.wiki_get({"id": doc["id"]})
    assert current["status"] == "pending"
    assert current["keywords"] == []


def test_prompt_variables_and_saved_history(service):
    prompt = service.prompts_save({"title": "테스트", "template": "요약: {{ 내용 }}", "system_prompt": "친절하게", "category": "업무"})
    with pytest.raises(UserError):
        service.prompts_run({"prompt_id": prompt["id"]})
    result = service.prompts_run({"prompt_id": prompt["id"], "variables": {"내용": "주간 계획"}})
    assert result["input"] == "요약: 주간 계획"
    listed = service.runs_list({})
    assert len(listed) == 1  # The rejected input above is not recorded.
    assert listed[0]["preview"] == "검토 결과입니다." and listed[0]["status"] == "success"
    assert service.runs_get({"id": result["id"]})["output"] == "검토 결과입니다."
    service.runs_delete({"id": result["id"]})
    assert service.runs_list({}) == []


def test_a_template_change_keeps_what_it_leaves_out(service):
    fields = ("title", "template", "description", "category", "system_prompt")
    prompt = service.prompts_save({"title": "보고", "template": "{{내용}}", "description": "주간 보고", "category": "업무",
                                   "system_prompt": "간결하게"})
    # An AI tool's PUT /prompts/{id} sends what its catalog asks for: the title and the prompt.
    changed = service.prompts_save({"id": prompt["id"], "title": "주간 보고", "template": "{{내용}} 정리"})
    assert [changed[key] for key in fields] == ["주간 보고", "{{내용}} 정리", "주간 보고", "업무", "간결하게"]
    assert changed["created_at"] == prompt["created_at"] and changed["updated_at"] > prompt["updated_at"]
    # A null keeps a value too; an empty text clears one that may be empty.
    cleared = service.prompts_save({"id": prompt["id"], "description": "", "system_prompt": None})
    assert [cleared[key] for key in fields] == ["주간 보고", "{{내용}} 정리", "", "업무", "간결하게"]
    # A new template takes the defaults and needs a title and a prompt.
    fresh = service.prompts_save({"title": "새 템플릿", "template": "본문"})
    assert [fresh[key] for key in fields] == ["새 템플릿", "본문", "", "나만의 프롬프트", ""]
    with pytest.raises(UserError, match="제목"):
        service.prompts_save({"template": "본문"})


def test_data_persists_and_seed_does_not_return_after_deletion(tmp_path):
    path = tmp_path / "test.db"
    a = Service(Database(path), FakeLLM())
    doc = document(a)
    for prompt in a.prompts_list({}):
        a.prompts_delete({"id": prompt["id"]})
    with a.db.connect() as db:
        db.execute("UPDATE documents SET status='indexing'")
    b = Service(Database(path), FakeLLM())
    assert b.prompts_list({}) == []
    assert b.wiki_get({"id": doc["id"]})["status"] == "pending"


def test_a_copied_certificate_path_loses_its_quotes():
    # Explorer's "Copy as path" puts the path in double quotes.
    assert LLM().update({"ca_bundle": ' "C:\\인증서\\private-ca.pem" '})["ca_bundle"] == "C:\\인증서\\private-ca.pem"


@pytest.mark.parametrize("value", [float('nan'), float('inf'), -1, 5])
def test_invalid_temperature(value):
    with pytest.raises(UserError):
        LLM().update({"temperature": value})


@pytest.mark.parametrize("reply", [
    '```json\n{"keywords": ["python"]}\n```',
    '```json {"keywords": ["python"]} ```',
    '분석 결과입니다.\n{"keywords": ["python"]}\n이상입니다.',
    '<think>{"keywords": ["wrong"]} 생각해 보면…</think>\n{"keywords": ["python"]}',
    '{"keywords": ["python"]} {"keywords": ["second"]}',
])
def test_llm_json_is_found_among_what_models_put_around_it(monkeypatch, reply):
    llm = LLM()
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: reply)
    assert llm.structured("system", "doc")["keywords"] == ["python"]


@pytest.mark.parametrize("reply", ["not JSON", "[1, 2]", "<think>{\"keywords\": [\"cut short\"]}", '{"keywords": '])
def test_llm_reply_without_a_json_object_is_a_format_error(monkeypatch, reply):
    from agent.llm import LLMFormatError
    llm = LLM()
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: reply)
    with pytest.raises(LLMFormatError, match="JSON"):
        llm.structured("system", "doc")


def configured_llm():
    llm = LLM()
    llm.update({"provider": "general", "base_url": "https://llm.example/api", "api_key": "test-secret", "model_name": "test-model"})
    return llm


@pytest.mark.parametrize("status", [302, 401, 429, 503])
def test_http_errors_never_expose_response_body_or_credentials(monkeypatch, status):
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def json(self): raise AssertionError("Error body must not be read or disclosed")

    # Every request goes through the LLM's session; the retries of 429 and 503 do not wait here.
    monkeypatch.setattr("agent.llm.requests.Session.post", lambda *a, **kw: Response())
    monkeypatch.setattr("agent.llm.BACKOFF", 0)
    with pytest.raises(UserError, match=f"HTTP {status}") as error:
        configured_llm().complete([{"role": "user", "content": "hi"}])
    assert "test-secret" not in str(error.value)
    assert "https://llm.example" not in str(error.value)


def test_failed_llm_request_preserves_existing_configuration(monkeypatch):
    import requests
    def fail(*args, **kwargs):
        raise requests.exceptions.Timeout("sensitive diagnostic test-secret")
    monkeypatch.setattr("agent.llm.requests.Session.post", fail)
    monkeypatch.setattr("agent.llm.BACKOFF", 0)
    llm = configured_llm()
    with pytest.raises(UserError, match="제한 시간") as error:
        llm.complete([{"role": "user", "content": "hi"}])
    assert "test-secret" not in str(error.value)
    assert llm.public()["configured"]


def test_dashboard_preferences_merge_validate_and_persist(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    # The LLM's help with logins is off until the user turns it on: it sends login pages to the LLM server.
    assert service.dispatch("preferences.get", {}) == {"widgets": None, "ai_answer": True, "web_assist": False}
    layout = [{"id": "briefing", "size": "l"}, {"id": "tasks", "size": "m", "extra": "dropped"}]
    saved = service.dispatch("preferences.save", {"widgets": layout})
    assert saved["widgets"] == [{"id": "briefing", "size": "l"}, {"id": "tasks", "size": "m"}]
    # The admin page saves each AI option alone; the layout and the other option stay.
    assert service.dispatch("preferences.save", {"ai_answer": False})["widgets"] == saved["widgets"]
    assert service.dispatch("preferences.save", {"web_assist": True})["ai_answer"] is False
    restarted = Service(Database(tmp_path / "wiki.db"), None)
    assert restarted.dispatch("preferences.get", {}) == {"widgets": saved["widgets"], "ai_answer": False,
                                                         "web_assist": True}
    for bad in ([{"id": "Tasks", "size": "m"}], [{"id": "tasks", "size": "huge"}],
                [{"id": "tasks", "size": "m"}, {"id": "tasks", "size": "s"}], "tasks", [{"id": "x", "size": "m"}] * 41,
                [{"id": "tasks-2", "type": "Tasks", "size": "m"}], [{"id": "tasks-2", "view": 3, "size": "m"}],
                [{"id": "tasks-2", "type": None, "size": "m"}], [{"id": "tasks", "size": "m", "height": "xl"}],
                # Every JavaScript object answers `constructor`: the screen would take it for a widget.
                [{"id": "constructor", "size": "m"}], [{"id": "tasks", "type": "constructor", "size": "m"}],
                [{"id": "tasks", "type": "tasks", "view": "constructor", "size": "m"}]):
        with pytest.raises(UserError, match="위젯"):
            service.dispatch("preferences.save", {"widgets": bad})
    with pytest.raises(UserError, match="AI"):
        service.dispatch("preferences.save", {"ai_answer": "yes"})
    with pytest.raises(UserError, match="AI 분석 설정"):
        service.dispatch("preferences.save", {"web_assist": 1})
    # A damaged record falls back to the defaults instead of breaking the dashboard.
    with service.db.connect() as db:
        db.execute("UPDATE metadata SET value='{broken' WHERE key='preferences'")
    assert service.dispatch("preferences.get", {}) == {"widgets": None, "ai_answer": True, "web_assist": False}
    assert service.dispatch("preferences.save", {"widgets": None})["widgets"] is None


def test_a_dashboard_places_one_content_in_several_formats_and_heights(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    layout = [{"id": "tasks", "type": "tasks", "view": "checklist", "size": "m", "height": "auto"},
              {"id": "tasks-2", "type": "tasks", "view": "week", "size": "xl", "height": "m"},
              {"id": "briefing", "size": "l"}]
    saved = service.dispatch("preferences.save", {"widgets": layout})["widgets"]
    # An auto height is the default and left out; a layout saved before formats keeps its plain items.
    assert saved == [{"id": "tasks", "type": "tasks", "view": "checklist", "size": "m"},
                     {"id": "tasks-2", "type": "tasks", "view": "week", "size": "xl", "height": "m"},
                     {"id": "briefing", "size": "l"}]
    assert Service(Database(tmp_path / "wiki.db"), None).dispatch("preferences.get", {})["widgets"] == saved
    # A damaged item of a stored layout is left out instead of breaking the dashboard, a name the screen would take
    # for a function (saved by an earlier version) too.
    with service.db.connect() as db:
        db.execute("UPDATE metadata SET value=? WHERE key='preferences'", (json.dumps({"widgets": [
            {"id": "tasks", "type": "tasks", "view": "board", "size": "m", "height": "huge"},
            {"id": "constructor", "type": "constructor", "size": "m"},
            {"id": "tasks-2", "type": "tasks", "view": "constructor", "size": "m"},
            {"id": "documents", "type": "documents", "view": "cards", "size": "l", "height": "s"}]}),))
    assert service.dispatch("preferences.get", {})["widgets"] == [
        {"id": "documents", "type": "documents", "view": "cards", "size": "l", "height": "s"}]
    # A stored layout longer than a save allows keeps its first widgets rather than none.
    with service.db.connect() as db:
        db.execute("UPDATE metadata SET value=? WHERE key='preferences'", (json.dumps({"widgets": [
            {"id": f"tasks-{n}", "type": "tasks", "size": "s"} for n in range(2, 45)]}),))
    assert [item["id"] for item in service.dispatch("preferences.get", {})["widgets"]] == [
        f"tasks-{n}" for n in range(2, 42)]


def test_a_request_the_backend_cannot_read_is_refused_and_the_next_one_answered(tmp_path):
    """Nesting deeper than the JSON parser goes raised RecursionError, which ended the backend for every screen."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    deep = '{"id":"deep","method":"health","params":{"a":' + "[" * 100_000 + "]" * 100_000 + "}}"
    lines = [deep, json.dumps({"id": "nameless", "method": 7}), "not json", json.dumps({"id": "ok", "method": "health"})]
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(backend / "main.py")], input="\n".join(lines) + "\n",
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                            env={**os.environ, "PRIVATE_AGENT_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1"})
    assert result.returncode == 0, result.stderr
    replies = {reply["id"]: reply for reply in map(json.loads, result.stdout.splitlines())}
    # The callers hear back at once, by the id their line starts with when it can be read at all.
    assert replies["deep"]["error"] == replies["nameless"]["error"] == replies[None]["error"] == "잘못된 RPC 요청입니다."
    assert replies["ok"]["result"] == {"ok": True}


def test_quick_requests_have_their_own_lane_beside_llm_requests(tmp_path):
    """Four answers in progress must not refuse the dashboard's list and status reads."""
    import contextlib
    import os
    import subprocess
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    release = threading.Event()

    class SlowLLM(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            release.wait(20)
            body = json.dumps({"choices": [{"message": {"content": "연결 성공"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    backend = Path(__file__).resolve().parents[1]
    # Windows starts Python and opens sockets only with its own variables (SystemRoot, USERPROFILE), so the child
    # keeps the environment. Every LLM setting is given, so a developer's .env changes nothing, and no proxy, from
    # the environment or the Windows settings, stands between the backend and the local LLM.
    env = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")}
    env.update({"PRIVATE_AGENT_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1", "PYTHONPATH": "", "NO_PROXY": "127.0.0.1",
                "BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "API_KEY": "k", "MODEL_NAME": "m",
                "TEMPERATURE": "0.5", "LLM_TIMEOUT": "120", "CA_BUNDLE": "", "LLM_MAX_TOKENS": "0",
                "LLM_CONTEXT_WINDOW": "64000", "LLM_CONCURRENCY": "3"})
    child = subprocess.Popen([sys.executable, str(backend / "main.py")], cwd=backend, text=True, encoding="utf-8",
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    try:
        def send(ident, method, params=None):
            child.stdin.write(json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n")
            child.stdin.flush()

        def receive():
            line = child.stdout.readline()
            assert line, f"the backend exited with code {child.wait(20)}; its error is in the captured stderr"
            reply = json.loads(line)
            replies[reply["id"]] = reply

        for i in range(5):
            send(f"slow{i}", "settings.test")
        time.sleep(0.5)
        for i in range(10):
            send(f"quick{i}", "wiki.list")
        replies = {}
        while len(replies) < 11:
            receive()
        # The fifth answer is refused; every quick read is served while four answers still wait.
        assert "진행 중인 요청이 많습니다" in replies["slow4"]["error"]
        assert all(replies[f"quick{i}"]["result"] == [] for i in range(10))
        release.set()
        while len(replies) < 15:
            receive()
        assert all(replies[f"slow{i}"]["result"]["message"] == "연결 성공" for i in range(4))
    finally:
        release.set()
        with contextlib.suppress(OSError):  # The pipe is already closed when the backend has exited.
            child.stdin.close()
        child.wait(20)
        child.stdout.close()
        server.shutdown()
        server.server_close()


def test_requests_the_user_waits_for_get_the_llm_first():
    """The LLM requests of a question, a test of the connection or a prompt run go before batch work's (llm.Slots);
    those of a scheduled run, an import or a routine do not. Stopping an answer is a quick request of its own."""
    import main
    from agent.llm import BATCH, INTERACTIVE
    assert all(main.priority_of({"method": name}) == INTERACTIVE
               for name in ("assistant.ask", "settings.test", "prompts.run", "wiki.analyze", "tasks.extract"))
    assert all(main.priority_of({"method": name}) == BATCH
               for name in ("schedules.execute", "analysis.start", "routines.run", "office.start"))
    assert not main.slow({"method": "assistant.cancel"}) and main.slow({"method": "assistant.ask"})


def test_a_streamed_answer_comes_as_events_before_its_answer_and_stops_when_asked(tmp_path):
    """Through the backend's own input and output, as Electron talks to it: the parts of an answer asked with stream
    are lines without an id, ended by assistant.done, before the answer's line; assistant.cancel, a quick request,
    stops an answer whose stream stands still, at once."""
    import contextlib
    import os
    import queue
    import subprocess
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    release = threading.Event()

    class StreamingLLM(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def chunk(self, data):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            stalls = "멈출" in body["messages"][-1]["content"]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            with contextlib.suppress(OSError):  # A stopped answer's connection is gone.
                for number, part in enumerate(['{"answer": "캐시를 ', '도입했습니다. [1]", ', '"citations": [1]}']):
                    event = {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
                    self.chunk(b"data: " + json.dumps(event).encode() + b"\n\n")
                    if stalls and number == 0:
                        release.wait(20)  # The rest comes only after the user stopped the answer.
                self.chunk(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n')
                self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), StreamingLLM)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    backend = Path(__file__).resolve().parents[1]
    env = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")}
    env.update({"PRIVATE_AGENT_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1", "PYTHONPATH": "", "NO_PROXY": "127.0.0.1",
                "BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "API_KEY": "k", "MODEL_NAME": "m",
                "TEMPERATURE": "0.5", "LLM_TIMEOUT": "60", "CA_BUNDLE": "", "LLM_MAX_TOKENS": "0",
                "LLM_CONTEXT_WINDOW": "64000", "LLM_CONCURRENCY": "3"})
    child = subprocess.Popen([sys.executable, str(backend / "main.py")], cwd=backend, text=True, encoding="utf-8",
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(json.loads(line)) for line in child.stdout], daemon=True).start()
    try:
        def send(ident, method, params):
            child.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
            child.stdin.flush()

        def until(ident):
            """The events before the answer of request `ident`, and that answer."""
            events = []
            while True:
                message = lines.get(timeout=20)
                if message.get("id") == ident:
                    return events, message
                if "id" not in message:
                    events.append(message)

        send("save", "wiki.save", {"title": "회의록", "content": "개발팀이 캐시를 도입하기로 결정했습니다."})
        doc = until("save")[1]["result"]["id"]
        send("ask", "assistant.ask", {"question": "결정은?", "document_ids": [doc], "stream": True, "request_id": "r1"})
        events, reply = until("ask")
        deltas = [event["params"] for event in events if event["event"] == "assistant.delta"]
        assert "".join(delta["text"] for delta in deltas) == reply["result"]["messages"][-1]["content"]
        assert reply["result"]["messages"][-1]["content"] == "캐시를 도입했습니다. [1]"
        assert events[-1] == {"event": "assistant.done", "params": {
            "request_id": "r1", "conversation_id": reply["result"]["conversation_id"], "status": "completed",
            "truncated": False}}
        send("stalled", "assistant.ask", {"question": "멈출 질문", "document_ids": [doc], "stream": True,
                                          "request_id": "r2"})
        # The first part, without the space at its end until more follows.
        assert lines.get(timeout=20)["params"]["text"] == "캐시를"
        started = time.monotonic()
        send("stop", "assistant.cancel", {"request_id": "r2"})
        assert until("stop")[1]["result"] == {"ok": True, "cancelled": True}
        events, reply = until("stalled")
        assert time.monotonic() - started < 10 and events[-1]["params"]["status"] == "cancelled"
        assert reply["result"]["cancelled"]
        assert reply["result"]["messages"][-1]["content"] == "캐시를\n\n(답변을 중지했습니다.)"
    finally:
        release.set()
        with contextlib.suppress(OSError):
            child.stdin.close()
        child.wait(20)
        child.stdout.close()
        server.shutdown()
        server.server_close()
