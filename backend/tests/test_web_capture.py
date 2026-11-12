import csv
import io
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent import web_capture
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


def test_deleting_a_capture_removes_its_items_and_screenshot_only(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    web = service.web
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고 A"}]}
    first = web._keep("coupang", "a", "chat", "t1", page, b"jpeg")
    second = web._keep("coupang", "b", "chat", "t2", page, b"jpeg")
    shots = {item["id"]: tmp_path / item["screenshot"] for item in web.captures({})["captures"]}
    assert all(shot.is_file() for shot in shots.values())
    outside = tmp_path / "wiki-note.txt"
    outside.write_text("keep")
    with service.db.connect() as conn:  # A damaged row naming another file leaves that file alone.
        conn.execute("UPDATE web_captures SET screenshot=? WHERE id=?", ("../wiki-note.txt", second["capture_id"]))
    assert web.delete_capture({"id": first["capture_id"]}) == {"deleted": first["capture_id"]}
    assert not shots[first["capture_id"]].exists()
    assert [item["id"] for item in web.captures({})["captures"]] == [second["capture_id"]]
    assert web.records({})["total"] == 1
    web.delete_capture({"id": second["capture_id"]})
    assert outside.read_text() == "keep"
    assert web.records({})["total"] == 0
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        web.delete_capture({"id": first["capture_id"]})


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
