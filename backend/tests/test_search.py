import contextlib
import json
import sqlite3
import unicodedata
from html import escape

import pytest

from agent import pageindex, search
from agent.confluence import import_page
from agent.db import Database
from agent.html_document import page
from agent.llm import UserError
from agent.rtf_import import RtfImporter
from agent.service import EXTRACT, Service


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
