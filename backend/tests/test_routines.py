"""Automatic routines (routines.py): the catalog and the forms' checks, schedules and the queue, the runs of each recipe
on files and documents in the Wiki, and the JIRA and Confluence routines in a real browser against the local
sites (jira_server.py, confluence_server.py)."""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import confluence_server
import jira_server
from agent import jira_import, routines
from agent.changes import compare
from agent.db import Database
from agent.html_document import page
from agent.jira import import_issue
from agent.llm import UserError
from agent.routines import MAX_ACTIVE, RECIPES, Cancelled, Run, upcoming
from agent.service import Service, now
from confluence_server import ConfluenceServer
from jira_server import JiraServer

CATALOG = json.loads((Path(__file__).resolve().parents[2] / "electron" / "login-methods.json").read_text(encoding="utf-8"))["methods"]
# A PNG of one pixel, for an image file the image importer reads without Office.
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class FakeLLM:
    """Answers each request at once with what its system prompt asked for; `fail` makes the answers fail."""

    def __init__(self):
        self.calls, self.fail, self.gate = [], False, None

    def public(self):
        return {"configured": True}

    def complete(self, messages, **_):
        self.calls.append(messages)
        if self.gate:
            self.gate.wait(10)
        if self.fail:
            raise UserError("LLM 오류")
        return f"## 정리\n\n- {messages[0]['content'][:30]}\n- 입력 {len(messages[-1]['content'])}자"

    def structured(self, system, content, **_):
        self.calls.append([{"role": "system", "content": system}, {"role": "user", "content": content}])
        return {"summary": "요약", "keywords": ["routine"], "relations": []}


def plan(credentials, site_id="site-1"):
    return {"site_id": site_id, "site_name": "JIRA", "mode": "auto", "methods": CATALOG,
            "credentials": dict(credentials)}


@pytest.fixture
def service(tmp_path):
    found = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    yield found
    found.routines.shutdown()


def wait(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def save(service, kind, **values):
    """Make a routine as the app's screen does: Electron lets it read this PC's files."""
    return service.dispatch("routines.save", {"kind": kind, "allow_files": True, **values})


def forget_session(importer, server):
    """The session the last run left in the site's profile is gone, as after the site signed the user out."""
    async def clear():
        async with importer._browser(server.origin, headless=True) as context:
            await context.clear_cookies()
    asyncio.run(clear())


def finish(service, ident, logins=()):
    """Run the routine now, as ▶ on the screen does, and wait for the run to end: the run as the screens read it. It is
    told by its id, not as the latest: a clock the system sets back (a time sync) orders it before the last run."""
    before = {run["id"] for run in service.dispatch("routines.runs", {"id": ident, "limit": routines.MAX_RUNS})}
    service.dispatch("routines.run", {"id": ident, "logins": list(logins), "allow_files": True})
    wait(lambda: not service.routines.pending(ident))
    return next(run for run in service.dispatch("routines.runs", {"id": ident, "limit": routines.MAX_RUNS})
                if run["id"] not in before)


# --- Catalog, forms and schedules -----------------------------------------------------------------------------------


def test_a_link_typed_without_its_scheme_is_saved_as_the_https_link_it_opens(service):
    watch = save(service, "jira_watch", config={"urls": "jira.test/browse/TEAM-3\nhttps://jira.test/browse/TEAM-3\n"
                                                        "//jira.test/browse/TEAM-4"})
    assert watch["config"]["urls"] == ["https://jira.test/browse/TEAM-3", "https://jira.test/browse/TEAM-4"]
    page = save(service, "confluence_watch", config={"url": "wiki.test/confluence/pages/viewpage.action?pageId=1"})
    assert page["config"]["url"] == "https://wiki.test/confluence/pages/viewpage.action?pageId=1"
    # The account of the https link signs the run in.
    assert service.dispatch("routines.needs", {"id": page["id"]}) == [{"kind": "confluence", "url": page["config"]["url"]}]
    for config in ({"url": "wiki test/confluence/display/S/P"}, {"url": "C:\\wiki\\display\\S\\P"}):
        with pytest.raises(UserError, match="웹 주소"):
            save(service, "confluence_watch", config=config)


def test_paths_and_links_are_read_one_a_line_as_electron_checks_them():
    """Electron checks an AI tool's paths for other computers read exactly as here (api-methods.cjs routinePath): an
    entry another reading could split or unquote differently, into a network path, is refused."""
    assert routines.lines_of('C:\\docs\r\n \t"D:\\팀 자료" \n\n', "경로", 20) == ["C:\\docs", "D:\\팀 자료"]
    assert routines.lines_of(['"C:\\docs"', "C:\\docs"], "경로", 20) == ["C:\\docs"]
    for value in ("C:\\ok\r\\\\evil\\share\\x", "C:\\ok\u2028\\\\evil\\share\\x", ['"\\\\evil\\share\\x'],
                  ["C:\\a\x1c"], ["C:\\a\x85b"]):
        with pytest.raises(UserError, match="따옴표나 보이지 않는 문자"):
            routines.lines_of(value, "경로", 20)


def test_saving_a_routine_while_a_run_is_queued_never_holds_up_both(service):
    """A run being queued holds the runner's lock while it writes; a save writes first and reads it afterwards."""
    first = save(service, "analyze_new", enabled=False)
    other = save(service, "analyze_new", enabled=False)
    with ThreadPoolExecutor(1) as pool:
        with service.routines.lock:  # As routines.run and routines.execute hold it.
            saving = pool.submit(service.dispatch, "routines.save", {"id": first["id"], "name": "새 이름"})
            time.sleep(0.5)  # The save has written its change by now.
            started = time.monotonic()
            service.dispatch("routines.run", {"id": other["id"], "logins": []})
            assert time.monotonic() - started < 5
        assert saving.result(10)["name"] == "새 이름"
    wait(lambda: not service.routines.pending(other["id"]))


def test_toggle_the_limit_of_routines_on_and_delete(service):
    first = save(service, "analyze_new")
    off = service.dispatch("routines.toggle", {"id": first["id"], "enabled": False})
    assert (off["enabled"], off["next_run_at"]) == (False, None)
    on = service.dispatch("routines.toggle", {"id": first["id"], "enabled": True})
    assert on["enabled"] and on["next_run_at"] <= now()
    for _ in range(MAX_ACTIVE - 1):
        save(service, "analyze_new")
    with pytest.raises(UserError, match=f"최대 {MAX_ACTIVE}개"):
        save(service, "analyze_new")
    paused = save(service, "analyze_new", enabled=False)
    with pytest.raises(UserError, match=f"최대 {MAX_ACTIVE}개"):
        service.dispatch("routines.toggle", {"id": paused["id"], "enabled": True})
    service.dispatch("routines.delete", {"id": first["id"]})
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        service.dispatch("routines.get", {"id": first["id"]})
    assert len(service.dispatch("routines.list", {})) == MAX_ACTIVE


def test_a_due_routine_runs_once_and_missed_runs_collapse(service):
    routine = save(service, "analyze_new", frequency="daily", run_time="09:00")
    assert not service.dispatch("routines.due", {})
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with service.db.connect() as db:
        db.execute("UPDATE routines SET next_run_at=? WHERE id=?", (past, routine["id"]))
    due = service.dispatch("routines.due", {})
    assert due == [{"id": routine["id"], "next_run_at": past, "needs": []}]
    # Two lookups of the same occurrence: only the first runs.
    assert service.dispatch("routines.execute", {"id": routine["id"], "next_run_at": past, "logins": []})["queued"]
    assert not service.dispatch("routines.execute", {"id": routine["id"], "next_run_at": past, "logins": []})["queued"]
    wait(lambda: not service.routines.pending(routine["id"]))
    current = service.dispatch("routines.get", {"id": routine["id"]})
    assert current["next_run_at"] == upcoming(routine) > now()  # The next 9:00, once.
    runs = service.dispatch("routines.runs", {"id": routine["id"]})
    assert [(run["started_by"], run["status"]) for run in runs] == [("schedule", "success")]
    assert current["last_summary"] == runs[0]["summary"] == "분석할 새 문서가 없습니다 · 남은 문서 0개"
    # An interval counts from the end of the last run.
    every = save(service, "analyze_new", interval=30)
    finish(service, every["id"])
    later = datetime.fromisoformat(service.dispatch("routines.get", {"id": every["id"]})["next_run_at"])
    assert timedelta(minutes=29) < later - datetime.now(timezone.utc) <= timedelta(minutes=30)
    with pytest.raises(UserError):
        service.dispatch("routines.execute", {"id": every["id"], "next_run_at": "", "logins": []})


def test_events_tell_electron_about_runs_that_ended(service):
    cursor = service.dispatch("routines.events", {})["cursor"]
    routine = save(service, "analyze_new", notify=True)
    finish(service, routine["id"])
    events = service.dispatch("routines.events", {"after": cursor})
    assert [(run["routine_id"], run["status"], run["notify"], run["logins"]) for run in events["runs"]] == \
        [(routine["id"], "success", True, [])]
    assert events["active"] == 0
    assert service.dispatch("routines.events", {"after": events["cursor"]})["runs"] == []


# --- Files, analysis and reports --------------------------------------------------------------------------------------


def test_source_refresh_updates_changed_files_takes_new_ones_and_tells_missing_ones(service, tmp_path):
    folder = tmp_path / "업무"
    (folder / "하위").mkdir(parents=True)
    note, deep = folder / "회의.md", folder / "하위" / "설계.md"
    note.write_text("# 회의\n\n결정 A", encoding="utf-8")
    deep.write_text("# 설계\n\n초안", encoding="utf-8")
    (folder / "그림.png").write_bytes(PIXEL)
    gone = folder / "지난.md"
    gone.write_text("# 지난\n\n옛 내용", encoding="utf-8")
    routine = save(service, "source_refresh", config={"paths": str(folder)})
    assert routine["files"] and service.dispatch("routines.needs", {"id": routine["id"]}) == []
    run = finish(service, routine["id"])
    assert run["status"] == "success", run
    assert run["summary"] == "새 문서 4 · 그대로 0" and run["changed"]
    assert sorted(item["title"] for item in run["items"]) == ["그림.png", "설계.md", "지난.md", "회의.md"]
    assert service.images.status()["dismissed"]  # The routine's job left the import box.

    note.write_text("# 회의\n\n결정 B", encoding="utf-8")
    (folder / "새 메모.md").write_text("# 새 메모", encoding="utf-8")
    gone.unlink()
    edited = next(doc for doc in service.wiki_list({}) if doc["title"] == "설계")
    service.wiki_save({"id": edited["id"], "title": "설계", "content": "내가 고친 설계"})
    deep.write_text("# 설계\n\n최종안", encoding="utf-8")
    run = finish(service, routine["id"])
    counts = {item["title"]: item["status"] for item in run["items"]}
    assert counts == {"회의.md": "changed", "새 메모.md": "new", "설계.md": "edited", "지난": "missing"}
    assert run["summary"] == "업데이트 1 · 새 문서 1 · 그대로 1 · 고친 문서라 건너뜀 1 · 원본 없음 1"
    assert "결정 B" in service.wiki_get({"id": next(i["doc_id"] for i in run["items"] if i["title"] == "회의.md")})["content"]
    assert service.wiki_get({"id": edited["id"]})["content"] == "내가 고친 설계"

    # A path that is gone fails alone; this PC's files without Office or Notepad are left out with the reason.
    other = save(service, "source_refresh", config={"paths": f"{tmp_path / '없는 폴더'}\n{folder}"})
    run = finish(service, other["id"])
    assert run["items"][0] == {"title": str(tmp_path / "없는 폴더"), "status": "failed", "doc_id": None,
                               "url": str(tmp_path / "없는 폴더"), "message": "파일이나 폴더를 찾을 수 없습니다."}


def test_source_refresh_leaves_out_the_files_whose_documents_were_purged(service, tmp_path):
    folder = tmp_path / "업무"
    folder.mkdir()
    (folder / "회의.md").write_text("# 회의\n\n결정 A", encoding="utf-8")
    (folder / "그림.png").write_bytes(PIXEL)
    routine = save(service, "source_refresh", config={"paths": str(folder)})
    assert finish(service, routine["id"])["summary"] == "새 문서 2 · 그대로 0"
    service.wiki_delete_many({"ids": [doc["id"] for doc in service.wiki_list({})]})
    service.wiki_purge({"all": True})
    # Emptied from the trash for good: the routine does not bring them back, and says why.
    run = finish(service, routine["id"])
    assert (run["summary"], run["changed"], run["items"]) == ("그대로 0 · 영구 삭제한 문서라 건너뜀 2", False, [])
    assert service.wiki_list({}) == []


def test_source_refresh_counts_every_file_of_a_folder_whose_job_lists_fewer(service, tmp_path, monkeypatch):
    # A folder job lists the results of up to MAX_RESULTS files; the run counts the others by the job's counts.
    monkeypatch.setattr("agent.file_import.MAX_RESULTS", 1)
    folder = tmp_path / "그림"
    folder.mkdir()
    for n in range(3):
        (folder / f"{n}.png").write_bytes(PIXEL + bytes([n]))  # Bytes after its end make another file of it.
    routine = save(service, "source_refresh", config={"paths": str(folder)})
    run = finish(service, routine["id"])
    assert (run["status"], run["summary"]) == ("success", "새 문서 3 · 그대로 0"), run
    assert [item["title"] for item in run["items"]] == ["2.png"]

    edited = next(doc for doc in service.wiki_list({}) if doc["title"] == "1")
    service.wiki_save({"id": edited["id"], "title": "1", "content": "내가 고친 설명"})
    (folder / "1.png").write_bytes(PIXEL + bytes([9]))
    run = finish(service, routine["id"])
    assert run["summary"] == "그대로 2 · 고친 문서라 건너뜀 1", run


def test_analyze_new_and_the_work_report(service):
    docs = [service.wiki_save({"title": f"문서 {n}", "content": f"내용 {n}"}) for n in range(3)]
    routine = save(service, "analyze_new", config={"limit": 10})
    assert not routine["notify"]  # Quiet unless the user asks.
    run = finish(service, routine["id"])
    assert (run["summary"], run["changed"]) == ("3개 분석 · 남은 문서 0개", True)
    assert all(service.wiki_get({"id": doc["id"]})["index_mode"] == "wiki" for doc in docs)

    # With failed documents retried, those never tried still come first.
    failed = service.wiki_save({"title": "실패한 문서", "content": "내용"})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET status='error' WHERE id=?", (failed["id"],))
    fresh = service.wiki_save({"title": "새 문서", "content": "내용"})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET updated_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", fresh["id"]))
    one = save(service, "analyze_new", config={"limit": 10, "retry": True}, enabled=False)
    with service.db.connect() as db:
        db.execute("UPDATE routines SET config=? WHERE id=?", (json.dumps({"limit": 1, "retry": True}), one["id"]))
    assert finish(service, one["id"])["summary"] == "1개 분석 · 남은 문서 1개"
    assert service.wiki_get({"id": fresh["id"]})["index_mode"] == "wiki"
    assert service.wiki_get({"id": failed["id"]})["index_mode"] == ""
    service.dispatch("routines.delete", {"id": one["id"]})

    service.dispatch("tasks.save", {"title": "보고서 제출", "status": "done"})
    report = save(service, "work_report", config={"period": "week", "focus": "팀장 보고용"})
    run = finish(service, report["id"])
    assert run["status"] == "success" and run["changed"], run
    # The three documents, and the failed one, which changed this week too.
    assert run["summary"] == "주간 업무 보고 초안을 썼습니다 · 참고한 문서 4개"
    draft = service.wiki_get({"id": run["documents"][0]["id"]})
    assert draft["title"].startswith("업무 보고 초안 · ") and draft["title"].endswith("(주간)")
    request = json.loads(service.llm.calls[-1][-1]["content"])
    assert request["request"] == "팀장 보고용" and [task["title"] for task in request["tasks_done"]] == ["보고서 제출"]
    assert request["routine_findings"] == [{"routine": "새 문서 AI 분석", "found": "3개 분석 · 남은 문서 0개"}]
    # The next report leaves the earlier drafts out of what it reads.
    finish(service, report["id"])
    titles = [doc["title"] for doc in json.loads(service.llm.calls[-1][-1]["content"])["documents"]]
    assert not any(title.startswith("업무 보고 초안") for title in titles)


def test_the_same_unexpected_failure_again_is_told_once_though_each_has_a_log_id_of_its_own(service, monkeypatch):
    def broken(run):
        raise RuntimeError("연결이 끊어졌습니다")

    monkeypatch.setattr(RECIPES["analyze_new"], "run", broken)
    routine = save(service, "analyze_new", notify=True)
    cursor = service.dispatch("routines.events", {})["cursor"]
    errors = [finish(service, routine["id"])["error"] for _ in range(3)]
    assert len(set(errors)) == 3 and all(error.startswith("자동 작업을 실행하지 못했습니다.") for error in errors)
    runs = service.dispatch("routines.events", {"after": cursor})["runs"]
    assert [run["quiet"] for run in runs] == [False, True, True]


def test_cancel_a_queued_or_running_run_and_shutdown(service):
    service.llm.gate = threading.Event()
    first = save(service, "work_report")
    second = save(service, "analyze_new")
    service.wiki_save({"title": "문서", "content": "내용"})
    service.dispatch("routines.run", {"id": first["id"], "logins": []})
    service.dispatch("routines.run", {"id": second["id"], "logins": []})
    wait(lambda: service.dispatch("routines.get", {"id": first["id"]})["running"])
    assert service.dispatch("routines.get", {"id": second["id"]})["queued"]
    with pytest.raises(UserError, match="이미 실행 중"):
        service.dispatch("routines.run", {"id": first["id"], "logins": []})
    # Turned off, a waiting run does not start.
    assert not service.dispatch("routines.toggle", {"id": second["id"], "enabled": False})["queued"]
    service.dispatch("routines.cancel", {"id": first["id"]})
    service.llm.gate.set()
    wait(lambda: not service.routines.pending(first["id"]))
    assert service.dispatch("routines.runs", {"id": first["id"]})[0]["status"] == "cancelled"
    assert service.dispatch("routines.runs", {"id": second["id"]})[0]["status"] == "cancelled"
    # Runs cut off by the app's end show as failed on the next start.
    service.llm.gate = threading.Event()
    service.dispatch("routines.run", {"id": first["id"], "logins": []})
    wait(lambda: service.dispatch("routines.get", {"id": first["id"]})["running"])
    with service.db.connect() as db:
        db.execute("UPDATE routine_runs SET status='running' WHERE routine_id=?", (first["id"],))
    restarted = Service(Database(service.db.path), FakeLLM())
    assert restarted.dispatch("routines.runs", {"id": first["id"]})[0]["error"] == routines.STOPPED
    service.routines.shutdown()
    service.llm.gate.set()
    with pytest.raises(UserError):
        service.dispatch("routines.runs", {"limit": 0})


class FakeImporter:
    """A file importer that keeps one job, as file_import.FileImporter does."""

    def __init__(self, job, ending="completed"):
        self.lock, self.job, self.ending, self.dismissed = threading.RLock(), job, ending, []

    def start(self):
        self.job = {"id": "routine-job", "status": "running", "results": [], "routine": "r"}
        threading.Timer(0.3, lambda: self.job.update(status=self.ending)).start()
        return dict(self.job)

    def cancel(self):
        pass

    def dismiss(self, params):
        if self.job["id"] == params["id"]:
            self.job["dismissed"] = True
            self.dismissed.append(params["id"])


def test_a_routine_job_gives_back_the_users_import_and_stops_with_it(service, monkeypatch):
    monkeypatch.setattr(routines, "POLL", 0.05)
    routine = save(service, "analyze_new")
    with service.db.connect() as db:
        row = db.execute("SELECT * FROM routines WHERE id=?", (routine["id"],)).fetchone()
    run = Run(service.routines, row, [], threading.Event())
    users = {"id": "users-job", "status": "completed", "results": [{"path": "a"}], "routine": ""}
    importer = FakeImporter(users)
    job = run.job(importer, importer.start, "가짜")
    # The run's job left the import box, which shows the user's finished import again.
    assert (job["id"], job["status"], importer.dismissed) == ("routine-job", "completed", ["routine-job"])
    assert importer.job is users
    # A job the user stopped in the import box stops the run.
    stopped = FakeImporter(None, ending="cancelled")
    with pytest.raises(Cancelled):
        run.job(stopped, stopped.start, "가짜")


# --- What changed in a page or ticket -----------------------------------------------------------------------------


def test_an_import_tells_what_changed_in_the_text_imported_before(service):
    assert compare("a\nb\nc", "a\nb\nc") is None
    assert compare("a\n\nb", "a\nB\n\nc") == {"added": 2, "removed": 1, "more": 0,
                                               "lines": [{"op": "+", "text": "B"}, {"op": "-", "text": "b"},
                                                         {"op": "+", "text": "c"}]}
    long = compare("\n".join(map(str, range(200))), "\n".join(f"x{n}" for n in range(200)))
    assert (len(long["lines"]), long["more"]) == (60, 340)

    capture = lambda body: {"source_url": "https://jira.test/browse/TEAM-1", "issue_key": "TEAM-1",
                            "html": f"<html><head><title>[TEAM-1] 제목</title></head><body>{body}</body></html>",
                            "track_changes": True}
    first = import_issue(service, capture("<p>상태 Open</p>"))
    assert first["status"] == "imported" and "change" not in first
    assert "change" not in import_issue(service, capture("<p>상태 Open</p>"))
    updated = import_issue(service, capture("<p>상태 In Progress</p>"))
    assert updated["status"] == "updated"
    assert updated["change"]["lines"] == [{"op": "-", "text": "상태 Open"}, {"op": "+", "text": "상태 In Progress"}]
    # Text the user changed stays, and the change is still told.
    service.wiki_save({"id": first["doc_id"], "title": "제목", "content": "내 메모"})
    skipped = import_issue(service, capture("<p>상태 Done</p>"))
    assert (skipped["status"], skipped["reason"]) == ("skipped", "edited")
    assert skipped["change"]["lines"][-1] == {"op": "+", "text": "상태 Done"}
    # Without track_changes nothing is compared.
    assert "change" not in import_issue(service, {**capture("<p>다른 내용</p>"), "track_changes": False})


# --- Sites in a real browser --------------------------------------------------------------------------------------


@pytest.fixture
def jira(tmp_path):
    server = JiraServer()
    found = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    importer = found.jira
    importer.profile_root = tmp_path / "profiles"
    importer.optional_seconds, importer.form_seconds, importer.login_seconds = 1, 15, 20
    yield server, found
    found.routines.shutdown()
    importer.shutdown()
    server.close()


def test_real_browser_jira_watch_search_and_project_report(jira, monkeypatch):
    server, service = jira
    ticket3 = f"{server.origin}/jira/browse/TEAM-3"
    login = [{"kind": "jira", "url": server.url, "login": plan(server.credentials)}]
    watch = save(service, "jira_watch", config={"urls": [server.url, ticket3]})
    assert service.dispatch("routines.needs", {"id": watch["id"]}) == [{"kind": "jira", "url": server.url}]
    run = finish(service, watch["id"], login)
    assert (run["status"], run["summary"], run["changed"]) == ("success", "티켓 2개를 기준으로 가져왔습니다", False), run
    assert len(service.wiki_list({})) == 2 and service.jira.status()["job"]["dismissed"]
    assert service.jira.status()["job"]["routine"] == "JIRA 티켓 변경 확인"
    # The login method it found is told to Electron with the run.
    events = service.dispatch("routines.events", {"after": ""})
    assert events["runs"][0]["logins"] == [{"site_id": "site-1", "ok": True, "method": "sso-link", "detected": True,
                                            "learned": None, "hosts": ["localhost"], "error": "", "diagnosis": None}]

    monkeypatch.setitem(jira_server.ISSUES, "TEAM-1", {**jira_server.ISSUES["TEAM-1"], "status": "In Progress",
                                                       "category": "indeterminate", "updated": "2026-10-06T10:00:00.000+0900"})
    run = finish(service, watch["id"], login)
    assert (run["summary"], run["changed"]) == ("바뀐 티켓 1개 · 티켓 2개 확인", True), run
    item = run["items"][0]
    assert (item["title"], item["status"]) == ("[TEAM-1] 로그인 화면 오류", "changed")
    assert {"op": "+", "text": "| 상태 | In Progress |"} in item["change"]["lines"]
    assert {"op": "-", "text": "| 상태 | Open |"} in item["change"]["lines"]
    assert "| 상태 | In Progress |" in service.wiki_get({"id": item["doc_id"]})["content"]
    assert service.llm.calls[-1][0]["content"] == routines.CHANGES and run["text"].startswith("## 정리")
    assert "+ | 상태 | In Progress |" in service.llm.calls[-1][-1]["content"]

    # A saved search: new, changed and dropped tickets after the first run, which sets the baseline.
    search = save(service, "jira_filter", config={"url": f"{server.origin}/jira", "jql": "project = TEAM\nAND resolution = Unresolved"})
    assert search["config"]["jql"] == "project = TEAM AND resolution = Unresolved"
    site = [{"kind": "jira", "url": f"{server.origin}/jira", "login": plan(server.credentials)}]
    run = finish(service, search["id"], site)
    assert (run["summary"], run["changed"]) == ("티켓 2개를 기준으로 삼았습니다", False), run
    assert server.jql[-1] == "project = TEAM AND resolution = Unresolved ORDER BY updated DESC"
    monkeypatch.setitem(jira_server.ISSUES, "TEAM-3", {**jira_server.ISSUES["TEAM-3"], "updated": "2026-10-06T11:00:00.000+0900",
                                                       "text": "세션이 60분 뒤 만료됩니다"})
    monkeypatch.setitem(jira_server.ISSUES, "TEAM-1", {**jira_server.ISSUES["TEAM-1"], "category": "done", "status": "Done"})
    run = finish(service, search["id"], site)
    assert (run["summary"], run["changed"]) == ("수정 1개 · 빠짐 1개 · 모두 1개", True), run
    assert [(item["title"], item["status"]) for item in run["items"]] == [
        ("[TEAM-3] 로그인 세션 만료", "changed"), ("TEAM-1", "removed")]
    changed = run["items"][0]
    assert {"op": "+", "text": "세션이 60분 뒤 만료됩니다"} in changed["change"]["lines"]
    doc = service.wiki_get({"id": run["documents"][0]["id"]})
    assert doc["title"] == "JIRA 검색 결과 · JIRA 검색 결과 알림" and "- 결과에서 빠짐: TEAM-1" in doc["content"]

    # A project's progress, with the in-progress tickets imported.
    monkeypatch.setitem(jira_server.ISSUES, "TEAM-3", {**jira_server.ISSUES["TEAM-3"], "status": "In Progress",
                                                       "category": "indeterminate", "due": "2026-01-01"})
    project = save(service, "jira_project", config={"url": f"{server.origin}/jira/projects/TEAM", "import": True})
    board = [{"kind": "jira", "url": f"{server.origin}/jira/projects/TEAM", "login": plan(server.credentials)}]
    run = finish(service, project["id"], board)
    assert (run["status"], run["changed"]) == ("success", True), run
    assert run["summary"] == "진행 중 1 · 미해결 1 · 7일 완료 2 · 기한 지남 1"
    assert [(metric["label"], metric["value"]) for metric in run["metrics"]] == [
        ("전체", 3), ("미해결", 1), ("진행 중", 1), ("7일 새로", 3), ("7일 완료", 2), ("기한 지남", 1)]
    report = service.wiki_get({"id": run["documents"][0]["id"]})
    assert report["title"] == "JIRA 진행 현황 · TEAM"
    for expected in ("# JIRA 진행 현황 · TEAM · 팀 프로젝트", "## AI 진행 요약", "## 진행 중인 티켓 1개",
                     "| TEAM-3 | 로그인 세션 만료 | In Progress | 김개발 |", "## 기한 지난 티켓 1개", "## 최근 7일 완료한 티켓 2개",
                     "| 김개발 | 1 | 1 |"):
        assert expected in report["content"], expected
    assert any('project = "TEAM" AND resolution = Unresolved ORDER BY updated DESC' == jql for jql in server.jql)
    assert service.jira.status()["job"]["results"][0]["key"] == "TEAM-3"
    # The report again, with no ticket started or finished since: no news.
    run = finish(service, project["id"], board)
    assert (run["status"], run["changed"], run["items"]) == ("success", False, []), run

    # The user changed the ticket's Wiki document: the ticket's next change is told once, not at every run.
    team1 = next(doc for doc in service.wiki_list({}) if doc["title"].startswith("[TEAM-1]"))
    service.wiki_save({"id": team1["id"], "title": team1["title"], "content": "내 메모"})
    monkeypatch.setitem(jira_server.ISSUES, "TEAM-1", {**jira_server.ISSUES["TEAM-1"], "text": "다시 바뀐 설명",
                                                       "updated": "2026-10-06T12:00:00.000+0900"})
    run = finish(service, watch["id"], login)
    assert (run["summary"], run["changed"], run["items"][0]["status"]) == ("바뀐 티켓 1개 · 티켓 2개 확인", True, "edited")
    run = finish(service, watch["id"], login)
    assert (run["summary"], run["changed"]) == ("바뀐 티켓 없음 · 티켓 2개 확인", False)
    assert run["items"][0]["repeat"] and "지난번에 알린 변경과 같습니다" in run["items"][0]["message"]
    assert service.wiki_get({"id": team1["id"]})["content"] == "내 메모"

    # A search imports at most MAX_IMPORTS tickets a run; the others follow, told only once.
    monkeypatch.setattr(routines, "MAX_IMPORTS", 1)
    for key, stamp in (("TEAM-1", "2026-10-07T09:00:00.000+0900"), ("TEAM-3", "2026-10-07T10:00:00.000+0900")):
        monkeypatch.setitem(jira_server.ISSUES, key, {**jira_server.ISSUES[key], "category": "indeterminate",
                                                      "status": "In Progress", "updated": stamp})
    run = finish(service, search["id"], site)
    assert run["summary"].startswith("새 티켓 1개 · 수정 1개"), run
    assert len(service.jira.status()["job"]["results"]) == 1
    run = finish(service, search["id"], site)
    assert (run["summary"], run["changed"]) == ("바뀐 티켓 없음 · 모두 2개", False), run
    assert [result["key"] for result in service.jira.status()["job"]["results"]] == ["TEAM-1"]
    # A search that found more than it reads cannot tell which tickets left it.
    monkeypatch.setattr(jira_import, "MAX_FOUND", 1)
    run = finish(service, search["id"], site)
    assert "빠짐" not in run["summary"], run
    monkeypatch.setattr(jira_import, "MAX_FOUND", 500)

    # A refused login after the password was sent turns the routine off. The session the runs before left in the
    # profile would sign in by itself, so it goes first.
    forget_session(service.jira, server)
    wrong = [{"kind": "jira", "url": server.url, "login": plan({"id": "tester", "password": "wrong"})}]
    run = finish(service, watch["id"], wrong)
    assert run["status"] == "error", run
    current = service.dispatch("routines.get", {"id": watch["id"]})
    assert not current["enabled"] and current["last_error"].startswith(routines.PAUSED)


def test_real_browser_confluence_watch_tells_what_changed(tmp_path, monkeypatch):
    server = ConfluenceServer()
    service = Service(Database(tmp_path / "data" / "wiki.db"), None)
    importer = service.confluence
    importer.profile_root = tmp_path / "profiles"
    try:
        routine = save(service, "confluence_watch", config={"url": server.url})
        run = finish(service, routine["id"])
        assert (run["status"], run["summary"], run["changed"]) == ("success", "페이지 1개를 기준으로 가져왔습니다", False), run
        run = finish(service, routine["id"])
        assert (run["summary"], run["items"]) == ("바뀐 곳 없음 · 페이지 1개 확인", [])
        monkeypatch.setattr(confluence_server, "BODY", confluence_server.BODY + "<h2>새 결정</h2><p>배포는 금요일</p>")
        run = finish(service, routine["id"])
        assert (run["summary"], run["changed"]) == ("바뀐 페이지 1개 · 페이지 1개 확인", True), run
        item = run["items"][0]
        assert item["status"] == "changed" and item["title"] == "페이지 1"
        assert item["change"]["lines"] == [{"op": "+", "text": "## 새 결정"}, {"op": "+", "text": "배포는 금요일"}]
        # Without an LLM the lines alone, with a note.
        assert run["text"] == "" and run["notes"] == ["LLM이 연결되어 있지 않아 바뀐 내용을 요약하지 않았습니다."]
        assert "배포는 금요일" in service.wiki_get({"id": item["doc_id"]})["content"]
        # A page that cannot be read is a failure to tell, not a quiet "no change".
        server.forbidden = True
        hidden = save(service, "confluence_watch", config={"url": server.url.replace("pageId=1", "pageId=2")})
        run = finish(service, hidden["id"])
        assert run["status"] == "error" and "403" in run["error"], run
        assert finish(service, hidden["id"])["status"] == "error"
    finally:
        service.routines.shutdown()
        importer.shutdown()
        server.close()
