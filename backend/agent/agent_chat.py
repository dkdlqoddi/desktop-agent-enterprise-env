"""The agent chat (채팅): requests in plain words, carried out by the bundled Hermes agent.

One conversation, kept in agent_messages. A message goes to the agent with the recent conversation (send). The agent
is Hermes (hermes.py), which a packaged app always carries: it acts at once with its own tools (terminal, files, code,
web search, a browser, memory, skills) and with the app's actions below, which it calls as tools. Each call of an app
action is checked here, its name and every argument (card), run (run), and kept with the answer as a card that says
what it did or why it failed. A command Hermes' guard finds dangerous pauses the turn for the user's decision
(agent.approve_command), and a question the agent asks waits for the user's answer (agent.answer_clarify). While a
turn runs the chat reads its progress (agent.status) and, loaded meanwhile, finds the turn again (agent.active).

A source run that has not built Hermes (npm run build:hermes) has no tools: the connected LLM answers with a JSON plan
instead (engine "direct", run_direct). Its actions that only read what the app keeps run at once (a document search, a
summary of the web records); one that opens a site, records a page, adds a task or schedules a routine waits for the
user's 확인 후 실행 (agent.approve) or 취소 (agent.reject), and the answer says which engine it was.

Nothing here clicks a site's page: the web records read pages, and ads count as clicked only when the user clicks them
(web_capture.py).
"""

import json
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import diagnostics
from .hermes import Hermes
from .hermes_gateway import Gateway
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
# Hermes' names of the web services a key of the user's selects (electron/hermes-settings.cjs); ddgs needs no key and
# only searches.
WEB_SERVICES = ("ddgs", "tavily", "exa", "parallel", "firecrawl", "brave-free")
NOT_CONNECTED = "LLM이 연결되지 않았습니다. 관리 → LLM 연결에서 먼저 연결해 주세요."
NO_REPLY = "요청을 이해하지 못했습니다. 무엇을 할지 조금 더 구체적으로 적어 주세요."

# The system prompt for the bundled Hermes agent (live tools). The app's own actions are offered as tools the agent
# calls itself; the app runs them and returns the result.
SYSTEM = """You are the agent of Private Agent, a desktop app on the user's own PC that keeps their documents, tasks
and web records. Reply in Korean, in short concrete sentences.

You act directly with your tools — files, terminal, code, web search, a browser, memory and skills on this PC — and
with the app's own tools below. Do the work; do not ask permission for ordinary steps. Dangerous commands are shown
to the user for approval on their own, so run the command and let that happen. Never claim a result you do not have,
and do not invent records, prices or ads.

The app's own tools:
- search_documents: search the user's Wiki documents.
- list_records: summarize the web records already kept (ads, products, results, and the ads the user clicked).
- web_capture {site, query}: open a shopping or search site's results in the visible browser and record the ads,
  products and results. Sites: coupang (쿠팡), amazon (아마존), naver (네이버), google (구글).
- open_site {site?, url?}: open a site or an http(s) address in the visible browser for the user.
- capture_page: record the ads and items of the page the browser shows now.
- add_task {title, due?}: add a task.
- schedule_capture {site, query, time, days}: record that search on a schedule.

Reading a page's text with web_extract needs a web service key, which the user sets under 관리 → 채팅 에이전트
(Firecrawl, Tavily, Exa or Parallel). When web_extract says it has no service, open the page with the browser tool
instead, and do not ask the user to change a setting file.

Never click, open or follow an advertisement or a sponsored link yourself, with any tool (the browser, computer use, a
script or a web request): automated ad clicks are invalid traffic that harms advertisers and breaks the sites' terms.
If asked to, decline in one sentence and offer to record the ads instead. The app's own tools only read shopping
pages; ads the user clicks themselves in the visible browser are recorded with their landing page. If a request is
ambiguous (which site? what to search?), use the clarify tool to ask."""

# Added for a conversation in a messenger (hermes_gateway.py): the one who writes there is the user, away from the PC.
MESSENGER = """This conversation is in a messenger (Telegram, Discord or Slack). The user writes from there and may be
away from the PC: what the app's tools open, they open on the PC, so say in words what you did and what you found. A
dangerous command is asked for approval in this messenger."""

# The prompt of a source run without the bundled Hermes (engine "direct"). The connected LLM has no tools there, so it
# answers with a plan as JSON, which run_direct checks and shows as cards. Kept apart from SYSTEM: an LLM told that it
# acts with tools answers in words, and a plan is read from JSON alone.
DIRECT_SYSTEM = """You are the agent of Private Agent, a desktop app that keeps the user's documents, tasks and web
records on their own PC. Reply in Korean, in short concrete sentences.

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
question and propose no action. Answer ONLY with JSON:
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
        # The message being carried out, {"request_id", "text"}: what a chat screen loaded meanwhile shows again.
        self.active = None
        # Hermes runtime settings the main process passes (agent.configure): the MCP servers, whether computer use is
        # on, and the services that search the web and read pages. The service keys and tokens live in the worker's
        # environment, not here.
        self.settings = {"computer_use": True, "mcp_servers": {}, "search_backend": "ddgs", "extract_backend": "ddgs"}
        # The folder the agent's terminal and file tools start in; '' for its own home in the Wiki folder.
        self.workspace = ""
        # The keys of the chat worker's environment, as last set; the gateway's process gets them with its bot tokens.
        self.env = {}
        self.gateway = Gateway(self)
        self.methods = {
            "agent.messages": self.messages,
            "agent.send": self.send,
            "agent.approve": self.approve,
            "agent.reject": self.reject,
            "agent.approve_command": self.approve_command,
            "agent.answer_clarify": self.answer_clarify,
            "agent.status": self.status,
            "agent.active": self.current,
            "agent.stop": self.stop,
            "agent.clear": self.clear,
            "agent.engine": self.engine,
            "agent.configure": self.configure,
            "agent.gateway": self.gateway.status,
        }

    # -- the agent ---------------------------------------------------------------

    def hermes_ready(self):
        try:
            self.hermes.launch_paths()
            return True
        except UserError:
            return False

    def engine(self, _params=None):
        """Which engine the next message uses: the bundled Hermes agent (full tools, live actions), or — only in a
        source run that has not built it — the connected LLM proposing app actions as cards (no tools, no live run)."""
        if self.hermes_ready():
            return {"engine": "hermes", "ready": True}
        if getattr(sys, "frozen", False):
            return {"engine": "hermes", "ready": False}
        return {"engine": "direct", "ready": True}

    def configure(self, params):
        """The main process (never the renderer) sets the Hermes runtime options and, over the backend's private
        pipe, the service keys that go into the worker's environment. A change recycles the idle worker so the next
        turn picks it up; the secret values are never returned."""
        settings = dict(self.settings)
        if "computer_use" in params:
            settings["computer_use"] = bool(params["computer_use"])
        if isinstance(params.get("mcp_servers"), dict):
            settings["mcp_servers"] = params["mcp_servers"]
        # The service that searches the web and the one that reads pages, by the keys the user stored; anything but
        # a service Hermes knows by that name is the search that needs no key.
        for field in ("search_backend", "extract_backend"):
            if field in params:
                settings[field] = params[field] if params[field] in WEB_SERVICES else "ddgs"
        self.settings = settings
        if "workspace" in params:
            folder = params["workspace"]
            self.workspace = folder if isinstance(folder, str) and len(folder) <= 1024 else ""
        if "env" in params:
            env = params["env"] if isinstance(params["env"], dict) else {}
            extra = {name: value for name, value in env.items() if isinstance(name, str) and isinstance(value, str)}
            self.env = extra
            self.hermes.recycle(extra or None)
        # The messenger gateway, last: it is started with the settings above.
        if "gateway" in params:
            self.gateway.configure(params["gateway"], self.env)
        return {"ok": True}

    @staticmethod
    def gateway_system():
        return SYSTEM + "\n\n" + MESSENGER

    def folder(self):
        """The folder a turn's terminal and file tools start in: the one the user chose, while it is a folder that is
        still there, and otherwise '' (the agent's own home in the Wiki folder)."""
        try:
            chosen = Path(self.workspace) if self.workspace else None
            return str(chosen) if chosen and chosen.is_absolute() and chosen.is_dir() else ""
        except (OSError, ValueError):
            return ""

    def toolsets(self):
        """The Hermes toolsets the chat turns on: the app's own actions, plus files, terminal, code, web, browser,
        memory, skills, todo, past-conversation search, subagents, vision, image generation and clarify. Computer use
        only when the user turned it on; MCP server tools by their configured names. Hermes' own scheduling (cronjob)
        stays off: its jobs are run by a gateway, which this app does not start, so one would be saved and never
        run. A schedule is the app's own routine instead (schedule_capture)."""
        names = [
            "private_agent",
            "file",
            "terminal",
            "code_execution",
            "web",
            "browser",
            "memory",
            "skills",
            "todo",
            "session_search",
            "delegation",
            "vision",
            "image_gen",
            "clarify",
        ]
        if self.settings.get("computer_use", True):
            names.append("computer_use")
        names.extend(name for name in (self.settings.get("mcp_servers") or {}) if isinstance(name, str))
        return names

    def status(self, params):
        """What a turn is doing, for the chat while it waits. `active`: the request is still being carried out, which
        the engine's own phase does not tell for a plan of the connected LLM (engine "direct")."""
        status = self.hermes.status(params)
        with self.lock:
            status["active"] = bool(self.active and self.active["request_id"] == params.get("request_id"))
        return status

    def current(self, _params=None):
        """The message being carried out now, {"request_id", "text"} ('' and '' for none): a chat screen that was
        left, or loaded again, while a turn ran finds it here and shows its progress and questions again."""
        with self.lock:
            return dict(self.active) if self.active else {"request_id": "", "text": ""}

    def stop(self, params):
        return self.hermes.stop(params)

    def approve_command(self, params):
        """The user's decision on a dangerous command the agent paused on: once | session | always | deny."""
        decision = params.get("decision")
        if decision not in ("once", "session", "always", "deny"):
            raise UserError("승인 값을 확인해 주세요.")
        return self.hermes.resolve(params, "approval")

    def answer_clarify(self, params):
        """The user's answers to the clarify questions the agent asked."""
        answers = params.get("answers")
        if not isinstance(answers, dict):
            raise UserError("답변 형식을 확인해 주세요.")
        return self.hermes.resolve({**params, "outcome": "submitted"}, "clarify")

    def shutdown(self):
        self.gateway.shutdown()
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

    @staticmethod
    def tool_schemas():
        """The app's actions as Hermes tools (OpenAI function schemas): Hermes calls them itself, the app checks every
        argument (card) and runs them (run), and Hermes reads the result."""
        site = {"type": "string", "description": "coupang (쿠팡), amazon (아마존), naver (네이버) or google (구글)"}
        day = {"type": "string", "description": "A date as YYYY-MM-DD"}
        schemas = {
            "search_documents": (
                "Search the user's Wiki documents kept in Private Agent. Returns the count and up to five titles.",
                {"query": {"type": "string", "description": "Words to search for"}},
                ["query"],
            ),
            "list_records": (
                "Summarize the web records Private Agent already kept: ads, products and results of earlier captures, "
                "and the ads the user clicked themselves.",
                {
                    "site": site,
                    "kind": {"type": "string", "enum": list(RECORD_KINDS)},
                    "text": {"type": "string", "description": "Words the records contain"},
                    "since": day,
                },
                [],
            ),
            "web_capture": (
                "Open a shopping or search site's results for a query in Private Agent's visible browser and record "
                "the ads, products and results the page shows. It reads the page; it never clicks ads.",
                {"site": site, "query": {"type": "string"}},
                ["site", "query"],
            ),
            "open_site": (
                "Open a site's home page or an http(s) address in Private Agent's visible browser for the user. Ads "
                "the user clicks there are recorded with their landing page.",
                {"site": site, "url": {"type": "string", "description": "An http(s) address"}},
                [],
            ),
            "capture_page": ("Record the ads and items of the page Private Agent's browser shows now.", {}, []),
            "add_task": (
                "Add a task to the user's task list in Private Agent.",
                {"title": {"type": "string"}, "due": day},
                ["title"],
            ),
            "schedule_capture": (
                "Record a site's search results on a schedule (a routine of Private Agent).",
                {
                    "site": site,
                    "query": {"type": "string"},
                    "time": {"type": "string", "description": "HH:MM, 24-hour clock"},
                    "days": {"type": "string", "enum": list(DAYS)},
                },
                ["site", "query", "time"],
            ),
        }
        return [
            {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            }
            for name, (description, properties, required) in schemas.items()
        ]

    def call_tool(self, name, args):
        """One of the app's tools as Hermes calls it: the card that shows it in the chat, with what it did or why it
        could not run. The card is checked exactly as a proposed action is."""
        try:
            card = self.card({"tool": name, "args": args if isinstance(args, dict) else {}})
        except UserError as error:
            return {
                "tool": name if name in self.tools() else "unknown",
                "args": {},
                "title": "확인할 수 없는 작업",
                "approval": False,
                "status": "failed",
                "result": {"text": str(error)},
            }
        return self.run({**card, "status": "running"})

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
        if not (self.service.llm and self.service.llm.public().get("configured")):
            raise UserError(NOT_CONNECTED)
        with self.lock:
            if self.sending:
                raise BusyError("이전 메시지를 처리하고 있습니다. 끝난 뒤 다시 보내 주세요.")
            self.sending = True
            self.active = {"request_id": request_id, "text": message}
        try:
            context = self.context(message)
            with self.service.db.connect() as db:
                user_seq = self.store(db, "user", message)
            if self.engine()["engine"] == "hermes":
                reply = self.run_agent(context, request_id)
            else:
                reply = self.run_direct(context)
            with self.service.db.connect() as db:
                seq = self.store(db, "assistant", reply["content"], reply["actions"], reply["engine"])
            return {"user": self.public(self.row(user_seq)), "reply": self.public(self.row(seq))}
        finally:
            with self.lock:
                self.sending = False
                self.active = None

    def run_agent(self, context, request_id):
        """The bundled Hermes agent runs the turn with its full tools, acting live. The app's own actions run here as
        they are called (checked first); dangerous commands pause for the user. Returns the stored message's fields."""
        executed = []

        def on_app_tool(tool, args):
            card = self.call_tool(tool, args)
            executed.append(card)
            return card

        try:
            result = self.hermes.plan(
                SYSTEM,
                context,
                request_id=request_id,
                toolsets=self.toolsets(),
                app_tools=self.tool_schemas(),
                settings=self.settings,
                on_app_tool=on_app_tool,
                on_interaction=lambda kind, payload: None,
                workspace=self.folder(),
            )
        except UserError as error:
            return {"content": str(error), "actions": executed, "engine": "error"}
        reply = spoken(result.get("message"))
        if not reply and not executed:
            reply = NO_REPLY
        return {"content": reply, "actions": executed, "engine": "hermes"}

    def run_direct(self, context):
        """A source run without the bundled Hermes: the connected LLM proposes app actions as cards, read-only ones
        run at once and the rest wait for the user's approval. No tools, no live actions — the badge says so."""
        try:
            plan = self.service.llm.structured(DIRECT_SYSTEM, context)
        except UserError as error:
            return {"content": str(error), "actions": [], "engine": "error"}
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
        cards = [card if card["approval"] else self.run(card) for card in cards]
        return {"content": reply, "actions": cards, "engine": "direct"}

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
