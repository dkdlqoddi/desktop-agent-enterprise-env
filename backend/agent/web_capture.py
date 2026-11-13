"""The agent's visible browser and its web records (웹 기록).

A web task of the chat (agent_chat.py) or of an automatic routine opens a site in a Chromium window the user sees: the
search page of a shopping or search site, or a page to browse. What a search page shows is recorded: its ads, products
and results, read by the page script the Android app shares (shared/web-capture/capture.js), with a screenshot. The
window stays open for the user to look and click; an ad they click themselves is recorded with its landing page
(click-watch.js reports trusted clicks only). The app never clicks, types into or submits a site's page: an automatic
click on an ad would bill its advertiser for a visit nobody made, and the sites' terms forbid it. A page that asks
whether a robot is visiting (a CAPTCHA) stops the task and is left to the user in the window; nothing here tries to
get past it.

One browser (one Chromium profile, in Electron's userData: PRIVATE_AGENT_WEB_PROFILES) runs on a thread and event loop
of its own while the backend runs, a tab per site, and does one web task at a time. A scheduled capture (routines.py)
reads in a tab of its own and waits for the other tasks, so the user's tabs and tasks go on as they were. Chromium's
renderer sandbox stays on and downloads are off, as for the importers' browsers (browser_import.py). The records live
in wiki.db (web_captures, web_items) and the screenshots in the Wiki folder's web-captures folder; the CSV exports go to
its exports folder.
"""

import asyncio
import base64
import collections
import concurrent.futures
import contextlib
import csv
import io
import json
import os
import re
import sys
import threading
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import diagnostics
from .browser_import import BROWSER_ARGS, NO_SANDBOX, use_shared_browsers
from .llm import BusyError, UserError
from .service import now, text

KINDS = ("ad", "product", "result")
SOURCES = ("chat", "schedule", "manual")
MAX_ITEMS = 80  # Items a capture keeps.
MAX_CAPTURES = 5000  # Captures kept: the oldest go, with their screenshots.
MAX_TOKENS = 200  # Captures of open pages whose items a click can still name.
MAX_PAGE = 200  # Rows a list answers at once.
MAX_EXPORT = 50_000  # Rows a CSV export holds.
MAX_DELETE = 1000  # Records one delete names: as many as one trash.restore brings back.
MAX_SHOT = 20 * 1024 * 1024  # The largest screenshot the screen is sent, in bytes.
JPEG = bytes((0xFF, 0xD8, 0xFF))  # How a JPEG file begins, as the screenshots are taken.
# How many days the user keeps the records (web.retention): off (0, kept until deleted) unless they choose one.
MAX_KEPT_DAYS = 3650
QUERY_CHARS = 100
SCROLLS = 4  # Viewports a capture scrolls down, so pages that load their items as they come into view show them.
SCROLL_PAUSE = 0.8  # Seconds
SETTLE = 1.5  # Seconds a page has after it loaded before it is read.
NAVIGATION_MS = 45_000
LOAD_MS = 15_000
TASK_SECONDS = 150  # A capture or a page to open, the browser's start included.
# Seconds the browser stays free after a web task before a scheduled capture may take it: the user's (or the chat's)
# next task in a row goes first.
QUIET = 10
# A click's landing page: the navigation the click started begins (or its new tab opens) within START_SECONDS of it,
# and its page loads within LANDING_SECONDS. A move the page makes by itself up to REDIRECT_SECONDS after it loaded is
# followed. What the browser told of up to EARLY seconds before the click's report came may be the click's doing.
START_SECONDS = 15
LANDING_SECONDS = 60
REDIRECT_SECONDS = 3
EARLY = 1.0
WINDOW = {"width": 1280, "height": 900}
STOPPED = "웹 작업을 중지했습니다."
BLOCKED = (
    "사이트가 로봇 확인(CAPTCHA)을 요청했습니다. 열린 브라우저 창에서 직접 확인을 마친 뒤 다시 요청해 주세요. "
    "앱은 확인을 대신하거나 우회하지 않습니다."
)
BUSY = "다른 웹 작업이 진행 중입니다. 끝난 뒤 다시 요청해 주세요."
NO_BROWSER = (
    "웹 기록용 브라우저를 시작하지 못했습니다. 앱을 다시 실행해 주세요."
    " 개발 환경에서는 npm run setup:browser를 실행해 주세요."
)
COLUMNS = [
    ("captured_at", "기록 시각"),
    ("site", "사이트"),
    ("query", "검색어"),
    ("kind", "종류"),
    ("position", "순서"),
    ("title", "제목"),
    ("price", "가격"),
    ("display_url", "표시 주소"),
    ("link_url", "링크"),
    ("clicked_at", "클릭 시각"),
    ("landing_url", "도착 페이지"),
    ("landing_title", "도착 페이지 제목"),
]
KIND_NAMES = {"ad": "광고", "product": "상품", "result": "검색 결과"}


def assets():
    """The folder of the shared page scripts: inside a frozen backend's bundle, else the repository's."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "web_capture"
    return Path(__file__).resolve().parents[2] / "shared" / "web-capture"


def load_sites():
    sites = json.loads((assets() / "sites.json").read_text(encoding="utf-8"))
    return {key: value for key, value in sites.items() if isinstance(value, dict) and value.get("search")}


def headless():
    """Tests and CI, where no one could see a window, run the browser without one."""
    return os.environ.get("PRIVATE_AGENT_WEB_HEADLESS") == "1"


def web_url(value):
    """An address to open: http(s) only, https when none is written (as the site accounts take it)."""
    value = text(value, "주소", 2000)
    # A scheme written out must be http(s) (javascript:, file:, mailto: ... are refused); host:port is no scheme.
    if re.match(r"[a-z][a-z0-9+.-]*:(?!\d)", value, re.I) and not re.match(r"https?://", value, re.I):
        raise UserError("http 또는 https 주소만 열 수 있습니다.")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UserError("http 또는 https 주소만 열 수 있습니다.")
    return value


def clip(value, limit):
    return " ".join(str(value or "").split())[:limit]


def link(value):
    """A link the page script read; only http(s) addresses are kept."""
    value = clip(value, 2000)
    return value if urlsplit(value).scheme in ("http", "https") else ""


def same(address, other):
    """Whether two addresses name one page: a place in it (#...) aside, which a request does not carry."""
    return address.split("#")[0] == other.split("#")[0]


DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def moment(value, last=False):
    """The start (since) or the end (until, `last`) of a period of the records as the time theirs are compared with,
    a UTC one. A day (YYYY-MM-DD) is the user's own day, on this PC's clock, and the last day of a period counts to
    its end: 시작일 and 종료일 of the same day are that day. A full time is taken as it is written."""
    try:
        if DAY.fullmatch(value):
            day = datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1 if last else 0)
            return day.astimezone(timezone.utc).isoformat()
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        raise UserError("기간을 YYYY-MM-DD 형식으로 입력해 주세요.") from None


class WebCapture:
    def __init__(self, service):
        self.service = service
        root = os.environ.get("PRIVATE_AGENT_WEB_PROFILES") or Path.home() / ".private-agent" / "web-profiles"
        self.profile = Path(root) / "default"
        self.sites = load_sites()
        self.capture_js = (assets() / "capture.js").read_text(encoding="utf-8")
        self.watch_js = (assets() / "click-watch.js").read_text(encoding="utf-8")
        self.lock = threading.RLock()
        self.loop = self.thread = None
        self.playwright = self.context = None
        self.tabs = {}  # site key -> the page of that site
        self.current = None  # The page a web task used last: what "이 페이지 기록" reads.
        # site key -> (page, address): the tab the last scheduled capture of that site read in, and the page it left.
        self.apart = {}
        self.tokens = {}  # A capture's token (in its page's marks) -> its ID, for the clicks of pages still open.
        self.pending = None  # The user's last click on a marked link, until its landing page settled (_clicked).
        self.watched = weakref.WeakSet()  # The pages whose navigations are followed.
        # The main-frame navigations of each page and the tabs opened in the last moments: the browser may tell of
        # them before the report of the click that caused them comes.
        self.recent = weakref.WeakKeyDictionary()
        self.opened = collections.deque(maxlen=8)
        self.claimed = weakref.WeakSet()  # The new tabs that were a click's landing page.
        self.busy = False
        self.freed = float("-inf")  # When the last web task ended (time.monotonic()).
        self.methods = {
            "web.sites": self.sites_list,
            "web.status": self.status,
            "web.capture": self.capture_rpc,
            "web.capture_page": self.capture_page_rpc,
            "web.open": self.open_rpc,
            "web.close": lambda params: self.close(),
            "web.captures": self.captures,
            "web.capture_get": self.capture_get,
            "web.records": self.records,
            "web.export": self.export,
            "web.delete_capture": self.delete_capture,
            "web.delete_captures": self.delete_captures,
            "web.screenshot": self.screenshot,
            "web.retention": self.retention,
            "web.retention_save": self.retention_save,
        }

    # The folder of the Wiki, where the screenshots and exports go.
    @property
    def folder(self):
        return Path(self.service.db.path).resolve().parent

    def sites_list(self, _params=None):
        return [{"key": key, "name": site["name"], "home": site.get("home", "")} for key, site in self.sites.items()]

    def site_key(self, value):
        key = text(value, "사이트", 40).lower()
        if key not in self.sites:
            names = ", ".join(site["name"] for site in self.sites.values())
            raise UserError(f"지원하는 사이트는 {names}입니다.")
        return key

    def site_of(self, url):
        """The site an address belongs to, by the hosts sites.json names; '' for any other."""
        host = (urlsplit(url).hostname or "").lower()
        for key, site in self.sites.items():
            if any(host == name or host.endswith("." + name) for name in site.get("hosts", [])):
                return key
        return ""

    def status(self, _params=None):
        with self.lock:
            open_ = self.context is not None
            return {"open": open_, "busy": self.busy, "headless": headless()}

    # The browser's thread and event loop, started with the first web task.

    def _loop(self):
        with self.lock:
            if self.loop and self.thread.is_alive():
                return self.loop
            loop, ready = asyncio.new_event_loop(), threading.Event()

            def run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            self.thread = threading.Thread(target=run, name="web-browser", daemon=True)
            self.thread.start()
            if not ready.wait(10):
                raise UserError(NO_BROWSER)
            self.loop = loop
            return loop

    def _call(self, coroutine, seconds):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop())
        try:
            return future.result(seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise UserError(
                "웹 작업이 제한 시간 안에 끝나지 않았습니다. 브라우저 창의 페이지를 확인해 주세요."
            ) from None
        except concurrent.futures.CancelledError:
            raise UserError(STOPPED) from None

    async def _context(self):
        if self.context is not None:
            return self.context
        use_shared_browsers()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise UserError(NO_BROWSER) from None
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        playwright = await async_playwright().start()
        hidden = headless()
        options = {
            "channel": "chromium",
            "headless": hidden,
            "accept_downloads": False,
            "args": BROWSER_ARGS + ([] if hidden else [f"--window-size={WINDOW['width']},{WINDOW['height']}"]),
            # A window the user sees takes its own size; one without takes a fixed viewport.
            **({"viewport": WINDOW} if hidden else {"no_viewport": True}),
        }
        try:
            try:
                context = await playwright.chromium.launch_persistent_context(
                    str(self.profile), chromium_sandbox=True, **options
                )
            except Exception as error:
                # The same rule as the importers' browsers: Windows always has the sandbox; only a Linux without
                # unprivileged user namespaces (a container, CI) goes on without it, and the log says so.
                if sys.platform == "win32" or not NO_SANDBOX.search(str(error)):
                    raise
                diagnostics.LOG.warning("웹 기록 브라우저를 샌드박스 없이 시작합니다: %s", str(error).splitlines()[0])
                context = await playwright.chromium.launch_persistent_context(
                    str(self.profile), chromium_sandbox=False, **options
                )
        except Exception:
            with contextlib.suppress(Exception):
                await playwright.stop()
            diagnostics.LOG.warning("웹 기록 브라우저 시작 실패", exc_info=True)
            raise UserError(NO_BROWSER) from None
        await context.expose_binding("__paReportClick", self._clicked)
        await context.add_init_script(script=self.watch_js)
        context.on("page", self._opened)
        context.on("close", lambda _context: self._forget())
        self.playwright, self.context = playwright, context
        return context

    def _forget(self):
        """The user closed the window: the next web task starts a new browser."""
        playwright = self.playwright
        self.context = self.playwright = self.current = self.pending = None
        self.tabs.clear()
        self.apart.clear()
        self.opened.clear()
        if playwright is not None:
            asyncio.ensure_future(playwright.stop())

    async def _tab(self, site):
        context = await self._context()
        page = self.tabs.get(site)
        if page is None or page.is_closed():
            blank = [p for p in context.pages if p.url == "about:blank" and p not in self.tabs.values()]
            page = blank[0] if blank else await context.new_page()
            self.tabs[site] = page
            self._watch(page)
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        self.current = page
        return page

    async def _apart(self, site):
        """The tab a scheduled capture of `site` reads in: never one the user browses, so the page they look at stays as
        it is. The tab the last scheduled capture of the site read in is used again while it still shows that page
        (nobody went on from it there); otherwise a new tab opens. It comes to the front as any web task's page does,
        and it does not become the page "이 페이지 기록" reads (self.current)."""
        context = await self._context()
        page, left = self.apart.get(site, (None, ""))
        if page is None or page.is_closed() or page.url != left:
            taken = {*self.tabs.values(), self.current, *(kept for kept, _ in self.apart.values())}
            blank = [p for p in context.pages if p.url == "about:blank" and p not in taken]
            page = blank[0] if blank else await context.new_page()
            self._watch(page)
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        return page

    # Clicks and their landing pages. click-watch.js reports a click of the user's on a link of an item a capture
    # marked. The page that click opens is the item's landing page: the main-frame navigation to the link's address (or
    # to the address the site's script gave the link as the click went, which a follow-up report tells), in this tab,
    # another one or a new one, followed through its redirects and through a move the page makes by itself as it loads.
    # Nothing else is: a page that did not load, going back, a page the user or the app opened afterwards, any page that
    # merely came next. The pending click (self.pending) is {item, token, ref, at, page (where the click was), source
    # (its address), starts (the link's addresses), elsewhere (meant to open a tab), tab (where its navigation runs,
    # once seen), chain (that navigation's addresses), committed, loaded (when its page was recorded)}.

    def _watch(self, page):
        if page in self.watched:
            return
        self.watched.add(page)
        page.on("request", lambda request: self._requested(page, request))
        page.on("requestfailed", lambda request: self._failed(page, request))
        page.on("framenavigated", lambda frame: self._navigated(page, frame))
        # Its requests hold the page: they go with it.
        page.on("close", lambda _page: self.recent.pop(page, None))

    def _live(self):
        """The pending click, or None once its time is over: its navigation never came, or its page settled."""
        pending, at = self.pending, time.monotonic()
        if pending is None:
            return None
        loaded = pending["loaded"]
        if (loaded is None and at - pending["at"] > LANDING_SECONDS) or (
            loaded is not None and at - loaded > REDIRECT_SECONDS
        ):
            self.pending = None
            return None
        return pending

    def _opened(self, page):
        """A page opened in a new tab or window: the landing page of a click meant to open there, or one that opened
        the link's own address. The app's own new tabs open blank and are not; neither is a tab that failed to load."""
        self._watch(page)
        at = time.monotonic()
        self.opened.append((at, page))
        self._new_tab(page, at)

    def _new_tab(self, page, at):
        pending = self._live()
        # A tab that opened for an earlier click is that click's, not a new tab of this one.
        if pending is None or pending["tab"] is not None or page is pending["page"] or page in self.claimed:
            return
        if not pending["at"] - EARLY <= at <= pending["at"] + START_SECONDS:
            return
        url = link(page.url)
        if url and (pending["elsewhere"] or any(same(url, start) for start in pending["starts"])):
            self.claimed.add(page)
            pending.update(tab=page, chain=[page.url], committed=True)
            asyncio.ensure_future(self._landing(page, pending))

    def _requested(self, page, request):
        try:
            if not request.is_navigation_request() or request.frame != page.main_frame:
                return
        except Exception:  # A request of a frame that is gone.
            return
        at = time.monotonic()
        self.recent.setdefault(page, collections.deque(maxlen=12)).append((at, request))
        self._step(page, request, at)

    def _step(self, page, request, at):
        """A main-frame navigation of `page` began at `at`: the pending click's own (its link's address), the next step
        of its chain, or another page in its place."""
        pending = self._live()
        if pending is None:
            return
        if pending["tab"] is None:
            in_time = pending["at"] - EARLY <= at <= pending["at"] + START_SECONDS
            if in_time and any(same(request.url, start) for start in pending["starts"]):
                pending.update(tab=page, chain=[request.url])
            return
        if pending["tab"] is not page:
            return
        before = request.redirected_from
        if before is not None and same(before.url, pending["chain"][-1]):
            pending["chain"].append(request.url)  # The server sent it on.
        elif (
            pending["committed"]
            and not same(request.url, pending["source"])
            and (pending["loaded"] is None or at - pending["loaded"] <= REDIRECT_SECONDS)
        ):
            # The landing page moved on by itself (a script or a meta refresh): the page it moves to is the one.
            pending["chain"].append(request.url)
            pending.update(committed=False, loaded=None)
        else:
            # Another page took its place before it opened, the user went back, or went on from it: what was recorded
            # stays, and nothing after it is the landing page.
            self.pending = None

    def _failed(self, page, request):
        pending = self.pending
        if pending is None or pending["tab"] is not page or not pending["chain"]:
            return
        with contextlib.suppress(Exception):
            if request.is_navigation_request() and same(request.url, pending["chain"][-1]):
                self.pending = None  # The page did not open (no connection, a download, a stop): no landing page.

    def _navigated(self, page, frame):
        pending = self._live()
        if pending is None or frame != page.main_frame or pending["tab"] is not page:
            return
        if any(same(frame.url, step) for step in pending["chain"]):
            pending["committed"] = True
            asyncio.ensure_future(self._landing(page, pending))

    async def _landing(self, page, pending):
        """Record the page of the click's navigation once it has loaded: only a page its chain led to (an error page,
        or the page before it while it is still on its way, is not)."""
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("load", timeout=30_000)
        if self.pending is not pending or page.is_closed():
            return
        url = link(page.url)
        if not url or not any(same(url, step) for step in pending["chain"]):
            return
        try:
            title = clip(await page.title(), 300)
        except Exception:
            title = ""
        if self.pending is not pending:
            return
        pending.update(committed=True, loaded=time.monotonic())
        await asyncio.to_thread(self._save_landing, pending["item"], url, title)

    def _save_landing(self, item, url, title):
        with self.service.db.connect() as conn:
            conn.execute("UPDATE web_items SET landing_url=?, landing_title=? WHERE id=?", (url, title, item))

    def _leave(self, page):
        """The app is about to open another page in `page`: a click's navigation there ends, unrecorded if its page has
        not loaded yet, as the app's page takes its place. One running in another tab goes on."""
        pending = self.pending
        if pending is not None and (pending["tab"] is page or (pending["tab"] is None and pending["page"] is page)):
            self.pending = None

    def _catch_up(self, pending):
        """What the browser did in the moment before a report came (its messages may come in either order): the
        navigations and new tabs since just before the click, taken as if they came after it."""
        events = [(at, page, request) for page, recent in list(self.recent.items()) for at, request in recent]
        events += [(at, page, None) for at, page in self.opened]
        for at, page, request in sorted(events, key=lambda event: event[0]):
            if at < pending["at"] - EARLY or self.pending is not pending:
                continue
            if request is None:
                self._new_tab(page, at)
            else:
                self._step(page, request, at)
        tab = pending["tab"]
        if tab is not None and self.pending is pending:
            # Its page may have opened already, before the report came.
            if any(same(tab.url, step) for step in pending["chain"]):
                pending["committed"] = True
            asyncio.ensure_future(self._landing(tab, pending))

    def _clicked(self, source, message):
        """click-watch.js reports a click of the user's on a link of an item it marked: {token, ref, kind, href, tab,
        at}, and with `follow` the address the site's script gave the link as the click went. Only the page's own
        document carries the marks, so a report from a frame inside it (an ad's iframe) is not one."""
        frame = source.get("frame") if isinstance(source, dict) else None
        if frame is not None and frame.parent_frame is not None:
            return
        try:
            report = json.loads(message) if isinstance(message, str) else {}
            token = str(report.get("token", ""))
            capture, ref = self.tokens.get(token), int(report.get("ref"))
        except (TypeError, ValueError, AttributeError):
            return
        if capture is None:
            return
        href = link(report.get("href"))
        if report.get("follow"):
            # Not a click of its own: the address the clicked link's page opens. It counts for that click alone, as
            # long as the click's navigation has not been seen.
            pending = self._live()
            if (
                href
                and pending is not None
                and (pending["token"], pending["ref"]) == (token, ref)
                and pending["tab"] is None
                and time.monotonic() - pending["at"] <= START_SECONDS
            ):
                pending["starts"].append(href)
                self._catch_up(pending)
            return
        with self.service.db.connect() as conn:
            row = conn.execute("SELECT id FROM web_items WHERE capture_id=? AND ref=?", (capture, ref)).fetchone()
            if row is None:
                return
            # The link the page showed when it was read stays: the click's own address only fills one that could not
            # be read then. (A script of the page can send a report too; it must not rewrite what was recorded.)
            conn.execute(
                "UPDATE web_items SET clicked_at=?, landing_url='', landing_title='', "
                "link_url=CASE WHEN link_url='' THEN ? ELSE link_url END WHERE id=?",
                (now(), href, row[0]),
            )
        page = source.get("page") if isinstance(source, dict) else None
        self.pending = None
        if not href:
            return  # A link to another app or a mail address: no page of its own to record.
        self.pending = {
            "item": row[0],
            "token": token,
            "ref": ref,
            "at": time.monotonic(),
            "page": page,
            "source": page.url if page is not None else "",
            "starts": [href],
            "elsewhere": bool(report.get("tab")),
            "tab": None,
            "chain": [],
            "committed": False,
            "loaded": None,
        }
        self._catch_up(self.pending)

    # Web tasks.

    def _task(self, coroutine, seconds=TASK_SECONDS, scheduled=False):
        """Run a web task on the browser's loop, one at a time: while another runs it fails at once (BusyError), and
        it is never queued. A scheduled one, which its routine tries again until the browser is free, also stays away
        for QUIET seconds after a task ended: the user's tasks in a row go on before it and never wait for it."""
        with self.lock:
            if self.busy or (scheduled and time.monotonic() - self.freed < QUIET):
                coroutine.close()
                raise BusyError(BUSY)
            self.busy = True
        try:
            return self._call(coroutine, seconds)
        finally:
            with self.lock:
                self.busy = False
                self.freed = time.monotonic()

    async def _read(self, page, site):
        """Bring a loaded page's lazy items into view, then read it with the shared page script."""
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("load", timeout=LOAD_MS)
        await asyncio.sleep(SETTLE)
        for _ in range(SCROLLS):
            await page.evaluate("window.scrollBy(0, Math.round(innerHeight * 0.9))")
            await asyncio.sleep(SCROLL_PAUSE)
        await page.evaluate("window.scrollTo(0, 0)")
        token = uuid.uuid4().hex
        result = await page.evaluate(
            self.capture_js, {"site": site or "generic", "maxItems": MAX_ITEMS, "token": token}
        )
        shot = b""
        if isinstance(result, dict) and not result.get("blocked"):
            with contextlib.suppress(Exception):
                shot = await page.screenshot(type="jpeg", quality=55, full_page=True, timeout=20_000)
        return token, result, shot

    async def _search(self, site, query, apart=False):
        page = await (self._apart(site) if apart else self._tab(site))
        url = self.sites[site]["search"].replace("{query}", quote(query, safe=""))
        self._leave(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_MS)
        read = await self._read(page, site)
        if apart:
            self.apart[site] = (page, page.url)
        return read

    async def _visit(self, site, url):
        page = await self._tab(site or "other")
        self._leave(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_MS)
        return page.url

    async def _read_current(self):
        page = self.current
        if page is None or page.is_closed():
            raise UserError("기록할 페이지가 없습니다. 먼저 사이트를 열어 주세요.")
        return page.url, await self._read(page, self.site_of(page.url))

    def capture(self, site, query, source="chat", scheduled=False):
        """Search a site in the visible browser and record what the result page shows. A scheduled capture (the
        routine's) reads in a tab of its own (_apart) and gives way to the other web tasks (_task)."""
        site, query = self.site_key(site), text(query, "검색어", QUERY_CHARS)
        token, result, shot = self._task(self._search(site, query, apart=scheduled), scheduled=scheduled)
        return self._keep(site, query, source, token, result, shot)

    def capture_rpc(self, params):
        source = params.get("source", "manual")
        return self.capture(params.get("site"), params.get("query"), source if source in SOURCES else "manual")

    def capture_page(self, source="chat"):
        url, (token, result, shot) = self._task(self._read_current())
        return self._keep(self.site_of(url) or "other", "", source, token, result, shot)

    def capture_page_rpc(self, params):
        source = params.get("source", "manual")
        return self.capture_page(source if source in SOURCES else "manual")

    def open(self, site="", url=""):
        """Open a site's home page or an address in the visible browser, for the user to browse."""
        if site:
            site = self.site_key(site)
            url = web_url(url) if url else self.sites[site]["home"]
        else:
            url = web_url(url)
            site = self.site_of(url)
        opened = self._task(self._visit(site, url))
        return {"url": link(opened) or url, "site": site, "name": self.sites.get(site, {}).get("name", "")}

    def open_rpc(self, params):
        return self.open(params.get("site") or "", params.get("url") or "")

    def close(self):
        async def stop():
            context, playwright = self.context, self.playwright
            self.context = self.playwright = self.current = self.pending = None
            self.tabs.clear()
            self.apart.clear()
            self.opened.clear()
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
            if playwright is not None:
                with contextlib.suppress(Exception):
                    await playwright.stop()

        if self.loop and self.thread and self.thread.is_alive():
            with contextlib.suppress(UserError):
                self._call(stop(), 20)
        return {"closed": True}

    def shutdown(self):
        self.close()
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)

    # Records.

    def _keep(self, site, query, source, token, result, shot):
        if not isinstance(result, dict):
            raise UserError("페이지를 읽지 못했습니다. 페이지가 다 열린 뒤 다시 시도해 주세요.")
        if result.get("blocked"):
            raise UserError(BLOCKED)
        items = [item for item in result.get("items") or [] if isinstance(item, dict)][:MAX_ITEMS]
        capture, stamp = uuid.uuid4().hex, now()
        screenshot = ""
        if shot:
            relative = Path("web-captures") / stamp[:7] / f"{capture}.jpg"
            try:
                path = self.folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(shot)
                screenshot = relative.as_posix()
            except OSError:
                diagnostics.LOG.warning("웹 기록 스크린샷을 저장하지 못했습니다.", exc_info=True)
        rows, counts = [], {kind: 0 for kind in KINDS}
        for index, item in enumerate(items):
            kind = item.get("kind") if item.get("kind") in KINDS else "result"
            counts[kind] += 1
            ref = item.get("ref") if isinstance(item.get("ref"), int) else index
            rows.append(
                (
                    capture,
                    ref,
                    kind,
                    index + 1,
                    clip(item.get("title"), 300),
                    clip(item.get("price"), 60),
                    clip(item.get("displayUrl"), 300),
                    link(item.get("link")),
                    link(item.get("image")),
                    clip(item.get("text"), 500),
                )
            )
        with self.service.db.connect() as conn:
            conn.execute(
                "INSERT INTO web_captures(id,site,query,url,title,source,ad_count,product_count,result_count,"
                "screenshot,captured_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    capture,
                    site,
                    query,
                    link(result.get("url")),
                    clip(result.get("title"), 300),
                    source,
                    counts["ad"],
                    counts["product"],
                    counts["result"],
                    screenshot,
                    stamp,
                ),
            )
            conn.executemany(
                "INSERT INTO web_items(capture_id,ref,kind,position,title,price,display_url,link_url,image_url,snippet)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        with self.lock:
            self.tokens[token] = capture
            while len(self.tokens) > MAX_TOKENS:
                self.tokens.pop(next(iter(self.tokens)))
        self._trim()
        name = self.sites.get(site, {}).get("name", site)
        return {
            "capture_id": capture,
            "site": site,
            "name": name,
            "query": query,
            "url": link(result.get("url")),
            "counts": counts,
            "ads": [clip(item.get("title"), 80) for item in items if item.get("kind") == "ad"][:5],
            "products": [clip(item.get("title"), 80) for item in items if item.get("kind") == "product"][:5],
            "screenshot": bool(screenshot),
        }

    def _trim(self):
        with self.service.db.connect() as conn:
            old = conn.execute(
                "SELECT id, screenshot FROM web_captures ORDER BY captured_at DESC LIMIT -1 OFFSET ?", (MAX_CAPTURES,)
            ).fetchall()
            for ident, _ in old:
                conn.execute("DELETE FROM web_captures WHERE id=?", (ident,))
        for _, screenshot in old:
            if screenshot:
                with contextlib.suppress(OSError):
                    (self.folder / screenshot).unlink()

    @staticmethod
    def _filters(params):
        """The WHERE clause and its arguments of a records query: site, kind ('clicked' for the items clicked), the
        words of a title, snippet or search, the capture, and a period (since/until, ISO dates)."""
        where, args = ["1=1"], []
        site = params.get("site") or ""
        if site:
            where.append("c.site=?")
            args.append(text(site, "사이트", 40))
        kind = params.get("kind") or ""
        if kind == "clicked":
            where.append("i.clicked_at IS NOT NULL")
        elif kind:
            if kind not in KINDS:
                raise UserError("종류를 확인해 주세요.")
            where.append("i.kind=?")
            args.append(kind)
        words = text(params.get("text") or "", "검색어", 100, required=False)
        if words:
            where.append("(i.title LIKE ? OR i.snippet LIKE ? OR c.query LIKE ?)")
            args += [f"%{words}%"] * 3
        if params.get("capture_id"):
            where.append("c.id=?")
            args.append(text(params["capture_id"], "기록 ID", 80))
        for key, operator in (("since", ">="), ("until", "<")):
            value = params.get(key) or ""
            if value:
                where.append(f"c.captured_at {operator} ?")
                args.append(moment(value, last=key == "until"))
        return " AND ".join(where), args

    @staticmethod
    def _page(params):
        try:
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
        except (TypeError, ValueError):
            raise UserError("목록 범위를 확인해 주세요.") from None
        return max(0, offset), min(MAX_PAGE, max(1, limit))

    def records(self, params):
        where, args = self._filters(params)
        offset, limit = self._page(params)
        with self.service.db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM web_items i JOIN web_captures c ON c.id=i.capture_id WHERE {where}", args
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT i.id, c.id, c.site, c.query, c.captured_at, i.kind, i.position, i.title, i.price,"
                " i.display_url, i.link_url, i.image_url, i.snippet, i.clicked_at, i.landing_url, i.landing_title"
                f" FROM web_items i JOIN web_captures c ON c.id=i.capture_id WHERE {where}"
                " ORDER BY c.captured_at DESC, i.position LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        keys = (
            "id capture_id site query captured_at kind position title price display_url link_url image_url snippet"
            " clicked_at landing_url landing_title"
        ).split()
        return {"total": total, "items": [dict(zip(keys, row, strict=True)) for row in rows]}

    def captures(self, params):
        offset, limit = self._page(params)
        site = params.get("site") or ""
        with self.service.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, site, query, url, title, source, ad_count, product_count, result_count, screenshot,"
                " captured_at FROM web_captures WHERE (?='' OR site=?) ORDER BY captured_at DESC LIMIT ? OFFSET ?",
                (site, site, limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM web_captures WHERE (?='' OR site=?)", (site, site)).fetchone()[0]
        keys = "id site query url title source ad_count product_count result_count screenshot captured_at".split()
        return {"total": total, "captures": [dict(zip(keys, row, strict=True)) for row in rows]}

    def capture_get(self, params):
        ident = text(params.get("id"), "기록 ID", 80)
        with self.service.db.connect() as conn:
            row = conn.execute(
                "SELECT id, site, query, url, title, source, ad_count, product_count, result_count, screenshot,"
                " captured_at FROM web_captures WHERE id=?",
                (ident,),
            ).fetchone()
        if row is None:
            raise UserError("기록을 찾을 수 없습니다.")
        keys = "id site query url title source ad_count product_count result_count screenshot captured_at".split()
        found = dict(zip(keys, row, strict=True))
        found["items"] = self.records({"capture_id": ident, "limit": MAX_PAGE})["items"]
        return found

    def delete_capture(self, params):
        """Move one recorded page with its items to the trash (trash.py), where it waits 30 days for the user to restore
        it, its screenshot kept until then: the screen asks first. The web records are the user's alone, and so is
        their entry there (an AI tool does not meet it)."""
        ident = text(params.get("id"), "기록 ID", 80)
        with self.service.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            moved = self.service.trash.discard_many(conn, "web_capture", [ident])
        if not moved:
            raise UserError("기록을 찾을 수 없습니다.")
        return {"deleted": ident, "trash_ids": moved}

    def delete_captures(self, params):
        """Move the recorded pages chosen together (`ids`, 1~MAX_DELETE) to the trash with their items, at once: what
        is gone meanwhile is passed over. How many went, and their entries for 되돌리기 (trash.restore)."""
        ids = params.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DELETE:
            raise UserError(f"지울 기록을 1~{MAX_DELETE:,}개 골라 주세요.")
        ids = [text(ident, "기록 ID", 80) for ident in ids]
        with self.service.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            moved = self.service.trash.discard_many(conn, "web_capture", ids)
        return {"deleted": len(moved), "trash_ids": moved}

    def screenshot(self, params):
        """The screenshot of one recorded page, as the data: URL the screen shows it by (its policy lets pictures come
        from the app or data: URLs only). Only a JPEG file of the screenshots folder is read: a record never names
        another one."""
        ident = text(params.get("id"), "기록 ID", 80)
        with self.service.db.connect() as conn:
            row = conn.execute("SELECT screenshot FROM web_captures WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise UserError("기록을 찾을 수 없습니다.")
        if not row[0]:
            raise UserError("이 기록에는 스크린샷이 없습니다. 페이지를 읽을 때 찍지 못했습니다.")
        unreadable = "스크린샷 파일을 읽지 못했습니다. Wiki 폴더의 web-captures에서 지워졌거나 손상되었을 수 있습니다."
        shot = (self.folder / row[0]).resolve()
        try:
            if not shot.is_relative_to((self.folder / "web-captures").resolve()):
                raise UserError(unreadable)
            if shot.stat().st_size > MAX_SHOT:
                raise UserError("스크린샷이 너무 커서 열지 못했습니다. Wiki 폴더의 web-captures에서 직접 여세요.")
            data = shot.read_bytes()
        except OSError:
            raise UserError(unreadable) from None
        if not data.startswith(JPEG):
            raise UserError(unreadable)
        return {"data": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}

    def retention(self, _params=None):
        """How many days the user keeps the records: {"days"}, 0 while they are kept until deleted."""
        with self.service.db.connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='web_retention'").fetchone()
        try:
            days = json.loads(row[0])["days"] if row else 0
        except (ValueError, KeyError, TypeError):
            days = 0
        return {"days": days if type(days) is int and 0 <= days <= MAX_KEPT_DAYS else 0}

    def retention_save(self, params):
        """Keep the records `days` days (0: until deleted). The retention policy then moves those older to the trash
        once a day (Service.retain), where they wait 30 days more with their screenshots."""
        days = params.get("days")
        if type(days) is not int or not 0 <= days <= MAX_KEPT_DAYS:
            raise UserError(f"보관 기간(days)은 0(계속 보관)이나 1~{MAX_KEPT_DAYS:,}일로 입력해 주세요.")
        with self.service.db.connect() as conn:
            conn.execute(
                "INSERT INTO metadata VALUES ('web_retention', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps({"days": days}),),
            )
        return {"days": days}

    def forget_screenshots(self, paths):
        """Remove the screenshot files `paths` names (relative to the Wiki folder, as the records keep them) once their
        records are gone for good. Only a file of the screenshots folder: a record never names another one, and one
        that does (a damaged row) leaves that file alone."""
        folder = (self.folder / "web-captures").resolve()
        for relative in paths:
            shot = (self.folder / relative).resolve()
            if shot.is_relative_to(folder):
                try:
                    shot.unlink(missing_ok=True)
                except OSError:
                    diagnostics.LOG.warning("웹 기록 스크린샷을 지우지 못했습니다.", exc_info=True)

    def export(self, params):
        """Write the records the filters select to a CSV file (UTF-8 with BOM, which Excel reads) in the Wiki folder's
        exports folder, and answer its path."""
        where, args = self._filters(params)
        with self.service.db.connect() as conn:
            rows = conn.execute(
                "SELECT c.captured_at, c.site, c.query, i.kind, i.position, i.title, i.price, i.display_url,"
                " i.link_url, i.clicked_at, i.landing_url, i.landing_title"
                f" FROM web_items i JOIN web_captures c ON c.id=i.capture_id WHERE {where}"
                " ORDER BY c.captured_at DESC, i.position LIMIT ?",
                [*args, MAX_EXPORT],
            ).fetchall()
        if not rows:
            raise UserError("내보낼 기록이 없습니다. 조건을 바꿔 다시 시도해 주세요.")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([name for _, name in COLUMNS])
        for row in rows:
            values = list(row)
            values[1] = self.sites.get(values[1], {}).get("name", values[1])
            values[3] = KIND_NAMES.get(values[3], values[3])
            # A cell that starts like a formula would run as one in a spreadsheet: it is kept as text.
            writer.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in values])
        folder = self.folder / "exports"
        folder.mkdir(exist_ok=True)
        path = folder / f"web-records-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        path.write_text("﻿" + buffer.getvalue(), encoding="utf-8", newline="")
        return {"path": str(path), "rows": len(rows)}

    def summary(self, params):
        """A short account of the records the filters select, for the chat: counts by kind and the newest titles."""
        _, limit = self._page({"limit": params.get("limit") or 10})
        listed = self.records({**params, "offset": 0, "limit": min(20, limit)})
        counts = {kind: 0 for kind in (*KINDS, "clicked")}
        where, args = self._filters(params)
        with self.service.db.connect() as conn:
            for kind, count in conn.execute(
                f"SELECT i.kind, COUNT(*) FROM web_items i JOIN web_captures c ON c.id=i.capture_id WHERE {where}"
                " GROUP BY i.kind",
                args,
            ):
                counts[kind] = count
            counts["clicked"] = conn.execute(
                f"SELECT COUNT(*) FROM web_items i JOIN web_captures c ON c.id=i.capture_id WHERE {where}"
                " AND i.clicked_at IS NOT NULL",
                args,
            ).fetchone()[0]
        return {"total": listed["total"], "counts": counts, "items": listed["items"]}
