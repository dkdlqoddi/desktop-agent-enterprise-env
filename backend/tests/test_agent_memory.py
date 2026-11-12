"""What the chat's agent remembers, read and changed by the user (agent_memory.py)."""

import json

import pytest

from agent.agent_memory import DELIMITER, LIMITS
from agent.hermes_llm import completion
from agent.llm import BusyError, UserError
from test_hermes import bundled, chat, connection


@pytest.fixture
def service(tmp_path, model_server):
    made = chat(tmp_path, connection(model_server))
    yield made
    made.agent.shutdown()


def test_the_entries_are_listed_as_the_agent_reads_them(tmp_path, service):
    # Nothing was remembered yet: empty lists, with the limits.
    shown = service.dispatch("agent.memory", {})
    assert shown == {
        "memory": {"entries": [], "chars": 0, "limit": LIMITS["memory"]},
        "user": {"entries": [], "chars": 0, "limit": LIMITS["user"]},
        "records": {"files": 0, "bytes": 0},
    }
    folder = tmp_path / "hermes" / "memories"
    folder.mkdir(parents=True)
    # A file as the agent wrote it, with an entry twice and a blank one.
    (folder / "MEMORY.md").write_text(DELIMITER.join(["첫째 메모", "둘째\n두 줄", "첫째 메모", "  "]), encoding="utf-8")
    shown = service.dispatch("agent.memory", {})
    assert shown["memory"]["entries"] == ["첫째 메모", "둘째\n두 줄"]
    assert shown["memory"]["chars"] == len("첫째 메모" + DELIMITER + "둘째\n두 줄")
    assert shown["user"]["entries"] == []


def test_a_list_is_saved_as_the_user_left_it(tmp_path, service):
    saved = service.dispatch(
        "agent.memory_save", {"target": "user", "entries": [" 한국어로 답한다 ", "", "오전에 일한다"]}
    )
    assert saved["user"]["entries"] == ["한국어로 답한다", "오전에 일한다"]
    file = tmp_path / "hermes" / "memories" / "USER.md"
    assert file.read_text(encoding="utf-8") == "한국어로 답한다" + DELIMITER + "오전에 일한다"
    # An entry edited, one removed: the list is the new one, and the other file is not touched.
    saved = service.dispatch("agent.memory_save", {"target": "user", "entries": ["영어로 답한다"]})
    assert saved["user"]["entries"] == ["영어로 답한다"] and saved["memory"]["entries"] == []
    assert not (tmp_path / "hermes" / "memories" / "MEMORY.md").exists()
    # Emptied, the file holds nothing.
    assert service.dispatch("agent.memory_save", {"target": "user", "entries": []})["user"]["chars"] == 0
    assert file.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "params",
    [
        {"target": "other", "entries": []},
        {"target": "memory", "entries": "one"},
        {"target": "memory", "entries": [1]},
        {"target": "memory", "entries": ["a\x00b"]},
        {"target": "memory", "entries": ["x"] * 201},
    ],
)
def test_a_list_that_is_not_one_is_refused(service, params):
    with pytest.raises(UserError, match="기억 목록"):
        service.dispatch("agent.memory_save", params)


def test_an_entry_cannot_hold_the_line_that_separates_entries(service):
    with pytest.raises(UserError, match="§"):
        service.dispatch("agent.memory_save", {"target": "memory", "entries": ["위\n§\n아래"]})
    # The sign inside a line is only a sign.
    kept = service.dispatch("agent.memory_save", {"target": "memory", "entries": ["제1조 § 2항"]})
    assert kept["memory"]["entries"] == ["제1조 § 2항"]


def test_a_list_over_the_limit_can_only_get_shorter(tmp_path, service):
    limit = LIMITS["user"]
    with pytest.raises(UserError, match="1,375자"):
        service.dispatch("agent.memory_save", {"target": "user", "entries": ["가" * (limit + 1)]})
    assert service.dispatch("agent.memory", {})["user"]["entries"] == []
    # A file that grew past the limit (written from outside the tool) is trimmed step by step.
    folder = tmp_path / "hermes" / "memories"
    folder.mkdir(parents=True, exist_ok=True)
    entries = ["가" * 800, "나" * 800, "다" * 800]
    (folder / "USER.md").write_text(DELIMITER.join(entries), encoding="utf-8")
    with pytest.raises(UserError, match="줄이거나 지워"):
        service.dispatch("agent.memory_save", {"target": "user", "entries": [*entries, "라"]})
    trimmed = service.dispatch("agent.memory_save", {"target": "user", "entries": entries[:2]})
    assert trimmed["user"]["entries"] == entries[:2] and trimmed["user"]["chars"] > limit


def test_nothing_is_changed_while_a_turn_runs(service):
    service.agent.sending = True
    try:
        with pytest.raises(BusyError, match="작업 중"):
            service.dispatch("agent.memory_save", {"target": "memory", "entries": ["메모"]})
        with pytest.raises(BusyError, match="작업 중"):
            service.dispatch("agent.memory_forget", {})
        # Reading is always possible.
        assert service.dispatch("agent.memory", {})["memory"]["entries"] == []
    finally:
        service.agent.sending = False


def test_the_records_of_the_turns_are_deleted_whole_and_the_memory_stays(tmp_path, service):
    home = tmp_path / "hermes"
    (home / "memories").mkdir(parents=True)
    (home / "sessions" / "2026").mkdir(parents=True)
    (home / "memories" / "MEMORY.md").write_text("남길 메모", encoding="utf-8")
    (home / "state.db").write_bytes(b"x" * 10)
    (home / "state.db-wal").write_bytes(b"x" * 5)
    (home / "sessions" / "one.json").write_text("{}", encoding="utf-8")
    (home / "sessions" / "2026" / "two.json").write_text("{}", encoding="utf-8")
    (home / "config.yaml").write_text("{}", encoding="utf-8")
    assert service.dispatch("agent.memory", {})["records"] == {"files": 4, "bytes": 19}

    # While the messenger gateway is on it keeps the records open: they are left alone.
    service.agent.gateway.state = "running"
    with pytest.raises(UserError, match="게이트웨이"):
        service.dispatch("agent.memory_forget", {})
    assert (home / "state.db").exists()
    service.agent.gateway.state = "off"

    cleared = service.dispatch("agent.memory_forget", {})
    assert cleared["records"] == {"files": 0, "bytes": 0}
    assert cleared["memory"]["entries"] == ["남길 메모"]
    assert not (home / "state.db").exists() and not (home / "state.db-wal").exists()
    assert (home / "sessions").is_dir() and not any((home / "sessions").iterdir())
    assert (home / "config.yaml").exists()


@bundled
def test_what_the_user_wrote_reaches_the_agent_and_what_the_agent_wrote_is_listed(tmp_path, model_server):
    service = chat(tmp_path, connection(model_server))
    try:
        service.dispatch("agent.memory_save", {"target": "user", "entries": ["답은 항상 존댓말로 한다"]})

        def reply(request):
            if any(message["role"] == "tool" for message in request["messages"]):
                return 200, completion({"content": "기억했습니다."}, request["model"]), False
            arguments = {"action": "add", "target": "memory", "content": "작업 폴더는 projects/site"}
            call = {
                "id": "call_1",
                "type": "function",
                "function": {"name": "memory", "arguments": json.dumps(arguments)},
            }
            return 200, completion({"content": None, "tool_calls": [call]}, request["model"]), False

        model_server.reply = reply
        sent = service.dispatch("agent.send", {"text": "작업 폴더를 기억해 줘", "request_id": "r-memory"})
        assert sent["reply"]["content"] == "기억했습니다."
        # The entry the user saved was in what the agent read at the start of the turn.
        assert "답은 항상 존댓말로 한다" in json.dumps(model_server.requests[0]["messages"], ensure_ascii=False)
        # What the agent wrote with its own tool is listed, and the turn left records behind.
        shown = service.dispatch("agent.memory", {})
        assert shown["memory"]["entries"] == ["작업 폴더는 projects/site"]
        assert shown["user"]["entries"] == ["답은 항상 존댓말로 한다"]
        assert shown["records"]["files"] > 0
        # Deleted, the records are gone and the next turn starts as usual.
        assert service.dispatch("agent.memory_forget", {})["records"]["files"] == 0
        model_server.reply = lambda request: (200, completion({"content": "네."}, request["model"]), False)
        assert service.dispatch("agent.send", {"text": "고마워", "request_id": "r-after"})["reply"]["content"] == "네."
    finally:
        service.agent.shutdown()
