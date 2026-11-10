"""The agent chat (채팅): requests in plain words, planned by the bundled Hermes agent and carried out by the app.

One conversation, kept in agent_messages. A message goes to the planner with the recent conversation; the planner
answers in words and proposes up to four actions (Hermes' prepare_actions tool). Each action is checked here against
TOOLS, its name and every argument, and kept with the answer as a card. An action that only reads what the app keeps
runs at once (a document search, a summary of the web records). One that opens a site in the visible browser, records
a page, adds a task or schedules a routine waits for the user's 확인 후 실행 (agent.approve) or 취소 (agent.reject),
and runs only then. What ran, what it found or why it failed is written on the card. The planner runs nothing itself,
and nothing here clicks a site's page: the web records read pages, and ads count as clicked only when the user clicks
them (web_capture.py).

The planner is Hermes (hermes.py), which a packaged app always carries. A source run that has not built it
(npm run build:hermes) plans with the connected LLM's JSON answer instead (engine "direct"), and the answer says so.
"""

import json
import re
import sys
import threading
from datetime import datetime, timedelta

from . import diagnostics
from .hermes import Hermes
from .llm import BusyError, UserError
from .service import now, text
from .web_capture import KIND_NAMES, web_url

MAX_MESSAGES = 1000  # Messages kept: the oldest go.
HISTORY = 12  # Earlier messages the planner reads with the new one.
HISTORY_CHARS = 6000
MESSAGE_CHARS = 4000
MAX_ACTIONS = 4
DAYS = {"daily": "매일", "weekdays": "평일마다"}
RECORD_KINDS = ("ad", "product", "result", "clicked")
NOT_CONNECTED = "LLM이 연결되지 않았습니다. 관리 → LLM 연결에서 먼저 연결해 주세요."
NO_REPLY = "요청을 이해하지 못했습니다. 무엇을 할지 조금 더 구체적으로 적어 주세요."

SYSTEM = """You are the agent of Private Agent, a desktop app that keeps the user's documents, tasks and web records
on their own PC. Reply in Korean, in short concrete sentences.

You cannot browse or act yourself. Propose app actions; the app shows each one to the user, and anything that opens a
website, records a page, adds a task or schedules a job runs only after the user approves it. Never say an action ran
or claim results you do not have: say what you prepared. Do not invent records, prices or ads.

Actions (tool and args):
- search_documents {"query"}: search the user's Wiki documents.
- list_records {"site"?, "kind"?: "ad"|"product"|"result"|"clicked", "text"?, "since"?: "YYYY-MM-DD"}: summarize the
  web records already kept (ads, products and results of earlier captures, and the ads the user clicked).
- web_capture {"site", "query"}: open the site's search for the query in the visible browser and record the ads,
  products and results the page shows. Sites: coupang (쿠팡), amazon (아마존), naver (네이버), google (구글).
- open_site {"site"?, "url"?}: open a site's home page or an http(s) address in the visible browser for the user.
- capture_page {}: record the ads and items of the page the browser shows now.
- add_task {"title", "due"?: "YYYY-MM-DD"}: add a task.
- schedule_capture {"site", "query", "time": "HH:MM", "days": "daily"|"weekdays"}: record that search on a schedule.

The app only reads pages: never propose clicking ads or anything else on a page. Ads the user clicks themselves in the
browser are recorded with their landing page. If the request is unclear (which site? what to search?), ask one short
question and propose no action. When native tools are unavailable, answer ONLY with JSON:
{"message": "...", "actions": [{"tool": "...", "args": {...}}]}"""


def day(value, name):
    value = text(value, name, 10)
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise UserError(f"{name}은 YYYY-MM-DD 형식이어야 합니다.") from None
    return value


def spoken(value):
    """The planner's words as the chat shows them: without a reasoning block, and nothing that reads as a raw plan."""
    words = re.sub(r"<think>.*?(?:</think>|$)", "", str(value or ""), flags=re.S).strip()
    return "" if words.startswith(("{", "[", "<")) else words[:MESSAGE_CHARS]


def clock(value):
    value = text(value, "시각", 5)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise UserError("시각은 HH:MM(24시간) 형식이어야 합니다.")
    return value


class AgentChat:
    def __init__(self, service):
        self.service = service
        self.hermes = Hermes(service)
        self.lock = threading.Lock()
        self.sending = False
        self.methods = {
            "agent.messages": self.messages,
            "agent.send": self.send,
            "agent.approve": self.approve,
            "agent.reject": self.reject,
            "agent.status": self.status,
            "agent.stop": self.stop,
            "agent.clear": self.clear,
            "agent.engine": self.engine,
        }

    # -- the planner -------------------------------------------------------------

    def hermes_ready(self):
        try:
            self.hermes.launch_paths()
            return True
        except UserError:
            return False

    def engine(self, _params=None):
        """Which planner the next message uses: the bundled Hermes, or (a source run without it) the LLM's JSON."""
        if self.hermes_ready():
            return {"engine": "hermes", "ready": True}
        if getattr(sys, "frozen", False):
            return {"engine": "hermes", "ready": False}
        return {"engine": "direct", "ready": True}

    def plan(self, system, context, request_id):
        if not (self.service.llm and self.service.llm.public().get("configured")):
            raise UserError(NOT_CONNECTED)
        engine = self.engine()
        if engine["engine"] == "hermes":
            # A packaged app without its Hermes (a damaged install) says so (launch_paths) instead of planning
            # another way unannounced.
            return self.hermes.plan(system, context, request_id=request_id, tools=list(self.tools())), "hermes"
        return self.service.llm.structured(system, context), "direct"

    def status(self, params):
        return self.hermes.status(params)

    def stop(self, params):
        return self.hermes.stop(params)

    def shutdown(self):
        self.hermes.shutdown()

    # -- the actions ---------------------------------------------------------------

    @staticmethod
    def tools():
        return (
            "search_documents",
            "list_records",
            "web_capture",
            "open_site",
            "capture_page",
            "add_task",
            "schedule_capture",
        )

    def site_name(self, key):
        return self.service.web.sites[key]["name"]

    def card(self, action):
        """A proposed action, checked: {"tool", "args", "title", "approval", "status"}; or UserError why not."""
        if not isinstance(action, dict):
            raise UserError("작업 형식을 확인할 수 없습니다.")
        tool, args = action.get("tool"), action.get("args") or {}
        if tool not in self.tools() or not isinstance(args, dict):
            raise UserError("알 수 없는 작업입니다.")
        web = self.service.web
        if tool == "search_documents":
            query = text(args.get("query"), "검색어", 200)
            return self.made(tool, {"query": query}, f"문서에서 '{query}' 검색", False)
        if tool == "list_records":
            checked = {}
            if args.get("site"):
                checked["site"] = web.site_key(args["site"])
            if args.get("kind"):
                if args["kind"] not in RECORD_KINDS:
                    raise UserError("기록 종류를 확인할 수 없습니다.")
                checked["kind"] = args["kind"]
            if args.get("text"):
                checked["text"] = text(args["text"], "검색어", 100)
            if args.get("since"):
                checked["since"] = day(args["since"], "시작일")
            what = " · ".join(
                filter(
                    None,
                    (
                        self.site_name(checked["site"]) if "site" in checked else "",
                        {**KIND_NAMES, "clicked": "누른 광고"}.get(checked.get("kind", ""), ""),
                        f"'{checked['text']}'" if "text" in checked else "",
                        f"{checked['since']} 이후" if "since" in checked else "",
                    ),
                )
            )
            return self.made(tool, checked, "웹 기록 요약" + (f" ({what})" if what else ""), False)
        if tool == "web_capture":
            site, query = web.site_key(args.get("site")), text(args.get("query"), "검색어", 100)
            return self.made(
                tool, {"site": site, "query": query}, f"{self.site_name(site)}에서 '{query}' 검색 결과 기록", True
            )
        if tool == "open_site":
            site = web.site_key(args["site"]) if args.get("site") else ""
            url = web_url(args["url"]) if args.get("url") else ""
            if not site and not url:
                raise UserError("열 사이트나 주소가 없습니다.")
            return self.made(tool, {"site": site, "url": url}, f"{url or self.site_name(site)} 열기", True)
        if tool == "capture_page":
            return self.made(tool, {}, "브라우저에 열린 페이지 기록", True)
        if tool == "add_task":
            title = text(args.get("title"), "할 일", 200)
            checked = {"title": title}
            if args.get("due"):
                checked["due"] = day(args["due"], "마감일")
            due = f" (마감 {checked['due']})" if "due" in checked else ""
            return self.made(tool, checked, f"할 일 추가: {title}{due}", True)
        site, query = web.site_key(args.get("site")), text(args.get("query"), "검색어", 100)
        when, days = clock(args.get("time")), args.get("days") or "daily"
        if days not in DAYS:
            raise UserError("반복은 매일(daily) 또는 평일(weekdays)이어야 합니다.")
        checked = {"site": site, "query": query, "time": when, "days": days}
        return self.made(tool, checked, f"{DAYS[days]} {when}에 {self.site_name(site)} '{query}' 기록 예약", True)

    @staticmethod
    def made(tool, args, title, approval):
        return {"tool": tool, "args": args, "title": title, "approval": approval, "status": "pending", "result": None}

    def run(self, card):
        """Carry out a checked action: the card with its result line (and details), or its failure."""
        tool, args, web = card["tool"], card["args"], self.service.web
        try:
            if tool == "search_documents":
                found = self.service.wiki_search({"query": args["query"], "mode": "word"})
                documents = [{"id": doc["id"], "title": doc["title"]} for doc in found["documents"][:5]]
                line = f"문서 {found['total']}개를 찾았습니다." if found["total"] else "찾은 문서가 없습니다."
                result = {"text": line, "documents": documents}
            elif tool == "list_records":
                summary = web.summary(args)
                counts = summary["counts"]
                line = (
                    f"기록 {summary['total']}건: 광고 {counts['ad']} · 상품 {counts['product']} · "
                    f"검색 결과 {counts['result']} · 누른 광고 {counts['clicked']}"
                    if summary["total"]
                    else "조건에 맞는 기록이 없습니다."
                )
                items = [
                    {key: item[key] for key in ("kind", "title", "price", "site", "captured_at", "link_url")}
                    for item in summary["items"][:10]
                ]
                result = {"text": line, "records": items}
            elif tool == "web_capture":
                kept = web.capture(args["site"], args["query"], source="chat")
                result = self.captured(kept)
            elif tool == "capture_page":
                result = self.captured(web.capture_page(source="chat"))
            elif tool == "open_site":
                opened = web.open(args.get("site", ""), args.get("url", ""))
                result = {
                    "text": "브라우저 창에서 열었습니다. 직접 둘러보며 누른 광고는 도착 페이지와 함께 기록됩니다.",
                    "url": opened["url"],
                }
            elif tool == "add_task":
                task = {"title": args["title"]}
                if args.get("due"):
                    # 9 o'clock of that day on this PC's clock, as a time the task list reads with its zone.
                    task["due_at"] = datetime.strptime(args["due"], "%Y-%m-%d").replace(hour=9).astimezone().isoformat()
                saved = self.service.assistant.tasks_save(task)
                result = {"text": "할 일을 추가했습니다.", "task_id": saved.get("id")}
            else:
                name = f"{self.site_name(args['site'])} '{args['query']}' 광고 기록"
                saved = self.service.routines.save(
                    {
                        "kind": "web_capture",
                        "name": name,
                        "config": {"site": args["site"], "query": args["query"]},
                        "frequency": args["days"],
                        "run_time": args["time"],
                    }
                )
                result = {
                    "text": f"자동 작업 '{name}'을 등록했습니다. AI 작업 → 자동 작업에서 바꿀 수 있습니다.",
                    "routine_id": saved.get("id"),
                }
            return {**card, "status": "done", "result": result}
        except UserError as error:
            return {**card, "status": "failed", "result": {"text": str(error)}}
        except Exception as error:
            diagnostics.report(error, tool, "채팅 작업")
            return {
                **card,
                "status": "failed",
                "result": {"text": "작업 중 문제가 생겼습니다. 진단 로그를 확인해 주세요."},
            }

    def captured(self, kept):
        counts = kept["counts"]
        where = f"{kept['name']}에서 '{kept['query']}'" if kept["query"] else kept["name"] or "열린 페이지"
        line = (
            f"{where} 검색 결과의 광고 {counts['ad']}건, 상품 {counts['product']}건, "
            f"검색 결과 {counts['result']}건을 기록했습니다."
        )
        return {"text": line, "capture_id": kept["capture_id"], "ads": kept["ads"], "products": kept["products"]}

    # -- the conversation ----------------------------------------------------------

    @staticmethod
    def public(row):
        return {
            "seq": row["seq"],
            "role": row["role"],
            "content": row["content"],
            "actions": json.loads(row["actions"] or "[]"),
            "engine": row["engine"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def messages(self, params):
        try:
            limit = max(1, min(200, int(params.get("limit", 100))))
        except (TypeError, ValueError):
            raise UserError("목록 범위를 확인해 주세요.") from None
        with self.service.db.connect() as db:
            rows = db.execute("SELECT * FROM agent_messages ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [self.public(row) for row in reversed(rows)]

    def context(self, message):
        """The recent conversation as the planner reads it, ending with the new message."""
        with self.service.db.connect() as db:
            rows = db.execute("SELECT * FROM agent_messages ORDER BY seq DESC LIMIT ?", (HISTORY,)).fetchall()
        lines, used = [], 0
        for row in rows:
            said = row["content"]
            if row["role"] == "assistant":
                done = [
                    f"{card['title']} → {(card.get('result') or {}).get('text') or card['status']}"
                    for card in json.loads(row["actions"] or "[]")
                ]
                if done:
                    said += "\n[작업] " + " / ".join(done)
            entry = f"{'사용자' if row['role'] == 'user' else '에이전트'}: {said}"
            if used + len(entry) > HISTORY_CHARS:
                break
            lines.append(entry)
            used += len(entry)
        today = datetime.now().astimezone()
        return (
            f"Today is {today:%Y-%m-%d} ({today:%A}); tomorrow is {today + timedelta(days=1):%Y-%m-%d}.\n"
            "Conversation so far:\n" + "\n".join(reversed(lines)) + f"\n\nNew message from the user:\n{message}"
        )

    def store(self, db, role, content, actions=(), engine=""):
        stamp = now()
        cursor = db.execute(
            "INSERT INTO agent_messages(role,content,actions,engine,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (role, content, json.dumps(list(actions), ensure_ascii=False), engine, stamp, stamp),
        )
        db.execute(
            "DELETE FROM agent_messages WHERE seq NOT IN (SELECT seq FROM agent_messages ORDER BY seq DESC LIMIT ?)",
            (MAX_MESSAGES,),
        )
        return cursor.lastrowid

    def row(self, seq):
        with self.service.db.connect() as db:
            found = db.execute("SELECT * FROM agent_messages WHERE seq=?", (seq,)).fetchone()
        if found is None:
            raise UserError("메시지를 찾을 수 없습니다. 화면을 새로고침해 주세요.")
        return found

    def send(self, params):
        message = text(params.get("text"), "메시지", MESSAGE_CHARS)
        request_id = text(params.get("request_id") or "chat", "요청 ID", 80)
        with self.lock:
            if self.sending:
                raise BusyError("이전 메시지를 처리하고 있습니다. 끝난 뒤 다시 보내 주세요.")
            self.sending = True
        try:
            context = self.context(message)
            with self.service.db.connect() as db:
                user_seq = self.store(db, "user", message)
            try:
                plan, engine = self.plan(SYSTEM, context, request_id)
            except UserError as error:
                with self.service.db.connect() as db:
                    seq = self.store(db, "assistant", str(error), engine="error")
                return {"user": self.public(self.row(user_seq)), "reply": self.public(self.row(seq))}
            reply = spoken(plan.get("message")) if isinstance(plan, dict) else ""
            proposed = plan.get("actions") if isinstance(plan, dict) else []
            cards, dropped = [], 0
            for action in (proposed if isinstance(proposed, list) else [])[:MAX_ACTIONS]:
                try:
                    cards.append(self.card(action))
                except UserError:
                    dropped += 1
            if dropped:
                reply += ("\n" if reply else "") + f"제안 중 확인할 수 없는 작업 {dropped}개는 뺐습니다."
            if not reply and not cards:
                reply = NO_REPLY
            if any(card["approval"] for card in cards) and not reply:
                reply = "아래 작업을 확인한 뒤 실행해 주세요."
            # What only reads the app's own records runs at once; the rest waits for the user.
            cards = [card if card["approval"] else self.run(card) for card in cards]
            with self.service.db.connect() as db:
                seq = self.store(db, "assistant", reply, cards, engine)
            return {"user": self.public(self.row(user_seq)), "reply": self.public(self.row(seq))}
        finally:
            with self.lock:
                self.sending = False

    def card_of(self, params, status):
        """The message and card the user acts on, after checking it still waits ("pending")."""
        seq = params.get("seq")
        index = params.get("index")
        if type(seq) is not int or type(index) is not int:
            raise UserError("작업을 찾을 수 없습니다. 화면을 새로고침해 주세요.")
        row = self.row(seq)
        cards = json.loads(row["actions"] or "[]")
        if not 0 <= index < len(cards):
            raise UserError("작업을 찾을 수 없습니다. 화면을 새로고침해 주세요.")
        if cards[index]["status"] != "pending":
            raise UserError("이미 처리한 작업입니다.")
        cards[index]["status"] = status
        with self.service.db.connect() as db:
            # Only the one that still sees it pending moves it on: a second click (or a second window) does nothing.
            changed = db.execute(
                "UPDATE agent_messages SET actions=?, updated_at=? WHERE seq=? AND actions=?",
                (json.dumps(cards, ensure_ascii=False), now(), seq, row["actions"]),
            ).rowcount
        if not changed:
            raise UserError("이미 처리한 작업입니다.")
        return seq, index

    def update(self, seq, index, card):
        with self.service.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT actions FROM agent_messages WHERE seq=?", (seq,)).fetchone()
            if row is None:
                return
            cards = json.loads(row["actions"] or "[]")
            cards[index] = card
            db.execute(
                "UPDATE agent_messages SET actions=?, updated_at=? WHERE seq=?",
                (json.dumps(cards, ensure_ascii=False), now(), seq),
            )

    def approve(self, params):
        seq, index = self.card_of(params, "running")
        card = json.loads(self.row(seq)["actions"])[index]
        done = self.run(card)
        self.update(seq, index, done)
        return self.public(self.row(seq))

    def reject(self, params):
        seq, index = self.card_of(params, "cancelled")
        return self.public(self.row(seq))

    def clear(self, _params=None):
        with self.service.db.connect() as db:
            db.execute("DELETE FROM agent_messages")
        return {"ok": True}
