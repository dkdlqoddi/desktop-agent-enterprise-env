"""The trash of what the user deletes beside documents: conversations, prompt runs, tasks, prompt templates, prompt
schedules and automatic routines.

A deleted document waits where it is (documents.deleted_at), as much refers to it. These rows are read all over the
backend instead (the reminders, the runner of the schedules and routines, the briefing, the retention policy), so a
deleted one leaves its table as it always did, and nothing that reads the table can meet it: a schedule or a routine in
the trash never runs, and a task in it never reminds. What left is kept here for service.TRASH_DAYS: the row itself
(`payload`) and the rows that went with it (`related`: a conversation's messages, a template's schedules, a routine's
runs), as JSON by column name.

A restore puts the rows back by the columns their tables have now: a column the row lacks takes its default and a value
whose column is gone is left out, so what an earlier version deleted still comes back. A schedule or routine that comes
back does not run for the times that passed while it was away: its next run is counted from now. Only trash.purge,
which the local API gives the `destructive` scope, and the retention policy (Service.retain, after TRASH_DAYS) delete an
entry for good."""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from .llm import UserError
from .service import TRASH_DAYS, now, text

# Entries one request names, and one list answers (the newest).
MAX_ENTRIES = 1000


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
}


def detail(db, kind, row, related):
    """A line that tells an entry from another of the same title, as the list shows it under the title."""
    if kind == "conversation":
        return f"메시지 {len(related['messages']):,}개"
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
    spec = KINDS[kind]
    row = db.execute(f"SELECT * FROM {spec.table} WHERE id=?", (ident,)).fetchone()
    if row is None:
        return {"ok": True, "deleted": 0, "trash_ids": []}
    row = dict(row)
    related = {
        table: [dict(item) for item in db.execute(f"SELECT * FROM {table} WHERE {owner}=? ORDER BY {order}", (ident,))]
        for table, owner, order in spec.related
    }
    entry = str(uuid.uuid4())
    db.execute(
        """INSERT INTO trash (id,kind,item_id,title,detail,recipe,payload,related,deleted_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            entry,
            kind,
            ident,
            str(row.get(spec.title) or "")[:200],
            detail(db, kind, row, related),
            str(row.get("kind") or "") if kind == "routine" else "",
            json.dumps(row, ensure_ascii=False),
            json.dumps(related, ensure_ascii=False),
            now(),
        ),
    )
    db.execute(f"DELETE FROM {spec.table} WHERE id=?", (ident,))
    return {"ok": True, "deleted": 1, "trash_ids": [entry]}


def expire(db, before):
    """Delete for good the entries deleted before `before` (the retention policy); how many."""
    return db.execute("DELETE FROM trash WHERE deleted_at<?", (before,)).rowcount


def insert(db, table, row):
    """Put `row` (values by column name) back into `table` as the table is now: a column the row lacks takes its
    default, and a value whose column is gone is left out."""
    columns = [info[1] for info in db.execute(f"PRAGMA table_info({table})") if info[1] in row]
    db.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
        [row[column] for column in columns],
    )


def entry_ids(value):
    """The distinct entries a request names, in the order given."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ENTRIES:
        raise UserError(f"휴지통의 항목을 1~{MAX_ENTRIES:,}개 선택해 주세요.")
    return list(dict.fromkeys(text(ident, "ID", 80) for ident in value))


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


class Trash:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self.methods = {
            "trash.list": self.list,
            "trash.restore": self.restore,
            "trash.purge": self.purge,
            "trash.routines": self.routines,
        }

    discard, expire = staticmethod(discard), staticmethod(expire)

    def list(self, p=None):
        """The entries of the trash, the last deleted first (at most MAX_ENTRIES; `total` counts them all), and how many
        days each waits (`days`). A routine's entry names its recipe. `without_recipes` leaves the routines of those
        recipes out, as the main process asks for an AI tool."""
        without = recipes((p or {}).get("without_recipes"))
        skip = f"NOT (kind='routine' AND recipe IN ({','.join('?' * len(without))}))" if without else "1"
        with self.db.connect() as db:
            total = db.execute(f"SELECT count(*) FROM trash WHERE {skip}", without).fetchone()[0]
            # Entries deleted in the same instant in the order of their titles, so a list reads the same twice.
            rows = db.execute(
                f"""SELECT id,kind,item_id,title,detail,recipe,deleted_at FROM trash WHERE {skip}
                ORDER BY deleted_at DESC, title, id LIMIT ?""",
                (*without, MAX_ENTRIES),
            ).fetchall()
        items = []
        for row in rows:
            item = {key: row[key] for key in ("id", "kind", "item_id", "title", "detail", "deleted_at")}
            if row["kind"] == "routine":
                item["recipe"] = row["recipe"]
            items.append(item)
        return {"items": items, "total": total, "days": TRASH_DAYS}

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
        """Delete the chosen entries for good, or all of them (`all`): nothing brings them back. `without_recipes`
        leaves the routines of those recipes where they are."""
        everything = p.get("all", False)
        if not isinstance(everything, bool):
            raise UserError("휴지통을 모두 비울지(all) 확인해 주세요.")
        ids = [] if everything else entry_ids(p.get("ids"))
        without = recipes(p.get("without_recipes"))
        skip = f"NOT (kind='routine' AND recipe IN ({','.join('?' * len(without))}))" if without else "1"
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if everything:
                purged = db.execute(f"DELETE FROM trash WHERE {skip}", without).rowcount
            else:
                purged = sum(
                    db.execute(
                        f"DELETE FROM trash WHERE id IN ({','.join('?' * len(chunk))}) AND {skip}", [*chunk, *without]
                    ).rowcount
                    for chunk in chunks(ids)
                )
        return {"purged": purged}

    def restore(self, p):
        """Bring the chosen entries back, all or none of them: each row with the rows that went with it. An entry no
        longer in the trash is passed over (`restored` counts those that came back, a template that came back for its
        schedule among them; `items` names them). `notes` tells what did not come back as it was: a schedule or routine
        restored turned off. Refused, with nothing restored: an entry whose ID is in use again, a schedule whose
        template was deleted for good, a routine beyond the most there may be, and a routine that reads this PC's files
        unless Electron says the caller may have their text (`allow_files`, as Routines.guard asks for)."""
        ids = entry_ids(p.get("ids"))
        allow_files = p.get("allow_files") is True
        without = recipes(p.get("without_recipes"))
        held = self.held(ids)
        items, notes = [], []
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for ident in ids:
                entry = db.execute("SELECT * FROM trash WHERE id=?", (ident,)).fetchone()
                # Gone since the list was read, or come back already as the template of a schedule before it.
                if entry is None or (entry["kind"] == "routine" and entry["recipe"] in without):
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
        if db.execute(f"SELECT 1 FROM {spec.table} WHERE id=?", (row["id"],)).fetchone():
            raise UserError(taken)
        try:
            if entry["kind"] == "conversation":
                self.conversation_back(db, row, related)
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
        messages = [message for message in related.get("messages") or [] if isinstance(message, dict)]
        numbers = [message["seq"] for message in messages if message.get("seq") is not None]
        # The messages keep their numbers, which order them, unless one is in use (a Wiki whose numbering began anew):
        # then they take new ones in the same order.
        renumber = any(
            db.execute(f"SELECT 1 FROM messages WHERE seq IN ({','.join('?' * len(chunk))}) LIMIT 1", chunk).fetchone()
            for chunk in chunks(numbers)
        )
        for message in messages:
            insert(db, "messages", {key: value for key, value in message.items() if not (renumber and key == "seq")})

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
