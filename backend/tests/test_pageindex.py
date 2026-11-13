import json
from itertools import pairwise

import pytest
import requests

from agent import llm as llm_module, pageindex
from agent.db import Database
from agent.llm import LLM, LLMRequestError, LLMTruncatedError, OutOfTime, UserError, deadline
from agent.service import Service
from test_llm import Clock, Response
from test_search import RoutingLLM, long_document, paragraphs

BUSY = "LLM API가 HTTP 503를 반환했습니다. 서버가 바쁘거나 일시적인 오류일 수 있으니 잠시 후 다시 시도해 주세요."


def alternating():
    """Long and short sections in turn (ids 1, 3, … 13 long, 2, 4, … 14 short), packed into four requests:
    [1, 3], [5, 7], [9, 11] and the last long one with every short one."""
    return "".join(f"## 긴 {i}\n\n{'가' * 5900}\n\n## 짧은 {i}\n\n{'나' * 350}\n\n" for i in range(7)).strip()


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), RoutingLLM())


def test_tree_follows_headings_and_ignores_code_fences():
    content = (
        "출처: https://wiki/page\n\n# 개요\n\n소개 문단\n\n## 배경\n\n배경 문단\n\n"
        "```md\n# 코드 안의 제목\n```\n\n## 설계 ##\n\n설계 문단\n\n# 3\\. **결론** [링크](https://x)\n\n끝"
    )
    nodes = pageindex.build(content)
    assert [(n["id"], n["parent"], n["title"]) for n in nodes] == [
        ("1", None, "문서 앞부분"),
        ("2", None, "개요"),
        ("2.1", "2", "배경"),
        ("2.2", "2", "설계"),
        ("3", None, "3. 결론 링크"),
    ]
    # A section spans its subsections and ends where the next section of its level starts.
    assert content[nodes[1]["start"] : nodes[1]["end"]].startswith("# 개요")
    assert nodes[1]["end"] == nodes[4]["start"] and nodes[2]["end"] == nodes[3]["start"]
    assert "코드 안의 제목" in content[nodes[2]["start"] : nodes[2]["end"]]
    assert nodes[-1]["end"] == len(content)
    assert pageindex.own_text(content, nodes, 1).strip() == "소개 문단"
    assert pageindex.path(nodes, "2.2") == "개요 › 설계"
    assert pageindex.locate(nodes, content.index("설계 문단")) == "2.2"


def test_long_text_is_split_into_pages_at_paragraphs():
    content = paragraphs(80, "보고")
    nodes = pageindex.build(content)
    assert len(nodes) > 3 and all(n["kind"] == "page" for n in nodes)
    assert nodes[0]["start"] == 0 and nodes[-1]["end"] == len(content)
    assert all(a["end"] == b["start"] for a, b in pairwise(nodes))
    assert all(n["end"] - n["start"] <= pageindex.PAGE_CHARS for n in nodes)
    assert all(content[n["start"] : n["end"]].startswith("보고 문단") for n in nodes)
    assert nodes[0]["title"].startswith(f"1/{len(nodes)}쪽 · 보고 문단 0")
    # A long section keeps its heading and gets pages as its first children.
    content = "# 회의록\n\n" + paragraphs(40, "안건") + "\n\n## 결정\n\n결정 문단"
    nodes = pageindex.build(content)
    assert nodes[0]["title"] == "회의록" and nodes[1]["title"].startswith("회의록 앞부분 (1/")
    assert nodes[-1]["title"] == "결정" and nodes[-1]["parent"] == "1"


def test_a_heading_line_of_many_spaces_is_read_at_once():
    """Headings are read without the regular expression that took seconds on a line of many spaces, and as it did."""
    import re
    import time

    started = time.monotonic()
    found = pageindex.headings("#" + " " * 20000 + "제목" + " " * 20000 + "끝\n" + "## " + " \t" * 20000 + "\n본문")
    assert time.monotonic() - started < 1 and found == [(0, 1, "제목 끝")]
    before = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*")
    for line in (
        "# 제목",
        "  ## 제목 ##  ",
        "### 제목#",
        "#### #",
        "# ##",
        "# 제목 # 끝",
        "#\t제목\t#\t",
        "#",
        "#5",
        "####### 일곱",
        "    # 코드",
        "# 3\\. **결론**",
        "## 제목 \\#",
        "# 제목 ###x",
        "   ###### 여섯 #",
    ):
        match = before.fullmatch(line)
        assert pageindex.heading(line) == ((len(match.group(1)), match.group(2) or "") if match else None), line


def test_many_headings_are_folded_and_grouped():
    nested = "".join(
        f"# 장 {a}\n\n"
        + "".join(f"## 절 {a}.{b}\n\n" + "".join(f"### 항 {a}.{b}.{c}\n\n내용\n\n" for c in range(4)) for b in range(4))
        for a in range(4)
    )
    nodes = pageindex.build(nested)
    assert len(nodes) == 20 and max(n["depth"] for n in nodes) == 2
    flat = "".join(f"## 항목 {i}\n\n" + "짧은 내용 " * 30 + "\n\n" for i in range(200))
    nodes = pageindex.build(flat)
    assert len(nodes) <= pageindex.MAX_SECTIONS + 5
    assert nodes[0]["kind"] == "group" and nodes[0]["title"].startswith("항목 0 ~ 항목")
    assert nodes[-1]["end"] == len(flat)


def test_outline_keeps_focus_within_budget():
    content = long_document()
    nodes = pageindex.extractive(content, pageindex.build(content))
    full = pageindex.outline(nodes, 100_000)
    assert "생략" not in full and len(full.splitlines()) == len(nodes)
    # Titles come before summaries, except for the focused section.
    lines = pageindex.outline(nodes, 200, focus=["1.1"]).splitlines()
    assert lines[0] == "1 플랫폼 설계" and lines[1].startswith("1.1 배경 — 배경 문단 0")
    assert lines[2:] == ["1.2 캐시 전략", "1.3 운영", "2 부록"]
    # The focused section and its parents come first; omitted sections are counted.
    assert pageindex.outline(nodes, 25, focus=["1.3"]).splitlines() == [
        "1 플랫폼 설계",
        "1.3 운영",
        "2 부록",
        "(목차 2개 항목 생략)",
    ]


def test_section_text_orders_bounds_and_prefers_specific_sections():
    content = long_document()
    nodes = pageindex.build(content)
    text, labels = pageindex.section_text(content, nodes, ["2", "1.1", "1", "9.9"], 3000)
    # The subsection is the more specific choice; its parent, cut to the budget, would miss it.
    assert labels == ["1.1 배경", "2 부록"]
    assert len(text) <= 3000 and text.startswith("[1.1 배경]\n## 배경")
    text, labels = pageindex.section_text(content, nodes, ["1", "1.2"], 6000)
    assert labels == ["1.2 캐시 전략"] and "Redis 클러스터" in text


def test_long_document_analysis_uses_bounded_requests(service):
    content = long_document()
    doc = service.wiki_save({"title": "플랫폼 설계서", "content": content})
    analyzed = service.wiki_analyze({"id": doc["id"]})
    stages = [stage for stage, _, _ in service.llm.calls]
    assert stages.count("extract") == 1 and stages.count("summarize") >= 2
    # No request carries the whole document; the metadata comes from the outline.
    assert all(len(raw) < pageindex.BATCH_CHARS + 2000 for _, _, raw in service.llm.calls)
    extract = next(raw for stage, _, raw in service.llm.calls if stage == "extract")
    assert "목차와 섹션 요약" in extract and content not in extract
    assert analyzed["status"] == "ready" and analyzed["index_mode"] == "pageindex" and analyzed["large"]
    assert analyzed["keywords"] == ["cache", "platform"]
    outline = analyzed["outline"]
    assert [(o["id"], o["heading"]) for o in outline] == [("1", 0), ("1.1", 1), ("1.2", 2), ("1.3", 3), ("2", 4)]
    summarized = {
        s["id"] for _, payload, _ in service.llm.calls if isinstance(payload, dict) for s in payload.get("sections", [])
    }
    assert all(o["summary"] == f"요약 {o['id']}" for o in outline if o["id"] in summarized)
    assert all(len(o["summary"]) <= pageindex.SHORT_CHARS for o in outline if o["id"] not in summarized)
    # Editing the document drops the tree; a short document is analyzed in one request.
    edited = service.wiki_save({"id": doc["id"], "title": "짧은 문서", "content": "짧은 캐시 문서"})
    assert edited["index_mode"] == "" and edited["outline"] == [] and not edited["large"]
    with service.db.connect() as db:
        assert not db.execute("SELECT count(*) FROM doc_nodes").fetchone()[0]
    service.llm.calls.clear()
    assert service.wiki_analyze({"id": doc["id"]})["index_mode"] == "wiki"
    assert [stage for stage, _, _ in service.llm.calls] == ["extract"]


def test_summaries_are_packed_into_few_bounded_requests():
    # Long and short sections alternate; packing the longest first needs 4 requests, not 7.
    content = alternating()
    nodes = pageindex.build(content)
    llm = RoutingLLM()
    assert pageindex.summarize(llm, "문서", content, nodes) == {
        "batches": 4,
        "failed": [],
        "labels": [],
        "error": "",
        "late": False,
    }
    for _, payload, _ in llm.calls:
        sections = payload["sections"]
        assert sum(len(s["text"]) for s in sections) <= pageindex.BATCH_CHARS
        assert [s["id"] for s in sections] == sorted((s["id"] for s in sections), key=lambda i: int(i))
    assert all(n["summary"] == f"요약 {n['id']}" for n in nodes)


def test_summary_batches_fail_cleanly_or_fall_back(service):
    content = long_document()
    doc = service.wiki_save({"title": "설계서", "content": content})

    def unreachable(payload):
        raise UserError("LLM API에 연결할 수 없습니다.")

    service.llm.summarize = unreachable
    with pytest.raises(UserError, match="연결할 수 없습니다"):
        service.wiki_analyze({"id": doc["id"]})
    failed = service.wiki_get({"id": doc["id"]})
    assert failed["status"] == "error" and failed["outline"] == []
    # A reply not in the form asked for costs its sections their AI summaries, not the analysis.
    service.llm.summarize = lambda payload: {"wrong": []}
    analyzed = service.wiki_analyze({"id": doc["id"]})
    assert analyzed["status"] == "ready" and analyzed["outline"]
    assert all(o["summary"] and not o["summary"].startswith("요약 ") for o in analyzed["outline"])
    doc = service.wiki_save({"id": doc["id"], "title": "설계서", "content": content + "\n\n추가"})
    # Sections the model skipped keep the beginning of their text.
    service.llm.summarize = lambda payload: {"summaries": [{"id": payload["sections"][0]["id"], "summary": "하나만"}]}
    analyzed = service.wiki_analyze({"id": doc["id"]})
    summaries = [o["summary"] for o in analyzed["outline"]]
    assert "하나만" in summaries and all(summaries)


def asked(llm):
    """The section ids of each summary request the LLM got, in no particular order."""
    return sorted(
        tuple(s["id"] for s in payload["sections"]) for stage, payload, _ in llm.calls if stage == "summarize"
    )


def test_a_summary_batch_that_fails_is_asked_again_in_halves():
    content, llm = alternating(), RoutingLLM()
    nodes = pageindex.build(content)
    answer = llm.summarize

    def busy_once(payload):
        if [s["id"] for s in payload["sections"]] == ["1", "3"]:
            raise LLMRequestError(BUSY, 503, transient=True)
        return answer(payload)

    llm.summarize = busy_once
    assert pageindex.summarize(llm, "문서", content, nodes)["failed"] == []
    # The other batches were not asked again; the failed one was, in halves.
    assert asked(llm) == sorted(
        [("1", "3"), ("5", "7"), ("9", "11"), ("2", "4", "6", "8", "10", "12", "13", "14"), ("1",), ("3",)]
    )
    assert all(n["summary"] == f"요약 {n['id']}" for n in nodes)


def test_a_document_of_one_request_asks_it_again():
    content, llm = "# 제목\n\n" + "가" * 400, RoutingLLM()
    nodes = pageindex.build(content)
    answer, tries = llm.summarize, []

    def busy_once(payload):
        tries.append(payload)
        if len(tries) == 1:
            raise LLMRequestError(BUSY, 503, transient=True)
        return answer(payload)

    llm.summarize = busy_once
    assert pageindex.summarize(llm, "문서", content, nodes)["failed"] == [] and len(tries) == 2
    assert nodes[0]["summary"] == "요약 1"


def test_a_batch_that_fails_again_keeps_what_succeeded_and_says_which_sections_lost_their_summaries(service):
    doc = service.wiki_save({"title": "설계서", "content": alternating()})
    answer = service.llm.summarize

    def busy(payload):
        if "1" in [s["id"] for s in payload["sections"]]:
            raise LLMRequestError(BUSY, 503, transient=True)
        return answer(payload)

    service.llm.summarize = busy
    analyzed = service.wiki_analyze({"id": doc["id"]})
    # The analysis stands, with every summary that came; the section whose requests failed keeps its text's beginning.
    assert analyzed["status"] == "ready" and analyzed["index_mode"] == "pageindex" and analyzed["keywords"]
    summaries = {o["id"]: o["summary"] for o in analyzed["outline"]}
    assert summaries["1"] == "가" * 159 + "…" and all(summaries[i] == f"요약 {i}" for i in summaries if i != "1")
    assert analyzed["error"] == (
        f"섹션 1개(1 긴 0)는 LLM 요약에 실패해 본문 앞부분으로 요약을 대신했습니다: {BUSY} "
        "다시 분석하면 이 섹션들도 다시 요약합니다."
    )
    # Analyzed again once the LLM answers, the note goes.
    service.llm.summarize = answer
    assert service.wiki_analyze({"id": doc["id"]})["error"] == ""


def test_when_no_batch_is_answered_the_analysis_fails_without_another_round(service):
    doc = service.wiki_save({"title": "설계서", "content": alternating()})

    def busy(payload):
        raise LLMRequestError(BUSY, 503, transient=True)

    service.llm.summarize = busy
    with pytest.raises(UserError, match="HTTP 503"):
        service.wiki_analyze({"id": doc["id"]})
    assert len(asked(service.llm)) == 4
    failed = service.wiki_get({"id": doc["id"]})
    assert failed["status"] == "error" and failed["outline"] == []


def test_answers_cut_short_are_asked_again_in_halves_and_their_sections_counted(service):
    doc = service.wiki_save({"title": "설계서", "content": alternating()})
    answer = service.llm.summarize

    def cut_short(payload):
        if len(payload["sections"]) > 1:
            raise LLMTruncatedError("LLM 응답이 최대 출력 길이에서 잘려 결과를 읽지 못했습니다.")
        return answer(payload)

    service.llm.summarize = cut_short
    analyzed = service.wiki_analyze({"id": doc["id"]})
    # No batch of the first round was answered, yet cut-short answers are asked again: the pairs of long sections
    # one by one, the halves of the large batch still too many at once.
    assert len(asked(service.llm)) == 4 + 8
    assert analyzed["status"] == "ready" and analyzed["error"].startswith(
        "섹션 8개(2 짧은 0, 4 짧은 1, 6 짧은 2, 8 짧은 3, 10 짧은 4 외 3개)는 LLM 요약에 실패해"
    )


def test_stored_tree_is_ignored_after_content_changes_elsewhere(service):
    content = long_document()
    doc = service.wiki_save({"title": "설계서", "content": content})
    service.wiki_analyze({"id": doc["id"]})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET content=?, revision=revision+1 WHERE id=?", (content[:13000], doc["id"]))
        row = dict(db.execute("SELECT * FROM documents WHERE id=?", (doc["id"],)).fetchone())
        nodes = pageindex.tree(db, row)
    assert nodes and all(n["end"] <= 13000 for n in nodes)
    assert not any(n["summary"].startswith("요약 ") for n in nodes)


@pytest.fixture
def clock(monkeypatch):
    """The time agent.llm reads, which the requests of a test's LLM move on. The summary requests go one at a time, so
    a test knows the time at which each is sent."""
    clock = Clock()
    monkeypatch.setattr(llm_module, "time", clock)
    monkeypatch.setattr(pageindex, "CONCURRENCY", 1)
    return clock


def taking(llm, clock, seconds, answer=None):
    """Summary requests that take `seconds` each and are answered by `answer` (the LLM's own by default)."""
    answer = answer or llm.summarize

    def summarize(payload):
        clock.now += seconds
        return answer(payload)

    llm.summarize = summarize


def test_summary_batches_the_deadline_leaves_no_time_for_are_not_sent(clock):
    content, llm = alternating(), RoutingLLM()
    nodes = pageindex.build(content)
    taking(llm, clock, 10)
    with deadline(15):
        result = pageindex.summarize(llm, "문서", content, nodes)
    # The second request went with five seconds left; for the others there was no time.
    assert asked(llm) == [("1", "3"), ("5", "7")]
    assert result["late"] and result["failed"] == ["2", "4", "6", "8", "9", "10", "11", "12", "13", "14"]
    assert all(n["summary"] == f"요약 {n['id']}" for n in nodes if n["id"] in ("1", "3", "5", "7"))
    assert all(n["summary"] and not n["summary"].startswith("요약 ") for n in nodes if n["id"] in result["failed"])
    note = pageindex.failure_note(result)
    assert note.startswith("섹션 10개(2 짧은 0, 4 짧은 1, 6 짧은 2, 8 짧은 3, 9 긴 4 외 5개)는 제한 시간 안에")
    assert "문서 목록에서 이 문서를 골라 AI 분석을 실행하면 시간 제한 없이" in note


def test_a_retry_in_halves_is_not_started_without_the_time_for_it(clock):
    content, llm = alternating(), RoutingLLM()
    nodes = pageindex.build(content)
    answer = llm.summarize

    def busy_first(payload):
        if [s["id"] for s in payload["sections"]] == ["1", "3"]:
            raise LLMRequestError(BUSY, 503, transient=True)
        return answer(payload)

    taking(llm, clock, 10, busy_first)
    with deadline(35):
        result = pageindex.summarize(llm, "문서", content, nodes)
    # Every batch of the first round went, the last with five seconds left; the halves of the one that failed found no
    # time left, where they would have been asked (test_a_summary_batch_that_fails_is_asked_again_in_halves).
    assert len(asked(llm)) == 4 and result["failed"] == ["1", "3"] and result["late"]


def test_when_no_summary_came_in_time_the_error_of_a_request_sent_is_told(clock):
    content, llm = alternating(), RoutingLLM()
    nodes = pageindex.build(content)

    def timed_out(payload):
        raise LLMRequestError("LLM 응답 제한 시간을 초과했습니다. 잠시 후 다시 시도해 주세요.", transient=True)

    taking(llm, clock, 10, timed_out)
    with deadline(5), pytest.raises(LLMRequestError, match="응답 제한 시간을 초과") as error:
        pageindex.summarize(llm, "문서", content, nodes)
    # Not that time ran out for the three requests never sent.
    assert not isinstance(error.value, OutOfTime) and len(asked(llm)) == 1


def scripted(monkeypatch, clock, seconds):
    """The LLM server, answering each request in `seconds(system)` of the test's clock: a summary request with a
    summary of each section, the request for the document's metadata with them. A request that would take longer than
    the time it was given times out at its end, as a read of the server's answer does. Returns the requests, as
    (system prompt, seconds given, time sent)."""
    sent = []

    def post(session, url, **kwargs):
        system, content = (message["content"] for message in kwargs["json"]["messages"])
        given = kwargs["timeout"][1]
        sent.append((system, given, clock.now))
        took = seconds(system)
        if took > given:
            clock.now += given
            raise requests.exceptions.ReadTimeout()
        clock.now += took
        if system == pageindex.SUMMARIZE:
            sections = json.loads(content)["sections"]
            reply = {"summaries": [{"id": s["id"], "summary": f"요약 {s['id']}"} for s in sections]}
        else:
            reply = {"summary": "설계 요약", "keywords": ["design"], "relations": []}
        return Response(body={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]})

    monkeypatch.setattr("agent.llm.requests.Session.post", post)
    return sent


@pytest.fixture
def timed(tmp_path, clock):
    """A Wiki with a long document of twelve long sections, summarized two to a request in six requests, whose LLM has
    the settings' default time limit of 300 seconds."""
    llm = LLM()
    llm.update(
        {
            "provider": "general",
            "api_format": "openai",
            "base_url": "https://llm.example/v1/chat/completions",
            "api_key": "sk-test",
            "model_name": "test-model",
            "headers": [],
            "timeout": 300,
            "max_tokens": 0,
        }
    )
    service = Service(Database(tmp_path / "wiki.db"), llm)
    content = "".join(f"## 장 {i}\n\n{'가' * 5900}\n\n" for i in range(12)).strip()
    return service, service.wiki_save({"title": "설계서", "content": content})


def test_an_analysis_its_caller_waits_for_keeps_what_it_had_time_for(monkeypatch, clock, timed):
    """The window waits 31 minutes for wiki.analyze (electron/main.cjs RPC_TIMEOUTS): its LLM requests end a minute
    before, and the summaries leave the request that reads them the LLM's time limit of 300 seconds."""
    service, doc = timed
    started = clock.now
    sent = scripted(monkeypatch, clock, lambda system: 290 if system == pageindex.SUMMARIZE else 5)
    analyzed = service.dispatch("wiki.analyze", {"id": doc["id"]}, within=1860)
    summaries = [(given, at - started) for system, given, at in sent if system == pageindex.SUMMARIZE]
    # The sixth summary had the 50 seconds left before the summaries end at 1,500 and ran out of them; the halves it
    # would have been asked again in found no time.
    assert summaries == [(300, 0), (300, 290), (300, 580), (300, 870), (300, 1160), (50, 1450)]
    extract = [(given, at - started) for system, given, at in sent if system != pageindex.SUMMARIZE]
    assert extract == [(300, 1500)] and clock.now - started <= 1800
    assert analyzed["status"] == "ready" and analyzed["keywords"] == ["design"]
    assert analyzed["error"] == (
        "섹션 2개(11 장 10, 12 장 11)는 제한 시간 안에 요약하지 못해 본문 앞부분으로 요약을 대신했습니다. 문서 "
        "목록에서 이 문서를 골라 AI 분석을 실행하면 시간 제한 없이 모든 섹션을 요약합니다."
    )
    summaries = {o["id"]: o["summary"] for o in analyzed["outline"]}
    assert all(summaries[str(i)] == f"요약 {i}" for i in range(1, 11)) and summaries["12"].startswith("가가")
    # Analyzed from the document list, or by an import, it has no time limit.
    sent.clear()
    assert service.wiki_analyze({"id": doc["id"]})["error"] == ""
    assert sum(system == pageindex.SUMMARIZE for system, _, _ in sent) == 6


def test_an_analysis_that_cannot_end_in_time_says_so_before_its_caller_stops_waiting(monkeypatch, clock, timed):
    service, doc = timed
    started = clock.now
    # The request that reads the summaries would take longer than the time kept for it.
    sent = scripted(monkeypatch, clock, lambda system: 290 if system == pageindex.SUMMARIZE else 1000)
    with pytest.raises(UserError, match=r"요청의 제한 시간\(31분\) 안에 끝나지 않아") as error:
        service.dispatch("wiki.analyze", {"id": doc["id"]}, within=1860)
    assert "시간 제한 없이 분석합니다" in str(error.value) and clock.now - started == 1800
    assert [at - started for _, _, at in sent] == [0, 290, 580, 870, 1160, 1450, 1500]
    failed = service.wiki_get({"id": doc["id"]})
    assert failed["status"] == "error" and failed["error"] == str(error.value)


def test_the_backend_answers_an_analysis_before_its_caller_stops_waiting(tmp_path):
    """Through the backend's own input and output, as Electron talks to it: the time limit a wiki.analyze line carries
    (limit_ms) bounds the analysis, so an LLM that never answers costs it no more than that time."""
    import contextlib
    import os
    import queue
    import subprocess
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    release = threading.Event()

    class SilentLLM(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            release.wait(60)  # No answer while the test runs.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SilentLLM)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    backend = Path(__file__).resolve().parents[1]
    # Every LLM setting is given, so a developer's .env changes nothing, and no proxy stands before the local LLM.
    env = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")}
    env.update(
        {
            "PRIVATE_AGENT_DATA_DIR": str(tmp_path),
            "PYTHONUTF8": "1",
            "PYTHONPATH": "",
            "NO_PROXY": "127.0.0.1",
            "BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            "API_KEY": "k",
            "MODEL_NAME": "m",
            "TEMPERATURE": "0.5",
            "LLM_TIMEOUT": "60",
            "CA_BUNDLE": "",
            "LLM_MAX_TOKENS": "0",
            "LLM_CONTEXT_WINDOW": "64000",
            "LLM_CONCURRENCY": "3",
        }
    )
    child = subprocess.Popen(
        [sys.executable, str(backend / "main.py")],
        cwd=backend,
        text=True,
        encoding="utf-8",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
    )
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(json.loads(line)) for line in child.stdout], daemon=True).start()
    try:

        def send(ident, method, params, **extra):
            child.stdin.write(json.dumps({"id": ident, "method": method, "params": params, **extra}) + "\n")
            child.stdin.flush()

        def until(ident, seconds):
            while True:
                message = lines.get(timeout=seconds)
                if message.get("id") == ident:
                    return message

        send("save", "wiki.save", {"title": "설계서", "content": alternating()})
        # A cold start of the backend on a slow machine takes a while.
        doc = until("save", 120)["result"]["id"]
        started = time.monotonic()
        send("analyze", "wiki.analyze", {"id": doc}, limit_ms=8000)
        reply = until("analyze", 20)
        assert time.monotonic() - started < 8
        assert reply["error"] == "LLM 응답 제한 시간을 초과했습니다. 잠시 후 다시 시도해 주세요."
        send("get", "wiki.get", {"id": doc})
        assert until("get", 20)["result"]["status"] == "error"
    finally:
        release.set()
        with contextlib.suppress(OSError):  # The pipe is already closed when the backend has exited.
            child.stdin.close()
        child.wait(20)
        child.stdout.close()
        server.shutdown()
        server.server_close()
