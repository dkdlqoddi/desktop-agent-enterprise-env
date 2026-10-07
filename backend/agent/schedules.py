"""Prompt schedules: recurring runs that Electron's scheduler executes while the app is open."""
import json
import re
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone

from .assistant import timestamp
from .llm import UserError
from .service import SCHEDULE_INPUTS, now, text

FREQUENCIES = ("once", "hourly", "daily", "weekdays", "weekly", "monthly")
MAX_ACTIVE = 50  # Bounds background API usage, like Gemini Spark's limit on active schedules.


def next_run(schedule, after=None):
    """The next occurrence strictly after `after` (naive local wall time), as a UTC ISO string."""
    after = after or datetime.now()
    hour, minute = (int(part) for part in schedule["run_time"].split(":"))
    frequency = schedule["frequency"]
    if frequency == "hourly":
        candidate = after.replace(minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(hours=1)
        return candidate.astimezone(timezone.utc).isoformat()
    for offset in range(367):
        day = after.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute)
        if candidate <= after:
            continue
        # A monthly day past the month's end (e.g. the 31st) runs on its last day.
        if (frequency == "daily" or (frequency == "weekdays" and day.weekday() < 5)
                or (frequency == "weekly" and day.weekday() == schedule["weekday"])
                or (frequency == "monthly" and day.day == min(schedule["month_day"], monthrange(day.year, day.month)[1]))):
            return candidate.astimezone(timezone.utc).isoformat()
    raise ValueError(frequency)


class Schedules:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self.methods = {
            "schedules.list": self.list, "schedules.save": self.save, "schedules.toggle": self.toggle,
            "schedules.delete": self.delete, "schedules.run": self.run_now,
            "schedules.due": self.due, "schedules.execute": self.execute,
        }
        # A run cut off by shutdown never finished; show it as failed rather than running forever.
        with self.db.connect() as db:
            db.execute("""UPDATE schedules SET last_status='error',
                last_error='앱이 종료되어 실행이 중단되었습니다.' WHERE last_status='running'""")

    @staticmethod
    def public(row):
        return {**dict(row), "variables": json.loads(row["variables"]),
                "enabled": bool(row["enabled"]), "notify": bool(row["notify"])}

    @staticmethod
    def get(db, ident):
        row = db.execute("""SELECT s.*,p.title AS prompt_title FROM schedules s
            JOIN prompts p ON p.id=s.prompt_id WHERE s.id=?""", (ident,)).fetchone()
        if not row:
            raise UserError("예약을 찾을 수 없습니다.")
        return row

    @staticmethod
    def upcoming(schedule):
        if schedule["frequency"] != "once":
            return next_run(schedule)
        if schedule["run_at"] <= now():
            raise UserError("예약한 실행 시각이 지났습니다. 새 시각을 지정해 주세요.")
        return schedule["run_at"]

    @staticmethod
    def check_limit(db, ident):
        active = db.execute("SELECT count(*) FROM schedules WHERE enabled=1 AND id!=?", (ident,)).fetchone()[0]
        if active >= MAX_ACTIVE:
            raise UserError(f"예약은 최대 {MAX_ACTIVE}개까지 켜 둘 수 있습니다. 사용하지 않는 예약을 일시정지해 주세요.")

    def list(self, _):
        with self.db.connect() as db:
            return [self.public(r) for r in db.execute("""SELECT s.*,p.title AS prompt_title FROM schedules s
                JOIN prompts p ON p.id=s.prompt_id
                ORDER BY s.enabled DESC,s.next_run_at IS NULL,s.next_run_at,s.created_at""")]

    def save(self, p):
        ident = text(p.get("id") or str(uuid.uuid4()), "ID", 80)
        prompt = self.service.prompt_row(text(p.get("prompt_id"), "프롬프트", 80))
        variables = p.get("variables", {})
        if not isinstance(variables, dict) or not all(isinstance(v, str) for v in variables.values()):
            raise UserError("변수 입력이 올바르지 않습니다.")
        self.service.prompt_messages(prompt, variables)  # Every template variable must be filled in.
        names = {name.strip() for name in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", prompt["template"])}
        frequency, run_time = p.get("frequency"), p.get("run_time", "09:00")
        weekday, month_day = p.get("weekday", 0), p.get("month_day", 1)
        enabled, notify = p.get("enabled", True), p.get("notify", True)
        if frequency not in FREQUENCIES:
            raise UserError("반복 주기를 선택해 주세요.")
        if not isinstance(run_time, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", run_time):
            raise UserError("실행 시각을 HH:MM 형식으로 입력해 주세요.")
        if type(weekday) is not int or not 0 <= weekday <= 6 or type(month_day) is not int or not 1 <= month_day <= 31:
            raise UserError("요일 또는 날짜를 확인해 주세요.")
        if type(enabled) is not bool or type(notify) is not bool:
            raise UserError("예약 설정 값을 확인해 주세요.")
        run_at = timestamp(p.get("run_at"), "실행 일시") if frequency == "once" else None
        if frequency == "once" and not run_at:
            raise UserError("한 번 실행할 날짜와 시간을 입력해 주세요.")
        values = {"id": ident, "prompt_id": prompt["id"], "name": text(p.get("name") or prompt["title"], "예약 이름", 120),
                  "variables": json.dumps({k: v for k, v in variables.items() if k in names}, ensure_ascii=False),
                  "frequency": frequency, "run_time": run_time, "weekday": weekday, "month_day": month_day,
                  "run_at": run_at, "enabled": int(enabled), "notify": int(notify), "stamp": now()}
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if enabled:
                self.check_limit(db, ident)
            values["next_run_at"] = self.upcoming(values) if enabled else None
            db.execute("""INSERT INTO schedules (id,prompt_id,name,variables,frequency,run_time,weekday,month_day,
                run_at,enabled,notify,next_run_at,created_at,updated_at) VALUES (:id,:prompt_id,:name,:variables,
                :frequency,:run_time,:weekday,:month_day,:run_at,:enabled,:notify,:next_run_at,:stamp,:stamp)
                ON CONFLICT(id) DO UPDATE SET prompt_id=excluded.prompt_id,name=excluded.name,
                variables=excluded.variables,frequency=excluded.frequency,run_time=excluded.run_time,
                weekday=excluded.weekday,month_day=excluded.month_day,run_at=excluded.run_at,
                enabled=excluded.enabled,notify=excluded.notify,next_run_at=excluded.next_run_at,
                updated_at=excluded.updated_at""", values)
            return self.public(self.get(db, ident))

    def toggle(self, p):
        ident, enabled = text(p.get("id"), "ID", 80), p.get("enabled")
        if type(enabled) is not bool:
            raise UserError("예약 설정 값을 확인해 주세요.")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.get(db, ident)
            if enabled:
                self.check_limit(db, ident)
            db.execute("UPDATE schedules SET enabled=?,next_run_at=?,updated_at=? WHERE id=?",
                       (int(enabled), self.upcoming(row) if enabled else None, now(), ident))
            return self.public(self.get(db, ident))

    def delete(self, p):
        with self.db.connect() as db:
            db.execute("DELETE FROM schedules WHERE id=?", (text(p.get("id"), "ID", 80),))
        return {"ok": True}

    def finish(self, ident, run):
        with self.db.connect() as db:
            db.execute("UPDATE schedules SET last_run_at=?,last_status=?,last_error=? WHERE id=?",
                       (run["created_at"], run["status"], run["error"], ident))
        return run

    def run_now(self, p):
        with self.db.connect() as db:
            row = self.get(db, text(p.get("id"), "ID", 80))
        prompt = self.service.prompt_row(row["prompt_id"])
        variables = json.loads(row["variables"])
        try:
            self.service.prompt_messages(prompt, variables)
        except UserError as error:  # A template changed since the schedule was saved needs new inputs.
            raise UserError(f"{error} {SCHEDULE_INPUTS}") from None
        return self.finish(row["id"], self.service.execute_prompt(prompt, variables, "manual", row["id"]))

    def due(self, _):
        with self.db.connect() as db:
            return [{**dict(r), "notify": bool(r["notify"])} for r in db.execute("""SELECT id,name,next_run_at,notify
                FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at LIMIT 10""", (now(),))]

    def execute(self, p):
        """Internal: run one due occurrence, claimed before the slow LLM call so it runs only once."""
        ident, expected = text(p.get("id"), "ID", 80), text(p.get("next_run_at"), "실행 시각", 64)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM schedules WHERE id=? AND enabled=1 AND next_run_at=?
                AND next_run_at<=?""", (ident, expected, now())).fetchone()
            if not row:
                return {"ran": False}
            # Occurrences missed while the app was closed collapse into this run. A one-time
            # schedule is finished afterwards and no longer counts toward the active limit.
            once = row["frequency"] == "once"
            db.execute("""UPDATE schedules SET next_run_at=?,enabled=?,last_status='running',updated_at=?
                WHERE id=?""", (None if once else next_run(row), 0 if once else 1, now(), ident))
        prompt = self.service.prompt_row(row["prompt_id"])
        run = self.finish(ident, self.service.execute_prompt(prompt, json.loads(row["variables"]), "scheduled", ident))
        return {"ran": True, "schedule": {"id": ident, "name": row["name"], "notify": bool(row["notify"])},
                "run": {k: run[k] for k in ("id", "title", "status", "error", "duration_ms", "created_at")}}
