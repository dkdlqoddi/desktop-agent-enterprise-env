import contextlib
import json
import sqlite3
import threading
import unicodedata
from html import escape

import pytest

from agent import db as database_module
from agent import pageindex, paging, search
from agent.confluence import import_page
from agent.db import FTS_DONE, Database, index_text
from agent.html_document import page
from agent.llm import UserError
from agent.rtf_import import RtfImporter
from agent.service import EXTRACT, Service

# Tests of the trigram index itself need an SQLite with FTS5 and its trigram tokenizer (3.34+); everything else runs on
# either, as search does.
needs_fts = pytest.mark.skipif(not database_module.fts_supported(), reason="SQLite without the FTS5 trigram tokenizer")


def paragraphs(count, word):
    return "\n\n".join(f"{word} 문단 {i}: " + "내용 " * 90 for i in range(count))


def long_document():
    """About 19,000 characters: 1 플랫폼 설계 (1.1 배경, 1.2 캐시 전략, 1.3 운영) and 2 부록."""
    return ("# 플랫폼 설계\n\n" + paragraphs(6, "개요") + "\n\n## 배경\n\n" + paragraphs(14, "배경") +
            "\n\n## 캐시 전략\n\n" + paragraphs(16, "전략") + "\n\nRedis 클러스터를 도입해 지연 시간을 30% 줄였습니다.\n\n" +
            "## 운영\n\n" + paragraphs(20, "운영") + "\n\n# 부록\n\n" + paragraphs(10, "부록")).strip()


class RoutingLLM:
    """Answers each stage by its system prompt. Agent answers are queued; without one the agent picks
    every candidate it was shown, in order."""

    def __init__(self):
        self.calls = []
        self.analysis = {"intent": "캐시 관련 기록 찾기", "terms": ["캐시"], "keywords": [], "explanation": "캐시 관련 기록"}
        self.agent = []
        self.summarize = lambda payload: {"summaries": [{"id": s["id"], "summary": f"요약 {s['id']}"}
                                                        for s in payload["sections"]]}
        self.extract = {"summary": "플랫폼 설계 요약", "keywords": ["platform", "cache"],
                        "relations": [{"source": "cache", "target": "platform", "label": "구성"}]}
        self.answers = []

    def public(self):
        return {"configured": True}

    def structured(self, system, content):
        stage = {search.ANALYZE: "analyze", search.AGENT: "agent", pageindex.SUMMARIZE: "summarize",
                 EXTRACT: "extract"}.get(system, "answer")
        payload = json.loads(content) if content.startswith("{") else content
        self.calls.append((stage, payload, content))
        if stage == "analyze":
            result = self.analysis
        elif stage == "agent":
            result = self.agent.pop(0) if self.agent else {
                "results": [{"ref": c["ref"], "reason": "관련"} for c in payload["candidates"]], "next": None}
        elif stage == "summarize":
            result = self.summarize(payload)
        elif stage == "extract":
            result = self.extract
        else:
            result = self.answers.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), RoutingLLM())


def save(service, title, content):
    return service.wiki_save({"title": title, "content": content})


def prompt(service, query="캐시 관련 기록을 찾아줘"):
    return service.wiki_search({"query": query, "mode": "prompt"})


def stages(service):
    return [stage for stage, _, _ in service.llm.calls]


def test_every_import_source_has_a_label_for_the_ai_search():
    from agent.service import SOURCES
    assert {kind for _, kind, _ in SOURCES} <= set(search.SOURCE_LABELS)


def test_question_words_drop_particles_and_filler():
    assert search.words("지난주 캐시를 도입한 문서를 찾아줘") == ["지난주", "캐시", "도입한"]
    assert search.words("PROJ-12 C++ 빌드는?") == ["proj-12", "c++", "빌드"]


def test_a_word_ending_in_a_particle_that_also_ends_nouns_is_searched_whole_too(service):
    # 재평가 and 민주주의 end in 가 and 의 as many nouns do; 를 ends no noun and is dropped.
    assert search.words("재평가 일정과 민주주의 정확도를") == ["재평가", "재평", "일정과", "일정", "민주주의", "민주주",
                                                     "정확도"]
    doc = save(service, "재평가 결과", "분기 재평가를 마쳤습니다.")
    result = service.wiki_search({"query": "재평가 결과", "mode": "auto"})
    assert [d["id"] for d in result["documents"]] == [doc["id"]]
    # The whole word is highlighted, not only its first syllables.
    assert result["highlights"][:2] == ["재평가", "재평"] and "재평가" in result["documents"][0]["match"]["snippets"][0]["text"]


IMPORTED = "R&D 팀의 API_SPEC_v2.docx 검토 · TEST_ID_01 결과 · C++ 빌드 · a=b 조건 · [긴급] 요청 · 1. 개요"


def test_imported_text_is_found_without_its_markdown_escapes(service, tmp_path):
    """An imported page's Markdown escapes what Markdown marks (R\\&D, API\\_SPEC); the search finds the words as the
    screen shows them, and the stored Markdown keeps its escapes."""
    confluence = import_page(service, {"source_url": "https://wiki.test/pages/viewpage.action?pageId=7", "page_id": "7",
                                       "html": f"<html><head><title>사양</title></head><body><p>{escape(IMPORTED)}</p>"
                                               "</body></html>"})["doc_id"]
    importer = RtfImporter(service, converter=lambda file, cancel: page("보고서", f"<p>{escape(IMPORTED)}</p>", "word"),
                           capability_check=lambda: {"available": True})
    source = tmp_path / "보고서.rtf"
    source.write_text("보고서", encoding="utf-8")
    importer.start({"path": str(source), "mode": "file"})
    importer.thread.join(5)
    rtf = importer.status()["results"][0]["doc_id"]
    for ident in (confluence, rtf):
        assert r"R\&D" in service.wiki_get({"id": ident})["content"]
    for query in ("R&D", "API_SPEC_v2.docx", "TEST_ID_01", "C++", "a=b", "[긴급]", '"1. 개요"'):
        for mode in ("word", "auto"):
            result = service.wiki_search({"query": query, "mode": mode})
            assert {d["id"] for d in result["documents"]} == {confluence, rtf}, (query, mode)
        # The passage shown is the plain text, with the words where the screen highlights them.
        snippet = result["documents"][0]["match"]["snippets"][0]["text"]
        assert snippet.strip("…") in IMPORTED and all(term in snippet.lower() for term in result["highlights"])


def test_documents_are_found_in_full_width_decomposed_and_other_case_forms(service):
    """Documents are compared in the same form as queries: full-width letters, ①, ℃ and decomposed Hangul as their
    plain composed text, and case folded beyond ASCII."""
    found = {
        "ＡＢＣ 보고서": ("abc", "ABC"),
        unicodedata.normalize("NFD", "캐시 전략 문서"): ("캐시 전략", "캐시"),
        "온도는 25℃로 유지": ("25℃", "25°c"),
        "①항을 먼저 검토": ("①항", "1항"),
        "Ärger mit ΔT Werten": ("ärger", "δt", "ÄRGER"),
    }
    ids = {content: save(service, "기록", content)["id"] for content in found}
    titled = save(service, "ＡＢＣ 회의", "내용")["id"]
    for content, queries in found.items():
        for query in queries:
            for mode in ("word", "auto"):
                result = service.wiki_search({"query": query, "mode": mode})
                expected = {ids[content], titled} if search.fold(query) == "abc" else {ids[content]}
                assert {d["id"] for d in result["documents"]} == expected, (query, mode)
    # A different case of a non-ASCII word still shows where it is.
    snippet = service.wiki_search({"query": "δt", "mode": "word"})["documents"][0]["match"]["snippets"][0]
    assert "ΔT" in snippet["text"]


def test_the_search_text_follows_every_change_and_fills_in_what_an_earlier_version_saved(service):
    doc = save(service, "메모", "첫 내용")
    with service.db.connect() as db:
        row = db.execute("SELECT revision,content FROM document_search WHERE doc_id=?", (doc["id"],)).fetchone()
    assert tuple(row) == (1, "첫 내용")
    service.wiki_save({"id": doc["id"], "title": "메모", "content": "R\\&D 두 번째"})
    assert [d["id"] for d in service.wiki_search({"query": "r&d", "mode": "word"})["documents"]] == [doc["id"]]
    # An earlier version (or another program) changed a document without its search text: the search reads the content
    # itself, and the next start fills the search text in.
    with contextlib.closing(sqlite3.connect(service.db.path)) as db, db:
        db.execute("UPDATE documents SET content='ＡＢＣ 세 번째', revision=revision+1 WHERE id=?", (doc["id"],))
        db.execute("INSERT INTO documents(id,title,content,created_at,updated_at) VALUES "
                   "('old','예전 문서','Ärger','2026-01-01','2026-01-01')")
    assert [d["id"] for d in service.wiki_search({"query": "abc", "mode": "word"})["documents"]] == [doc["id"]]
    assert [d["id"] for d in service.wiki_search({"query": "ärger", "mode": "auto"})["documents"]] == ["old"]
    Database(service.db.path)
    with service.db.connect() as db:
        assert dict(db.execute("SELECT doc_id,content FROM document_search").fetchall()) == {
            doc["id"]: "abc 세 번째", "old": "ärger"}
    # In the trash the document is not searched; its search text goes with it when the trash is emptied.
    service.wiki_delete({"id": "old"})
    assert not service.wiki_search({"query": "ärger", "mode": "auto"})["documents"]
    service.wiki_purge({"ids": ["old"]})
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM document_search").fetchone()[0] == 1


def test_word_search_needs_every_word_and_ranks_phrases_first(service):
    exact = save(service, "캐시 도입 회의", "이번 분기에 캐시 도입을 결정했습니다.")
    apart = save(service, "성능 회의", "캐시를 검토했고 다음 달에 도입합니다.")
    save(service, "다른 회의", "캐시만 언급합니다.")
    result = service.wiki_search({"query": "캐시  도입", "mode": "word"})
    assert [d["id"] for d in result["documents"]] == [exact["id"], apart["id"]]
    assert result["total"] == 2 and result["highlights"] == ["캐시", "도입"]
    quoted = service.wiki_search({"query": '"캐시 도입"', "mode": "word"})
    assert [d["id"] for d in quoted["documents"]] == [exact["id"]] and quoted["highlights"] == ["캐시 도입"]
    assert service.wiki_search({"query": '"캐시 도입', "mode": "word"})["highlights"] == ["캐시", "도입"]
    with pytest.raises(UserError, match="검색어를 입력"):
        service.wiki_search({"query": '""', "mode": "word"})
    assert not service.llm.calls


def test_search_box_matches_any_word_and_keywords_without_the_llm(service):
    both = save(service, "캐시 도입 회의", "이번 분기에 캐시 도입을 결정했습니다.")
    one = save(service, "성능 점검", "캐시 적중률을 확인했습니다.")
    tagged = save(service, "플랫폼 개요", "구성 요소 설명")
    save(service, "점심", "메뉴 추천")
    with service.db.connect() as db:
        db.execute("INSERT INTO keywords VALUES (?,?)", (tagged["id"], "도입"))
    # Question words and particles are dropped; documents with more of the words come first, and an
    # extracted keyword matches even when the text does not contain the word.
    result = service.wiki_search({"query": "캐시를 도입한 문서 찾아줘", "mode": "auto"})
    ids = [d["id"] for d in result["documents"]]
    assert ids[0] == both["id"] and set(ids) == {both["id"], one["id"], tagged["id"]}
    assert result["plan"]["terms"] == ["캐시", "도입한", "도입"] and result["plan"]["keywords"] == ["도입"]
    assert search.stem("권한") is None and search.stem("검토했습니다") == "검토" and search.stem("api") is None
    keyword = service.wiki_search({"query": "도입", "mode": "auto"})
    assert keyword["plan"]["keywords"] == ["도입"] and keyword["highlights"] == ["도입"]
    assert {d["id"] for d in keyword["documents"]} == {both["id"], tagged["id"]}
    quoted = service.wiki_search({"query": '"캐시 도입"', "mode": "auto"})
    assert [d["id"] for d in quoted["documents"]] == [both["id"]]
    # A query of filler words alone still searches for what was typed.
    assert service.wiki_search({"query": "자료", "mode": "auto"})["plan"]["terms"] == ["자료"]
    with pytest.raises(UserError, match="검색어를 입력"):
        service.wiki_search({"query": '""', "mode": "auto"})
    assert not service.llm.calls


def test_results_carry_plain_snippets_and_sections(service):
    save(service, "회의록", "# 회의\n\n" + "서론 " * 400 + "\n\n**최종 결정**: [Redis](https://x/y) 캐시를 도입한다.\n")
    snippet = service.wiki_search({"query": "캐시", "mode": "word"})["documents"][0]["match"]["snippets"][0]
    assert snippet["text"].startswith("…서론") and snippet["text"].endswith("최종 결정: Redis 캐시를 도입한다.")
    assert "section" not in snippet  # A short document has no table of contents.
    long = save(service, "설계서", long_document())
    result = service.wiki_search({"query": "redis", "mode": "word"})
    found = next(d for d in result["documents"] if d["id"] == long["id"])
    assert found["match"]["snippets"][0]["section"] == "플랫폼 설계 › 캐시 전략"
    assert "Redis 클러스터" in found["match"]["snippets"][0]["text"]
    assert "content" not in found and len(found["excerpt"]) == 200 and found["size"] == len(long_document())


def test_keyword_search_ranks_documents_with_more_keywords_first(service):
    one = save(service, "하나", "첫 문서")
    two = save(service, "둘", "둘째 문서")
    with service.db.connect() as db:
        db.executemany("INSERT INTO keywords VALUES (?,?)", [(one["id"], "cache"), (two["id"], "cache"), (two["id"], "api")])
    result = service.wiki_search({"query": "API, Cache", "mode": "keyword"})
    assert [d["id"] for d in result["documents"]] == [two["id"], one["id"]]
    assert result["plan"]["keywords"] == result["highlights"] == ["api", "cache"]


def test_prompt_search_runs_analysis_keyword_search_then_ai_search(service):
    retro = save(service, "캐시 회고", "Redis 캐시로 응답 속도를 개선했습니다.")
    course = save(service, "캐시 교육", "캐시 개념 교육 자료입니다.")
    save(service, "무관", "점심 메뉴")
    service.llm.analysis = {"intent": "응답 속도를 개선한 기록 찾기", "terms": ["캐시", "속도"], "keywords": [],
                            "explanation": "캐시와 속도"}
    service.llm.agent = [{"thinking": "회고가 직접 관련", "next": None,
                          "results": [{"ref": "D1", "reason": "속도 개선 사례", "sections": ["1"]}]}]
    result = prompt(service, "속도를 높인 기록을 찾아줘")
    assert stages(service) == ["analyze", "agent"]
    analysis, agent = service.llm.calls[0][1], service.llm.calls[1][1]
    # The analysis sees the question and keyword list only; the agent sees passages, not whole documents.
    assert analysis == {"question": "속도를 높인 기록을 찾아줘", "wiki_keywords": []}
    assert agent["question"] == "응답 속도를 개선한 기록 찾기" and agent["steps_left"] == 1
    assert [c["title"] for c in agent["candidates"]] == ["캐시 회고", "캐시 교육"]
    assert agent["candidates"][0]["snippets"] == ["Redis 캐시로 응답 속도를 개선했습니다."]
    docs = result["documents"]
    assert [d["id"] for d in docs] == [retro["id"], course["id"]]
    assert docs[0]["match"]["selected"] and docs[0]["match"]["reason"] == "속도 개선 사례"
    assert docs[0]["match"]["sections"] == [] and not docs[1]["match"]["selected"]
    assert result["ai"] and result["total"] == 2 and result["highlights"] == ["캐시", "속도"]
    assert [s["kind"] for s in result["steps"]] == ["analyze", "keyword", "agent"]
    assert result["steps"][2]["detail"] == "회고가 직접 관련" and result["steps"][1]["count"] == 2


def test_agent_searches_again_with_other_words_at_most_once(service):
    doc = save(service, "지연 개선", "API 지연 시간을 30% 줄였습니다.")
    service.llm.analysis = {"terms": ["속도"], "keywords": []}
    service.llm.agent = [{"results": [], "next": {"search": ["지연", "속도"]}},
                         {"results": [{"ref": "D1", "reason": "지연 감소"}], "next": {"search": ["또"]}}]
    result = prompt(service, "속도 개선 기록")
    assert stages(service) == ["analyze", "agent", "agent"]
    first, second = service.llm.calls[1][1], service.llm.calls[2][1]
    assert first["candidates"] == [] and first["note"] and second["steps_left"] == 0
    assert [c["title"] for c in second["candidates"]] == ["지연 개선"]
    assert [d["id"] for d in result["documents"]] == [doc["id"]] and result["documents"][0]["match"]["selected"]
    assert [s["kind"] for s in result["steps"]] == ["analyze", "keyword", "agent", "search", "agent"]
    assert result["steps"][3]["terms"] == ["지연"] and result["highlights"] == ["속도", "지연"]
    assert result["total"] == 1


def test_agent_answers_are_validated_and_outlines_can_be_opened(service):
    save(service, "설계서", long_document())
    service.llm.analysis = {"terms": ["redis"], "keywords": []}
    service.llm.agent = [{"results": [], "next": {"outline": "D1"}},
                         {"results": [{"ref": "D9"}, "bad", {"ref": 1, "reason": "x" * 500,
                                      "sections": ["1.2 캐시 전략", "9.9", "§2", True]}, {"ref": "d1"}], "next": None}]
    result = prompt(service)
    first, second = service.llm.calls[1][1]["candidates"][0], service.llm.calls[2][1]["candidates"][0]
    assert first["length"] == f"{len(long_document()):,}자" and "1.2 캐시 전략" in first["outline"]
    assert second["outline"].count("\n") >= first["outline"].count("\n")
    match = result["documents"][0]["match"]
    assert match["selected"] and len(match["reason"]) == 300
    assert match["sections"] == [{"id": "1.2", "title": "플랫폼 설계 › 캐시 전략"}, {"id": "2", "title": "부록"}]
    assert [s["kind"] for s in result["steps"]] == ["analyze", "keyword", "agent", "outline", "agent"]
    assert all(long_document() not in raw for _, _, raw in service.llm.calls)


def test_agent_failure_keeps_keyword_results(service):
    doc = save(service, "캐시", "캐시 문서")
    service.llm.agent = [UserError("LLM 응답 제한 시간을 초과했습니다.")]
    result = prompt(service)
    assert [d["id"] for d in result["documents"]] == [doc["id"]] and not result["ai"]
    assert not result["documents"][0]["match"]["selected"]
    assert result["steps"][-1] == {"kind": "fallback", "label": "AI 검색 실패", "detail": "LLM 응답 제한 시간을 초과했습니다."}
    service.llm.agent = [{"results": "wrong"}]
    assert prompt(service)["steps"][-1]["detail"] == "AI 검색 응답 형식이 올바르지 않습니다."


def test_analysis_keeps_known_keywords_and_falls_back_to_question_words(service):
    doc = save(service, "Python 캐시", "Python API에 캐시를 도입")
    service.wiki_analyze({"id": doc["id"]})
    service.llm.analysis = {"terms": ["x", 3, "API", "api"], "keywords": ["CACHE", "없는키워드", None]}
    plan = prompt(service)["plan"]
    assert plan["keywords"] == ["cache"] and plan["terms"] == ["api", "없는키워드"]
    assert "cache" in service.llm.calls[-2][1]["wiki_keywords"]
    service.llm.analysis = {"terms": "wrong"}
    assert prompt(service, "캐시를 쓰는 문서 찾아줘")["plan"]["terms"] == ["캐시", "쓰는"]
    service.llm.analysis = {}
    with pytest.raises(UserError, match="검색어를 추출하지 못했습니다"):
        prompt(service, "문서를 찾아줘")


def test_many_matches_are_limited_and_shown_after_the_ai_picks(service):
    for index in range(search.RESULT_LIMIT + 5):
        save(service, f"캐시 {index}", "캐시 " * (index + 1))
    service.llm.agent = [{"results": [{"ref": "D3", "reason": "세 번째"}], "next": None}]
    result = prompt(service)
    agent = service.llm.calls[1][1]
    assert len(agent["candidates"]) == search.AGENT_CANDIDATES
    assert len(result["documents"]) == search.RESULT_LIMIT and result["total"] == search.RESULT_LIMIT + 5
    assert result["documents"][0]["title"] == agent["candidates"][2]["title"]
    assert sum(d["match"]["selected"] for d in result["documents"]) == 1


# -- The trigram index ---------------------------------------------------------------------------------------------


def index_matches(service):
    """Whether the trigram index covers every row of the search text and holds exactly what it says (FTS5 checks its
    entries against document_search, and fails on any it lacks or has too many of)."""
    with service.db.connect() as db:
        db.execute("INSERT INTO document_fts(document_fts, rank) VALUES('integrity-check', 1)")
        return db.execute("SELECT value FROM metadata WHERE key='search_fts'").fetchone()[0] == str(FTS_DONE)


def found(service, query, mode="auto"):
    return [d["id"] for d in service.wiki_search({"query": query, "mode": mode})["documents"]]


def long_markdown(tmp_path, name, word):
    """A Markdown file too long for one document, which an import saves as three (parts.py), `word` in every part."""
    path = tmp_path / f"{name}.md"
    path.write_text("\n\n".join(f"## {word} 절 {i}\n\n" + "본문 " * 300 for i in range(190)), encoding="utf-8")
    return path


@needs_fts
def test_the_trigram_index_follows_every_write_of_the_search_text(service, tmp_path):
    assert service.db.fts and index_matches(service)
    doc = save(service, "분기 계획서", "레디스 클러스터 도입 검토")
    assert found(service, "클러스터") == [doc["id"]] and index_matches(service)
    service.wiki_save({"id": doc["id"], "title": "연간 로드맵", "content": "레디스 클러스터 도입 검토"})
    assert found(service, "로드맵") == [doc["id"]] and not found(service, "계획서") and index_matches(service)
    service.wiki_save({"id": doc["id"], "title": "연간 로드맵", "content": "카프카 스트림 전환"})
    assert not found(service, "클러스터") and found(service, "스트림") == [doc["id"]] and index_matches(service)
    service.llm.extract = {"summary": "메시지 브로커 교체 요약", "keywords": ["kafka"], "relations": []}
    service.wiki_analyze({"id": doc["id"]})
    assert found(service, "브로커") == [doc["id"]] and index_matches(service)
    service.wiki_clear_analysis({"ids": [doc["id"]]})
    assert not found(service, "브로커") and index_matches(service)
    # The parts of a long import, updated and cut down by an import of the same file again.
    path = long_markdown(tmp_path, "설계서", "아키텍처")
    first = service.markdown_import({"path": str(path)})["doc_ids"]
    assert len(first) == 3 and set(found(service, "아키텍처")) == set(first) and index_matches(service)
    path.write_text("## 요약본\n\n" + "짧아진 문서 " * 10, encoding="utf-8")
    assert service.markdown_import({"path": str(path)})["doc_ids"] == first[:1]
    assert not found(service, "아키텍처") and found(service, "요약본") == first[:1] and index_matches(service)
    # The trash keeps the search text for a restore; emptying it takes the text out of the index with the document.
    service.wiki_delete({"id": doc["id"]})
    assert not found(service, "스트림") and index_matches(service)
    service.wiki_restore({"ids": [doc["id"]]})
    assert found(service, "스트림") == [doc["id"]]
    service.wiki_delete({"id": doc["id"]})
    service.wiki_purge({"ids": [doc["id"]]})
    with service.db.connect() as db:
        assert not db.execute("SELECT 1 FROM document_search WHERE doc_id=?", (doc["id"],)).fetchone()
    assert index_matches(service)


def test_a_title_or_summary_changed_without_the_search_text_is_searched_as_it_is_now(service, monkeypatch):
    """Another program (or code that forgets index_text) changes a title and summary: the search text is marked out of
    date, so search reads the document itself, and the next start makes it anew."""
    doc = save(service, "주간보고서", "본문 내용")
    for i in range(5):
        save(service, f"기타 {i}", "다른 내용")
    with contextlib.closing(sqlite3.connect(service.db.path)) as db, db:
        db.execute("UPDATE documents SET title='월간회고록', summary='릴리스일정 정리' WHERE id=?", (doc["id"],))
    for share in (0, 10 ** 9):  # The index's rows read one by one, and in one pass with the rest.
        monkeypatch.setattr(search, "SCAN_SHARE", share)
        assert found(service, "회고록") == found(service, "릴리스") == [doc["id"]] and not found(service, "보고서")
    with service.db.connect() as db:
        assert db.execute("SELECT revision FROM document_search WHERE doc_id=?", (doc["id"],)).fetchone()[0] == -1
    Database(service.db.path)
    with service.db.connect() as db:
        assert tuple(db.execute("SELECT revision,title,summary FROM document_search WHERE doc_id=?",
                                (doc["id"],)).fetchone()) == (1, "월간회고록", "릴리스일정 정리")
    assert found(service, "회고록") == [doc["id"]] and (not service.db.fts or index_matches(service))


SEARCHES = [("auto", "레디스 클러스터"), ("auto", "캐시를 도입한 문서"), ("word", "레디스 클러스터"), ("word", "캐시"),
            ("auto", "abc"), ("auto", "r&d"), ("word", "api_spec_v2"), ("auto", "클러스터 운영"),
            ("auto", '"캐시 무효화"'), ("keyword", "cache"), ("word", "캐 시"), ("auto", "지연 시간과"),
            ("auto", "캐싱"), ("keyword", "캐싱"), ("word", "레디스 메뉴")]


def searched(service):
    """Every search of SEARCHES as titles, scores and matched words, which do not depend on the documents' ids."""
    titles = {doc["id"]: doc["title"] for doc in service.wiki_list({})}
    return [[(titles[d["id"]], d["match"]["score"], d["match"]["matched"]) for d in service.wiki_search(
        {"query": query, "mode": mode})["documents"]] for mode, query in SEARCHES]


def fill(service):
    """Documents the searches reach by every way: words of three or more characters and shorter ones, in titles,
    summaries and bodies, keywords and their aliases, Markdown escapes and full-width letters."""
    for title, content in (("캐시 도입 회의", "레디스 클러스터로 캐시를 도입했습니다. R\\&D 검토"),
                           ("ＡＢＣ 보고서", "Kafka 스트림 전환과 캐시 무효화 정책"),
                           ("성능 점검", "레디스 지연 시간과 클러스터 장애 대응"),
                           ("점심", "메뉴 추천"), ("릴리스 노트", "API\\_SPEC\\_v2 문서 정리 · 캐 시")):
        save(service, title, content)
    with service.db.connect() as db:
        ident = db.execute("SELECT id FROM documents WHERE title='점심'").fetchone()[0]
        db.execute("UPDATE documents SET summary='클러스터 운영 요약' WHERE id=?", (ident,))
        db.execute("INSERT INTO keywords VALUES (?, 'cache')", (ident,))
        # A word no text holds, which only the alias reaches past the trigram index.
        db.execute("INSERT INTO keyword_aliases VALUES (?, 'cache', '캐싱', '캐싱')", (ident,))
        index_text(db, [ident])


def test_without_a_trigram_index_search_compares_the_search_text_and_finds_the_same(tmp_path, monkeypatch):
    indexed = Service(Database(tmp_path / "indexed.db"), RoutingLLM())
    monkeypatch.setattr(database_module, "fts_supported", lambda: False)
    plain = Service(Database(tmp_path / "plain.db"), RoutingLLM())
    assert not plain.db.fts
    with plain.db.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'document_fts%'").fetchone()
    fill(indexed)
    fill(plain)
    # The index's rows read one by one, and read in one pass with the rest (Search.scanned), find the same.
    monkeypatch.setattr(search, "SCAN_SHARE", 0)
    expected = searched(indexed)
    monkeypatch.setattr(search, "SCAN_SHARE", 10 ** 9)
    assert searched(indexed) == expected
    assert searched(plain) == expected and all(expected[:-1]) and not expected[-1]
    # A start without FTS5 takes the triggers away, so writes go on, and leaves the index to be made anew.
    reopened = Service(Database(tmp_path / "indexed.db"), RoutingLLM())
    assert not reopened.db.fts and searched(reopened) == expected
    added = save(reopened, "장애 회고", "클러스터 장애 원인 분석")["id"]
    with reopened.db.connect() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='search_fts'").fetchone()[0] == "-1"
    assert found(reopened, "장애 원인", "word") == [added]
    monkeypatch.undo()
    again = Service(Database(tmp_path / "indexed.db"), RoutingLLM())
    assert again.db.fts == database_module.fts_supported() and found(again, "장애 원인", "word") == [added]
    assert not again.db.fts or index_matches(again)


@needs_fts
def test_a_partly_filled_index_finds_every_document_and_is_filled_a_chunk_at_a_time(service):
    ids = [save(service, f"문서 {i:02d}", f"공통어휘 {'희귀어휘' if i % 7 == 0 else '일반'} {i}")["id"] for i in range(30)]
    with service.db.connect() as db:
        # As a backfill that stopped leaves it: the index holds the rows up to a middle one and covers no more.
        middle = db.execute("SELECT id FROM document_search ORDER BY id LIMIT 1 OFFSET 9").fetchone()[0]
        db.execute("INSERT INTO document_fts(document_fts) VALUES('delete-all')")
        db.execute("""INSERT INTO document_fts(rowid,title,summary,content)
            SELECT id,title,summary,content FROM document_search WHERE id<=?""", (middle,))
        db.execute("UPDATE metadata SET value=? WHERE key='search_fts'", (str(middle),))
    assert set(found(service, "희귀어휘")) == {ids[i] for i in (0, 7, 14, 21, 28)}
    # Writes on both sides of the mark: the triggers change the index only for the rows it covers.
    service.wiki_save({"id": ids[3], "title": "문서 03", "content": "희귀어휘 새 내용"})
    service.wiki_save({"id": ids[20], "title": "문서 20", "content": "희귀어휘 바뀐 내용"})
    service.wiki_delete_many({"ids": [ids[0], ids[28]]})
    service.wiki_purge({"all": True})
    added = save(service, "새 문서", "희귀어휘 추가")["id"]
    expected = {ids[3], ids[7], ids[14], ids[20], ids[21], added}
    assert set(found(service, "희귀어휘")) == set(found(service, "희귀어휘", "word")) == expected
    assert service.db._index(budget=0, chunk=5) is False
    with service.db.connect() as db:
        assert db.execute(f"SELECT {database_module.FTS_COVERED}").fetchone()[0] > middle
    assert set(found(service, "희귀어휘")) == expected
    assert service.db._index(chunk=5) is True and index_matches(service)
    assert set(found(service, "희귀어휘")) == expected and found(service, "공통어휘", "word")


@needs_fts
def test_a_start_leaves_what_its_budget_does_not_reach_to_a_thread_of_its_own(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), RoutingLLM())
    # 450 documents an earlier version saved, without their search text.
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.executemany("INSERT INTO documents(id,title,content,created_at,updated_at) VALUES (?,?,?,?,?)",
                       [(f"d{i:03d}", f"문서 {i}", f"배경작업 {i}", "2026-01-01", "2026-01-01") for i in range(450)])
    before = set(threading.enumerate())
    monkeypatch.setattr(database_module, "INDEX_BUDGET", 0)
    Database(path)
    threads = [thread for thread in set(threading.enumerate()) - before if thread.name == "search-index"]
    # The start made one chunk and answered; until the rest is made, searches read it from the documents.
    assert len(threads) == 1 and service.wiki_search({"query": "배경작업", "mode": "word"})["total"] == 450
    threads[0].join(60)
    assert not threads[0].is_alive() and index_matches(service)
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM document_search WHERE title IS NOT NULL").fetchone()[0] == 450
    assert service.wiki_search({"query": "배경작업", "mode": "word"})["total"] == 450


def test_the_search_text_of_an_earlier_version_is_made_anew(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), RoutingLLM())
    doc = save(service, "이전 버전", "옛날 형식의 검색 텍스트")
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE document_search")
        db.execute("""CREATE TABLE document_search (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, content TEXT NOT NULL)""")
        db.execute("INSERT INTO document_search VALUES (?, 1, '옛날 형식의 검색 텍스트')", (doc["id"],))
    reopened = Service(Database(path), RoutingLLM())
    with reopened.db.connect() as db:
        assert [r[1] for r in db.execute("PRAGMA table_info(document_search)")] == [
            "id", "doc_id", "revision", "title", "summary", "length", "content"]
        assert tuple(db.execute("SELECT title,summary,length FROM document_search").fetchone()) == ("이전 버전", "", 13)
    assert found(reopened, "형식의 검색") == [doc["id"]] and (not reopened.db.fts or index_matches(reopened))


def test_a_word_search_weighs_a_rare_word_above_a_common_one(service):
    """Every document a word search ranks holds every word, so a word's IDF counts the documents that hold it at all
    (the OR set): the document with more of the rarer word comes first, though the other is newer."""
    rare = save(service, "기록 하나", "흔한단어 희귀단어 희귀단어 희귀단어")
    common = save(service, "기록 둘", "흔한단어 흔한단어 흔한단어 희귀단어")
    for i in range(6):
        save(service, f"기타 {i}", "흔한단어 기타 내용")
    assert found(service, "흔한단어 희귀단어", "word") == [rare["id"], common["id"]]


# -- Pages and filters ---------------------------------------------------------------------------------------------


def pages(call, params, size):
    """Every page of a paged list, following next_cursor to the end."""
    found, cursor = [], None
    while True:
        page = call({**params, "limit": size, **({"cursor": cursor} if cursor else {})})
        found.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            return found


def test_the_document_list_comes_in_pages_and_by_filters(service, tmp_path):
    docs = [save(service, f"문서 {i}", f"내용 {i}") for i in range(7)]
    whole = service.wiki_list({})
    ids = [d["id"] for d in whole]
    assert ids == [d["id"] for d in reversed(docs)]
    listed = pages(service.wiki_list, {}, 3)
    assert [[d["id"] for d in page["documents"]] for page in listed] == [ids[:3], ids[3:6], ids[6:]]
    assert [{k: v for k, v in d.items() if k != "parts"} for d in listed[0]["documents"]] == whole[:3]
    assert all(d["parts"] == [] for page in listed for d in page["documents"])
    with service.db.connect() as db:
        db.execute("UPDATE documents SET status='indexing' WHERE id=?", (docs[0]["id"],))
        db.execute("UPDATE documents SET status='error',error='실패' WHERE id=?", (docs[1]["id"],))
        db.execute("UPDATE documents SET status='ready',index_mode='wiki' WHERE id=?", (docs[2]["id"],))
    states = {state: {d["id"] for d in service.wiki_list({"analysis": state})} for state in paging.ANALYSIS}
    assert states == {"running": {docs[0]["id"]}, "failed": {docs[1]["id"]}, "analyzed": {docs[2]["id"]},
                      "not_analyzed": {d["id"] for d in docs[3:]}}
    ranged = service.wiki_list({"updated_from": docs[2]["updated_at"], "updated_to": docs[5]["updated_at"], "limit": 9})
    assert [d["id"] for d in ranged["documents"]] == [docs[4]["id"], docs[3]["id"], docs[2]["id"]]
    assert ranged["next_cursor"] is None
    # An import saved in parts is one entry of a page, its document updated last, with the import's documents.
    imported = service.markdown_import({"path": str(long_markdown(tmp_path, "설계서", "아키텍처"))})["doc_ids"]
    assert {d["id"] for d in service.wiki_list({"source": "markdown"})} == set(imported)
    assert {d["id"] for d in service.wiki_list({"source": "manual"})} == {d["id"] for d in docs}
    assert service.wiki_list({"source": "jira", "limit": 5}) == {"documents": [], "next_cursor": None}
    entries = [d for page in pages(service.wiki_list, {}, 1) for d in page["documents"]]
    assert len(entries) == 8 and len({d["id"] for d in entries}) == 8
    entry = next(d for d in entries if d["id"] in imported)
    assert [(p["id"], p["part"]) for p in entry["parts"]] == list(zip(imported, (1, 2, 3)))
    assert entry["parts"][1]["title"] == "설계서 (2/3)"
    service.wiki_save({"id": imported[1], "title": "설계서 (2/3)", "content": "아키텍처 둘째 부분을 고쳤습니다"})
    first = service.wiki_list({"limit": 2})["documents"]
    assert first[0]["id"] == imported[1] and first[1]["id"] not in imported
    assert [p["id"] for p in first[0]["parts"]] == imported
    # Filters choose among the import's documents; a part in the trash is not one of them.
    unedited = service.wiki_list({"limit": 50, "updated_to": first[0]["updated_at"], "source": "markdown"})
    assert len(unedited["documents"]) == 1 and unedited["documents"][0]["id"] != imported[1]
    service.wiki_delete({"id": imported[1]})
    entry = next(d for d in service.wiki_list({"limit": 50})["documents"] if d["id"] in imported)
    assert [p["id"] for p in entry["parts"]] == [imported[0], imported[2]]
    assert len(service.wiki_list({})) == 9  # Without limit: every document, as before.


def test_a_page_request_is_checked(service):
    save(service, "문서", "내용")
    for limit in (0, 501, "5", True, 2.5):
        with pytest.raises(UserError, match="1~500개"):
            service.wiki_list({"limit": limit})
    for cursor in ("잘못된", "e30", paging.encode("search", 1.0, "a", "b"), paging.encode("list", 1, "a"), 5):
        with pytest.raises(UserError, match="cursor"):
            service.wiki_list({"limit": 2, "cursor": cursor})
    with pytest.raises(UserError, match="source"):
        service.wiki_list({"source": "mail"})
    with pytest.raises(UserError, match="analysis"):
        service.wiki_list({"analysis": "done"})
    with pytest.raises(UserError, match="updated_from"):
        service.wiki_list({"updated_from": "2026-10-01"})
    # A cursor alone asks for a page of the usual size.
    assert service.wiki_list({"cursor": paging.encode("list", "9999", "z")})["next_cursor"] is None


def test_search_results_come_in_pages_with_an_import_as_one_entry(service, tmp_path):
    docs = [save(service, f"캐시 {i}", "캐시 " * (i + 1)) for i in range(5)]
    imported = service.markdown_import({"path": str(long_markdown(tmp_path, "캐시 설계", "캐시"))})["doc_ids"]
    whole = service.wiki_search({"query": "캐시", "mode": "auto"})
    assert whole["total"] == 8 and "next_cursor" not in whole
    listed = pages(lambda p: service.wiki_search({**p, "query": "캐시", "mode": "auto"}), {}, 2)
    assert [len(page["documents"]) for page in listed] == [2, 2, 2] and {page["total"] for page in listed} == {8}
    entries = [d for page in listed for d in page["documents"]]
    entry = next(d for d in entries if d["id"] in imported)
    assert [p["id"] for p in entry["parts"]] == imported and entry["match"]["snippets"]
    assert [d["id"] for d in entries] == [d["id"] for d in whole["documents"]
                                          if d["id"] not in imported or d["id"] == entry["id"]]
    assert all(d["parts"] == [] for d in entries if d is not entry)
    # Filters keep what they name, with or without pages.
    assert {d["id"] for d in service.wiki_search({"query": "캐시", "mode": "auto", "source": "markdown"})[
        "documents"]} == set(imported)
    manual = service.wiki_search({"query": "캐시", "mode": "word", "source": "manual", "limit": 9})
    assert {d["id"] for d in manual["documents"]} == {d["id"] for d in docs} and manual["next_cursor"] is None
    assert manual["total"] == 5
    with pytest.raises(UserError, match="1~100개"):
        service.wiki_search({"query": "캐시", "mode": "auto", "limit": 101})
    with pytest.raises(UserError, match="AI 검색"):
        service.wiki_search({"query": "캐시", "mode": "prompt", "limit": 5})
    with pytest.raises(UserError, match="AI 검색"):
        service.wiki_search({"query": "캐시", "mode": "prompt", "analysis": "analyzed"})
    assert not service.llm.calls


def test_conversations_come_in_pages(service):
    with service.db.connect() as db:
        db.executemany("INSERT INTO conversations VALUES (?,?,?,?)",
                       [(f"c{i}", f"대화 {i}", f"2026-10-0{i}T00:00:00+00:00", f"2026-10-0{i}T00:00:00+00:00")
                        for i in range(1, 6)])
    conversations = service.assistant.conversations
    assert [c["id"] for c in conversations({})] == ["c5", "c4", "c3", "c2", "c1"]
    listed = pages(conversations, {}, 2)
    assert [[c["id"] for c in page["conversations"]] for page in listed] == [["c5", "c4"], ["c3", "c2"], ["c1"]]
    ranged = conversations({"updated_from": "2026-10-02T00:00:00+00:00", "updated_to": "2026-10-04T09:00:00+09:00"})
    assert [c["id"] for c in ranged] == ["c3", "c2"]
    with pytest.raises(UserError, match="1~200개"):
        conversations({"limit": 201})
    with pytest.raises(UserError, match="cursor"):
        conversations({"cursor": paging.encode("list", "2026", "c1")})


# -- Keyword aliases -----------------------------------------------------------------------------------------------


def analyzed(service, title, content, keywords, aliases):
    """A document the LLM analyzed into these keywords and their aliases."""
    doc = save(service, title, content)
    service.llm.extract = {"summary": f"{title} 요약", "keywords": keywords, "aliases": aliases, "relations": []}
    return service.wiki_analyze({"id": doc["id"]})


def matches(service, query, mode="auto"):
    """{title: match} of a search's documents, in their order."""
    return {d["title"]: d["match"] for d in service.wiki_search({"query": query, "mode": mode})["documents"]}


def test_a_korean_word_finds_an_english_keyword_by_its_alias_and_the_other_way_round(service, monkeypatch):
    """The search box matches a word that is the alias of a keyword, in any form the search compares the same (case,
    full width, a particle), as that keyword: the trigram index, which finds documents by their text, does not keep the
    document out, and the result names the keyword with the alias that matched."""
    analyzed(service, "Tuning notes", "Index and query tuning of the production server.", ["database", "index"],
             {"database": ["데이터베이스", "DB"], "index": ["인덱스"]})
    analyzed(service, "결제 흐름", "승인과 환불 절차를 정리했습니다.", ["결제"], {"결제": ["Payment"]})
    save(service, "이전 기록", "데이터베이스를 새 서버로 옮겼습니다.")
    for share in (0, 10 ** 9):  # The index's rows read one by one, and in one pass with the rest.
        monkeypatch.setattr(search, "SCAN_SHARE", share)
        found = matches(service, "데이터베이스")
        assert list(found) == ["Tuning notes", "이전 기록"]
        assert found["Tuning notes"]["matched"] == ["데이터베이스", "database"]
        assert found["Tuning notes"]["aliases"] == [{"keyword": "database", "alias": "데이터베이스"}]
        # The document holding the word in its text matches as it always has.
        assert found["이전 기록"]["matched"] == ["데이터베이스"] and "aliases" not in found["이전 기록"]
        assert set(matches(service, "데이터베이스를 옮긴 기록")) == {"Tuning notes", "이전 기록"}
        # A word of two characters, which the trigram index does not look up.
        for query in ("db", "ＤＢ", "Db"):
            found = matches(service, query)
            assert list(found) == ["Tuning notes"] and found["Tuning notes"]["matched"] == ["db", "database"]
            assert found["Tuning notes"]["aliases"] == [{"keyword": "database", "alias": "DB"}]
        found = matches(service, "PAYMENT")
        assert list(found) == ["결제 흐름"] and found["결제 흐름"]["matched"] == ["payment", "결제"]
        assert found["결제 흐름"]["aliases"] == [{"keyword": "결제", "alias": "Payment"}]
    # The search words are what the screen highlights, as before; the alias is a keyword of the search.
    result = service.wiki_search({"query": "데이터베이스", "mode": "auto"})
    assert result["highlights"] == result["plan"]["terms"] == result["plan"]["keywords"] == ["데이터베이스"]
    # A word within an alias matches as a word within a keyword does: it finds the document, marking no keyword.
    part = matches(service, "데이터")["Tuning notes"]
    assert part["matched"] == ["데이터"] and "aliases" not in part
    assert stages(service) == ["extract", "extract"]


def test_a_document_found_by_an_alias_ranks_as_if_its_keyword_had_been_typed(service):
    """An alias is its keyword under another name: a document whose keyword database has the alias 데이터베이스 ranks
    for 데이터베이스 as one whose keyword is 데이터베이스 itself, in the search box and the keyword search."""
    by_alias = analyzed(service, "기록 하나", "같은 본문입니다.", ["database"], {"database": ["데이터베이스"]})
    by_keyword = analyzed(service, "기록 둘", "같은 본문입니다.", ["데이터베이스"], {})
    save(service, "기타", "다른 본문입니다.")
    for mode in ("auto", "keyword"):
        found = {d["id"]: d["match"] for d in service.wiki_search({"query": "데이터베이스", "mode": mode})["documents"]}
        assert set(found) == {by_alias["id"], by_keyword["id"]}
        assert found[by_alias["id"]]["score"] == found[by_keyword["id"]]["score"] > 0
        assert found[by_keyword["id"]]["matched"] == ["데이터베이스"] and "aliases" not in found[by_keyword["id"]]
        assert found[by_alias["id"]]["matched"] == ["데이터베이스", "database"]


def test_the_keyword_and_prompt_searches_match_aliases_and_the_word_search_does_not(service):
    english = analyzed(service, "Tuning notes", "Index and query tuning.", ["database", "index"],
                       {"database": ["데이터베이스"], "index": ["인덱스", "색인"]})
    korean = analyzed(service, "결제 흐름", "승인과 환불 절차", ["결제"], {"결제": ["payment"]})
    plain = save(service, "이전 기록", "데이터베이스를 옮겼습니다.")
    result = service.wiki_search({"query": "Payment, 인덱스", "mode": "keyword"})
    assert result["plan"]["keywords"] == result["highlights"] == ["payment", "인덱스"]
    assert {d["id"]: d["match"]["aliases"] for d in result["documents"]} == {
        english["id"]: [{"keyword": "index", "alias": "인덱스"}], korean["id"]: [{"keyword": "결제", "alias": "payment"}]}
    # The literal word search leaves aliases out, as it leaves keywords out.
    assert found(service, "데이터베이스", "word") == [plain["id"]]
    # The analysis of a prompt search is offered the English keyword of the question's Korean word first, and a term
    # that is an alias finds the keyword's document.
    service.llm.analysis = {"intent": "색인 기록 찾기", "terms": ["색인"], "keywords": [], "explanation": "색인"}
    result = prompt(service, "색인 기록을 찾아줘")
    analysis = next(payload for stage, payload, _ in service.llm.calls if stage == "analyze")
    assert analysis["wiki_keywords"] == ["index", "database", "결제"]
    assert [d["id"] for d in result["documents"]] == [english["id"]]
    assert result["documents"][0]["match"]["aliases"] == [{"keyword": "index", "alias": "색인"}]


def test_aliases_go_with_their_keywords(service):
    """keyword_aliases refers to the keyword it names: a new analysis, taking back the analysis, a new text and emptying
    the trash take the aliases with the keywords, and the trash keeps them for a restore."""
    def aliases(ident):
        with service.db.connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT keyword,alias,normalized FROM keyword_aliases WHERE doc_id=? ORDER BY normalized", (ident,))]

    ident = analyzed(service, "Tuning notes", "Index and query tuning.", ["database"],
                     {"database": ["데이터베이스", "DB"]})["id"]
    assert aliases(ident) == [("database", "DB", "db"), ("database", "데이터베이스", "데이터베이스")]
    with service.db.connect() as db:
        assert {(r["table"], r["from"], r["to"], r["on_delete"]) for r in db.execute(
            "PRAGMA foreign_key_list(keyword_aliases)")} == {("keywords", "doc_id", "doc_id", "CASCADE"),
                                                             ("keywords", "keyword", "keyword", "CASCADE")}
        assert [r[2] for r in db.execute("PRAGMA index_info(idx_keyword_aliases)")] == ["normalized"]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO keyword_aliases VALUES (?, 'unknown', 'x', 'x')", (ident,))
    service.llm.extract = {"summary": "다시", "keywords": ["database"], "aliases": {"database": ["디비"]}, "relations": []}
    service.wiki_analyze({"id": ident})
    assert aliases(ident) == [("database", "디비", "디비")] and found(service, "디비") == [ident]
    assert not found(service, "데이터베이스")
    service.wiki_delete({"id": ident})
    assert not found(service, "디비") and aliases(ident)
    service.wiki_restore({"ids": [ident]})
    assert found(service, "디비") == [ident]
    service.wiki_clear_analysis({"ids": [ident]})
    assert aliases(ident) == [] and not found(service, "디비")
    service.wiki_analyze({"id": ident})
    service.wiki_save({"id": ident, "title": "Tuning notes", "content": "다른 내용"})
    assert aliases(ident) == [] and not found(service, "디비")
    service.wiki_analyze({"id": ident})
    service.wiki_delete({"id": ident})
    service.wiki_purge({"ids": [ident]})
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM keyword_aliases").fetchone()[0] == 0


def test_aliases_a_search_does_not_meet_change_nothing_of_it(tmp_path):
    """A search none of whose words is an alias, or within one, finds and ranks as before aliases: the results of a Wiki
    with an alias they do not meet are those of the same Wiki without it, with no aliases of their own."""
    bare, named = (Service(Database(tmp_path / f"{name}.db"), RoutingLLM()) for name in ("bare", "named"))
    fill(bare)
    fill(named)
    with bare.db.connect() as db:
        db.execute("DELETE FROM keyword_aliases")

    def outcome(service, mode, query):
        result = service.wiki_search({"query": query, "mode": mode})
        return (result["plan"], result["highlights"], result["total"], result.get("ai"), result["steps"],
                [(d["title"], d["match"]) for d in result["documents"]])

    queries = [(mode, query) for mode, query in SEARCHES if query != "캐싱"] + [("prompt", "캐시 관련 기록을 찾아줘")]
    expected = [outcome(bare, *query) for query in queries]
    assert [outcome(named, *query) for query in queries] == expected
    assert all("aliases" not in match for *_, documents in expected for _, match in documents)
    assert [payload for _, payload, _ in bare.llm.calls] == [payload for _, payload, _ in named.llm.calls]
