import base64
import csv
import io
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent import service as service_module, web_capture
from agent.db import Database
from agent.llm import UserError
from agent.service import Service
from agent.web_capture import link, web_url
from web_pages import serve


class FakeLLM:
    def public(self):
        return {"configured": False}


@pytest.fixture
def site_server():
    server, base = serve()
    yield base
    server.shutdown()


@pytest.fixture
def service(tmp_path, monkeypatch, site_server):
    monkeypatch.setenv("PRIVATE_AGENT_WEB_HEADLESS", "1")
    monkeypatch.setenv("PRIVATE_AGENT_WEB_PROFILES", str(tmp_path / "profiles"))
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    # The sites' search pages, on the local server: the same keys and page rules, other addresses.
    for key in ("coupang", "amazon", "naver", "google"):
        service.web.sites[key] = {
            **service.web.sites[key],
            "search": f"{site_server}/{key}?q={{query}}",
            "home": f"{site_server}/home",
        }
    yield service
    service.web.shutdown()


def test_addresses_open_over_https_unless_http_is_written():
    assert web_url("shop.example.test/a") == "https://shop.example.test/a"
    assert web_url("http://shop.example.test/") == "http://shop.example.test/"
    with pytest.raises(UserError):
        web_url("javascript:alert(1)")
    with pytest.raises(UserError):
        web_url("file:///etc/passwd")
    assert web_url("localhost:8080/x") == "https://localhost:8080/x"  # host:port is no scheme
    assert link("javascript:alert(1)") == ""
    assert link("https://shop.example.test/x") == "https://shop.example.test/x"


def test_only_the_known_sites_are_searched(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    with pytest.raises(UserError, match="지원하는 사이트"):
        service.web.capture("unknown", "노트북")
    with pytest.raises(UserError, match="검색어"):
        service.web.capture("coupang", "")
    assert {site["key"] for site in service.web.sites_list()} == {"coupang", "amazon", "naver", "google"}


def test_records_filter_export_and_summary(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    result = {
        "url": "https://shop.example.test/s?q=a",
        "title": "검색",
        "blocked": False,
        "items": [
            {
                "ref": 0,
                "kind": "ad",
                "title": "=HYPERLINK(1)",
                "price": "39,900원",
                "link": "https://ad.example.test/1",
            },
            {"ref": 1, "kind": "product", "title": "이어폰", "link": "javascript:alert(1)"},
        ],
    }
    kept = web._keep("coupang", "이어폰", "chat", "token-1", result, b"")
    assert kept["counts"] == {"ad": 1, "product": 1, "result": 0}
    assert web.records({"kind": "ad"})["total"] == 1
    assert web.records({"text": "이어폰"})["total"] == 2
    assert web.records({"site": "amazon"})["total"] == 0
    listed = web.records({})["items"]
    assert [item["link_url"] for item in listed] == ["https://ad.example.test/1", ""]
    exported = web.export({})
    data = open(exported["path"], encoding="utf-8").read()
    assert data.startswith("﻿")
    rows = list(csv.reader(io.StringIO(data.lstrip("﻿"))))
    assert rows[0][0] == "기록 시각" and rows[1][1] == "쿠팡" and rows[1][3] == "광고"
    assert rows[1][5] == "'=HYPERLINK(1)"  # A formula is kept as text.
    summary = web.summary({})
    assert summary["counts"]["ad"] == 1 and summary["counts"]["clicked"] == 0
    with pytest.raises(UserError):
        web.records({"kind": "nothing"})
    with pytest.raises(UserError):
        web.records({"since": "어제"})


def test_the_days_of_a_period_are_the_users_own_and_its_last_day_counts(tmp_path, monkeypatch):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    # Half past midnight today and yesterday, on this PC's clock: east of UTC those are the day before in UTC, which
    # is how the records keep their time.
    early = datetime.now().replace(hour=0, minute=30, second=0, microsecond=0)
    for days, title in ((0, "오늘 새벽"), (1, "어제 새벽")):
        at = (early - timedelta(days=days)).astimezone(timezone.utc).isoformat()
        monkeypatch.setattr(web_capture, "now", lambda at=at: at)
        web._keep("coupang", title, "chat", "t", {"items": [{"ref": 0, "kind": "ad", "title": title}]}, b"")
    today, yesterday = f"{early:%Y-%m-%d}", f"{early - timedelta(days=1):%Y-%m-%d}"

    def titles(**period):
        return sorted(item["title"] for item in web.records(period)["items"])

    assert titles(since=today, until=today) == ["오늘 새벽"]
    assert titles(since=yesterday, until=yesterday) == ["어제 새벽"]
    assert titles(since=yesterday, until=today) == ["어제 새벽", "오늘 새벽"]
    assert titles(until=yesterday) == ["어제 새벽"] and titles(since=today) == ["오늘 새벽"]
    # The CSV follows the same period, and so does the chat's summary of the records.
    assert len(open(web.export({"since": today, "until": today})["path"], encoding="utf-8").readlines()) == 2
    assert web.summary({"since": today})["total"] == 1
    # A full time is an instant, as written.
    assert titles(since=(early - timedelta(minutes=1)).astimezone().isoformat()) == ["오늘 새벽"]
    with pytest.raises(UserError):
        web.records({"until": "2026-13-45"})


def test_a_deleted_capture_waits_in_the_trash_and_its_screenshot_goes_only_with_it_for_good(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {
        "url": "https://shop.example.test/",
        "title": "검색 결과",
        "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}, {"ref": 1, "kind": "product", "title": "상품 B"}],
    }
    first = web._keep("coupang", "a", "chat", "t1", page, b"jpeg")
    second = web._keep("other", "", "manual", "t2", page, b"jpeg")
    shots = {item["id"]: tmp_path / item["screenshot"] for item in web.captures({})["captures"]}
    assert all(shot.is_file() for shot in shots.values())
    items = web.records({"capture_id": first["capture_id"]})["items"]
    deleted = web.delete_capture({"id": first["capture_id"]})
    assert deleted["deleted"] == first["capture_id"] and len(deleted["trash_ids"]) == 1
    # Gone from the records, waiting in the trash by what was searched for, its screenshot kept meanwhile.
    assert [item["id"] for item in web.captures({})["captures"]] == [second["capture_id"]]
    assert web.records({})["total"] == 2 and shots[first["capture_id"]].is_file()
    entry = service.dispatch("trash.list", {})["items"][0]
    assert (entry["kind"], entry["title"]) == ("web_capture", "a")
    assert entry["detail"] == "쿠팡 · 광고 1 · 상품 1 · 검색 결과 0"
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        web.delete_capture({"id": first["capture_id"]})
    # It comes back as it was, items and all.
    assert service.dispatch("trash.restore", {"ids": deleted["trash_ids"]})["restored"] == 1
    assert web.records({"capture_id": first["capture_id"]})["items"] == items
    # Deleted for good, it takes its screenshot along; a page searched for nothing goes by its title.
    deleted = web.delete_capture({"id": first["capture_id"]})["trash_ids"]
    assert service.dispatch("trash.purge", {"ids": deleted}) == {"purged": 1}
    assert not shots[first["capture_id"]].exists()
    outside = tmp_path / "wiki-note.txt"
    outside.write_text("keep")
    with service.db.connect() as conn:  # A damaged row naming another file leaves that file alone.
        conn.execute("UPDATE web_captures SET screenshot=? WHERE id=?", ("../wiki-note.txt", second["capture_id"]))
    web.delete_capture({"id": second["capture_id"]})
    assert service.dispatch("trash.list", {})["items"][0]["title"] == "검색 결과"
    assert service.dispatch("trash.purge", {"all": True}) == {"purged": 1}
    assert outside.read_text() == "keep" and web.records({})["total"] == 0


def test_the_screenshot_of_a_record_in_the_trash_goes_with_the_retention_policy(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    kept = service.web._keep("naver", "캠핑", "schedule", "t1", page, b"jpeg")
    shot = tmp_path / service.web.captures({})["captures"][0]["screenshot"]
    service.web.delete_capture({"id": kept["capture_id"]})
    assert service.retain()["trash"] == 0 and shot.is_file()
    with service.db.connect() as conn:
        conn.execute("UPDATE trash SET deleted_at=?", ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    assert service.retain()["trash"] == 1 and not shot.exists()


def test_several_records_go_to_the_trash_at_once_and_come_back_together(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    ids = [web._keep("coupang", f"검색 {index}", "chat", f"t{index}", page, b"")["capture_id"] for index in range(3)]
    # What is gone meanwhile is passed over; the rest go together, each to an entry of its own.
    moved = web.delete_captures({"ids": [ids[0], ids[2], "없는 기록"]})
    assert moved["deleted"] == 2 and len(moved["trash_ids"]) == 2
    assert [item["id"] for item in web.captures({})["captures"]] == [ids[1]]
    titles = {item["title"] for item in service.dispatch("trash.list", {})["items"]}
    assert titles == {"검색 0", "검색 2"}
    assert service.dispatch("trash.restore", {"ids": moved["trash_ids"]})["restored"] == 2
    assert {item["id"] for item in web.captures({})["captures"]} == set(ids)
    assert web.delete_captures({"ids": ["없는 기록"]}) == {"deleted": 0, "trash_ids": []}
    for value in ([], "전부", [1], ["x"] * (web_capture.MAX_DELETE + 1)):
        with pytest.raises(UserError):
            web.delete_captures({"ids": value})


def test_a_records_screenshot_is_shown_as_a_picture_of_its_own_file_only(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    jpeg = web_capture.JPEG + b"jpeg data"
    shot = web._keep("coupang", "a", "chat", "t1", page, jpeg)["capture_id"]
    bare = web._keep("coupang", "b", "chat", "t2", page, b"")["capture_id"]
    assert web.screenshot({"id": shot}) == {"data": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}
    with pytest.raises(UserError, match="스크린샷이 없습니다"):
        web.screenshot({"id": bare})
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        web.screenshot({"id": "없는 기록"})
    # A file that is no JPEG, gone, or outside the screenshots folder (a damaged row) is not read.
    path = tmp_path / next(item for item in web.captures({})["captures"] if item["id"] == shot)["screenshot"]
    path.write_bytes(b"<script>")
    with pytest.raises(UserError, match="읽지 못했습니다"):
        web.screenshot({"id": shot})
    path.unlink()
    with pytest.raises(UserError, match="읽지 못했습니다"):
        web.screenshot({"id": shot})
    outside = tmp_path / "note.jpg"
    outside.write_bytes(jpeg)
    with service.db.connect() as conn:
        conn.execute("UPDATE web_captures SET screenshot=? WHERE id=?", ("../note.jpg", shot))
    with pytest.raises(UserError, match="읽지 못했습니다"):
        web.screenshot({"id": shot})


def test_records_older_than_the_days_kept_go_to_the_trash_with_the_retention_policy(tmp_path, monkeypatch):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    for days, query in ((40, "오래된 검색"), (10, "지난주 검색"), (0, "오늘 검색")):
        at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        monkeypatch.setattr(web_capture, "now", lambda at=at: at)
        web._keep("naver", query, "schedule", query, page, b"")
    # Kept until deleted unless the user chose how long: the policy moves none.
    assert web.retention({}) == {"days": 0}
    assert "web_captures" not in service.retain() and web.captures({})["total"] == 3
    assert web.retention_save({"days": 30}) == {"days": 30} and web.retention({}) == {"days": 30}
    assert service.retain()["web_captures"] == 1
    assert [item["query"] for item in web.captures({})["captures"]] == ["오늘 검색", "지난주 검색"]
    entry = service.dispatch("trash.list", {})["items"][0]
    assert (entry["kind"], entry["title"]) == ("web_capture", "오래된 검색")
    assert entry["detail"].endswith(service_module.RETIRED)
    assert web.retention_save({"days": 7}) == {"days": 7} and service.retain()["web_captures"] == 1
    assert web.retention_save({"days": 0}) == {"days": 0} and "web_captures" not in service.retain()
    for value in (-1, 3651, "30", 1.5, None, True):
        with pytest.raises(UserError, match="보관 기간"):
            web.retention_save({"days": value})
    # A value the screen did not write is read as kept until deleted.
    with service.db.connect() as conn:
        conn.execute("UPDATE metadata SET value='{\"days\": \"많이\"}' WHERE key='web_retention'")
    assert web.retention({}) == {"days": 0}


def test_a_robot_check_stops_the_capture(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    with pytest.raises(UserError, match="로봇 확인"):
        service.web._keep("google", "a", "chat", "t", {"blocked": True, "items": []}, b"")
    assert service.web.records({})["total"] == 0


@pytest.mark.browser
@pytest.mark.parametrize(
    ("site", "counts", "first"),
    [
        ("coupang", {"ad": 1, "product": 2, "result": 0}, "무선 이어폰 A"),
        ("amazon", {"ad": 1, "product": 1, "result": 0}, "Headphones Pro"),
        ("naver", {"ad": 1, "product": 0, "result": 1}, "캠핑의자 할인"),
        ("google", {"ad": 1, "product": 0, "result": 1}, "Laptop sale"),
    ],
)
def test_real_browser_reads_each_sites_ads_and_items(service, site, counts, first):
    kept = service.web.capture(site, "테스트 검색")
    assert kept["counts"] == counts
    items = service.web.records({"capture_id": kept["capture_id"]})["items"]
    assert items[0]["kind"] == "ad" and items[0]["title"] == first
    assert kept["screenshot"]
    shot = service.web.captures({})["captures"][0]["screenshot"]
    assert (service.web.folder / shot).stat().st_size > 0


def test_real_browser_finds_ads_by_their_label_on_another_site(service, site_server):
    service.web.open(url=f"{site_server}/labelled")
    kept = service.web.capture_page()
    assert kept["counts"]["ad"] == 1
    ads = service.web.records({"kind": "ad"})["items"]
    assert ads[0]["title"] == "특가 운동화 모음"


def test_real_browser_robot_check_is_left_to_the_user(service, site_server):
    # A known site's page that says so and shows nothing to record.
    service.web.sites["google"]["search"] = f"{site_server}/robot?q={{query}}"
    with pytest.raises(UserError, match="로봇 확인"):
        service.web.capture("google", "laptop")
    assert service.web.records({})["total"] == 0


def test_real_browser_records_the_ad_the_user_clicked_and_its_landing_page(service):
    web = service.web
    kept = web.capture("coupang", "이어폰")
    assert web.records({"kind": "clicked"})["total"] == 0  # Reading the page clicked nothing.
    page = web.current
    # The user's click: a real input event, as Playwright sends it (event.isTrusted), on the ad.
    web._call(page.click('[data-pa-kind="ad"] .name'), 30)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        clicked = web.records({"kind": "clicked", "capture_id": kept["capture_id"]})["items"]
        if clicked and clicked[0]["landing_title"]:
            break
        time.sleep(0.2)
    assert clicked[0]["title"] == "무선 이어폰 A"
    assert clicked[0]["landing_title"] == "도착 페이지"
    assert clicked[0]["landing_url"].endswith("/landing?ad=1")
    # A script's click is not the user's: it is not recorded.
    web._call(page.go_back(), 30)
    web._call(page.evaluate("document.querySelector('[data-pa-kind]')?.click()"), 30)
    assert web.records({"kind": "clicked"})["total"] == 1


def test_real_browser_records_the_same_page_again(service, site_server):
    web = service.web
    web.open(url=f"{site_server}/labelled")
    first = web.capture_page()
    again = web.capture_page()
    assert first["counts"] == again["counts"] == {"ad": 1, "product": 0, "result": 0}
    # The page's marks are the later capture's: a click from now on goes with it.
    token = web._call(web.current.evaluate("document.querySelector('[data-pa-capture-id]').dataset.paCaptureId"), 30)
    assert web.tokens[token] == again["capture_id"]


def test_real_browser_robot_words_among_results_are_not_a_robot_check(service, site_server):
    web = service.web
    web.sites["google"]["search"] = f"{site_server}/captcha-results?q={{query}}"
    assert web.capture("google", "captcha")["counts"]["result"] == 1
    # Any other page may be about them, too; a check's widget stops the capture there, results or not.
    web.open(url=f"{site_server}/robot")
    assert web.capture_page()["counts"] == {"ad": 0, "product": 0, "result": 0}
    web.open(url=f"{site_server}/challenge")
    with pytest.raises(UserError, match="로봇 확인"):
        web.capture_page()
    # The badge of a check that asks nothing is no check: the page is read.
    web.open(url=f"{site_server}/badge")
    assert web.capture_page()["counts"]["ad"] == 1


def test_real_browser_generic_ad_titles_leave_out_the_label_and_price(service, site_server):
    web = service.web
    web.open(url=f"{site_server}/inline")
    web.capture_page()
    ad = web.records({"kind": "ad"})["items"][0]
    assert ad["title"] == "특가 모자" and ad["price"] == "12,000원"


def test_real_browser_a_click_on_an_svg_ad_link_keeps_its_address(service, site_server):
    web = service.web
    web.open(url=f"{site_server}/svg")
    kept = web.capture_page()
    assert kept["counts"]["ad"] == 1
    web._call(web.current.click("svg text"), 30)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        clicked = web.records({"kind": "clicked"})["items"]
        if clicked and clicked[0]["landing_title"]:
            break
        time.sleep(0.2)
    assert clicked[0]["link_url"] == f"{site_server}/landing?svg=1"
    assert clicked[0]["landing_title"] == "도착 페이지"


def cards(web, site_server):
    """The ad cards page, opened and recorded: its tab and the capture's ID."""
    web.open(url=f"{site_server}/cards")
    kept = web.capture_page()
    assert kept["counts"]["ad"] == 7
    return web.current, kept["capture_id"]


def clicked_item(web, capture, title, done=lambda item: bool(item["landing_url"]), seconds=15):
    """The item `title` of a capture once it is clicked and `done` holds for it (its landing page is recorded), or as
    it is after `seconds` (None when it was never clicked)."""
    deadline = time.monotonic() + seconds
    while True:
        found = [
            item for item in web.records({"kind": "clicked", "capture_id": capture})["items"] if item["title"] == title
        ]
        if (found and done(found[0])) or time.monotonic() > deadline:
            return found[0] if found else None
        time.sleep(0.2)


def test_real_browser_a_click_beside_an_ads_link_opens_nothing_and_records_nothing(service, site_server):
    """A click on an ad's card that is not on its link (a wish-list button, an empty spot, a link to a place on the
    same page) is no click on the ad: the page opened next, by the user or by the app, is no landing page."""
    web = service.web
    page, _ = cards(web, site_server)
    web._call(page.click("#plain .wish"), 30)
    web._call(page.click("#plain", position={"x": 4, "y": 4}), 30)
    web._call(page.click("#plain .wish-link"), 30)
    web._call(page.click("#other"), 30)
    web._call(page.wait_for_url(f"{site_server}/home"), 30)
    web.open(url=f"{site_server}/landing?ad=later")
    time.sleep(1)
    assert web.records({"kind": "clicked"})["total"] == 0
    # The ad's own link is a click on it, and its page the landing page.
    page, capture = cards(web, site_server)
    web._call(page.click("#plain .title"), 30)
    assert clicked_item(web, capture, "일반 링크 광고 상품")["landing_url"] == f"{site_server}/landing?ad=plain"


def test_real_browser_the_landing_page_is_the_one_the_ads_link_opens(service, site_server):
    """The landing page is where the ad's link leads: through the server's redirects, in a new tab when the link opens
    one or the user asks for one (a middle click), to the address a click counter gave the link as it was clicked
    (the link the page showed stays recorded), and on to the page the landing page moves to by itself."""
    web = service.web
    page, capture = cards(web, site_server)
    web._call(page.click("#redirect .title"), 30)
    landed = clicked_item(web, capture, "리다이렉트 광고 상품")
    assert landed["landing_url"] == f"{site_server}/landing?ad=redirect"
    page, capture = cards(web, site_server)
    web._call(page.click("#blank .title"), 30)
    assert clicked_item(web, capture, "새 탭 광고 상품")["landing_url"] == f"{site_server}/landing?ad=blank"
    web._call(page.click("#redirect .title", button="middle"), 30)
    landed = clicked_item(web, capture, "리다이렉트 광고 상품")
    assert landed["landing_url"] == f"{site_server}/landing?ad=redirect" and page.url == f"{site_server}/cards"
    web._call(page.click("#counted .title"), 30)
    counted = clicked_item(web, capture, "집계하는 광고 상품")
    assert counted["landing_url"] == f"{site_server}/landing?ad=counted"
    assert counted["link_url"] == f"{site_server}/landing?ad=shown" and counted["landing_title"] == "도착 페이지"
    page, capture = cards(web, site_server)
    web._call(page.click("#hop .title"), 30)
    hop = clicked_item(web, capture, "스스로 넘어가는 광고 상품", lambda item: item["landing_url"].endswith("ad=hop"))
    assert hop["landing_url"] == f"{site_server}/landing?ad=hop"


def test_real_browser_no_landing_page_but_the_one_the_click_opened(service, site_server, monkeypatch):
    """A link whose page does not load leaves no landing page, and the page opened after it is none either; when the
    page keeps the click for itself, neither a tab the app opens next nor a page the user opens next is one."""
    monkeypatch.setattr(web_capture, "QUIET", 0)
    web = service.web
    page, capture = cards(web, site_server)
    web._call(page.click("#fail .title"), 30)
    failed = clicked_item(web, capture, "열리지 않는 광고 상품", lambda item: bool(item["clicked_at"]))
    assert failed["link_url"] == "http://nowhere.invalid/ad"
    time.sleep(2)
    web.open(url=f"{site_server}/home")
    time.sleep(1)
    failed = clicked_item(web, capture, "열리지 않는 광고 상품", seconds=0)
    assert (failed["landing_url"], failed["landing_title"]) == ("", "")
    page, capture = cards(web, site_server)
    web._call(page.click("#kept .title"), 30)
    assert clicked_item(web, capture, "페이지가 처리하는 광고 상품", lambda item: bool(item["clicked_at"]))
    web.capture("coupang", "이어폰", source="schedule", scheduled=True)  # A tab of the app's own.
    web._call(page.click("#other"), 30)
    web._call(page.wait_for_url(f"{site_server}/home"), 30)
    time.sleep(1)
    kept = clicked_item(web, capture, "페이지가 처리하는 광고 상품", seconds=0)
    assert (kept["landing_url"], kept["landing_title"]) == ("", "")


def test_a_click_report_from_a_frame_inside_the_page_is_not_the_users(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    kept = web._keep("coupang", "a", "chat", "t1", page, b"")
    web.tokens["t1"] = kept["capture_id"]
    report = '{"token": "t1", "ref": 0, "kind": "ad", "href": "https://ad.example.test/1"}'
    frame = type("Frame", (), {"parent_frame": object()})()
    web._clicked({"frame": frame, "page": None}, report)
    assert web.records({"kind": "clicked"})["total"] == 0
    web._clicked({"frame": type("Frame", (), {"parent_frame": None})(), "page": None}, report)
    assert web.records({"kind": "clicked"})["total"] == 1
    # The click fills the link of an item whose link could not be read with the page.
    assert web.records({"kind": "clicked"})["items"][0]["link_url"] == "https://ad.example.test/1"
    # It never replaces a link that was recorded (a script of the page can send a report as well).
    forged = '{"token": "t1", "ref": 0, "kind": "ad", "href": "https://other.example.test/"}'
    web._clicked({"frame": type("Frame", (), {"parent_frame": None})(), "page": None}, forged)
    assert web.records({"kind": "clicked"})["items"][0]["link_url"] == "https://ad.example.test/1"


def test_real_browser_one_task_at_a_time(service):
    web = service.web
    web.busy = True
    with pytest.raises(UserError, match="다른 웹 작업"):
        web.capture("coupang", "이어폰")
    web.busy = False


def test_real_browser_a_scheduled_capture_reads_in_a_tab_of_its_own(service, site_server, monkeypatch):
    """A scheduled capture never takes the tab the user browses: the page they went on to stays, and so does the page
    "이 페이지 기록" reads. Its own tab is used again while it shows what the last scheduled capture left; once the user
    went on from it there, the next one opens a new tab."""
    monkeypatch.setattr(web_capture, "QUIET", 0)
    web = service.web
    web.capture("coupang", "이어폰")
    users = web.current
    web._call(users.goto(f"{site_server}/landing"), 30)  # The user goes on from the results in that tab.
    scheduled = web.capture("coupang", "노트북", source="schedule", scheduled=True)
    assert scheduled["url"].endswith("/coupang?q=%EB%85%B8%ED%8A%B8%EB%B6%81")
    assert users.url == f"{site_server}/landing" and web.current is users
    assert web.capture_page()["url"] == f"{site_server}/landing"
    own = web.apart["coupang"][0]
    assert own is not users and len(web.context.pages) == 2
    # The next scheduled capture of the site reads in that tab again.
    web.capture("coupang", "키보드", source="schedule", scheduled=True)
    assert web.apart["coupang"][0] is own and len(web.context.pages) == 2
    assert own.url.endswith("/coupang?q=%ED%82%A4%EB%B3%B4%EB%93%9C") and users.url == f"{site_server}/landing"
    # The user went on from it: it is theirs now, and the next one opens a tab of its own again.
    web._call(own.goto(f"{site_server}/home"), 30)
    web.capture("coupang", "마우스", source="schedule", scheduled=True)
    assert web.apart["coupang"][0] not in (own, users) and len(web.context.pages) == 3
    assert own.url == f"{site_server}/home" and users.url == f"{site_server}/landing"
    assert [item["source"] for item in web.captures({})["captures"]].count("schedule") == 3
