"""Search pages in the markup of the sites the web records know, on one local origin, for test_web_capture.py: their
ads (marked as each site marks them), products and results, a page whose ads only a label tells apart, a robot check,
and a landing page an ad links to. The words and links are made up (example.test)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LANDING = "<!doctype html><title>도착 페이지</title><h1>광고주 페이지</h1>"

PAGES = {
    "/coupang": """<!doctype html><title>쿠팡 검색</title><ul id="product-list">
      <li class="search-product"><a href="/landing?ad=1"><span class="ad-badge">광고</span>
        <div class="name">무선 이어폰 A</div><strong class="price-value">39,900</strong>원</a></li>
      <li class="search-product"><a href="https://shop.example.test/vp/products/2">
        <div class="name">무선 이어폰 B</div><strong class="price-value">29,900</strong>원</a></li>
      <li class="search-product"><a href="https://shop.example.test/vp/products/3">
        <div class="name">유선 이어폰 C</div><strong class="price-value">9,900</strong>원</a></li>
    </ul>""",
    "/amazon": """<!doctype html><title>Amazon search</title>
      <div data-component-type="s-search-result" data-asin="A1"><span class="puis-sponsored-label-text">Sponsored</span>
        <h2><a href="https://shop.example.test/dp/A1"><span>Headphones Pro</span></a></h2>
        <span class="a-price"><span class="a-offscreen">$49.99</span></span></div>
      <div data-component-type="s-search-result" data-asin="A2">
        <h2><a href="https://shop.example.test/dp/A2"><span>Headphones Lite</span></a></h2>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span></div>""",
    "/naver": """<!doctype html><title>네이버 검색</title>
      <section id="power_link_body"><ul><li><a class="lnk_head" href="https://ad.example.test/1">캠핑의자 할인</a>
        <span class="lnk_url">camp.example.test</span></li></ul></section>
      <ul class="lst_type"><li><a class="tit" href="https://blog.example.test/1">캠핑의자 고르는 법</a></li></ul>""",
    "/google": """<!doctype html><title>Google</title>
      <div id="tads"><div data-text-ad="1"><a href="https://ad.example.test/g"><div role="heading">Laptop sale</div>
        </a><cite>store.example.test</cite></div></div>
      <div id="rso"><div class="g"><a href="https://news.example.test/1"><h3>Laptop review</h3></a></div></div>""",
    "/labelled": """<!doctype html><title>다른 사이트</title>
      <div class="card"><div class="badge">광고</div><a href="https://ad.example.test/x">특가 운동화 모음</a></div>
      <div class="card"><a href="https://shop.example.test/y">일반 운동화</a></div>""",
    "/robot": "<!doctype html><title>확인</title><p>We detected unusual traffic from your computer network.</p>",
    # Results of a search for the word a robot check uses, and a page that shows a check's widget over its results.
    "/captcha-results": """<!doctype html><title>Google</title>
      <div id="rso"><div class="g"><a href="https://news.example.test/c"><h3>What a CAPTCHA is</h3></a>
        <span>CAPTCHA tests tell people and programs apart.</span></div></div>""",
    "/challenge": """<!doctype html><title>Google</title><div class="g-recaptcha" style="width:300px;height:80px"></div>
      <div id="rso"><div class="g"><a href="https://news.example.test/1"><h3>Laptop review</h3></a></div></div>""",
    # The badge of a check that asks nothing, which such a site shows on every page.
    "/badge": """<!doctype html><title>다른 사이트</title>
      <div class="card"><div class="badge">광고</div><a href="https://ad.example.test/b">특가 가방 모음</a></div>
      <div class="grecaptcha-badge" style="position:fixed;bottom:14px;right:0;width:256px;height:60px">
        <iframe src="/robot?recaptcha=anchor&amp;size=invisible" style="width:256px;height:60px"></iframe></div>""",
    # A label and a price inside the ad's own link; an ad whose link is an SVG one.
    "/inline": """<!doctype html><title>다른 사이트</title>
      <div class="card"><a href="https://ad.example.test/hat"><span class="badge">광고</span> 특가 모자
        <span class="cost">12,000원</span></a></div>""",
    "/svg": """<!doctype html><title>다른 사이트</title>
      <div class="card"><div class="badge">광고</div><svg width="300" height="40">
        <a href="/landing?svg=1"><text x="4" y="24">그림 링크 광고 상품</text></a></svg></div>""",
    "/landing": LANDING,
    "/home": "<!doctype html><title>홈</title><p>홈</p>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(urlsplit(self.path).path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((body or "not found").encode())

    def log_message(self, *args):
        pass


def serve():
    """A started server on 127.0.0.1 and its base address."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
