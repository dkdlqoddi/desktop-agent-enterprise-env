import threading

import pytest

from agent.db import Database
from agent.llm import UserError
from agent.service import Service
from test_search import RoutingLLM, long_document


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), RoutingLLM())


def save(service, title, content="캐시 전략을 정리한 문서입니다."):
    return service.wiki_save({"title": title, "content": content})["id"]


def finished(service):
    service.analysis.thread.join(10)
    assert not service.analysis.thread.is_alive()
    return service.dispatch("analysis.status", {})


def test_chosen_documents_are_analyzed_one_after_another(service):
    ids = [save(service, "첫 문서"), save(service, "긴 문서", long_document()), save(service, "세 번째")]
    job = service.dispatch("analysis.start", {"ids": ids + [ids[0]]})
    assert (job["status"], job["total"]) == ("running", 3)
    job = finished(service)
    assert {k: job[k] for k in ("status", "processed", "analyzed", "skipped", "failed", "current", "queue")} == \
        {"status": "completed", "processed": 3, "analyzed": 3, "skipped": 0, "failed": 0, "current": None, "queue": []}
    assert job["results"] == []
    docs = {doc["id"]: doc for doc in service.wiki_list({})}
    assert [(docs[i]["status"], docs[i]["index_mode"]) for i in ids] == \
        [("ready", "wiki"), ("ready", "pageindex"), ("ready", "wiki")]
    # In the order chosen, each document's extraction before the next one starts.
    assert [content.split("\n")[0] for stage, _, content in service.llm.calls if stage == "extract"] == \
        ["제목: 첫 문서", "제목: 긴 문서", "제목: 세 번째"]


def test_failed_deleted_and_busy_documents_are_reported_and_the_rest_analyzed(service):
    ids = [save(service, "실패할 문서"), save(service, "지운 문서"), save(service, "분석 중인 문서"), save(service, "성공할 문서")]
    original = service.llm.structured

    def structured(system, content):
        if content.startswith("제목: 실패할"):
            raise UserError("LLM 응답 형식이 올바르지 않습니다.")
        return original(system, content)

    service.llm.structured = structured
    service.wiki_delete({"id": ids[1]})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET status='indexing' WHERE id=?", (ids[2],))
    service.analysis.start({"ids": ids})
    job = finished(service)
    assert {k: job[k] for k in ("status", "processed", "analyzed", "skipped", "failed")} == \
        {"status": "completed", "processed": 4, "analyzed": 1, "skipped": 2, "failed": 1}
    assert [(r["id"], r["title"], r["status"], r["message"]) for r in job["results"]] == [
        (ids[0], "실패할 문서", "failed", "LLM 응답 형식이 올바르지 않습니다."),
        (ids[1], "", "skipped", "삭제된 문서입니다."),
        (ids[2], "분석 중인 문서", "skipped", "다른 작업이 분석하고 있는 문서입니다.")]
    assert service.wiki_get({"id": ids[0]})["status"] == "error"
    assert service.wiki_get({"id": ids[2]})["status"] == "indexing"
    assert service.wiki_get({"id": ids[3]})["status"] == "ready"


def test_a_stopped_run_ends_after_the_document_being_analyzed(service):
    ids = [save(service, f"문서 {i}") for i in range(3)]
    started, release = threading.Event(), threading.Event()
    original = service.llm.structured

    def slow(system, content):
        started.set()
        assert release.wait(5)
        return original(system, content)

    service.llm.structured = slow
    service.analysis.start({"ids": ids})
    assert started.wait(5)
    job = service.analysis.status()
    # The screens show the document being analyzed and the ones waiting for their turn.
    assert job["current"] == {"id": ids[0], "title": "문서 0"} and job["queue"] == ids
    assert service.wiki_get({"id": ids[0]})["status"] == "indexing"
    with pytest.raises(UserError, match="이미 문서를 AI로 분석"):
        service.analysis.start({"ids": ids[1:]})
    job = service.analysis.cancel()
    assert job["status"] == "cancelling" and job["queue"] == []
    release.set()
    job = finished(service)
    assert {k: job[k] for k in ("status", "processed", "analyzed")} == {"status": "cancelled", "processed": 1, "analyzed": 1}
    assert [service.wiki_get({"id": i})["status"] for i in ids] == ["ready", "pending", "pending"]
    # A finished run is cleared for every screen, and the next one may start.
    assert "dismissed" not in service.analysis.dismiss({"id": "another"})
    assert service.analysis.dismiss({"id": job["id"]})["dismissed"] is True
    service.llm.structured = original
    assert service.analysis.start({"ids": ids[1:]})["total"] == 2
    assert finished(service)["analyzed"] == 2


def test_documents_an_import_saved_join_a_run_under_way(service):
    ids = [save(service, "첫 문서"), save(service, "두 번째")]
    started, release = threading.Event(), threading.Event()
    original = service.llm.structured

    def slow(system, content):
        started.set()
        assert release.wait(5)
        return original(system, content)

    service.llm.structured = slow
    service.analysis.start({"ids": ids})
    assert started.wait(5)
    imported = save(service, "가져온 문서")
    # Without `append` a second run is refused. With it, the documents wait after the run's own: one already waiting is
    # not added twice, and the one being analyzed is analyzed again afterwards (its text may have changed since).
    with pytest.raises(UserError, match="이미 문서를 AI로 분석"):
        service.analysis.start({"ids": [imported]})
    job = service.analysis.start({"ids": [imported, ids[1], ids[0]], "append": True})
    assert (job["total"], job["queue"]) == (4, ids + [imported, ids[0]])
    release.set()
    job = finished(service)
    assert {k: job[k] for k in ("status", "processed", "analyzed", "failed", "queue")} == \
        {"status": "completed", "processed": 4, "analyzed": 4, "failed": 0, "queue": []}
    assert service.wiki_get({"id": imported})["status"] == "ready"
    # A run that has ended takes nothing more: the documents start a run of their own.
    service.llm.structured = original
    job = service.analysis.start({"ids": [imported], "append": True})
    assert (job["status"], job["total"], job["processed"]) == ("running", 1, 0)
    assert finished(service)["analyzed"] == 1


def test_a_run_needs_chosen_documents_and_a_connected_llm(service):
    assert service.analysis.status() is None
    for bad in (None, [], "id", ["x"] * 1001):
        with pytest.raises(UserError, match="문서를 1~1,000개 선택해 주세요"):
            service.analysis.start({"ids": bad})
    with pytest.raises(UserError, match="ID"):
        service.analysis.start({"ids": [3]})
    service.llm.public = lambda: {"configured": False}
    with pytest.raises(UserError, match="LLM을 연결"):
        service.analysis.start({"ids": [save(service, "문서")]})
    assert service.analysis.status() is None
