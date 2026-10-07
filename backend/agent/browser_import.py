"""Python Playwright sessions of the importers that sign in to configured sites (Confluence, JIRA).

Every browser of the app runs here: each importer keeps a Chromium profile per site, so settings such as a sign-in
prompt's "always allow" last, and runs one browser at a time: its background job, its visible login window or a login
check. A profile goes once the last site account that uses it is deleted (forget). Electron keeps the site accounts
and sends the site's login plan (web_login.py) with each request. The importers add what they collect; this module is
what they share. With the LLM connected and the option on, a job or a login check that cannot open the site or sign
in gets the LLM's help and its analysis of why (web_assist.py).

A site address saved without its scheme (portal.example.com) is opened as the browser's address bar opens it: over https, or
over http when https does not answer. The scheme found is kept for the host while the app runs.
"""
import asyncio
import contextlib
import hashlib
import os
import shutil
import sys
import threading
import traceback
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from . import diagnostics
from .llm import BusyError, UserError
from .service import text
from .web_assist import Assist, error_page
from .web_login import has_scheme, origin_of, place_of, redirected, validate_plan, with_scheme

ACTIVE = ("scanning", "login", "running", "cancelling")
BUSY_ERROR = "진행 중인 가져오기 또는 로그인 창을 먼저 닫아 주세요."
CHECK_FAILED = "로그인을 확인하지 못했습니다. 주소와 네트워크 연결을 확인해 주세요."
CHECK_STOPPED = "로그인 확인을 중지했습니다."
BROWSER_ARGS = ["--disable-notifications", "--no-sandbox", "--disable-setuid-sandbox"]
# On some PCs a browser start now and then fails between starts that work, likely interrupted by security software;
# such a start is tried once more after a pause.
START_ATTEMPTS = 2
START_PAUSE = 2  # Seconds
# Jobs Electron may still read by ID after the next one started: it keeps how each job signed in for the site.
RECENT_JOBS = 8
# A site address without its scheme: how long its try over https, then over http, may take to get an answer.
REACH_SECONDS = 15


def use_shared_browsers():
    """A frozen backend looks for browsers inside its own bundle by default. Use Playwright's
    shared cache instead unless Electron points to the packaged browsers."""
    if not getattr(sys, "frozen", False) or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    if sys.platform == "win32":
        cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")


class Worker:
    """One Playwright session on its own thread and event loop, cancellable from RPC threads."""

    def __init__(self, work, name="site-browser"):
        self.cancelled = False
        ready, failures = threading.Event(), []

        async def main():
            self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
            ready.set()
            await work()

        def run():
            try:
                asyncio.run(main())
            except BaseException as error:  # The work reports its own outcome.
                failures.append(error)
            finally:
                ready.set()

        self.thread = threading.Thread(target=run, name=name, daemon=True)
        self.thread.start()
        ready.wait()
        if not hasattr(self, "task"):
            # No event loop, e.g. security software refused the local socket pair Windows builds it on: the work never
            # ran, so it could not report anything.
            reason = f" ({first_line(failures[0])})" if failures else ""
            raise UserError("브라우저 작업을 시작하지 못했습니다. 백신이나 보안 프로그램이 앱을 막고 있는지 확인한 뒤 "
                            f"앱을 다시 실행해 주세요.{reason}")

    def cancel(self, again=False):
        """Stop the work. A second cancel would interrupt the browser cleanup, so only `again` sends one: to a worker
        whose cleanup hangs, e.g. a browser that does not close, which then goes on to stop Playwright's driver, and
        the browser with it."""
        if self.cancelled and not again:
            return
        self.cancelled = True
        try:
            self.loop.call_soon_threadsafe(self.task.cancel)
        except (AttributeError, RuntimeError):
            pass  # Already finished.


def first_line(error):
    """Playwright's first line says why, e.g. a missing browser or a profile another window still uses."""
    return " ".join(str(error).split("\n", 1)[0].split())[:200]


async def open_page(page, url, product, assist=None):
    """Open a page of the site; a client-side redirect, e.g. to the SSO page, may replace the navigation. With the
    LLM's help, a page that does not open may be opened once more (web_assist.Assist.reconnect)."""
    try:
        return await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as error:
        if redirected(error):
            return None
        failure = UserError(f"{product}에 접속하지 못했습니다. 링크와 네트워크 연결을 확인해 주세요.")
        if assist is None:
            raise failure from error
        return await assist.reconnect(page, url, error, failure)


async def reached(page, url, seconds):
    """The address the browser came to opening `url`, once the site answered within `seconds`: with its page, a redirect
    or an error status, as the address bar takes an answer. None when it did not answer in time or the connection, its
    certificate or the name failed; the browser's error page, which shows a moment later, is waited for, so it does not
    interrupt the next page opened."""
    try:
        await page.goto(url, wait_until="commit", timeout=seconds * 1000)
    except Exception as error:
        if not redirected(error):
            await error_page(page)
            return None
    return page.url


async def close_popup(page, popup):
    """Background work never keeps extra windows."""
    if popup != page:
        with contextlib.suppress(Exception):
            await popup.close()


class BrowserImporter:
    """The browser, login window and login check of one importer. Subclasses name the product, start the jobs
    and say how a login window opens and a login check signs in."""

    product = ""  # The site's name in messages: Confluence, JIRA.
    site_error = ""  # A site address that is not an http(s) link.
    profiles_variable = profiles_folder = ""  # Where the browser profiles live: Electron's setting, or a default.

    def __init__(self, service):
        self.service = service
        # Electron keeps the profiles in its userData folder, apart from the Wiki folder.
        self.profile_root = Path(os.environ.get(self.profiles_variable)
                                 or Path.home() / ".private-agent" / self.profiles_folder)
        self.lock = threading.RLock()
        self.job = None
        self.recent = {}  # The last RECENT_JOBS jobs by ID, the current one included.
        self.job_worker = None
        self.login_worker = None
        self.login_open = False
        self.login_error = ""
        # How the last login window signed in, for Electron to save the site's login method.
        self.login_result = None
        self.check_worker = None
        self.checking = False
        # Login checks out of time whose browser had not closed when they answered, as (worker, done): no browser of
        # the importer starts beside one, and each is stopped once more before the next check and at the app's exit.
        self.lingering = []
        # The browser profiles of deleted site accounts, deleted once no browser of the importer runs (forget).
        self.forgotten = set()
        self.sweeping = False
        # Seconds; tests shorten them.
        self.optional_seconds = 3
        self.form_seconds = 30
        self.login_seconds = 100
        # A login check answers within Electron's RPC time limit (900 seconds), the LLM's help included: a check out
        # of time gets close_seconds for its browser to close, then as much again once stopped harder.
        self.check_seconds = 720
        self.close_seconds = 20
        self.login_headless = False
        self.reach_seconds = REACH_SECONDS
        self.schemes = {}

    # -- RPC ----------------------------------------------------------------

    def status(self, params=None):
        """The current job, or with `id` the recent job of that ID, and the login window."""
        with self.lock:
            ident = params.get("id") if isinstance(params, dict) else None
            job = self.recent.get(ident, self.job) if isinstance(ident, str) else self.job
            return {"job": deepcopy(job), "login_open": self.login_open, "login_error": self.login_error,
                    "login_result": deepcopy(self.login_result)}

    def cancel(self, _=None):
        with self.lock:
            if self.job and self.job["status"] in ACTIVE:
                self.job["status"] = "cancelling"
                self.job_worker.cancel()
            return self.status()

    def dismiss(self, params):
        """The user cleared the finished job `id` from the import box: every screen leaves it out from now on.
        The job itself stays, as Electron may still read the login method it detected."""
        with self.lock:
            job = self.job
            if job and job["id"] == params.get("id") and job["status"] not in ACTIVE:
                job["dismissed"] = True
            return self.status()

    def login(self, params):
        """Visible window on any page of the site. With a login plan it signs in the same way as the background,
        so the user can approve a sign-in prompt once; without one the user signs in by hand."""
        url, origin = self._site(params)
        login = validate_plan(params.get("login"))
        with self.lock:
            if self._busy():
                raise BusyError(BUSY_ERROR)
            self.login_open, self.login_error, self.login_result = True, "", None
            try:
                self.login_worker = Worker(lambda: self._login_window(url, origin, login))
            except BaseException:
                self.login_open = False
                raise
            return self.status()

    def login_close(self, _=None):
        if worker := self.login_worker:
            worker.cancel()
            worker.thread.join(20)
        return self.status()

    def check(self, params):
        """Sign in once in the background with the site's login plan and report the method that worked, or with ok
        False why it did not, with the LLM's analysis as `diagnosis` when it made one. A check the user stopped
        (check_cancel) answers with `stopped`, as soon as its browser has closed."""
        url, origin = self._site(params)
        if params.get("login") is None:
            raise UserError("로그인 정보를 저장한 뒤 확인해 주세요.")
        login = validate_plan(params.get("login"))
        outcome, done = {}, threading.Event()
        self._settle()
        with self.lock:
            if self._busy():
                raise BusyError(BUSY_ERROR)
            self.checking = True
            try:
                worker = self.check_worker = Worker(lambda: self._check(url, origin, login, outcome, done))
            except BaseException:
                self.checking = False
                raise
        try:
            if not done.wait(self.check_seconds):
                outcome["error"] = "로그인 확인 시간이 초과되었습니다. 주소를 확인하거나 ‘로그인 창 열기’에서 직접 확인해 주세요."
                worker.cancel()
                if not done.wait(self.close_seconds):
                    # The browser does not close: its cleanup is stopped too, which stops Playwright's driver.
                    worker.cancel(again=True)
                    if not done.wait(self.close_seconds):
                        with self.lock:
                            self.lingering.append((worker, done))
        finally:
            with self.lock:
                self.checking, self.check_worker = False, None
        diagnosis = {"diagnosis": outcome["diagnosis"]} if outcome.get("diagnosis") else {}
        if "result" not in outcome:
            if outcome.get("stopped"):
                return {"ok": False, "stopped": True, "message": CHECK_STOPPED}
            return {"ok": False, "message": outcome.get("error") or CHECK_FAILED, **diagnosis}
        result = outcome["result"]
        # Methods tried before the one that signed in, e.g. an SSO login that refused the account.
        tried = f" (먼저 시도한 방식: {', '.join(result['tried'])})" if result.get("tried") else ""
        return {"ok": True, **result, "message": f"로그인했습니다 · {result['label']}{tried}" if result["label"]
                else "로그인이 필요 없거나 이미 로그인되어 있습니다.", **diagnosis}

    def check_cancel(self, _=None):
        """The user stops the running login check: its browser closes, and check() answers that it was stopped."""
        with self.lock:
            if worker := self.check_worker:
                worker.cancel()
            return {"stopping": bool(worker)}

    def shutdown(self, _=None):
        """App exit: stop the job, the login window and a login check, and let their browsers close; a check out of time
        whose browser has not closed is stopped once more."""
        self.cancel()
        workers = [worker for worker in (self.job_worker, self.login_worker, self.check_worker) if worker]
        for worker in workers:
            worker.cancel()
        lingering = [worker for worker, _ in self._lingering()]
        for worker in lingering:
            worker.cancel(again=True)
        for worker in workers + lingering:
            worker.thread.join(10)
        self._lingering()
        return {"ok": True}

    def forget(self, params):
        """The last site account of a browser profile was deleted (Electron): the profile of the site at `url`, with the
        sessions and settings it kept, is deleted now, or before the next browser of the importer starts when one runs
        now. `removed` says whether it went now."""
        _, origin = self._site(params)
        profile = self._profile(origin)
        with self.lock:
            self.forgotten.add(profile)
            if self._busy():
                return {"removed": False}
            self.sweeping = True
        try:
            self._sweep()
        finally:
            with self.lock:
                self.sweeping = False
        return {"removed": not profile.exists()}

    # -- helpers --------------------------------------------------------------

    def _busy(self):
        return bool(self.job and self.job["status"] in ACTIVE) or self.login_open or self.checking \
            or bool(self._lingering()) or self.sweeping

    def _lingering(self):
        """The login checks out of time whose browser has not closed yet, as (worker, done)."""
        with self.lock:
            self.lingering = [(worker, done) for worker, done in self.lingering if not done.is_set()]
            return list(self.lingering)

    def _settle(self):
        """Before a login check: the checks out of time whose browser had not closed are stopped once more, and each
        gets close_seconds to close."""
        for worker, done in self._lingering():
            worker.cancel(again=True)
            done.wait(self.close_seconds)

    def _sweep(self):
        """Delete the profiles forget() keeps; one that cannot go now, e.g. a file another program holds, stays for the
        next time. Called while no browser of the importer runs."""
        with self.lock:
            profiles, self.forgotten = self.forgotten, set()
        left = set()
        for profile in profiles:
            with contextlib.suppress(OSError):
                shutil.rmtree(profile)
            if profile.exists():
                left.add(profile)
        with self.lock:
            self.forgotten |= left

    def _begin(self, job, work, name):
        """Run `job` with work() in a browser worker of its own, as the current job. Called with self.lock held."""
        self.job = job
        self.recent[job["id"]] = job
        while len(self.recent) > RECENT_JOBS:
            del self.recent[next(iter(self.recent))]
        try:
            self.job_worker = Worker(work, name)
        except BaseException:
            self.job = None
            del self.recent[job["id"]]
            raise

    def _site(self, params):
        """Any page of the site, e.g. the site address saved for a login, and its origin. An address without its scheme
        stays so, with its origin over https, which also names its browser profile; the browser finds the scheme it
        opens with (_reach)."""
        url = text(params.get("url"), f"{self.product} 주소", 4096)
        origin = origin_of(with_scheme(url))
        if not origin:
            raise UserError(self.site_error)
        return url, origin

    async def _reach(self, page, url):
        """The site address `url` as the browser opens it, and its origin. One without its scheme opens as in the
        address bar: over https, or over http when https does not answer or the site sends the browser on to http. The
        scheme found is kept for the host; when neither answers, https stays, and the steps after this fail as for any
        address that does not open."""
        if has_scheme(url):
            return url, origin_of(url)
        host = urlsplit(with_scheme(url)).netloc.lower()
        scheme = self.schemes.get(host)
        if scheme is None:
            for tried in ("https", "http"):
                address = await reached(page, with_scheme(url, tried), self.reach_seconds)
                if address is not None:
                    scheme = self.schemes[host] = "http" if "http" in (tried, place_of(address)[0]) else "https"
                    break
        url = with_scheme(url, scheme or "https")
        return url, origin_of(url)

    def _update(self, job, **values):
        with self.lock:
            if job["status"] == "cancelling":
                values.pop("status", None)
            job.update(values)

    def _timeouts(self):
        return {"optional_seconds": self.optional_seconds, "form_seconds": self.form_seconds,
                "login_seconds": self.login_seconds}

    def _assistant(self, page, login, update):
        """The LLM's help in this browser (web_assist.Assist), watching `page`, when an LLM is connected and the
        option is on; None otherwise. `update(**values)` takes its notice and the analysis of a page it opened again."""
        try:
            if not self.service.assist_on():
                return None
        except Exception:  # The settings cannot be read now: work as without the option.
            return None
        return Assist(self.service.llm, self.product, login["credentials"] if login else None, update).watch(page)

    @contextlib.asynccontextmanager
    async def _explaining(self, record, assist, page, stage):
        """Steps that open the site and sign in. When one fails, the LLM's analysis of why goes to record["diagnosis"]
        (a job, or a login check's outcome): the one made when a page did not open, or a new one of `stage`, a name or a
        function that gives one at the time."""
        try:
            yield
        except Exception as error:
            if assist is not None:
                found = getattr(error, "diagnosis", None) or await assist.diagnose(
                    page, stage() if callable(stage) else stage, str(error) if isinstance(error, UserError) else "",
                    error)
                with self.lock:
                    record["diagnosis"] = found
            raise

    @contextlib.asynccontextmanager
    async def _browser(self, origin, headless):
        use_shared_browsers()
        if self.forgotten:  # No other browser of the importer runs while this one starts.
            await asyncio.to_thread(self._sweep)
        profile = self._profile(origin)
        for attempt in range(1, START_ATTEMPTS + 1):
            try:
                playwright, context = await self._start(profile, headless)
                break
            except UserError:
                if attempt == START_ATTEMPTS:
                    raise
                # The user sees nothing of a start that works the second time; the log keeps why the first failed.
                diagnostics.logged(f"{self.product} 브라우저 시작 (다시 시도함)", traceback.format_exc())
                await asyncio.sleep(START_PAUSE)
        try:
            try:
                yield context
            finally:
                with contextlib.suppress(Exception):
                    await context.close()
        finally:
            with contextlib.suppress(Exception):
                await playwright.stop()

    async def _start(self, profile, headless):
        """Playwright's driver and the browser, as (playwright, context). A failure leaves neither running and is a
        UserError with Playwright's reason."""
        from playwright.async_api import async_playwright
        playwright = None
        try:
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            playwright = await async_playwright().start()
            return playwright, await self._launch(playwright, profile, headless)
        except BaseException as error:  # Cancelled too: the driver stops either way.
            if playwright is not None:
                with contextlib.suppress(Exception):
                    await playwright.stop()
            if isinstance(error, UserError) or not isinstance(error, Exception):
                raise
            raise UserError(f"{self.product} 브라우저를 시작하지 못했습니다: {first_line(error)} 앱을 다시 실행해 주세요. "
                            "개발 환경에서는 npm run setup:python과 npm run setup:browser를 실행해 주세요.") from error

    def _profile(self, origin):
        # One profile per site, so an existing profile keeps settings such as a sign-in prompt's "always allow".
        return self.profile_root / hashlib.sha256(origin.encode()).hexdigest()

    async def _launch(self, playwright, profile, headless):
        # Full Chromium also for new-headless background runs; no browser downloads.
        return await playwright.chromium.launch_persistent_context(
            str(profile), channel="chromium", headless=headless, accept_downloads=False, args=BROWSER_ARGS,
            permissions=["notifications"], viewport={"width": 1920, "height": 1080})

    async def _login_window(self, url, origin, login):
        try:
            async with self._browser(origin, headless=self.login_headless) as context:
                closed = asyncio.Event()
                context.on("close", lambda _: closed.set())
                page = context.pages[0] if context.pages else await context.new_page()
                url, origin = await self._reach(page, url)
                helper = await self._prepare_login(context, page, url, origin, login)
                try:
                    await closed.wait()
                finally:
                    if helper:
                        helper.cancel()
        except asyncio.CancelledError:
            pass
        except UserError as error:
            with self.lock:
                self.login_error = str(error)
        except Exception:
            with self.lock:
                self.login_error = "로그인 창을 열지 못했습니다. 주소와 네트워크 연결을 확인해 주세요."
        finally:
            with self.lock:
                self.login_open = False

    async def _prepare_login(self, context, page, url, origin, login):
        """Show the site in the login window; may return a task that works in it until the window closes."""
        raise NotImplementedError

    async def _check(self, url, origin, login, outcome, done):
        try:
            async with self._browser(origin, headless=True) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                context.on("page", lambda popup: close_popup(page, popup))
                url, origin = await self._reach(page, url)
                # A page opened again keeps its analysis for the result; a check shows no notices.
                assist = self._assistant(page, login, lambda **values: outcome.update(
                    {key: value for key, value in values.items() if key == "diagnosis"}))
                async with self._explaining(outcome, assist, page, "로그인 확인"):
                    outcome["result"] = await self._verify(context, page, url, origin, login, assist)
        except asyncio.CancelledError:
            # Stopped by the user or the app's exit; a check out of time keeps saying so.
            if "error" not in outcome:
                outcome["stopped"] = True
        except UserError as error:
            outcome["error"] = str(error)
        except Exception:
            outcome["error"] = CHECK_FAILED
        finally:
            # The browser has closed: check() answers without waiting for an LLM answer still on its way, which
            # the worker's thread waits for before it ends.
            done.set()

    async def _verify(self, context, page, url, origin, login, assist):
        """Sign in with the plan for a login check, with the LLM's help when `assist` is given: {"method", "label",
        "detected"} as web_login.sign_in."""
        raise NotImplementedError
