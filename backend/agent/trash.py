"""The trash of what the user deletes beside documents: conversations, prompt runs, tasks, prompt templates, prompt
schedules and automatic routines, the chat cleared (대화 비우기) and the pages of the web records; and of the runs and
conversations the retention policy removes (Service.retain).

A deleted document waits where it is (documents.deleted_at), as much refers to it. These rows are read all over the
backend instead (the reminders, the runner of the schedules and routines, the briefing, the retention policy, the
chat's turns), so a deleted one leaves its table as it always did, and nothing that reads the table can meet it: a
schedule or a routine in the trash never runs, a task in it never reminds and a chat in it is no turn's conversation.
What left is kept here for service.TRASH_DAYS: the row itself (`payload`) and the rows that went with it (`related`: a
conversation's messages, a template's schedules, a routine's runs, a web record's items), as JSON by column name. The
chat goes whole, as one entry: its messages with what each turn did. A web record's screenshot stays in the Wiki
folder's web-captures until its entry is deleted for good.

A restore puts the rows back by the columns their tables have now: a column the row lacks takes its default and a value
whose column is gone is left out, so what an earlier version deleted still comes back. A schedule or routine that comes
back does not run for the times that passed while it was away: its next run is counted from now. Only trash.purge,
which the local API gives the `destructive` scope, and the retention policy (Service.retain, after TRASH_DAYS) delete an
entry for good.

The chat and the web records are the user's alone (AGENTS.md: agent.* and web.* are not in the local API): the main
process names the kinds an AI tool meets here (`only_kinds`), never theirs, so a tool neither lists nor restores nor
purges what they put here, nor learns its title. The same goes for the web records' routines (`without_recipes`)."""

import functools
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from . import paging
from .llm import UserError
from .service import TRASH_DAYS, now, text

# Entries one request names (trash.restore, trash.purge), and the newest the list answers when it asks for no page.
MAX_ENTRIES = 1000
# The most entries one page of the list holds (paging: `limit`).
PAGE_LIMIT = 500
# The order of the list: the last deleted first, and entries deleted in the same instant by their titles, so that a
# list reads the same twice and a page goes on after the last entry of the one before (a cursor of these keys).
ORDER = "deleted_at DESC, title, id"


class Kind(NamedTuple):
    table: str  # where a row of this kind lives
    label: str  # what the screens call one
    title: str  # the column that names it
    related: tuple = ()  # (table, the column of its owner's id, order) of the rows a delete takes with it


KINDS = {
    "conversation": Kind("conversations", "대화", "title", (("messages", "conversation_id", "seq"),)),
    "run": Kind("runs", "실행 기록", "title"),
    "task": Kind("tasks", "할 일", "title"),
    "prompt": Kind("prompts", "프롬프트 템플릿", "title", (("schedules", "prompt_id", "created_at, id"),)),
    "schedule": Kind("schedules", "예약", "name"),
    "routine": Kind("routines", "자동 작업", "name", (("routine_runs", "routine_id", "created_at, id"),)),
    # The whole chat at once (discard_chat): its messages and what each turn did.
    "chat": Kind("agent_messages", "채팅", "title"),
    # A page of the web records, by what was searched for (or its title), with its items.
    "web_capture": Kind("web_captures", "웹 기록", "query", (("web_items", "capture_id", "position"),)),
}


@functools.cache
def site_names():
    """The web records' sites by their keys, as the records name them (shared/web-capture/sites.json)."""
    from .web_capture import load_sites

    return {key: site["name"] for key, site in load_sites().items()}


def title_of(kind, row):
    """What the list calls an entry: its row's name; a web record searched for nothing goes by its page's title."""
    return str(row.get(KINDS[kind].title) or row.get("title") or row.get("url") or "")[:200]


def detail(db, kind, row, related):
    """A line that tells an entry from another of the same title, as the list shows it under the title."""
    if kind == "conversation":
        return f"메시지 {len(related['messages']):,}개"
    if kind == "web_capture":
        site = site_names().get(row.get("site")) or ""
        found = f"광고 {row.get('ad_count') or 0} · 상품 {row.get('product_count') or 0} · 검색 결과 "
        return " · ".join(part for part in (site, found + str(row.get("result_count") or 0)) if part)
    if kind == "run":
        made = "예약 실행" if row.get("kind") == "scheduled" else "직접 실행"
        return made + (" · 실패" if row.get("status") == "error" else "")
    if kind == "task":
        state = {"open": "진행 중", "paused": "보류", "done": "완료"}.get(row.get("status"), "")
        return state + (" · 알림 있음" if row.get("remind_at") else "")
    if kind == "prompt":
        count = len(related["schedules"])
        return str(row.get("category") or "") + (f" · 예약 {count:,}개 포함" if count else "")
    if kind == "schedule":
        prompt = db.execute("SELECT title FROM prompts WHERE id=?", (row.get("prompt_id"),)).fetchone()
        return f"‘{prompt['title']}’ 템플릿의 예약" if prompt else ""
    from .routines import RECIPES

    recipe, count = RECIPES.get(row.get("kind")), len(related["routine_runs"])
    return (recipe.title if recipe else str(row.get("kind") or "")) + (
        f" · 실행 기록 {count:,}개 포함" if count else ""
    )


def discard(db, kind, ident):
    """Move the `kind` named `ident` to the trash, within the caller's transaction (BEGIN IMMEDIATE, so that the row
    kept is the row deleted): the row and what the delete takes with it are kept, then deleted as before. What a delete
    method answers: `deleted` (1, or 0 when there was none) and the entry made (`trash_ids`), which trash.restore
    brings back."""
    made = discard_many(db, kind, [ident])
    return {"ok": True, "deleted": len(made), "trash_ids": made}


ENTRY = """INSERT INTO trash (id,kind,item_id,title,detail,recipe,payload,related,deleted_at)
    VALUES (?,?,?,?,?,?,?,?,?)"""


def discard_many(db, kind, ids, note=""):
    """Move the `kind`s named `ids` to the trash as discard does one, each to an entry of its own, a chunk of them a
    statement: what the retention policy moves, and the web records chosen together. `note` ends the line under the
    entry's title, saying why it went when nobody deleted it. The entries made, in the order of `ids`; an ID whose row
    is not there makes none."""
    if kind == "chat":
        raise ValueError("The chat goes whole: discard_chat.")
    spec = KINDS[kind]
    made = []
    for chunk in chunks(list(dict.fromkeys(ids))):
        marks = ",".join("?" * len(chunk))
        rows = {row["id"]: dict(row) for row in db.execute(f"SELECT * FROM {spec.table} WHERE id IN ({marks})", chunk)}
        related = {ident: {table: [] for table, _, _ in spec.related} for ident in rows}
        for table, owner, order in spec.related:
            for item in db.execute(f"SELECT * FROM {table} WHERE {owner} IN ({marks}) ORDER BY {order}", chunk):
                related[item[owner]][table].append(dict(item))
        stamp = now()
        for ident in chunk:
            row = rows.get(ident)
            if row is None:
                continue
            entry = str(uuid.uuid4())
            db.execute(
                ENTRY,
                (
                    entry,
                    kind,
                    ident,
                    title_of(kind, row),
                    " · ".join(part for part in (detail(db, kind, row, related[ident]), note) if part),
                    str(row.get("kind") or "") if kind == "routine" else "",
                    json.dumps(row, ensure_ascii=False),
                    json.dumps(related[ident], ensure_ascii=False),
                    stamp,
                ),
            )
            made.append(entry)
        if rows:
            db.execute(f"DELETE FROM {spec.table} WHERE id IN ({marks})", chunk)
    return made


def discard_chat(db):
    """Move the chat (agent_chat.py) to the trash as one entry, within the caller's transaction: every message with
    what each turn did (agent_activity), named by the first thing the user asked. What was allowed for this
    conversation is not kept here: it ends with 대화 비우기, and a restore brings back the messages alone. The entry
    made, or none for a chat without messages."""
    messages = [dict(row) for row in db.execute("SELECT * FROM agent_messages ORDER BY seq")]
    if not messages:
        return []
    activity = [dict(row) for row in db.execute("SELECT * FROM agent_activity ORDER BY seq")]
    asked = next((m["content"] for m in messages if m["role"] == "user" and str(m["content"] or "").strip()), "")
    title = " ".join(str(asked).split())[:80] or "채팅"
    ident, entry = str(uuid.uuid4()), str(uuid.uuid4())
    row = {"id": ident, "title": title, "first_at": messages[0]["created_at"], "last_at": messages[-1]["created_at"]}
    related = {"agent_messages": messages, "agent_activity": activity}
    db.execute(
        ENTRY,
        (
            entry,
            "chat",
            ident,
            title,
            f"메시지 {len(messages):,}개",
            "",
            json.dumps(row, ensure_ascii=False),
            json.dumps(related, ensure_ascii=False),
            now(),
        ),
    )
    db.execute("DELETE FROM agent_messages")
    db.execute("DELETE FROM agent_activity")
    return [entry]


def insert(db, table, row):
    """Put `row` (values by column name) back into `table` as the table is now: a column the row lacks takes its
    default, and a value whose column is gone is left out."""
    columns = [info[1] for info in db.execute(f"PRAGMA table_info({table})") if info[1] in row]
    db.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
        [row[column] for column in columns],
    )


def numbered_back(db, table, rows, key):
    """Put `rows` back into `table` keeping their numbers (`key`, the INTEGER PRIMARY KEY that orders them), unless one
    of them is in use (a Wiki whose numbering began anew): then they all take new ones, in the same order. Their old
    numbers to their new ones, and whether they changed."""
    rows = [row for row in rows if isinstance(row, dict)]
    numbers = [row[key] for row in rows if row.get(key) is not None]
    renumber = any(
        db.execute(f"SELECT 1 FROM {table} WHERE {key} IN ({','.join('?' * len(chunk))}) LIMIT 1", chunk).fetchone()
        for chunk in chunks(numbers)
    )
    moved = {}
    for row in rows:
        insert(db, table, {column: value for column, value in row.items() if not (renumber and column == key)})
        moved[row.get(key)] = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return moved, renumber


def entry_ids(value):
    """The distinct entries a request names, in the order given."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ENTRIES:
        raise UserError(f"휴지통의 항목을 1~{MAX_ENTRIES:,}개 선택해 주세요.")
    return list(dict.fromkeys(text(ident, "ID", 80) for ident in value))


def kinds(value):
    """The kinds of entry a request may meet at all (`only_kinds`), None for every kind: the main process names those an
    AI tool may meet (never the chat's or the web records'), never the tool itself."""
    if value is None:
        return None
    if not isinstance(value, list) or not value or not all(isinstance(name, str) and name in KINDS for name in value):
        raise UserError("휴지통에서 다룰 종류(only_kinds)를 확인해 주세요.")
    return tuple(dict.fromkeys(value))


def recipes(value):
    """The routine recipes whose entries a request must not meet (`without_recipes`): the main process names those an
    AI tool has no part in (the web records' routine), never the tool itself."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 20 or not all(isinstance(name, str) for name in value):
        raise UserError("휴지통에서 뺄 자동 작업 종류(without_recipes)를 확인해 주세요.")
    return tuple(value)


def chunks(ids, size=500):
    return [ids[start : start + size] for start in range(0, len(ids), size)]


def scope(p):
    """What a request may meet of the trash, as an SQL condition on its entries with the condition's arguments: the
    entries of the kinds `only_kinds` names (all without it) but the routines of the recipes `without_recipes` names.
    The main process names both for an AI tool."""
    only, without = kinds(p.get("only_kinds")), recipes(p.get("without_recipes"))
    clauses, args = [], []
    if only is not None:
        clauses.append(f"kind IN ({','.join('?' * len(only))})")
        args += only
    if without:
        clauses.append(f"NOT (kind='routine' AND recipe IN ({','.join('?' * len(without))}))")
        args += without
    return " AND ".join(clauses) or "1", args


class Trash:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self.methods = {
            "trash.list": self.list,
            "trash.restore": self.restore,
            "trash.purge": self.purge,
            "trash.routines": self.routines,
        }

    discard, discard_many, discard_chat = staticmethod(discard), staticmethod(discard_many), staticmethod(discard_chat)

    def expire(self, before):
        """Delete for good the entries deleted before `before` (the retention policy), with the screenshots of the web
        records among them; how many."""
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            shots = self.screenshots(db, "deleted_at<?", [before])
            count = db.execute("DELETE FROM trash WHERE deleted_at<?", (before,)).rowcount
        self.service.web.forget_screenshots(shots)
        return count

    @staticmethod
    def screenshots(db, test, args):
        """The screenshots of the web records among the entries `test` keeps, which go once those are deleted for good:
        their files, relative to the Wiki folder, but one a record outside the trash names too."""
        found = []
        for (payload,) in db.execute(f"SELECT payload FROM trash WHERE kind='web_capture' AND {test}", args):
            try:
                shot = json.loads(payload).get("screenshot")
            except (ValueError, AttributeError):
                continue
            if isinstance(shot, str) and shot:
                found.append(shot)
        if found:
            live = {row[0] for row in db.execute("SELECT screenshot FROM web_captures WHERE screenshot!=''")}
            found = [shot for shot in found if shot not in live]
        return found

    def list(self, p=None):
        """The entries of the trash in ORDER, all of them or those of one `kind`, and how many days each waits (`days`).
        Without `limit` and `cursor` the newest MAX_ENTRIES; with either one page of them and next_cursor (paging).
        `total` counts the entries the list holds in all and `kinds` those of each kind in the trash, whatever `kind`
        is, so that a screen shows how many there are while it has a page. A routine's entry names its recipe.
        `only_kinds` and `without_recipes` leave out what an AI tool may not meet, as the main process asks (scope)."""
        p = p or {}
        test, args = scope(p)
        kind = p.get("kind")
        if kind is not None and kind not in KINDS:
            raise UserError(f"휴지통의 종류(kind)는 {', '.join(KINDS)} 중 하나로 입력해 주세요.")
        paged = paging.wanted(p)
        limit = paging.size(p, "휴지통 목록은", PAGE_LIMIT) if paged else MAX_ENTRIES
        after = paging.decode(p, "trash", (str, str, str)) if paged else None
        where, found = (f"{test} AND kind=?", [*args, kind]) if kind else (test, [*args])
        if after:
            # After the cursor's entry: written so that the index is searched from it rather than read from the top.
            where += " AND deleted_at<=? AND (deleted_at<? OR title>? OR (title=? AND id>?))"
            found += [after[0], after[0], after[1], after[1], after[2]]
        with self.db.connect() as db:
            kinds = dict(db.execute(f"SELECT kind,count(*) FROM trash WHERE {test} GROUP BY kind", args).fetchall())
            rows = db.execute(
                f"""SELECT id,kind,item_id,title,detail,recipe,deleted_at FROM trash WHERE {where}
                ORDER BY {ORDER} LIMIT ?""",
                (*found, limit + 1),
            ).fetchall()
        items = []
        for row in rows[:limit]:
            item = {key: row[key] for key in ("id", "kind", "item_id", "title", "detail", "deleted_at")}
            if row["kind"] == "routine":
                item["recipe"] = row["recipe"]
            items.append(item)
        total = kinds.get(kind, 0) if kind else sum(kinds.values())
        answer = {"items": items, "total": total, "kinds": kinds, "days": TRASH_DAYS}
        if paged:
            last = items[-1] if len(rows) > limit else None
            answer["next_cursor"] = last and paging.encode("trash", last["deleted_at"], last["title"], last["id"])
        return answer

    def routines(self, p):
        """Internal, for the main process: the routines among the entries `ids`, each with its recipe, whether it reads
        this PC's files and the sites a run signs in to. By them it decides whether an AI tool may restore one, as it
        does for a routine that is saved or turned on (Routines.guard decides on the files again in restore)."""
        from .routines import RECIPES

        rows = []
        with self.db.connect() as db:
            for chunk in chunks(entry_ids(p.get("ids"))):
                rows += db.execute(
                    f"SELECT id,payload FROM trash WHERE kind='routine' AND id IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
        found = []
        for row in rows:
            # A routine whose settings cannot be read fails here, and the main process then refuses the restore.
            routine = json.loads(row["payload"])
            recipe = RECIPES.get(routine["kind"])
            found.append(
                {
                    "id": row["id"],
                    "recipe": routine["kind"],
                    "files": bool(recipe and recipe.reads_files(json.loads(routine["config"]))),
                    "needs": self.service.routines.needs_of(routine),
                }
            )
        return found

    def purge(self, p):
        """Delete the chosen entries for good, or all of them (`all`): nothing brings them back, nor the screenshots of
        the web records among them. What a caller may not meet (scope) stays where it is."""
        everything = p.get("all", False)
        if not isinstance(everything, bool):
            raise UserError("휴지통을 모두 비울지(all) 확인해 주세요.")
        ids = [] if everything else entry_ids(p.get("ids"))
        test, args = scope(p)
        shots, purged = [], 0
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for chunk in [None] if everything else chunks(ids):
                where = test if chunk is None else f"id IN ({','.join('?' * len(chunk))}) AND {test}"
                found = args if chunk is None else [*chunk, *args]
                shots += self.screenshots(db, where, found)
                purged += db.execute(f"DELETE FROM trash WHERE {where}", found).rowcount
        self.service.web.forget_screenshots(shots)
        return {"purged": purged}

    def restore(self, p):
        """Bring the chosen entries back, all or none of them: each row with the rows that went with it. An entry no
        longer in the trash is passed over (`restored` counts those that came back, a template that came back for its
        schedule among them; `items` names them). `notes` tells what did not come back as it was: a schedule or routine
        restored turned off, and a chat put back beside the one there is now. Refused, with nothing restored: an entry
        whose ID is in use again, a schedule whose template was deleted for good, a routine beyond the most there may
        be, and a routine that reads this PC's files unless Electron says the caller may have their text
        (`allow_files`, as Routines.guard asks for). What a caller may not meet (scope) is passed over."""
        ids = entry_ids(p.get("ids"))
        allow_files = p.get("allow_files") is True
        test, args = scope(p)
        held = self.held(ids)
        items, notes = [], []
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for ident in ids:
                # Gone since the list was read, come back already as the template of a schedule before it, or none of
                # the caller's to meet.
                entry = db.execute(f"SELECT * FROM trash WHERE id=? AND {test}", (ident, *args)).fetchone()
                if entry is None:
                    continue
                self.put_back(db, entry, allow_files, held, items, notes)
        return {"restored": len(items), "items": items, "notes": notes}

    def held(self, ids):
        """The schedules and routines of the entries `ids` whose run still waits or runs, as lanes of the runner
        (Routines.lanes): the delete stopped it and it has not ended yet, so it still writes how it went. Asked before
        the restore's transaction, as the runner's lock is never taken within one (Routines.save says why)."""
        lanes = set()
        with self.db.connect() as db:
            for chunk in chunks(ids):
                for row in db.execute(
                    f"""SELECT kind,item_id,CASE WHEN kind='prompt' THEN related END AS related FROM trash
                    WHERE id IN ({",".join("?" * len(chunk))}) AND kind IN ('prompt','schedule','routine')""",
                    chunk,
                ):
                    if row["kind"] == "prompt":
                        try:
                            schedules = json.loads(row["related"]).get("schedules") or []
                        except (ValueError, AttributeError):
                            schedules = []
                        lanes.update(f"schedule:{schedule.get('id')}" for schedule in schedules)
                    else:
                        lanes.add(f"{row['kind']}:{row['item_id']}")
        return {lane for lane in lanes if self.service.routines.holding(lane)}

    def put_back(self, db, entry, allow_files, held, items, notes):
        """Restore one entry within the restore's transaction and take it out of the trash."""
        spec, title = KINDS.get(entry["kind"]), entry["title"]
        if spec is None:
            raise UserError(f"‘{title}’: 이 버전에서 복원할 수 없는 종류의 항목입니다.")
        try:
            row, related = json.loads(entry["payload"]), json.loads(entry["related"])
            if not isinstance(row, dict) or not isinstance(related, dict) or "id" not in row:
                raise ValueError()
        except ValueError:
            raise UserError(f"‘{title}’ {spec.label}: 휴지통에 남은 내용을 읽을 수 없어 복원하지 못했습니다.") from None
        taken = f"‘{title}’ {spec.label}: 같은 ID의 항목이 이미 있어 복원하지 못했습니다."
        # The chat's entry stands for no row of its own (discard_chat).
        if entry["kind"] != "chat" and db.execute(f"SELECT 1 FROM {spec.table} WHERE id=?", (row["id"],)).fetchone():
            raise UserError(taken)
        try:
            if entry["kind"] == "conversation":
                self.conversation_back(db, row, related)
            elif entry["kind"] == "chat":
                self.chat_back(db, related, notes)
            elif entry["kind"] == "web_capture":
                insert(db, "web_captures", row)
                numbered_back(db, "web_items", related.get("web_items") or [], "id")
            elif entry["kind"] == "task":
                # Its document was deleted for good meanwhile: the task comes back without a source.
                if (
                    row.get("source_id")
                    and not db.execute("SELECT 1 FROM documents WHERE id=?", (row["source_id"],)).fetchone()
                ):
                    row["source_id"] = None
                insert(db, "tasks", row)
            elif entry["kind"] == "prompt":
                insert(db, "prompts", row)
                for schedule in related.get("schedules") or []:
                    self.schedule_back(db, schedule, held, notes)
            elif entry["kind"] == "schedule":
                if not db.execute("SELECT 1 FROM prompts WHERE id=?", (row.get("prompt_id"),)).fetchone():
                    # Its template was deleted after it: the template comes back with it, with the schedules it had.
                    owner = db.execute(
                        "SELECT * FROM trash WHERE kind='prompt' AND item_id=? ORDER BY deleted_at DESC LIMIT 1",
                        (row.get("prompt_id"),),
                    ).fetchone()
                    if owner is None:
                        raise UserError(
                            f"‘{title}’ 예약: 프롬프트 템플릿이 영구 삭제되어 복원할 수 없습니다. 템플릿을 새로 만들고 "
                            "예약을 다시 만들어 주세요."
                        )
                    self.put_back(db, owner, allow_files, held, items, notes)
                self.schedule_back(db, row, held, notes)
            elif entry["kind"] == "routine":
                self.routine_back(db, row, related, entry["deleted_at"], allow_files, held, notes)
            else:
                insert(db, spec.table, row)
        except sqlite3.IntegrityError:
            # A row of its own, or one that came with it, is in the Wiki again under the same ID.
            raise UserError(taken) from None
        except (KeyError, TypeError, ValueError):
            # A row this version cannot read: a schedule's rule it does not know, a value of another type.
            raise UserError(f"‘{title}’ {spec.label}: 이 버전에서 읽을 수 없는 내용이라 복원하지 못했습니다.") from None
        db.execute("DELETE FROM trash WHERE id=?", (entry["id"],))
        items.append({"id": entry["id"], "kind": entry["kind"], "item_id": row["id"], "title": title})

    @staticmethod
    def conversation_back(db, row, related):
        insert(db, "conversations", row)
        numbered_back(db, "messages", related.get("messages") or [], "seq")

    @staticmethod
    def chat_back(db, related, notes):
        """Put the chat's messages back with what each turn did. A chat there is now stays: the messages come back
        before it, by their numbers, or after it when those are in use. Nothing comes back of what was allowed for that
        conversation: 대화 비우기 ended it (AgentChat.clear)."""
        there = db.execute("SELECT 1 FROM agent_messages LIMIT 1").fetchone()
        moved, renumbered = numbered_back(db, "agent_messages", related.get("agent_messages") or [], "seq")
        for step in related.get("agent_activity") or []:
            if isinstance(step, dict) and step.get("seq") in moved:
                insert(db, "agent_activity", {**step, "seq": moved[step["seq"]]})
        if there:
            notes.append(f"채팅에 지금 대화가 있어 복원한 메시지를 그 {'뒤' if renumbered else '앞'}에 두었습니다.")

    def schedule_back(self, db, row, held, notes):
        """Put a prompt schedule back: turned on as it was unless its one time has passed or as many are on as may be,
        with its next run counted from now, so that it does not run for a time that passed in the trash."""
        from .schedules import MAX_ACTIVE, next_run

        row, name = dict(row), str(row.get("name") or "")
        enabled = bool(row.get("enabled"))
        once = row.get("frequency") == "once"
        if enabled and once and not (row.get("run_at") and row["run_at"] > now()):
            enabled = False
            notes.append(f"‘{name}’ 예약은 실행 시각이 지나 꺼 둔 채로 복원했습니다. 새 시각을 정해 켜 주세요.")
        if enabled and db.execute("SELECT count(*) FROM schedules WHERE enabled=1").fetchone()[0] >= MAX_ACTIVE:
            enabled = False
            notes.append(f"켜 둔 예약이 {MAX_ACTIVE}개여서 ‘{name}’ 예약은 꺼 둔 채로 복원했습니다.")
        row["enabled"] = int(enabled)
        row["next_run_at"] = (row["run_at"] if once else next_run(row)) if enabled else None
        if row.get("last_status") == "running" and f"schedule:{row.get('id')}" not in held:
            # Its run ended while it was away, or never began: the run history has how its last run went.
            last = db.execute(
                "SELECT created_at,status,error FROM runs WHERE schedule_id=? ORDER BY created_at DESC LIMIT 1",
                (row.get("id"),),
            ).fetchone()
            row["last_run_at"], row["last_status"], row["last_error"] = (
                (last["created_at"], last["status"], last["error"]) if last else (row.get("last_run_at"), None, "")
            )
        insert(db, "schedules", row)

    def routine_back(self, db, row, related, deleted_at, allow_files, held, notes):
        """Put an automatic routine back with its runs: turned on as it was unless as many are on as may be, its next
        run counted from now. The run its delete stopped is shown as stopped, unless it has not ended yet."""
        from . import routines
        from .schedules import next_run

        name = str(row.get("name") or "")
        try:
            config = json.loads(row.get("config") or "{}")
        except ValueError:
            config = {}
        routines.Routines.guard({"allow_files": allow_files}, routines.RECIPES.get(row.get("kind")), config)
        if db.execute("SELECT count(*) FROM routines").fetchone()[0] >= routines.MAX_ROUTINES:
            raise UserError(
                f"자동 작업은 최대 {routines.MAX_ROUTINES}개까지 둘 수 있어 ‘{name}’을(를) 복원하지 못했습니다. 쓰지 "
                "않는 자동 작업을 지운 뒤 복원해 주세요."
            )
        enabled = bool(row.get("enabled"))
        if enabled and (
            db.execute("SELECT count(*) FROM routines WHERE enabled=1").fetchone()[0] >= routines.MAX_ACTIVE
        ):
            enabled = False
            notes.append(f"켜 둔 자동 작업이 {routines.MAX_ACTIVE}개여서 ‘{name}’은(는) 꺼 둔 채로 복원했습니다.")
        row["enabled"] = int(enabled)
        if not enabled:
            row["next_run_at"] = None
        elif row.get("frequency") != "interval":
            row["next_run_at"] = next_run(row)
        elif not (row.get("next_run_at") and row["next_run_at"] > now()):
            # Its time passed while it was away: one interval from now, rather than at once.
            interval = row.get("interval") if type(row.get("interval")) is int else 60
            row["next_run_at"] = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
        runs = [run for run in related.get("routine_runs") or [] if isinstance(run, dict)]
        if f"routine:{row.get('id')}" not in held:
            if row.get("last_status") in ("queued", "running"):
                row["last_status"] = "cancelled"
            for run in runs:
                if run.get("status") in ("queued", "running"):
                    run["status"], run["finished_at"] = "cancelled", run.get("finished_at") or deleted_at
        insert(db, "routines", row)
        for run in runs:
            insert(db, "routine_runs", run)
