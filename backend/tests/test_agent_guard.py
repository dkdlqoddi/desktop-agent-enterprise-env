"""What the chat's agent runs without asking and what it may not touch: the answers a command may take (once, this
conversation, always), the activity kept with an answer, and the protected paths. The turns run on the bundled Hermes
with a stand-in model server on this PC."""

import json
import os
from pathlib import Path

import pytest

from agent import agent_guard
from agent.hermes_llm import completion
from agent.llm import UserError
from test_hermes import bundled, chat, configured, connection, in_worker, waiting_turn

GUARDIAN = "security reviewer"


def commands(*lines, verdict="ESCALATE", heard=None):
    """A model that runs `lines` in the terminal, one a request, then says it is done. The guardian Hermes asks
    about a flagged command answers `verdict`. What each tool call returned is added to `heard`."""

    def reply(request):
        messages = request["messages"]
        if GUARDIAN in str(messages[0].get("content", "")):
            return 200, completion({"content": verdict}, request["model"]), False
        results = [message for message in messages if message["role"] == "tool"]
        if heard is not None:
            heard[:] = [str(message["content"]) for message in results]
        if len(results) >= len(lines):
            return 200, completion({"content": "마쳤습니다."}, request["model"]), False
        call = {
            "id": f"call_{len(results)}",
            "type": "function",
            "function": {"name": "terminal", "arguments": json.dumps({"command": lines[len(results)]})},
        }
        return 200, completion({"content": None, "tool_calls": [call]}, request["model"]), False

    return reply


def tool_call(name, arguments, heard):
    """A model that calls one tool once and then says it is done; what the call returned is added to `heard`."""

    def reply(request):
        results = [message for message in request["messages"] if message["role"] == "tool"]
        if results:
            heard[:] = [str(message["content"]) for message in results]
            return 200, completion({"content": "마쳤습니다."}, request["model"]), False
        call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        return 200, completion({"content": None, "tool_calls": [call]}, request["model"]), False

    return reply


def decide(service, request_id, status, decision):
    return service.dispatch(
        "agent.approve_command",
        {"request_id": request_id, "interaction_id": status["interaction"]["interaction_id"], "decision": decision},
    )


def finished(thread, outcome):
    thread.join(120)
    assert not thread.is_alive()
    assert "sent" in outcome, outcome.get("error")
    return outcome["sent"]["reply"]


def scratch(tmp_path, name):
    """A folder in the agent's home with a file in it, for a command to delete."""
    folder = tmp_path / "hermes" / name
    folder.mkdir(parents=True)
    (folder / "note.txt").write_text("x", encoding="utf-8")
    return folder


@bundled
def test_a_flagged_command_is_asked_about_and_the_turn_keeps_what_it_did(tmp_path, model_server):
    kept = scratch(tmp_path, "old-build")
    model_server.reply = commands("rm -rf ./old-build")
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-once")
    try:
        asked = status["interaction"]
        assert asked["kind"] == "approval" and asked["command"] == "rm -rf ./old-build"
        # One plain command: it may be allowed for the conversation, and to the letter for good.
        assert asked["smart_denied"] is False and asked["allow_session"] is True and asked["allow_permanent"] is True
        assert kept.is_dir()
        assert decide(service, "r-once", status, "once") == {"ok": True, "decision": "once"}
        # The question is answered once: a second answer finds nothing to answer.
        with pytest.raises(UserError, match="찾을 수 없습니다"):
            decide(service, "r-once", status, "deny")
        reply = finished(thread, outcome)
        assert reply["content"] == "마쳤습니다." and not kept.exists()
        # The answer carries what the turn did: the tool it ran and the command it asked about, with the decision.
        steps = reply["activity"]["steps"]
        assert [step["kind"] for step in steps] == ["tool", "approval"]
        assert steps[0]["text"].startswith("terminal") and "old-build" in steps[0]["text"]
        assert steps[1]["text"] == "rm -rf ./old-build" and steps[1]["result"] == "once" and steps[1]["note"]
        # The conversation read again has the activity with the answer, and the commands asked about are listed.
        loaded = service.dispatch("agent.messages", {})
        assert loaded[-1]["activity"]["steps"] == steps and loaded[0]["activity"] == {"steps": [], "more": 0}
        asked_about = service.dispatch("agent.activity", {})["approvals"]
        assert [(entry["text"], entry["result"]) for entry in asked_about] == [("rm -rf ./old-build", "once")]
        # Nothing was allowed beyond this run.
        assert service.dispatch("agent.grants", {}) == {"session": []} and service.agent.allowed == []
        # 대화 비우기 takes the activity with the messages.
        service.dispatch("agent.clear", {})
        assert service.dispatch("agent.activity", {}) == {"approvals": []}
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_one_of_the_apps_actions_is_told_once_in_what_the_turn_did(tmp_path, model_server):
    heard = []
    model_server.reply = tool_call("search_documents", {"query": "회의록"}, heard)
    service = chat(tmp_path, connection(model_server))
    try:
        sent = service.dispatch("agent.send", {"text": "회의록 찾아 줘", "request_id": "r-action"})
        steps = sent["reply"]["activity"]["steps"]
        assert [(step["kind"], step["text"], step["result"]) for step in steps] == [
            ("action", "문서에서 '회의록' 검색", "done")
        ]
    finally:
        service.agent.shutdown()


@bundled
def test_a_refused_command_does_not_run(tmp_path, model_server):
    kept = scratch(tmp_path, "keep-me")
    heard = []
    model_server.reply = commands("rm -rf ./keep-me", heard=heard)
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-deny")
    try:
        assert decide(service, "r-deny", status, "deny")["decision"] == "deny"
        reply = finished(thread, outcome)
        assert kept.is_dir() and "BLOCKED" in heard[0]
        assert reply["activity"]["steps"][-1]["result"] == "deny"
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_allowing_for_the_conversation_covers_that_kind_of_command_until_it_is_cleared(tmp_path, model_server):
    first, second, third = (scratch(tmp_path, name) for name in ("one", "two", "three"))
    model_server.reply = commands("rm -rf ./one")
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-session")
    try:
        assert decide(service, "r-session", status, "session")["decision"] == "session"
        assert finished(thread, outcome)["activity"]["steps"][-1]["result"] == "session"
        granted = service.dispatch("agent.grants", {})["session"]
        assert granted and all(isinstance(kind, str) for kind in granted)
        assert not first.exists() and service.agent.allowed == []

        # Another command of the same kind, in a later turn: it runs without a question.
        model_server.reply = commands("rm -rf ./two")
        sent = service.dispatch("agent.send", {"text": "둘째도 지워 줘", "request_id": "r-again"})
        assert sent["reply"]["content"] == "마쳤습니다." and not second.exists()
        assert [step["kind"] for step in sent["reply"]["activity"]["steps"]] == ["tool"]

        # After 대화 비우기 it is asked about again.
        service.dispatch("agent.clear", {})
        assert service.dispatch("agent.grants", {}) == {"session": []}
        model_server.reply = commands("rm -rf ./three")
        thread, outcome, status = waiting_turn(service, "r-cleared")
        assert status["interaction"]["kind"] == "approval" and third.is_dir()
        decide(service, "r-cleared", status, "deny")
        finished(thread, outcome)
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_always_allows_that_very_command_and_no_other(tmp_path, model_server):
    model_server.reply = commands("rm -rf ./stale-cache")
    service = chat(tmp_path, connection(model_server))
    scratch(tmp_path, "stale-cache")
    thread, outcome, status = waiting_turn(service, "r-always")
    try:
        # The answer names the command for the main process to keep; here it holds for this run of the backend.
        assert decide(service, "r-always", status, "always") == {
            "ok": True,
            "decision": "always",
            "allowed": "rm -rf ./stale-cache",
        }
        assert finished(thread, outcome)["activity"]["steps"][-1]["result"] == "always"
        assert service.agent.allowed == ["rm -rf ./stale-cache"]
        # Nothing of it is written where a copied or restored Wiki folder could bring it back: the kind of command is
        # not allowed for the conversation either.
        assert service.dispatch("agent.grants", {}) == {"session": []}

        # The same command later runs without a question.
        kept = scratch(tmp_path, "stale-cache")
        sent = service.dispatch("agent.send", {"text": "캐시 지워 줘", "request_id": "r-same"})
        assert sent["reply"]["content"] == "마쳤습니다." and not kept.exists()
        assert [step["kind"] for step in sent["reply"]["activity"]["steps"]] == ["tool"]

        # Another command of the same kind is asked about.
        other = scratch(tmp_path, "sources")
        model_server.reply = commands("rm -rf ./sources")
        thread, outcome, status = waiting_turn(service, "r-other")
        assert status["interaction"]["command"] == "rm -rf ./sources" and other.is_dir()
        decide(service, "r-other", status, "deny")
        finished(thread, outcome)

        # Taken back in the settings (the main process hands the list over again), it is asked about again.
        service.dispatch("agent.configure", {"allowed": []})
        kept = scratch(tmp_path, "stale-cache")
        model_server.reply = commands("rm -rf ./stale-cache")
        thread, outcome, status = waiting_turn(service, "r-revoked")
        assert status["interaction"]["command"] == "rm -rf ./stale-cache" and kept.is_dir()
        decide(service, "r-revoked", status, "deny")
        finished(thread, outcome)
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_a_command_of_several_cannot_be_allowed_for_good(tmp_path, model_server):
    kept = scratch(tmp_path, "joined")
    model_server.reply = commands("rm -rf ./joined && echo done")
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-joined")
    try:
        asked = status["interaction"]
        assert asked["allow_session"] is True and asked["allow_permanent"] is False
        # "Always" for it counts as this run only, and nothing is kept.
        assert decide(service, "r-joined", status, "always") == {"ok": True, "decision": "once"}
        assert finished(thread, outcome)["activity"]["steps"][-1]["result"] == "once"
        assert not kept.exists() and service.agent.allowed == []
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_a_command_the_safety_check_refused_can_only_be_run_once(tmp_path, model_server):
    scratch(tmp_path, "risky")
    model_server.reply = commands("rm -rf ./risky", verdict="DENY")
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-denied")
    try:
        asked = status["interaction"]
        assert asked["smart_denied"] is True
        assert asked["allow_session"] is False and asked["allow_permanent"] is False
        assert decide(service, "r-denied", status, "session") == {"ok": True, "decision": "once"}
        finished(thread, outcome)
        assert service.dispatch("agent.grants", {}) == {"session": []}
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_a_question_nobody_answered_is_kept_as_stopped(tmp_path, model_server):
    kept = scratch(tmp_path, "waiting")
    model_server.reply = commands("rm -rf ./waiting")
    service = chat(tmp_path, connection(model_server))
    thread, outcome, status = waiting_turn(service, "r-stop")
    try:
        assert status["interaction"]["kind"] == "approval"
        assert service.agent.stop({"request_id": "r-stop"}) == {"stopped": True}
        thread.join(60)
        assert not thread.is_alive() and kept.is_dir()
        asked_about = service.dispatch("agent.activity", {})["approvals"]
        assert [(entry["text"], entry["result"]) for entry in asked_about] == [("rm -rf ./waiting", "stopped")]
    finally:
        service.agent.shutdown()
        thread.join(30)


@bundled
def test_protected_paths_are_closed_to_the_file_tools_and_to_commands_naming_them(tmp_path, model_server):
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = vault / "key.txt"
    secret.write_text("PROTECTED-CONTENT", encoding="utf-8")
    plain = tmp_path / "hermes"
    plain.mkdir()
    (plain / "open.txt").write_text("OPEN-CONTENT", encoding="utf-8")
    service = chat(tmp_path, connection(model_server))
    service.dispatch("agent.configure", {"protected": {"credentials": False, "folders": [str(vault)]}})
    heard = []
    try:
        # A file of the agent's own folder is read as before.
        model_server.reply = tool_call("read_file", {"path": str(plain / "open.txt")}, heard)
        service.dispatch("agent.send", {"text": "읽어 줘", "request_id": "r-open"})
        assert "OPEN-CONTENT" in heard[0]

        # The file tools refuse a protected file, to read and to write.
        model_server.reply = tool_call("read_file", {"path": str(secret)}, heard)
        service.dispatch("agent.send", {"text": "읽어 줘", "request_id": "r-read"})
        assert "PROTECTED-CONTENT" not in heard[0] and "protected" in heard[0]
        model_server.reply = tool_call("write_file", {"path": str(secret), "content": "changed"}, heard)
        service.dispatch("agent.send", {"text": "바꿔 줘", "request_id": "r-write"})
        assert "protected" in heard[0] and secret.read_text(encoding="utf-8") == "PROTECTED-CONTENT"

        # A command that names the path is refused before any question, and so is one the user refuses by pattern.
        model_server.reply = commands(f"cat {secret}", heard=heard)
        sent = service.dispatch("agent.send", {"text": "보여 줘", "request_id": "r-cat"})
        assert "PROTECTED-CONTENT" not in heard[0] and "BLOCKED" in heard[0]
        assert [step["kind"] for step in sent["reply"]["activity"]["steps"]] == ["tool"]
        service.dispatch("agent.configure", {"blocked": ["*never-run-this*"]})
        model_server.reply = commands("echo never-run-this", heard=heard)
        service.dispatch("agent.send", {"text": "실행해 줘", "request_id": "r-pattern"})
        assert "BLOCKED" in heard[0]

        # The Wiki's own database is closed without any setting; the agent's home beside it is not.
        model_server.reply = tool_call("read_file", {"path": str(tmp_path / "wiki.db")}, heard)
        service.dispatch("agent.send", {"text": "읽어 줘", "request_id": "r-wiki"})
        assert "protected" in heard[0]
    finally:
        service.agent.shutdown()


@bundled
def test_a_search_of_a_parent_folder_leaves_out_what_lies_in_a_protected_one(tmp_path, model_server):
    work = tmp_path / "work"
    vault = work / "vault"
    vault.mkdir(parents=True)
    (vault / "key.txt").write_text("PROTECTED-CONTENT\nZebra-Secret\n", encoding="utf-8")
    (work / "open.txt").write_text("PROTECTED-CONTENT open\n", encoding="utf-8")
    service = chat(tmp_path, connection(model_server))
    service.dispatch("agent.configure", {"protected": {"credentials": False, "folders": [str(vault)]}})
    heard, asked = [], []

    def search(**args):
        """What the agent's file search returns for `args` over the folder that holds the protected one."""
        asked.append(args)
        model_server.reply = tool_call("search_files", {"path": str(work), **args}, heard)
        service.dispatch("agent.send", {"text": "찾아 줘", "request_id": f"r-search-{len(asked)}"})
        return heard[0]

    try:
        # Its lines, its files and its counts leave out the protected folder, and so does the number found.
        for mode in ("content", "files_only", "count"):
            found = search(pattern="PROTECTED-CONTENT", output_mode=mode)
            assert "open.txt" in found and "vault" not in found and "key.txt" not in found, (mode, found)
            assert json.loads(found)["total_count"] == 1, (mode, found)
        listed = search(pattern="*", target="files")
        assert "open.txt" in listed and "vault" not in listed and "key.txt" not in listed, listed
        assert json.loads(listed)["total_count"] == 1, listed
        # A search that finds nothing is not told what a protected file would have matched, as it otherwise would be.
        missed = search(pattern="zebra-secret")
        assert "vault" not in missed and "key.txt" not in missed and json.loads(missed)["total_count"] == 0, missed
        # A search of the protected folder itself is refused.
        refused = search(pattern="PROTECTED-CONTENT", path=str(vault))
        assert "protected" in refused and "PROTECTED-CONTENT" not in refused, refused
    finally:
        service.agent.shutdown()


@bundled
def test_the_config_of_a_turn_holds_what_runs_unasked_and_what_is_refused(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    request = {
        "model": "test-model",
        "settings": {},
        # A command with a wildcard or an operator would be read as more than itself: neither is taken.
        "allowed": ["git push --force", "rm -rf ./build/*", "rm -rf ./a && rm -rf ./b", 7],
        "protected": [str(tmp_path / "vault"), "relative/path", "/"],
        "blocked": ["*shutdown*", ""],
    }
    read = (
        "[config['command_allowlist'], config['approvals']['deny'], "
        "approval._command_matches_permanent_allowlist('git push --force'), "
        "approval._command_matches_permanent_allowlist('git push --force origin main'), "
        "approval._user_deny_block('cat " + str(tmp_path / "vault") + "/key') is not None, "
        "approval._user_deny_block('sudo shutdown now') is not None, "
        "approval._user_deny_block('ls') is None]"
    )
    statements = (
        "worker.bundled_paths()\nworker.own_settings_only()\n"
        f"config = worker.prepare_home({request!r})\nfrom tools import approval\napproval.load_permanent_allowlist()"
    )
    allowlist, deny, exact, longer, path_denied, pattern_denied, plain = in_worker(home, statements, read)
    assert allowlist == ["git push --force"]
    assert deny == ["*shutdown*", "*" + str(tmp_path / "vault") + "*"]
    assert (exact, longer, path_denied, pattern_denied, plain) == (True, False, True, True, True)
    # The settings a turn does not name leave both lists empty.
    written = "json.loads((worker.HOME / 'config.yaml').read_text(encoding='utf-8'))"
    assert configured(home, {}, f"[{written}['command_allowlist'], {written}['approvals']['deny']]") == [[], []]


@bundled
def test_always_in_a_messenger_counts_for_its_conversation_only(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    statements = (
        "worker.bundled_paths()\nworker.own_settings_only()\n"
        "worker.prepare_home({'model': 'test-model', 'settings': {}})\n"
        "from tools import approval\napproval.load_permanent_allowlist()\n"
        "worker.tell_grants(messenger=True)\n"
        "approval._persist_choice('chat-1', 'always', ['recursive delete'])"
    )
    read = (
        "[approval.is_approved('chat-1', 'recursive delete'), approval.is_approved('chat-2', 'recursive delete'), "
        "json.loads((worker.HOME / 'config.yaml').read_text())['command_allowlist']]"
    )
    assert in_worker(home, statements, read) == [True, False, []]


def test_a_protected_folder_is_opened_up_around_the_agents_own_folders(tmp_path):
    wiki = tmp_path / "wiki"
    home = wiki / "hermes"
    work = tmp_path / "projects" / "site"
    for folder in (home, wiki / "backups", wiki / "files", work / "keys", tmp_path / "projects" / "other"):
        folder.mkdir(parents=True)
    (wiki / "wiki.db").write_text("", encoding="utf-8")
    (home / "notes.txt").write_text("", encoding="utf-8")
    real = os.path.realpath

    def paths(*found):
        return sorted(real(path) for path in found)

    # What lies beside the way to a kept folder is protected; the kept folders stay open, with what is in them.
    found = agent_guard.zones([wiki, tmp_path / "projects", tmp_path / "missing"], [home, work])
    assert found == paths(
        wiki / "backups", wiki / "files", wiki / "wiki.db", tmp_path / "projects" / "other", tmp_path / "missing"
    )
    assert not any(agent_guard.under(real(home / "notes.txt"), zone) for zone in found)
    # A folder inside the work folder is protected as it is: the work folder does not open what lies in it.
    assert agent_guard.zones([work / "keys"], [home, work]) == paths(work / "keys")
    # The Wiki folder made the work folder: its files stay closed, the agent's home stays open.
    assert agent_guard.zones([wiki], [home, wiki]) == paths(wiki / "backups", wiki / "files", wiki / "wiki.db")
    # So do they where the work folder holds the Wiki folder.
    found = agent_guard.zones([wiki, wiki / "wiki.db"], [home, tmp_path])
    assert found == paths(wiki / "backups", wiki / "files", wiki / "wiki.db")
    # The same folders give the same list, whatever the order they were named in.
    assert agent_guard.zones([tmp_path / "missing", wiki], [work, home]) == agent_guard.zones(
        [wiki, tmp_path / "missing"], [home, work]
    )
    # The root of a drive is never a zone.
    assert agent_guard.zones([Path(real(tmp_path)).anchor], [home]) == []


def test_the_protected_paths_of_a_turn(tmp_path, monkeypatch):
    settings_folder = tmp_path / "app-settings"
    wiki = settings_folder / "wiki"
    home = wiki / "hermes"
    for folder in (home, wiki / "exports", wiki / "web-captures", settings_folder / "logs"):
        folder.mkdir(parents=True)
    (settings_folder / "hermes.json").write_text("{}", encoding="utf-8")
    person = tmp_path / "person"
    person.mkdir()
    monkeypatch.setattr(Path, "home", lambda: person)
    settings = {
        "app": [str(settings_folder), "relative"],
        "credentials": True,
        "folders": [str(tmp_path / "mine")],
    }
    real = os.path.realpath
    found = agent_guard.protected(wiki, home, "", settings)
    # The database with the files beside it and the backups, by name, though none is there yet; the user's folder.
    for path in (wiki / "wiki.db", wiki / "wiki.db-wal", wiki / "backups", wiki / "원본 백업", tmp_path / "mine"):
        assert real(path) in found
    assert real(person / ".ssh") in found and real(person / ".aws") in found
    # The app's settings folder holds the Wiki folder here: what lies beside the Wiki folder in it is closed, and
    # the Wiki folder stays open around what the app keeps in it (the agent's home, exports, screenshots).
    assert real(settings_folder / "hermes.json") in found and real(settings_folder / "logs") in found
    for path in (settings_folder, wiki, home, wiki / "exports", wiki / "web-captures"):
        assert not any(agent_guard.under(real(path), zone) for zone in found)
    # Turned off, the places credentials are kept are left out; the app's own stay.
    found = agent_guard.protected(wiki, home, "", {**settings, "credentials": False})
    assert real(person / ".ssh") not in found and real(wiki / "wiki.db") in found


def test_what_the_main_process_hands_over_is_read_strictly(tmp_path, model_server):
    service = chat(tmp_path, connection(model_server))
    try:
        service.dispatch(
            "agent.configure",
            {
                "allowed": ["git push --force", "git push --force", "two\nlines", "", 3, "x" * 1001],
                "blocked": ["*shutdown*", 5, "  "],
                "protected": {"app": [str(tmp_path / "app"), "relative"], "credentials": False, "folders": "none"},
            },
        )
        agent = service.agent
        assert agent.allowed == ["git push --force"] and agent.blocked == ["*shutdown*"]
        assert agent.protection == {"app": [str(tmp_path / "app")], "credentials": False, "folders": []}
        limits = agent.limits()
        assert limits["allowed"] == ["git push --force"] and limits["blocked"] == ["*shutdown*"]
        assert os.path.realpath(tmp_path / "app") in limits["protected"]
        # A messenger's conversation is not the chat's: what the chat allowed for its conversation stays out of it.
        agent.granted(["recursive delete"])
        assert agent.limits()["session_allowed"] == ["recursive delete"]
        assert agent.limits(conversation=False)["session_allowed"] == []
    finally:
        service.agent.shutdown()


def test_the_activity_of_a_turn_is_cut_short_and_goes_with_its_answer(tmp_path, model_server):
    service = chat(tmp_path, connection(model_server))
    agent = service.agent
    try:
        steps = [{"kind": "tool", "text": f"terminal: {n}", "note": "", "result": "", "at": "t"} for n in range(3)]
        with service.db.connect() as db:
            first = agent.store(db, "assistant", "하나", activity={"steps": steps, "more": 2})
            agent.store(db, "assistant", "둘", activity={"steps": [], "more": 0})
        loaded = service.dispatch("agent.messages", {})
        assert loaded[0]["activity"] == {"steps": steps, "more": 2} and loaded[1]["activity"]["steps"] == []
        # An answer that goes takes its activity with it.
        with service.db.connect() as db:
            db.execute("DELETE FROM agent_messages WHERE seq=?", (first,))
            agent.store(db, "user", "셋")
            assert db.execute("SELECT COUNT(*) FROM agent_activity").fetchone()[0] == 0
    finally:
        service.agent.shutdown()


def test_arguments_of_a_shape_no_check_expected_fail_the_action_and_not_the_turn(tmp_path, model_server):
    service = chat(tmp_path, connection(model_server))
    try:
        for name, args in (
            ("schedule_capture", {"site": "coupang", "query": "노트북", "time": "09:00", "days": ["daily"]}),
            ("search_documents", {"query": {"words": "회의록"}}),
            (["search_documents"], {}),
            ("add_task", "장보기"),
        ):
            card = service.agent.call_tool(name, args)
            assert card["status"] == "failed" and card["result"]["text"], (name, args)
    finally:
        service.agent.shutdown()
