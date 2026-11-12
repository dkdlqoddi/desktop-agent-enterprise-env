"""What the chat's agent remembers, for the user to read and change (관리 → 채팅 에이전트).

Hermes keeps two short files in its home (the Wiki folder's hermes/memories): MEMORY.md, its own notes, and USER.md,
what it learned about the user. Each holds entries separated by a line with § alone, is read into every turn, and has
a limit in characters that Hermes itself keeps to. Beside them it keeps a record of every turn it ran (state.db and
sessions/), which its search of earlier conversations reads.

The agent writes all of that by itself as it works, so the user must be able to see it, correct it and delete it: the
entries are listed, saved as the list the user left (agent.memory, agent.memory_save), and the records of the turns
are deleted whole (agent.memory_forget). Nothing is changed while a turn runs, and the records are left alone while
the messenger gateway, which keeps them open, is on.
"""

import contextlib
import os
import shutil
import threading

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

    def read(self, _params=None):
        """{"memory": {"entries", "chars", "limit"}, "user": {...}, "records": {"files", "bytes"}}: the agent's own
        notes, what it keeps about the user, and how much it has recorded of its turns."""
        shown = {}
        for target, limit in LIMITS.items():
            entries = self.entries(target)
            shown[target] = {"entries": entries, "chars": len(DELIMITER.join(entries)), "limit": limit}
        return {**shown, "records": self.records()}

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

    def forget(self, _params=None):
        """Delete Hermes' records of the turns it ran (its session database and transcripts). The chat's own
        conversation is not among them (대화 비우기), nor are the two memory files."""
        with self.lock:
            self.idle()
            if self.chat.gateway.status()["state"] != "off":
                raise UserError(
                    "메신저 게이트웨이가 켜져 있는 동안에는 지울 수 없습니다. 게이트웨이를 끈 뒤 다시 해 주세요."
                )
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
        return self.read()
