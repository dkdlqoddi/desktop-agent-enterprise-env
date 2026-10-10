"""Automatic sign-in with a site's login method (Python Playwright), for every site the app signs in to: Confluence,
JIRA.

Electron keeps the sites and the login method catalog (electron/login-methods.json) and sends a login plan with each
request: the site's ID and password and the methods to use, in the order to try them: the method the user chose for
the site (or, in auto mode, the one saved for it), and standard SSO methods (priority) before the
others. A method is data, the elements that show its login screen and the steps that click, fill (the ID, the
password or a text the user wrote) and submit, so login screens with other field and button positions need no code.
The methods are tried in order until one fits the page and signs in; a login screen of another kind waits a moment
for a priority method to show (PRIORITY_SECONDS). The result names the method so Electron can save it for an auto site
and try it first next time.

A login that was sent but did not sign in (the login screen came back, or the site never arrived) does not end the
sign-in: the page is opened again and the next method that fits it is tried. Never with the very fields of a login
sent already, though: the same ID and password into the same fields would only add a failed attempt, which can lock
the account. The error of a sign-in that ends without signing in lists every method tried and how it went.

With the LLM's help (web_assist.py), a login screen that none of the methods fits gets one more chance before the
sign-in gives up: the LLM picks the screen's fields, they are filled as a user-defined method's, and the fields that
signed in come back as `learned` for Electron to keep as the site's own method. A site that does not open may be
opened once more when the LLM expects that to work.

The ID and the password are typed only into a page the sign-in may give them to (Hosts): the site's own host, the
hosts the site sent the browser on to by itself or through the login method's clicks, never a host a click the LLM
chose led to; over https, unless the site's own address is http. The password goes only into a password field, and
the ID and the user's texts only into text fields. The hosts the account was typed into besides the site's own (its
identity provider) come back as `hosts`: Electron keeps them with the site (login_hosts), and from then on the plan
names them and the account goes to no other host, whatever the site sends the browser on to (an open redirect on
the site, a changed page), until the user resets them.

A session the browser profile keeps may still be signed in, from the last run or from the login window, where a site
that asks for a second factor is signed in to by hand: the sign-in then types nothing (`session`). Only a login check
(`fresh`) always signs in anew, as it is to tell whether the account and the method work.

An address may be given without its scheme (portal.example.com, jira.example.com/jira): it is then opened over https
(with_scheme), never over http, so the account never goes over a plain connection by a fallback; a site that only
answers over http is saved with http:// written out, the user's own choice.
"""

import asyncio
import contextlib
import re
from urllib.parse import urlsplit

from .llm import UserError

MODES = ("auto", "fixed")
MAX_METHODS, MAX_DETECT, MAX_STEPS = 12, 4, 16
MAX_HOSTS = 20  # Identity-provider hosts kept with a site (login_hosts).
# A host name (or an [IPv6] address) as a plan names the hosts the account may be typed into.
HOST = re.compile(r"[A-Za-z0-9.\-\[\]:]{1,253}")
SESSION_SECONDS = 8  # How long a page opened with the profile's session gets to show that it is signed in.
SESSION_LABEL = "저장된 로그인 세션 사용"  # What the import box shows for a sign-in that typed nothing.
MAX_TEXT = 200  # A text the user wrote for a field, e.g. a domain.
POLL = 0.25  # Seconds between checks of the page.
SETTLE = 0.5  # A login screen may still be rendering when the first method's element shows.
PRIORITY_SECONDS = 2  # Another login screen waits this long for a priority method, such as standard SSO, to show.
TRIED_SETTLE = 2  # Only an already tried login screen showing this long: no other method applies.
SUBMIT_SECONDS = 2  # Wait this long for a submit button before pressing Enter instead.
ENTER_SECONDS = 3  # Enter must start a request or replace the page within this time to count as submitting.
REJECT_SECONDS = 1  # The emptied password field must stay shown this long to count as a rejected login.
ASSIST_ROUNDS = 4  # Screens the LLM reads for one login, e.g. a notice, the SSO link, the ID, then the password.
CLICK_SECONDS = 2  # After a click the LLM chose, the page has this long to leave or redraw before it is read again.
# The method of the fields the LLM found, as sites.cjs names it.
LEARNED, LEARNED_LABEL = "learned", "AI가 찾은 로그인 화면"
# Something to fill or click: a page that shows one has rendered enough to be read.
READY = 'input:not([type="hidden"]), button, a[href], [role="button"]'
FIELDS = {"id": "ID 입력란", "password": "비밀번호 입력란", "text": "글자 입력란"}
# The fields each value may be typed into: input types, as PAGE_STATE (web_assist.py) tells text fields.
KINDS = {
    "id": ("text", "email", "tel", "number"),
    "password": ("password",),
    "text": ("text", "email", "tel", "number", "search", "url", "textarea"),
}
PLAN_ERROR = "사이트 로그인 정보 형식이 올바르지 않습니다."
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# JIRA and Confluence name the signed-in user in these page metadata (Server/Data Center and Cloud).
SIGNED_IN = """() => ['ajs-remote-user', 'ajs-atlassian-account-id'].some((name) =>
    !!document.querySelector(`meta[name="${name}"]`)?.getAttribute('content')?.trim())"""
# A field to type into: the address of its page, its kind (an input's type, else its tag) and its mark, which tells it
# from another field at the same place, e.g. a one-time code's where the password was (its kind and its name or id).
FIELD = """(element) => {
  const kind = element.localName === 'input' ? element.type : element.localName;
  return {url: element.ownerDocument.URL, kind, mark: [kind, element.getAttribute('name') || element.id || '']};
}"""
# Where a click on an element takes the browser by itself: the address of its link, or of the form it sends.
LEADS = """(element) => {
  const link = element.closest('a[href], area[href]');
  if (link) return link.href;
  const control = element.closest('button, input');
  return control && control.form && ['submit', 'image'].includes(control.type) ? control.formAction : '';
}"""
ARRIVED = object()
# An address that names its scheme, e.g. https://…, or ftp://… which no importer opens.
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# An address without one, as the address bar takes it: a host (a name, an IPv4 address or an [IPv6] one), its port, then
# its path, without spaces or backslashes. A Windows path (C:\…, \\server\…), mailto:… or about:blank is none.
BARE = re.compile(r"(?://)?(?:[\w-]+(?:\.[\w-]+)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?(?:[/?#][^\s\\]*)?")


class LoginError(UserError):
    """A sign-in failure the user can act on. Never contains the ID or the password. `sent`: the ID and password had
    been submitted, so trying again by itself, e.g. on a schedule, may count as one more failed login."""

    def __init__(self, message, sent=False):
        super().__init__(message)
        self.sent = sent


class StepError(Exception):
    """A required element of a method did not show: the page has another kind of login screen."""

    def __init__(self, what):
        super().__init__(what)
        self.what = what


class Repeated(Exception):
    """A method filled the very fields of a login sent before that did not sign in: sending them again would only add
    a failed attempt."""


class Elsewhere(Exception):
    """A click the LLM chose would take the browser to a host the account must not be typed into."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host


def has_scheme(value):
    return isinstance(value, str) and bool(SCHEME.match(value.strip()))


def with_scheme(value, scheme="https"):
    """An address as a URL: one given without a scheme (portal.example.com, also //portal.example.com) gets `scheme`, by
    default https, the only scheme a site address without one is opened over. Anything else comes back as it is: a URL,
    or a value that is no address."""
    if not isinstance(value, str) or has_scheme(value) or not BARE.fullmatch(value.strip()):
        return value
    return f"{scheme}://{value.strip().removeprefix('//')}"


def origin_of(value):
    """scheme://host[:port] of an http(s) URL, as parse_issue_url computes it, or None."""
    try:
        url = urlsplit(value.strip()) if isinstance(value, str) and len(value) <= 4096 else None
        port = url.port if url else None
    except ValueError:
        return None
    scheme = url.scheme.lower() if url else ""
    if scheme not in ("http", "https") or not url.hostname or "@" in url.netloc:
        return None
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    return f"{scheme}://{host}" + (f":{port}" if port and port != {"http": 80, "https": 443}[scheme] else "")


def page_of(value):
    """An address without its query and fragment, which change with each sign-in (e.g. an SSO request): which page a
    field is on."""
    try:
        url = urlsplit(value) if isinstance(value, str) else None
    except ValueError:
        return ""
    return f"{url.scheme.lower()}://{url.netloc.lower()}{url.path}" if url else ""


def place_of(value):
    """(scheme, host) of an http(s) address, lower case; ("", "") for anything else, e.g. about:blank."""
    try:
        url = urlsplit(value) if isinstance(value, str) else None
        host = (url.hostname or "") if url else ""
    except ValueError:
        return "", ""
    scheme = url.scheme.lower() if url else ""
    return (scheme, host.lower()) if scheme in ("http", "https") and host else ("", "")


# ---------------------------------------------------------------------------
# Where the account may be typed
# ---------------------------------------------------------------------------


class Hosts:
    """The hosts one sign-in may type the account into, as it follows the page: the host of the address it opens (the
    site's own), every host the browser goes on to without a click the LLM chose (the site's redirects, form posts and
    scripts, and the clicks of the login methods, e.g. JIRA → AD SSO → ADFS), the hosts a click the LLM chose leads to
    through the site's own redirects. A host the LLM's click took the browser to by itself is none of them: the page may
    have misled the LLM, e.g. with a link to another site that says "로그인". The account goes only to https pages,
    unless the site's own address is http (the user's choice). With `pinned` hosts (the site's login_hosts, learned from
    the first logins) the account goes only to the site's own host and those, however the browser got to another: `used`
    are the hosts it was typed into this time."""

    def __init__(self, page, url, pinned=()):
        scheme, self.home = place_of(url)
        self.secure = scheme == "https"
        self.known = {self.home} if self.home else set()
        self.pinned = {host.lower() for host in pinned}
        self.used = set()
        self.page, self.clicking = page, False
        # The navigations a click the LLM chose started toward another host, and the redirects that followed them.
        self.misled = set()
        page.on("request", self._request)

    def close(self):
        with contextlib.suppress(Exception):
            self.page.remove_listener("request", self._request)

    def trusts(self, host):
        return host in self.known and (not self.pinned or host == self.home or host in self.pinned)

    def _request(self, request):
        with contextlib.suppress(Exception):  # A frame that went away meanwhile.
            if not request.is_navigation_request() or request.frame.parent_frame is not None:
                return
            host = place_of(request.url)[1]
            if not host:
                return
            before = request.redirected_from
            # A redirect is the server's: it counts as where the navigation it follows was going.
            misled = before in self.misled if before is not None else self.clicking and not self.trusts(host)
            if misled:
                self.misled.add(request)
            else:
                self.known.add(host)

    @contextlib.contextmanager
    def following(self):
        """While a click the LLM chose, and the navigation it starts, take place."""
        self.clicking = True
        try:
            yield
        finally:
            self.clicking = False

    def lead(self, url):
        """A click the LLM chose that would take the browser to `url` (its link, or the form it sends; "" for none):
        Elsewhere when that is a host the account must not be typed into."""
        host = place_of(url)[1]
        if host and not self.trusts(host):
            raise Elsewhere(host)

    def require(self, url):
        """The page at `url` is one to type the account into, or LoginError: nothing has been typed or sent."""
        scheme, host = place_of(url)
        if not host:
            raise LoginError("로그인 화면의 주소를 확인할 수 없어 ID·비밀번호를 입력하지 않았습니다.")
        if not self.trusts(host):
            if host in self.known:  # Where the site led, but not where it has signed in before.
                raise LoginError(
                    f"로그인 화면이 이 사이트에 저장된 로그인 주소에 없는 곳({host})에 있어 ID·비밀번호를 "
                    "입력하지 않았습니다. 사이트의 로그인 화면이 바뀐 것이 맞다면 관리 → 사이트 계정에서 "
                    "‘로그인 주소 초기화’ 뒤 로그인 확인을 해 주세요."
                )
            raise LoginError(
                f"로그인 화면이 사이트 주소와 다른 곳({host})에 있어 ID·비밀번호를 입력하지 않았습니다. "
                "관리 → 사이트 계정에서 주소와 로그인 방식을 확인해 주세요."
            )
        if self.secure and scheme != "https":
            raise LoginError(
                f"로그인 화면이 암호화되지 않은 주소(http://{host})에 있어 ID·비밀번호를 입력하지 "
                "않았습니다. 관리 → 사이트 계정에서 주소와 로그인 방식을 확인해 주세요."
            )
        self.used.add(host)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _fail():
    raise UserError(PLAN_ERROR)


def _selector(value, *, required=True):
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        _fail()
    return value.strip()


def _step(step):
    """A step: click an element, fill a field with the account's ID or password or a text the user wrote, or submit the
    login (its button, or Enter in the field filled last). A click and a text may be optional (only when they show);
    any step may be skipped `unless` an element shows."""
    if not isinstance(step, dict) or step.get("action") not in ("click", "fill", "submit"):
        _fail()
    action = step["action"]
    value = step.get("value") if action == "fill" else None
    if action == "fill" and value not in FIELDS:
        _fail()
    text = step.get("text") if value == "text" else None
    if value == "text" and (not isinstance(text, str) or not text or len(text) > MAX_TEXT or CONTROL.search(text)):
        _fail()
    optional = step.get("optional")
    if optional is None:
        optional = False
    if not isinstance(optional, bool) or optional and action != "click" and value != "text":
        _fail()
    return {
        "action": action,
        "target": _selector(step.get("target"), required=action != "submit"),
        "value": value,
        "text": text,
        "optional": optional,
        "unless": _selector(step.get("unless"), required=False),
    }


def validate_plan(value):
    """The login plan Electron sends, checked and reduced to the known fields, or None without one. A method fills the
    password and ends with its submit step, its only one."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("mode") not in MODES:
        _fail()
    methods = value.get("methods")
    if not isinstance(methods, list) or not 1 <= len(methods) <= MAX_METHODS:
        _fail()
    found = {}
    for method in methods:
        if not isinstance(method, dict):
            _fail()
        ident, label, detect, steps = (method.get(key) for key in ("id", "label", "detect", "steps"))
        if (
            not isinstance(ident, str)
            or not 1 <= len(ident) <= 40
            or not isinstance(label, str)
            or not 1 <= len(label.strip()) <= 80
            or method.get("priority") not in (None, True, False)
        ):
            _fail()
        if (
            not isinstance(detect, list)
            or not 1 <= len(detect) <= MAX_DETECT
            or not isinstance(steps, list)
            or not 1 <= len(steps) <= MAX_STEPS
        ):
            _fail()
        clean = {
            "id": ident,
            "label": label.strip(),
            "detect": [_selector(item) for item in detect],
            "steps": [_step(step) for step in steps],
            "priority": method.get("priority") is True,
        }
        actions = [step["action"] for step in clean["steps"]]
        if (
            actions.count("submit") != 1
            or actions[-1] != "submit"
            or not any(step["value"] == "password" for step in clean["steps"])
        ):
            _fail()
        # The saved method comes first and may appear again in the rest of the catalog.
        found.setdefault(ident, clean)
    credentials = value.get("credentials")
    if not isinstance(credentials, dict):
        _fail()
    login_id, password = credentials.get("id"), credentials.get("password")
    if not isinstance(login_id, str) or not login_id or len(login_id) > 256 or CONTROL.search(login_id):
        _fail()
    if not isinstance(password, str) or not password or len(password) > 1024 or re.search(r"[\0\r\n]", password):
        _fail()
    site_id, site_name = value.get("site_id", ""), value.get("site_name", "")
    if not isinstance(site_id, str) or len(site_id) > 80 or not isinstance(site_name, str) or len(site_name) > 120:
        _fail()
    # The hosts the account may be typed into besides the site's own, once Electron keeps them for the site.
    hosts = value.get("hosts") or []
    if (
        not isinstance(hosts, list)
        or len(hosts) > MAX_HOSTS
        or not all(isinstance(host, str) and HOST.fullmatch(host) for host in hosts)
    ):
        _fail()
    return {
        "site_id": site_id,
        "site_name": site_name.strip(),
        "mode": value["mode"],
        "methods": list(found.values()),
        "credentials": {"id": login_id, "password": password},
        "hosts": list(dict.fromkeys(host.lower() for host in hosts)),
    }


# ---------------------------------------------------------------------------
# Page checks
# ---------------------------------------------------------------------------


def redirected(error):
    """A navigation replaced by another one, e.g. a client-side redirect to the SSO page; not one replaced by the
    browser's error page of a page that did not open."""
    reason = str(error)
    return (
        "interrupted by another navigation" in reason or "net::ERR_ABORTED" in reason
    ) and "chrome-error:" not in reason


def _alive(page):
    """A closed page (its window closed, the browser ended) ends the sign-in at once instead of at a time limit."""
    if page.is_closed():
        raise LoginError("로그인 브라우저가 닫혔습니다.")


async def signed_in(page, origin):
    """The page is on the site and names a signed-in user. Never raises."""
    try:
        return not page.is_closed() and origin_of(page.url) == origin and bool(await page.evaluate(SIGNED_IN))
    except Exception:  # Navigating or closed.
        return False


async def visible(page, selector):
    try:
        return await page.locator(selector).first.is_visible()
    except Exception:  # Navigating, or a selector that does not fit this page.
        return False


async def _arrived(arrived, page):
    try:
        return bool(await arrived(page))
    except Exception:
        return False


async def _attached(handle):
    try:
        return bool(await handle.evaluate("element => element.isConnected"))
    except Exception:  # The page navigated and destroyed the element's context.
        return False


async def _dispose(handle):
    if handle is not None:
        with contextlib.suppress(Exception):
            await handle.dispose()


async def _cleared(locator, mark):
    """The password field shows again, empty, as on a login screen shown again after a rejected login: a field of the
    same kind and name at its place (`mark`), not another one there, e.g. a one-time code's."""
    try:
        return (
            await locator.is_visible()
            and (await locator.evaluate(FIELD, timeout=1000))["mark"] == mark
            and await locator.input_value(timeout=1000) == ""
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


async def _has_session(context, url):
    """Whether the profile holds cookies for the site at `url`: a session an earlier sign-in or the login window may
    have left. Without any, nothing is signed in, and the page is opened once, by the sign-in itself."""
    try:
        return bool(await context.cookies(url))
    except Exception:
        return False


async def _open(page, url, product, assist=None):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as error:
        _alive(page)
        # A client-side redirect, e.g. to the SSO page, may replace the navigation.
        if redirected(error):
            return
        failure = LoginError(f"{product.split()[0]}에 접속하지 못했습니다. 주소와 네트워크 연결을 확인해 주세요.")
        if assist is None:
            raise failure from None
        await assist.reconnect(page, url, error, failure)


async def _shown(page, methods):
    found = []
    for method in methods:
        for selector in method["detect"]:
            if await visible(page, selector):
                found.append(method)
                break
    return found


async def _detect(page, candidates, tried, arrived, seconds, chosen=None):
    """The first candidate in order whose login screen shows, ARRIVED when the page needs no login, or None. While
    only screens of other methods show, the screen of the method the user chose (`chosen`, while not tried), or else
    of a priority method (standard SSO), gets PRIORITY_SECONDS to show too, e.g. an SSO link a script
    draws after the site's own form."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    chosen = chosen if any(method is chosen for method in candidates) else None
    preferred = any(method["priority"] for method in candidates)

    def wanted(method):
        return method is chosen if chosen is not None else method["priority"] or not preferred

    repeated = waiting = None
    while True:
        _alive(page)
        if await _arrived(arrived, page):
            return ARRIVED
        shown = await _shown(page, candidates)
        if shown:
            await asyncio.sleep(SETTLE)
            _alive(page)
            if await _arrived(arrived, page):
                return ARRIVED
            shown = await _shown(page, candidates)
            if shown:
                waiting = waiting or loop.time()
                if wanted(shown[0]) or loop.time() - waiting >= PRIORITY_SECONDS:
                    return shown[0]
                await asyncio.sleep(POLL)
                continue
            repeated = None
        elif tried and await _shown(page, tried):
            # The screen of a method already tried, and nothing else: waiting longer would not help.
            repeated = repeated or loop.time()
            if loop.time() - repeated >= TRIED_SETTLE:
                return None
        else:
            repeated = None
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(POLL)


async def _press_enter(page, field, handle):
    """Press Enter in a field. True only when that submitted something: a form without a submit button and with
    several text fields ignores Enter, and a login that never started must not be waited for."""
    loop = asyncio.get_running_loop()
    requests = []

    def seen(request):
        if request.method != "GET" or request.resource_type == "document":
            requests.append(request)

    page.on("request", seen)
    try:
        with contextlib.suppress(Exception):  # A press interrupted by the navigation it started still counts.
            await field.press("Enter", timeout=5000)
        deadline = loop.time() + ENTER_SECONDS
        while True:
            if requests or (handle is not None and not await _attached(handle)):
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(POLL)
    finally:
        page.remove_listener("request", seen)


async def _submit(page, button, field, handle):
    """Click the submit button, or press Enter in the last filled field when it does not show. False when the
    form could not be submitted."""
    shown = False
    if button is not None:
        with contextlib.suppress(Exception):
            await button.wait_for(state="visible", timeout=SUBMIT_SECONDS * 1000)
            shown = True
            await button.click(timeout=10000)
            return True
    if field is not None and await _press_enter(page, field, handle):
        return True
    return shown  # A shown button whose click raised may still have submitted, e.g. while navigating.


async def _fill(locator, value, text, hosts, seconds):
    """Type the account's ID or password or the user's text (`value`, its `text`) into the field `locator` names, only
    on a page `hosts` allows and only into a field of its kind (KINDS): the field's element and FIELD of it. Raises
    StepError when no such field shows, LoginError when the page is not one to type the account into."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        try:
            element = await locator.element_handle(timeout=max(deadline - loop.time(), 0.1) * 1000)
        except Exception:
            raise StepError(FIELDS[value]) from None
        try:
            # The element is the one checked and filled: a page that navigated meanwhile has another.
            found = await element.evaluate(FIELD)
            hosts.require(found["url"])
            if found["kind"] not in KINDS[value]:
                raise StepError(FIELDS[value])
            await element.fill(text, timeout=max(deadline - loop.time(), 0.1) * 1000)
            return element, found
        except (StepError, LoginError):
            await _dispose(element)
            raise
        except Exception:
            replaced = not await _attached(element)
            await _dispose(element)
            # A page that drew the field anew, e.g. while it set up its form, gets the new one filled.
            if not replaced or loop.time() >= deadline:
                raise StepError(FIELDS[value]) from None


async def _run(page, method, credentials, optional_seconds, form_seconds, hosts, sent=(), filled=None):
    """Carry out a method's steps up to submitting the form, typing the account only where `hosts` allows. The clicks
    of a method the LLM found (LEARNED) are the LLM's: they never lead to a host the account must not be typed into.
    Each field filled goes into `filled` as (value, page, mark, text); a submit whose fields are those of a login sent
    already (a key of `sent`) raises Repeated instead. Returns the password field's locator, its element and its mark,
    which tell a login screen shown again after a rejected login from the one submitted, or Nones."""
    password = handle = mark = field = None
    filled = [] if filled is None else filled
    try:
        for step in method["steps"]:
            _alive(page)
            if step["unless"] and await visible(page, step["unless"]):
                continue
            locator = page.locator(step["target"]).first if step["target"] else None
            if step["action"] == "click":
                if step["optional"]:
                    # Notices and links that only some screens show.
                    with contextlib.suppress(Exception):
                        await locator.wait_for(state="visible", timeout=optional_seconds * 1000)
                        await _click(locator, method, hosts, 5)
                    continue
                try:
                    await locator.wait_for(state="visible", timeout=form_seconds * 1000)
                    await _click(locator, method, hosts, 10)
                except Exception:
                    raise StepError("버튼") from None
            elif step["action"] == "fill":
                value = step["value"]
                try:
                    element, found = await _fill(
                        locator,
                        value,
                        step["text"] if value == "text" else credentials[value],
                        hosts,
                        optional_seconds if step["optional"] else form_seconds,
                    )
                except StepError:
                    if step["optional"]:  # A field that only some screens show.
                        continue
                    raise
                filled.append((value, page_of(found["url"]), tuple(found["mark"]), step["text"] or ""))
                field = locator
                if value == "password":
                    await _dispose(handle)
                    password, handle, mark = locator, element, found["mark"]
                else:
                    await _dispose(element)
            else:
                if frozenset(filled) in sent:
                    raise Repeated()
                if not await _submit(page, locator, password or field, handle):
                    raise StepError("버튼")
    except BaseException:
        await _dispose(handle)
        raise
    return password, handle, mark


async def _click(locator, method, hosts, seconds):
    if method.get("id") == LEARNED:
        await _clicked(locator, hosts)
    else:
        await locator.click(timeout=seconds * 1000)


async def _outcome(page, password, handle, mark, arrived, seconds):
    """'success' when the page arrives, 'rejected' when the login screen shows again with an empty password
    field, 'timeout' otherwise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    detached, since = False, None
    while loop.time() < deadline:
        _alive(page)
        if await _arrived(arrived, page):
            return "success"
        if handle is not None and not detached:
            detached = not await _attached(handle)
        # A field that is still the submitted one never counts: the page may be waiting for an approval.
        if detached and password is not None and await _cleared(password, mark):
            since = since or loop.time()
            if loop.time() - since >= REJECT_SECONDS:
                return "rejected"
        else:
            since = None
        await asyncio.sleep(POLL)
    return "timeout"


def _tried(notes):
    return f" (시도한 방식: {', '.join(notes)})" if notes else ""


async def _sent(page, field, arrived, login_seconds):
    """How a sent login went, with the password field _run() returned: "success" once signed in, "rejected" when the
    login screen came back, "timeout" when the site never arrived."""
    password, handle, mark = field
    try:
        return await _outcome(page, password, handle, mark, arrived, login_seconds)
    finally:
        await _dispose(handle)


def _note(label, outcome, login_seconds):
    """How a login sent with the method `label` went, for the list of methods tried."""
    return f"{label}: " + (
        "로그인 화면이 다시 표시됨" if outcome == "rejected" else f"{login_seconds:g}초 안에 사이트로 이동하지 않음"
    )


def _failed(sent, notes, product, login_seconds):
    """The LoginError of a sign-in that ends without signing in, after the logins `sent` (their outcomes by fields)."""
    outcomes = set(sent.values())
    if "rejected" in outcomes:
        return LoginError("로그인하지 못했습니다. ID·비밀번호를 확인해 주세요." + _tried(notes), sent=True)
    if "timeout" in outcomes:
        return LoginError(
            f"로그인 후 {login_seconds:g}초 안에 {product} 페이지로 이동하지 않았습니다. "
            "ID·비밀번호를 확인하고, 추가 인증이 필요한 사이트라면 관리 → 사이트 계정의 "
            "‘로그인 창 열기’에서 한 번 로그인해 주세요." + _tried(notes),
            sent=True,
        )
    return LoginError(
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 "
        "주세요." + _tried(notes)
    )


async def _ready(page, arrived, seconds):
    """A page that just opened or changed: wait until it shows something to fill or click, or `seconds`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    with contextlib.suppress(Exception):  # Still loading after `seconds`: read what shows.
        await page.wait_for_load_state("load", timeout=seconds * 1000)
    while loop.time() < deadline and not await _arrived(arrived, page) and not await visible(page, READY):
        _alive(page)
        await asyncio.sleep(POLL)
    await asyncio.sleep(SETTLE)


async def _clicked(locator, hosts):
    """Click an element the LLM named, then give the page a moment: until that element is gone (another page, a
    redrawn screen) or CLICK_SECONDS pass, which the navigation it starts counts as the LLM's in `hosts`. Raises
    Elsewhere, without a click, when its link or form leads to a host the account must not be typed into, and another
    error when it cannot be clicked."""
    handle = await locator.element_handle(timeout=5000)
    try:
        hosts.lead(await handle.evaluate(LEADS))
        with hosts.following():
            await handle.click(timeout=10000)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + CLICK_SECONDS
            while loop.time() < deadline and await _attached(handle):
                await asyncio.sleep(POLL)
    finally:
        await _dispose(handle)


def _step_of(action, target, value=None):
    return {"action": action, "target": target, "value": value, "text": None, "optional": False, "unless": None}


async def _assisted(
    context,
    page,
    url,
    plan,
    arrived,
    product,
    assist,
    notes,
    hosts,
    sent,
    optional_seconds,
    form_seconds,
    login_seconds,
):
    """None of the plan's methods fits the page (any more): the LLM reads the login screen (web_assist.py) and the
    fields it picks are filled as a user-defined method's, sending the account once at most, and never into the fields
    of a login sent already (`sent`, which the login it sends joins). A screen before the form (a notice, an SSO link)
    is clicked through, and an ID asked for before the password is sent on with its button, as long as neither leads to
    a host the account must not be typed into (`hosts`). Returns sign_in()'s result with the fields as `learned`, in the
    shape of a user-defined method, or ARRIVED, or None when the LLM finds no login screen to fill or the login it sent
    did not sign in; raises LoginError when the screen is on a page the account must not be typed into."""
    assist.update(notice="로그인 방식으로 로그인하지 못해 AI가 로그인 화면을 분석하고 있습니다.")
    try:
        await context.clear_cookies()
        await _open(page, url, product, assist)
        learned = {"before": [], "id": "", "next": "", "password": "", "submit": ""}
        filled = []
        for _ in range(ASSIST_ROUNDS):
            await _ready(page, arrived, form_seconds)
            if await _arrived(arrived, page):
                return ARRIVED
            found = await assist.login_form(page, hosts.home)
            if found["screen"] == "before_login":
                try:
                    await _clicked(page.locator(found["click"]).first, hosts)
                except Elsewhere as error:
                    notes.append(f"AI 분석: 다른 사이트({error.host})로 가는 버튼이라 누르지 않음")
                    return None
                except Exception:
                    _alive(page)
                    notes.append("AI 분석: 로그인 화면으로 가는 버튼을 누르지 못함")
                    return None
                learned["before"].append(found["click"])
                continue
            if found["screen"] != "login":
                notes.append(f"AI 분석: {found['reason'] or '로그인 화면이 아님'}")
                return None
            steps = []
            if found["id"] and not learned["id"]:  # Shown again with the password, it is filled already.
                learned["id"] = found["id"]
                steps.append(_step_of("fill", found["id"], "id"))
            if found["password"]:
                learned.update(password=found["password"], submit=found["submit"] or "")
                steps += [_step_of("fill", found["password"], "password"), _step_of("submit", found["submit"])]
            try:
                field = await _run(
                    page, {"steps": steps}, plan["credentials"], optional_seconds, form_seconds, hosts, sent, filled
                )
            except StepError as error:
                notes.append(f"AI 분석: {error.what}에 입력하지 못함")
                return None
            except Repeated:
                notes.append("AI 분석: 앞서 보낸 입력란과 같아 다시 보내지 않음")
                return None
            if not found["password"]:
                # The ID first: its button shows the password, on this page or the next.
                try:
                    await _clicked(page.locator(found["next"]).first, hosts)
                except Elsewhere as error:
                    notes.append(f"AI 분석: ID 입력 후 누를 버튼이 다른 사이트({error.host})로 가서 누르지 않음")
                    return None
                except Exception:
                    _alive(page)
                    notes.append("AI 분석: ID 입력 후 누를 버튼을 누르지 못함")
                    return None
                learned["next"] = found["next"]
                continue
            outcome = await _sent(page, field, arrived, login_seconds)
            if outcome != "success":
                sent[frozenset(filled)] = outcome
                notes.append(_note(LEARNED_LABEL, outcome, login_seconds))
                await assist.sent(page, LEARNED_LABEL, outcome)
                return None
            result = {"method": LEARNED, "label": LEARNED_LABEL, "detected": plan["mode"] == "auto"}
            # A screen that showed no ID field (one the site remembers) cannot be kept as a method.
            return {**result, "learned": learned} if learned["id"] else result
        notes.append("AI 분석: 로그인 화면까지 가지 못함")
        return None
    finally:
        assist.update(notice="")


async def sign_in(
    context,
    page,
    url,
    plan,
    *,
    arrived,
    product,
    optional_seconds=3,
    form_seconds=30,
    login_seconds=100,
    assist=None,
    watched=False,
    fresh=False,
):
    """Sign in with the plan's methods, unless the profile's session is signed in already (`fresh` signs in anyway).

    arrived(page) tells the signed-in page apart. Every login detects which method's login screen shows, in plan
    order (the user's choice or the saved method, standard SSO, then the others), and moves on to
    the next method that fits when one does not fit the page (an element it needs never shows, which submits nothing)
    or its login did not sign in: the page is opened again with a fresh session. A method never sends the fields of a
    login sent already (Repeated). Auto mode (`detected`) lets Electron keep the method that signed in. With `assist`
    (web_assist.Assist), the LLM reads a login screen no method fits once, and a site that does not open may be opened
    again. In a window the user watches (`watched`), a login that does not arrive in time ends the sign-in: the user may
    be in the middle of an approval. A login screen on a page the account must not be typed into (Hosts) ends the
    sign-in before anything is typed there. Returns {"method", "label", "detected"}, with method None when the page
    needed no login (a public page, or the profile's session signed in), `learned` when the LLM found the fields,
    `tried` when other methods were tried first and `hosts` when the account went to hosts besides the site's own;
    raises LoginError, with `sent` when a login was sent.
    """
    mode, credentials = plan["mode"], plan["credentials"]
    # The method the user chose for the site, which fixed mode sends first.
    chosen = plan["methods"][0] if mode == "fixed" else None
    tried, notes = [], []
    sent = {}  # The outcome of each login sent that did not sign in, by its fields.
    assisted = False
    hosts = Hosts(page, url, plan.get("hosts") or ())

    def signed(result):
        elsewhere = sorted(hosts.used - {hosts.home})
        found = {**result, **({"hosts": elsewhere} if elsewhere else {})}
        return {**found, "tried": list(notes)} if notes and result["method"] else found

    try:
        if not fresh and await _has_session(context, url):
            # The session the profile keeps may be signed in still: the last run's, or the login window's, where a
            # site that asks for a second factor is signed in to by hand. The account is sent only when it is not.
            await _open(page, url, product, assist)
            await _ready(page, arrived, min(form_seconds, SESSION_SECONDS))
            if await _arrived(arrived, page):
                return {"method": None, "label": SESSION_LABEL, "detected": False}
        while True:
            candidates = [method for method in plan["methods"] if all(method is not other for other in tried)]
            method = None
            if candidates:
                # Keep profile settings such as a sign-in prompt's "always allow", but sign in with a fresh session.
                await context.clear_cookies()
                await _open(page, url, product, assist)
                method = await _detect(page, candidates, tried, arrived, form_seconds, chosen)
            if method is ARRIVED:
                return {"method": None, "label": "", "detected": False}
            if method is None:
                # No other method fits the page: the LLM's try, once.
                if assist is not None and not assisted:
                    assisted = True
                    result = await _assisted(
                        context,
                        page,
                        url,
                        plan,
                        arrived,
                        product,
                        assist,
                        notes,
                        hosts,
                        sent,
                        optional_seconds,
                        form_seconds,
                        login_seconds,
                    )
                    if result is ARRIVED:
                        return {"method": None, "label": "", "detected": False}
                    if result is not None:
                        return signed(result)
                raise _failed(sent, notes, product, login_seconds)
            tried.append(method)
            filled = []
            try:
                field = await _run(page, method, credentials, optional_seconds, form_seconds, hosts, sent, filled)
            except StepError as error:
                notes.append(f"{method['label']}: {error.what} 찾지 못함")
                continue
            except Repeated:
                notes.append(f"{method['label']}: 앞서 보낸 입력란과 같아 다시 보내지 않음")
                continue
            outcome = await _sent(page, field, arrived, login_seconds)
            if outcome == "success":
                return signed({"method": method["id"], "label": method["label"], "detected": mode == "auto"})
            sent[frozenset(filled)] = outcome
            notes.append(_note(method["label"], outcome, login_seconds))
            if assist is not None:  # The site's message about the login, before the site opens again.
                await assist.sent(page, method["label"], outcome)
            if watched and outcome == "timeout":
                raise _failed(sent, notes, product, login_seconds)
    except LoginError as error:
        # Ended otherwise after a login was sent, e.g. by a screen on a page the account must not be typed into or a
        # closed browser: the logins sent before still count (a routine turns itself off), and the methods tried show.
        if notes and not str(error).endswith(_tried(notes)):
            error.args = (str(error) + _tried(notes),)
        error.sent = error.sent or bool(sent)
        raise
    finally:
        hosts.close()
