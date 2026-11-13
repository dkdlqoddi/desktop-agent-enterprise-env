"""What the chat's agent remembers, for the user to read and change (관리 → 채팅 에이전트).

Hermes keeps two short files in its home (the Wiki folder's hermes/memories): MEMORY.md, its own notes, and USER.md,
what it learned about the user. Each holds entries separated by a line with § alone, is read into every turn, and has
a limit in characters that Hermes itself keeps to. Beside them it keeps a record of every turn it ran (state.db and
sessions/), which its search of earlier conversations reads.

The agent writes all of that by itself as it works, so the user must be able to see it, correct it and delete it: the
entries are listed, saved as the list the user left (agent.memory, agent.memory_save; the screen offers to put back an
entry it deleted), and the records are listed by turn and by messenger conversation and deleted one at a time or all
together (agent.memory_forget). The messenger conversations among the records are shown in the chat to read
(agent.messenger, agent.messenger_messages): what the accounts the user listed wrote and what the agent answered,
without its tools' calls and results. Nothing is changed while a turn of the chat runs. The messenger gateway may run
meanwhile: a record is then deleted as Hermes deletes one, by a process of the bundled worker's (forget_sessions in
runtime/hermes/worker.py), which keeps a conversation the gateway answers in right now and stops the gateway from
writing a deleted one back.
"""

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone

from . import diagnostics
from .hermes import child_environment
from .llm import BusyError, UserError

DELIMITER = "\n§\n"
FILES = {"memory": "MEMORY.md", "user": "USER.md"}
# Hermes' own limits, in characters of the file (memory_char_limit, user_char_limit).
LIMITS = {"memory": 2200, "user": 1375}
MAX_ENTRIES = 200
# Its records of the turns it ran: the session database with SQLite's side files, and the folder of transcripts.
RECORDS = ("state.db", "state.db-wal", "state.db-shm", "state.db-journal")
SESSIONS = "sessions"
BUSY = "에이전트가 작업 중입니다. 끝난 뒤 다시 해 주세요."
# The records listed, newest first; and how one is named: the source Hermes keeps it under (the chat's own turns are
# private_agent), and the first words asked in it, cut to TITLE_CHARS.
SESSION_LIST = 50
SOURCES = {"private_agent": "채팅", "telegram": "텔레그램", "discord": "디스코드", "slack": "슬랙"}
TITLE_CHARS = 120
# What a chat turn is asked begins with the conversation before it (agent_chat.py, context); its own words follow this.
ASKED = "New message from the user:\n"
# A message Hermes keeps as JSON instead of text begins with this.
STRUCTURED = "\x00json:"
SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
# The messengers whose conversations the chat shows; how many conversations, and how many of one's last messages,
# each cut to MESSAGE_CHARS; and the longest key a conversation is asked for by.
MESSENGERS = ("telegram", "discord", "slack")
CONVERSATIONS = 30
CONVERSATION_MESSAGES = 200
MESSAGE_CHARS = 4000
KEY_CHARS = 300
# How long the worker that deletes records may take.
FORGET_SECONDS = 120
NOT_FORGOTTEN = "작업 기록을 지우지 못했습니다. 잠시 뒤 다시 해 주세요."


def words(content):
    """A message's words as Hermes keeps them: its text, or the text parts of one kept as JSON."""
    if not isinstance(content, str):
        return ""
    if content.startswith(STRUCTURED):
        try:
            parts = json.loads(content[len(STRUCTURED) :])
        except ValueError:
            return ""
        parts = parts if isinstance(parts, list) else [parts]
        return " ".join(
            str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text"
        )
    return content


def moment(value):
    """A time Hermes keeps (seconds since 1970) as the Wiki writes one (ISO 8601, UTC); '' for none."""
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def parse(raw):
    """The entries of a memory file, each once, as Hermes reads it."""
    return list(dict.fromkeys(entry.strip() for entry in raw.split(DELIMITER) if entry.strip()))


class Memory:
    def __init__(self, chat):
        self.chat = chat
        self.lock = threading.Lock()
        self.methods = {
            "agent.memory": self.read,
            "agent.memory_save": self.save,
            "agent.memory_forget": self.forget,
            "agent.messenger": self.messenger,
            "agent.messenger_messages": self.messenger_messages,
        }

    def folder(self):
        return self.chat.hermes.home / "memories"

    def entries(self, target):
        try:
            return parse((self.folder() / FILES[target]).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return []

    def records(self):
        """How much Hermes keeps of the turns it ran: {"files", "bytes"}."""
        home = self.chat.hermes.home
        paths = [home / name for name in RECORDS]
        with contextlib.suppress(OSError):
            paths += [path for path in (home / SESSIONS).rglob("*") if path.is_file()]
        files = size = 0
        for path in paths:
            with contextlib.suppress(OSError):
                size += path.stat().st_size
                files += 1
        return {"files": files, "bytes": size}

    @contextlib.contextmanager
    def database(self):
        """Hermes' session database opened to read only, or None while there is none (or it does not open)."""
        path = self.chat.hermes.home / "state.db"
        connection = None
        if path.is_file():
            with contextlib.suppress(sqlite3.Error):
                connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            yield connection
        finally:
            if connection is not None:
                connection.close()

    def messenger(self, _params=None):
        """The conversations of the messenger gateway, the latest first (CONVERSATIONS of them): [{"key", "source",
        "label", "account", "title", "at", "messages"}]. One per chat, over the records Hermes starts anew in it (a
        /new, a compression); `account` is the name or the ID of who wrote, `title` the first words they asked."""
        marks = ",".join("?" * len(MESSENGERS))
        try:
            with self.database() as database:
                rows = (
                    database.execute(
                        f"""SELECT s.session_key, s.source, MAX(COALESCE(s.last_activity_at, s.started_at)) AS at,
                        SUM(s.message_count), s.display_name, s.user_id
                        FROM sessions s WHERE s.source IN ({marks}) AND s.session_key IS NOT NULL
                        AND s.parent_session_id IS NULL
                        GROUP BY s.session_key ORDER BY at DESC LIMIT ?""",
                        (*MESSENGERS, CONVERSATIONS),
                    ).fetchall()
                    if database
                    else []
                )
                listed = []
                for key, source, at, count, name, user in rows:
                    first = database.execute(
                        """SELECT m.content FROM messages m JOIN sessions s ON s.id = m.session_id
                        WHERE s.session_key = ? AND m.role = 'user' AND COALESCE(m.observed, 0) = 0
                        ORDER BY m.timestamp, m.id LIMIT 1""",
                        (key,),
                    ).fetchone()
                    title = " ".join(diagnostics.redact(words(first[0] if first else "")).split())
                    listed.append(
                        {
                            "key": str(key),
                            "source": str(source),
                            "label": SOURCES.get(source, str(source)),
                            "account": str(name or user or ""),
                            "title": title[: TITLE_CHARS - 1] + "…" if len(title) > TITLE_CHARS else title,
                            "at": moment(at),
                            "messages": int(count or 0),
                        }
                    )
        except sqlite3.Error:
            return []
        return listed

    def messenger_messages(self, params):
        """One conversation of the messenger gateway (`key`, as messenger lists it): its last CONVERSATION_MESSAGES
        messages, oldest first, [{"role": user | assistant, "text", "at"}]. What the listed account wrote and what the
        agent answered, without the agent's tool calls, their results or its reasoning, nor what others wrote in a
        group chat that was not addressed to the bot; what reads as a secret is hidden."""
        key = params.get("key") if isinstance(params, dict) else None
        if not isinstance(key, str) or not 0 < len(key) <= KEY_CHARS:
            raise UserError("대화를 찾을 수 없습니다. 화면을 새로고침해 주세요.")
        marks = ",".join("?" * len(MESSENGERS))
        try:
            with self.database() as database:
                rows = (
                    database.execute(
                        f"""SELECT m.role, m.content, m.timestamp FROM messages m JOIN sessions s ON s.id = m.session_id
                        WHERE s.session_key = ? AND s.source IN ({marks}) AND m.role IN ('user', 'assistant')
                        AND m.active = 1 AND m._compressed_summary = 0 AND COALESCE(m.observed, 0) = 0
                        AND COALESCE(m.display_kind, '') <> 'hidden' AND COALESCE(m.content, '') <> ''
                        ORDER BY m.timestamp DESC, m.id DESC LIMIT ?""",
                        (key, *MESSENGERS, CONVERSATION_MESSAGES),
                    ).fetchall()
                    if database
                    else []
                )
        except sqlite3.Error:
            rows = []
        shown = []
        for role, content, at in reversed(rows):
            text = diagnostics.redact(words(content)).strip()
            if text:
                shown.append({"role": role, "text": text[:MESSAGE_CHARS], "at": moment(at)})
        return shown

    def sessions(self):
        """The turns and messenger conversations Hermes keeps a record of, the latest first (SESSION_LIST of them):
        [{"id", "source", "label", "title", "at", "messages"}]. Read from its session database without writing to it.
        The record of a subagent, or of a conversation continued after Hermes compressed it, goes with the one it
        belongs to and is not listed of its own. The title is the first words asked, with what reads as a secret
        hidden, as in the activity of a turn."""
        path = self.chat.hermes.home / "state.db"
        if not path.is_file():
            return []
        try:
            with contextlib.closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as database:
                rows = database.execute(
                    """SELECT s.id, s.source, s.title, s.started_at, s.last_activity_at, s.message_count,
                    (SELECT m.content FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.id LIMIT 1)
                    FROM sessions s WHERE s.parent_session_id IS NULL
                    AND (s.model_config IS NULL OR NOT json_valid(s.model_config)
                         OR json_extract(s.model_config, '$._delegate_from') IS NULL)
                    ORDER BY COALESCE(s.last_activity_at, s.started_at) DESC LIMIT ?""",
                    (SESSION_LIST,),
                ).fetchall()
        except sqlite3.Error:
            return []
        listed = []
        for ident, source, title, started, active, count, asked in rows:
            said = words(asked)
            if source == "private_agent" and ASKED in said:
                said = said.rsplit(ASKED, 1)[1]
            title = " ".join(diagnostics.redact(str(title or said or "")).split())
            listed.append(
                {
                    "id": str(ident),
                    "source": str(source or ""),
                    "label": SOURCES.get(source, str(source or "")),
                    "title": title[: TITLE_CHARS - 1] + "…" if len(title) > TITLE_CHARS else title,
                    "at": moment(active or started),
                    "messages": int(count or 0),
                }
            )
        return listed

    def read(self, _params=None):
        """{"memory": {"entries", "chars", "limit"}, "user": {...}, "records": {"files", "bytes"}, "sessions": [...]}:
        the agent's own notes, what it keeps about the user, how much it has recorded of its turns and conversations,
        and which they are (sessions)."""
        shown = {}
        for target, limit in LIMITS.items():
            entries = self.entries(target)
            shown[target] = {"entries": entries, "chars": len(DELIMITER.join(entries)), "limit": limit}
        return {**shown, "records": self.records(), "sessions": self.sessions()}

    def idle(self):
        """Raises unless no turn runs: the agent reads these files as a turn starts and writes them while it works."""
        with self.chat.lock:
            if self.chat.sending:
                raise BusyError(BUSY)

    def save(self, params):
        """Replace one of the two lists with `entries` ({"target": memory | user, "entries": [...]}): the list as the
        user left it, an entry edited, removed or added. It must fit Hermes' limit, unless it only got shorter (a
        file that grew past the limit can always be trimmed)."""
        target = params.get("target")
        given = params.get("entries")
        if target not in FILES or not isinstance(given, list) or len(given) > MAX_ENTRIES:
            raise UserError("기억 목록을 확인해 주세요.")
        entries = []
        for entry in given:
            if not isinstance(entry, str) or "\x00" in entry:
                raise UserError("기억 목록을 확인해 주세요.")
            entry = entry.strip()
            # A line of § alone would cut the entry in two the next time the file is read.
            if any(line.strip() == "§" for line in entry.splitlines()):
                raise UserError("기억 한 항목 안에 § 한 글자만 있는 줄은 쓸 수 없습니다.")
            if entry and entry not in entries:
                entries.append(entry)
        with self.lock:
            self.idle()
            size, before = len(DELIMITER.join(entries)), len(DELIMITER.join(self.entries(target)))
            if size > LIMITS[target] and size >= before:
                raise UserError(
                    f"기억은 {LIMITS[target]:,}자까지 둘 수 있습니다(지금 {size:,}자). 항목을 줄이거나 지워 주세요."
                )
            folder = self.folder()
            try:
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = folder / FILES[target]
                draft = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                draft.write_text(DELIMITER.join(entries), encoding="utf-8")
                os.replace(draft, path)
            except OSError:
                raise UserError("기억을 저장하지 못했습니다. Wiki 폴더의 권한을 확인해 주세요.") from None
        return self.read()

    def forget(self, params=None):
        """Delete Hermes' records: one turn or messenger conversation (`session_id`, as sessions lists it), or all of
        them. Returns what read() does, with "kept": the conversations left because the messenger gateway answers in
        them right now. All of them go whole, the session database and the transcripts, while the gateway is off; while
        it runs, and for one, each goes as Hermes deletes one (forget_sessions). The chat's own conversation is not
        among them (대화 비우기), nor are the two memory files."""
        params = params if isinstance(params, dict) else {}
        session = params.get("session_id")
        if session is not None and not (isinstance(session, str) and SESSION_ID.fullmatch(session)):
            raise UserError("지울 작업 기록을 찾을 수 없습니다. 화면을 새로고침해 주세요.")
        with self.lock:
            self.idle()
            if session is not None or self.chat.gateway.status()["state"] != "off":
                held = self.forget_sessions([session] if session is not None else None)
                if session is not None and held:
                    raise UserError("메신저에서 지금 답하고 있는 대화라 지우지 못했습니다. 잠시 뒤 다시 해 주세요.")
                return {**self.read(), "kept": held}
            # The chat's worker is kept between turns: it goes first, so nothing holds the files.
            self.chat.hermes.recycle(self.chat.hermes.extra_env)
            home = self.chat.hermes.home
            failed = False
            for name in RECORDS:
                try:
                    (home / name).unlink(missing_ok=True)
                except OSError:
                    failed = True
            sessions = home / SESSIONS
            if sessions.is_dir() and not sessions.is_symlink():
                for entry in sessions.iterdir():
                    try:
                        if entry.is_dir() and not entry.is_symlink():
                            shutil.rmtree(entry)
                        else:
                            entry.unlink()
                    except OSError:
                        failed = True
            if failed:
                raise UserError("일부 기록을 지우지 못했습니다. 잠시 뒤 다시 해 주세요.")
        return {**self.read(), "kept": 0}

    def forget_sessions(self, sessions):
        """Delete `sessions` (ids), or every record when None, as Hermes deletes one: by a process of the bundled
        worker's, which takes its messages, search index and files with it and keeps the gateway from writing it back.
        Returns how many the messenger gateway kept because it answers in them right now."""
        hermes = self.chat.hermes
        executable, worker, _root = hermes.launch_paths()
        home = hermes.prepared_home()
        frame = {"type": "forget", "sessions": sessions or [], "everything": sessions is None}
        try:
            done = subprocess.run(
                [str(executable), "-I", "-B", str(worker)],
                input=json.dumps(frame) + "\n",
                cwd=home,
                env=child_environment(home, {}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=FORGET_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            raise UserError(NOT_FORGOTTEN) from None
        for line in reversed(done.stdout.splitlines()):
            try:
                told = json.loads(line)
            except ValueError:
                continue
            if isinstance(told, dict) and told.get("type") == "forgotten":
                return int(told.get("held") or 0)
        raise UserError(NOT_FORGOTTEN)
