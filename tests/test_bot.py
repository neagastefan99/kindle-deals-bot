"""Unit tests for the Kindle Deals Bot (SFF).

Run: cd ~/kindle-deals-bot && PYTHONPATH=. venv/bin/python -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import yaml

from filters import BookFilter
from storage import Storage
from formatter import format_report, format_empty_report
from scraper import is_reportable
from sources.lightpanda_fetcher import LightpandaFetcher, US_COOKIES


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def config():
    cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml"))
    return cfg


@pytest.fixture
def bf(config):
    return BookFilter(config)


@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(tmp_path / "data")


def sample_book(**over):
    b = {
        "asin": "B0TEST00001",
        "title": "The Test Fantasy Novel",
        "author": "Test Author",
        "price": 1.99,
        "url": "https://www.amazon.com/dp/B0TEST00001",
        "list_price": 9.99,
        "savings_pct": 80,
    }
    b.update(over)
    return b


# ─── Filters: price ─────────────────────────────────────────────────

class TestPriceFilter:
    def test_under_max(self, bf):
        assert bf.matches_price(1.99) is True

    def test_at_max(self, bf):
        assert bf.matches_price(4.99) is True

    def test_over_max(self, bf):
        assert bf.matches_price(5.99) is False

    def test_none_price(self, bf):
        assert bf.matches_price(None) is False

    def test_zero_price(self, bf):
        # $0.00 is falsy in Python — must still pass the price gate
        assert bf.matches_price(0.0) is True


# ─── Filters: genre ─────────────────────────────────────────────────

class TestGenreFilter:
    def test_matches_fantasy(self, bf):
        assert bf.matches_genre("A Fantasy Novel") is True

    def test_matches_scifi(self, bf):
        assert bf.matches_genre("Space Opera Adventures") is True

    def test_no_match(self, bf):
        assert bf.matches_genre("A Cookbook of Pasta") is False

    def test_from_sff_page_skips(self, bf):
        assert bf.matches_genre("Anything At All", from_sff_page=True) is True

    def test_word_boundary(self, bf):
        # "war" should not match "toward" — boundary check for short keywords
        assert bf.matches_genre("Toward the Light") is False


# ─── Filters: tracked authors ───────────────────────────────────────

class TestAuthorFilter:
    def test_is_tracked_exact(self, bf):
        assert bf.is_tracked_author("Brandon Sanderson") is True

    def test_is_tracked_fuzzy_lastname(self, bf):
        assert bf.is_tracked_author("Sanderson, Brandon") is True

    def test_is_tracked_partial(self, bf):
        assert bf.is_tracked_author("Isaac Asimov") is True

    def test_is_tracked_single_name(self, bf):
        # "Asimov" alone should match tracked "Isaac Asimov"
        assert bf.is_tracked_author("Asimov") is True

    def test_not_tracked(self, bf):
        assert bf.is_tracked_author("Jane Doe") is False

    def test_matches_author_always_true(self, bf):
        # Tracked authors are promoted, never exclusive
        assert bf.matches_author("Anyone") is True


# ─── Filters: apply ─────────────────────────────────────────────────

class TestApply:
    def test_filters_price(self, bf):
        books = [sample_book(price=9.99), sample_book(price=1.99)]
        assert len(bf.apply(books)) == 1

    def test_keeps_all_sff_from_page(self, bf):
        books = [sample_book(from_sff_page=True, title="Weird Title No Keywords")]
        assert len(bf.apply(books)) == 1

    def test_marks_tracked_author(self, bf):
        books = [sample_book(author="Brandon Sanderson")]
        result = bf.apply(books)
        assert len(result) == 1


# ─── Storage ────────────────────────────────────────────────────────

class TestStorage:
    def test_new_book(self, tmp_storage):
        assert tmp_storage.is_new("B0TEST00001") is True

    def test_mark_seen_makes_not_new(self, tmp_storage):
        tmp_storage.mark_seen("B0TEST00001", "Test", 1.99)
        assert tmp_storage.is_new("B0TEST00001") is False

    def test_better_price_detection(self, tmp_storage):
        tmp_storage.mark_seen("B0TEST00001", "Test", 4.99)
        assert tmp_storage.is_better_price("B0TEST00001", 2.99) is True
        assert tmp_storage.is_better_price("B0TEST00001", 5.99) is False

    def test_lowest_price_tracks(self, tmp_storage):
        tmp_storage.mark_seen("B0TEST00001", "Test", 4.99)
        tmp_storage.mark_seen("B0TEST00001", "Test", 2.99)
        seen = tmp_storage._read_seen()
        assert seen["B0TEST00001"]["lowest_price"] == 2.99

    def test_log_run_appends(self, tmp_storage):
        tmp_storage.log_run({"scraped": 5})
        lines = tmp_storage.log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["scraped"] == 5


# ─── Formatter ──────────────────────────────────────────────────────

class TestFormatter:
    def test_header_present(self):
        r = format_report([sample_book()], 1, 0)
        assert "Kindle" in r

    def test_tracked_author_section(self):
        books = [
            sample_book(author="Brandon Sanderson", tracked_author=True),
            sample_book(asin="B0TEST00002", author="Other Author"),
        ]
        r = format_report(books, 2, 0)
        assert "Your Authors" in r
        assert "All Deals" in r

    def test_price_shown(self):
        r = format_report([sample_book(price=1.99)], 1, 0)
        assert "$1.99" in r

    def test_zero_price_not_na(self):
        # $0.00 must render as $0.00, not N/A
        r = format_report([sample_book(price=0.0)], 1, 0)
        assert "N/A" not in r
        assert "$0.00" in r

    def test_savings_shown(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=80)], 1, 0)
        assert "80% off" in r

    def test_empty_report(self):
        r = format_report([], 0, 0)
        assert "No" in r or "deals" in r.lower()

    def test_media_tags(self):
        r = format_report([sample_book(cover_path="/tmp/x.jpg")], 1, 0)
        assert "MEDIA:/tmp/x.jpg" in r

    def test_empty_report_format(self):
        r = format_empty_report()
        assert "Could not fetch" in r


# ─── Availability check (spike t_e934a2a3 §6b) ─────────────────────

def _pp_soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")


def _sff_scraper():
    from sources.amazon import AmazonDealsScraper
    cfg = {
        "sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }},
        "scraping": {"max_books_per_source": 50},
    }
    return AmazonDealsScraper(cfg)


KINDLE_AVAILABLE = '''<div id="tmmSwatches"><div class="swatchElement selected" id="tmm-grid-swatch-KINDLE">
<span class="slot-title">Kindle</span><span class="slot-price">$1.99</span>
<span class="a-size-small a-color-secondary">Available instantly</span></div>
<div class="swatchElement" id="tmm-grid-swatch-HARDCOVER">Hardcover $11.71</div></div>'''

KINDLE_UNAVAILABLE = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
<span>Kindle</span><span>$9.99</span>
<span class="a-size-small a-color-secondary">Currently unavailable</span></div></div>'''

KINDLE_UNAVAILABLE_PHRASE = '''<div id="formats"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
Kindle $7.99 This title is not currently available for purchase</div></div>'''

KINDLE_PREORDER = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">
Kindle $14.99 This title will be released on November 1, 2026</div></div>'''

BUYBOX_AVAILABLE = '''<div id="buybox"><input id="one-click-button" type="submit" value="Buy now with 1-Click"/>
<span id="checkoutButtonId-announce"> Buy now with 1-Click </span></div>'''

BUYBOX_PREORDER = '''<div id="buybox"><input id="one-click-button" type="submit" value="Pre-order with 1-Click"/>
<span>This title will be released on January 5, 2027</span></div>'''

NO_KINDLE_ROW = '''<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-AUDIO_DOWNLOAD">
Audiobook $0.00</div><div class="swatchElement" id="tmm-grid-swatch-PAPERBACK">Paperback $8.94</div></div>'''


class TestAvailability:
    def test_kindle_row_available(self):
        info = _sff_scraper().parse_product_page(_pp_soup(KINDLE_AVAILABLE))
        assert info.get("available") is True
        assert info.get("preorder", False) is False

    def test_kindle_row_currently_unavailable(self):
        info = _sff_scraper().parse_product_page(_pp_soup(KINDLE_UNAVAILABLE))
        assert info.get("available") is False

    def test_kindle_row_unavailable_phrase(self):
        info = _sff_scraper().parse_product_page(_pp_soup(KINDLE_UNAVAILABLE_PHRASE))
        assert info.get("available") is False

    def test_preorder_release_date(self):
        info = _sff_scraper().parse_product_page(_pp_soup(KINDLE_PREORDER))
        assert info.get("preorder") is True
        assert info.get("available") is False

    def test_buybox_fallback_available(self):
        info = _sff_scraper().parse_product_page(_pp_soup(BUYBOX_AVAILABLE))
        assert info.get("available") is True

    def test_buybox_preorder(self):
        info = _sff_scraper().parse_product_page(_pp_soup(BUYBOX_PREORDER))
        assert info.get("preorder") is True
        assert info.get("available") is False

    def test_no_kindle_row_unknown(self):
        # No Kindle row / no buybox signal → availability not asserted (kept)
        info = _sff_scraper().parse_product_page(_pp_soup(NO_KINDLE_ROW))
        assert "available" not in info
        assert info.get("preorder", False) is False

    def test_none_soup(self):
        assert _sff_scraper().parse_product_page(None) == {}


class TestReportable:
    """scraper.is_reportable — the availability gate applied after enrichment."""

    def test_available_kept(self):
        assert is_reportable({"available": True}) is True

    def test_unavailable_dropped(self):
        assert is_reportable({"available": False}) is False

    def test_preorder_dropped(self):
        assert is_reportable({"preorder": True}) is False

    def test_preorder_also_unavailable_dropped(self):
        assert is_reportable({"available": False, "preorder": True}) is False

    def test_unknown_kept(self):
        # No availability signal (e.g. fetch failed) → keep, no regression
        assert is_reportable({}) is True

    def test_available_none_kept(self):
        assert is_reportable({"available": None}) is True


# ─── Lightpanda fetcher helpers ─────────────────────────────────────

class TestLightpandaFetcher:
    def test_us_cookies_present(self):
        names = {c["name"] for c in US_COOKIES}
        assert "lc-acbuk" in names
        assert "i18n-prefs" in names
        assert "session-id" in names

    def test_cookie_jar_created(self, tmp_path, config):
        config["scraping"]["lightpanda_cookies"] = str(tmp_path / "cookies.json")
        f = LightpandaFetcher(config)
        assert f.cookie_path.exists()
        jar = json.loads(f.cookie_path.read_text())
        assert any(c["name"] == "lc-acbuk" for c in jar)

    def test_url_normalization_mapping(self):
        # Lightpanda percent-encodes URLs; our norm() must decode them
        from urllib.parse import unquote
        orig = "https://www.amazon.com/s?rh=n:668010011,p_n_deal_type:23566064011"
        encoded = "https://www.amazon.com/s?rh=n%3A668010011%2Cp_n_deal_type%3A23566064011"
        assert unquote(encoded) == orig


# ─── Lightpanda 503 rate-limit retry (regression: t_db840b83) ────────

def _fake_subprocess_run(monkeypatch, responses):
    """Patch subprocess.run with a fake returning responses in order (last repeats)."""
    import subprocess as _sp
    state = {"n": 0}

    def fake_run(cmd, capture_output=True, text=True, timeout=300):
        i = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return _sp.CompletedProcess([], 0, stdout=responses[i], stderr="")

    monkeypatch.setattr("sources.lightpanda_fetcher.subprocess.run", fake_run)
    return state


def _lp_result(url, status, content):
    return json.dumps({"results": [{"url": url, "http_status": status, "content": content}]})


def _lp_fetcher(tmp_path):
    cfg = {"scraping": {
        "lightpanda_cookies": str(tmp_path / "cookies.json"),
        "lightpanda_retries": 3,
        "lightpanda_retry_backoff": [0, 0],
    }}
    return LightpandaFetcher(cfg)


class _FakeFetcher:
    """Minimal primary/fallback stand-in with fetch_all + last_failures."""

    def __init__(self, results, failures=None):
        self._results = results
        self.last_failures = failures or {}
        self.calls = 0
        self.last_urls: list[str] = []

    def fetch_all(self, urls):
        self.calls += 1
        self.last_urls = list(urls)
        return dict(self._results)


class TestLightpanda503Retry:
    """Regression: Amazon 503 rate-limit must retry with backoff, and the
    FallbackFetcher must only fall back after retries are exhausted."""

    def test_503_then_200_retries_and_succeeds(self, tmp_path, monkeypatch):
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 503, "<html><body>Sorry! Something went wrong.</body></html>"),
            _lp_result(url, 200, "<html><div data-asin='B0TEST00001'>book</div></html>"),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 2          # exactly one retry
        assert out[url] is not None
        assert f.last_failures == {}    # recovered → no failure recorded

    def test_503_exhaustion_returns_none_and_records_failure(self, tmp_path, monkeypatch, capsys):
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 503, "Sorry! Something went wrong."),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 3          # all retries exhausted
        assert out[url] is None
        assert f.last_failures[url] == "HTTP 503"
        captured = capsys.readouterr()
        assert "failed 1 URL(s) after 3 attempt(s)" in captured.out
        assert "HTTP 503" in captured.out

    def test_error_page_content_detected_even_with_status_200(self, tmp_path, monkeypatch):
        """Lightpanda sometimes reports the CloudFront 503 page with
        http_status 200 — the body signature must still trigger a retry."""
        url = "https://www.amazon.com/dp/B0TEST00001"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, "<html><body>Sorry! Something went wrong.<br/>Request ID: abc123</body></html>"),
            _lp_result(url, 200, "<html><div data-asin='B0TEST00001'>book</div></html>"),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 2
        assert out[url] is not None

    def test_real_page_with_status_200_is_not_retried(self, tmp_path, monkeypatch):
        url = "https://www.amazon.com/dp/B0TEST00001"
        big_html = "<html><body>" + ("<div data-asin='B%d'>book</div>" * 200) + "</body></html>"
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, big_html),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 1          # no pointless retry
        assert out[url] is not None

    def test_captcha_page_is_retried_and_falls_back(self, tmp_path, monkeypatch):
        """Amazon anti-bot interstitial (validateCaptcha / 'Click the button
        below to continue shopping') must be treated as an error page so the
        retry loop runs and the curl_cffi fallback gets a chance — otherwise
        the 3KB captcha is cached as a valid page and every book dies at
        enrichment with no price."""
        url = "https://www.amazon.com/dp/B0TEST00001"
        captcha = (
            "<html><body><div class='a-box'>"
            "<h4>Click the button below to continue shopping</h4>"
            "<form action='/errors_page/validateCaptcha' method='get'>"
            "<input name='amzn' type='hidden' value='abc'/>"
            "<button type='submit'>Continue shopping</button>"
            "</form></div></body></html>"
        )
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, captcha),
            _lp_result(url, 200, "<html><div data-asin='B0TEST00001'>book</div></html>"),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 2          # captcha triggers exactly one retry
        assert out[url] is not None     # second fetch succeeded

    def test_captcha_exhaustion_returns_none(self, tmp_path, monkeypatch):
        """All attempts served captcha → None + failure recorded, so
        FallbackFetcher will retry the URL with curl_cffi."""
        url = "https://www.amazon.com/dp/B0TEST00001"
        captcha = ("<html><body><h4>Click the button below to continue "
                   "shopping</h4></body></html>")
        state = _fake_subprocess_run(monkeypatch, [
            _lp_result(url, 200, captcha),
        ])
        f = _lp_fetcher(tmp_path)
        out = f.fetch_all([url])
        assert state["n"] == 3          # all retries exhausted
        assert out[url] is None
        assert f.last_failures[url] == "captcha (continue shopping)"

    def test_fallback_only_after_exhaustion(self, config, capsys):
        from sources.fallback_fetcher import FallbackFetcher
        from bs4 import BeautifulSoup
        url = "https://www.amazon.com/dp/B0TEST00001"
        primary = _FakeFetcher({url: None}, failures={url: "HTTP 503"})
        fallback = _FakeFetcher({url: BeautifulSoup("<html><div data-asin='X'>b</div></html>", "lxml")})
        ff = FallbackFetcher(primary, fallback, config)
        out = ff.fetch_all([url])
        assert fallback.calls == 1
        assert out[url] is not None
        captured = capsys.readouterr()
        assert "1 URL(s) failed" in captured.out
        assert "503" in captured.out

    def test_no_fallback_on_partial_success(self, config, capsys):
        from sources.fallback_fetcher import FallbackFetcher
        from bs4 import BeautifulSoup
        u1 = "https://www.amazon.com/dp/B0TEST00001"
        u2 = "https://www.amazon.com/dp/B0TEST00002"
        soup = BeautifulSoup("<html><body>ok</body></html>", "lxml")
        primary = _FakeFetcher({u1: soup, u2: None}, failures={u2: "HTTP 503"})
        fallback = _FakeFetcher({u1: soup, u2: soup})
        ff = FallbackFetcher(primary, fallback, config)
        out = ff.fetch_all([u1, u2])
        # Partial failure → fallback runs ONLY for the failed URL, good
        # results are kept, and the 503 is logged.
        assert fallback.calls == 1
        assert fallback.last_urls == [u2]   # only the failed URL is retried
        assert out[u1] is not None and out[u2] is not None
        captured = capsys.readouterr()
        assert "partial failure: 1/2" in captured.out
        assert "HTTP 503" in captured.out


# ─── Edition guard: only Kindle ebook ASINs (t_663bdb53) ────────────

class TestEditionGuard:
    """Only report ASINs that resolve to the KINDLE ebook edition.

    parse_product_page must set is_ebook=True only when #tmmSwatches has a
    Kindle row WITH a price. Print/audiobook-only listings (no Kindle row,
    or a Kindle row without a price) → is_ebook=False so scraper.py drops
    them. No swatch block → unknown (is_ebook unset, book kept).
    """

    KINDLE_ROW = (
        '<div class="swatchElement selected" id="tmm-grid-swatch-KINDLE">'
        '<span class="a-button"><span class="a-button-inner"><a class="a-button-text">'
        '<span class="slot-title"><span aria-label="Kindle Format:">Kindle</span><br/></span>'
        '<span class="slot-price"><span aria-label="$1.99" class="ebook-price-value">$1.99</span></span>'
        '<span class="slot-extraMessage"><span class="kindleExtraMessage">'
        '<span aria-label="Available instantly">Available instantly</span></span></span>'
        '</a></span></span></div>'
    )
    PRINT_ROW = (
        '<div class="swatchElement unselected" id="tmm-grid-swatch-PAPERBACK">'
        '<span class="a-button"><span class="a-button-inner"><a class="a-button-text">'
        '<span class="slot-title"><span aria-label="Paperback Format:">Paperback</span></span>'
        '<span class="slot-price"><span aria-label="$8.94">$8.94</span></span>'
        '</a></span></span></div>'
    )
    HARDCOVER_ROW = PRINT_ROW.replace("PAPERBACK", "HARDCOVER").replace("Paperback", "Hardcover").replace("$8.94", "$11.71")
    AUDIO_ROW = (
        '<div class="swatchElement unselected" id="tmm-grid-swatch-AUDIO_DOWNLOAD">'
        '<span class="a-button"><span class="a-button-inner"><a class="a-button-text">'
        '<span class="slot-title"><span>Audiobook</span></span>'
        '<span class="slot-price"><span>$0.00</span></span>'
        '</a></span></span></div>'
    )

    def _scraper(self):
        from sources.amazon import AmazonDealsScraper
        cfg = {"sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }}, "scraping": {"max_books_per_source": 50}}
        return AmazonDealsScraper(cfg)

    def _info(self, body: str) -> dict:
        from bs4 import BeautifulSoup
        return self._scraper().parse_product_page(
            BeautifulSoup(f"<html><body>{body}</body></html>", "lxml"))

    def test_kindle_row_with_price_is_ebook(self):
        info = self._info(f'<div id="tmmSwatches"><ul>{self.KINDLE_ROW}{self.AUDIO_ROW}{self.HARDCOVER_ROW}{self.PRINT_ROW}</ul></div>')
        assert info.get("is_ebook") is True

    def test_print_audio_only_no_kindle_row_is_not_ebook(self):
        # Only print/audio rows → the ASIN is NOT the Kindle ebook edition
        info = self._info(f'<div id="tmmSwatches"><ul>{self.AUDIO_ROW}{self.HARDCOVER_ROW}{self.PRINT_ROW}</ul></div>')
        assert info.get("is_ebook") is False

    def test_kindle_row_without_price_is_not_ebook(self):
        # Kindle row exists but carries no price (e.g. currently unavailable)
        kindle_no_price = self.KINDLE_ROW.replace(
            '<span class="slot-price"><span aria-label="$1.99" class="ebook-price-value">$1.99</span></span>',
            '<span class="slot-extraMessage"><span class="kindleExtraMessage"><span>Currently unavailable</span></span></span>')
        info = self._info(f'<div id="tmmSwatches"><ul>{kindle_no_price}{self.PRINT_ROW}</ul></div>')
        assert info.get("is_ebook") is False

    def test_no_swatch_block_is_unknown(self):
        # No #tmmSwatches/#formats → cannot verify → is_ebook unset → keep
        info = self._info('<div class="a-section"><span class="apex-pricetopay-value">$ 1 . 99</span></div>')
        assert "is_ebook" not in info

    def test_empty_swatch_block_is_unknown(self):
        """Lightpanda renders #tmmSwatches but leaves it EMPTY (no format
        rows). An empty container carries no format evidence → is_ebook
        unset → keep. Regression: previously ANY present swatch block
        without a priced Kindle row set is_ebook=False, dropping every
        book on Lightpanda-rendered pages."""
        info = self._info('<div id="tmmSwatches"></div>')
        assert "is_ebook" not in info

    def test_whitespace_swatch_block_is_unknown(self):
        # Same as empty, but with whitespace-only content
        info = self._info('<div id="tmmSwatches">\n  \n</div>')
        assert "is_ebook" not in info

    def test_old_layout_li_rows_with_tmm_ebooks(self):
        # Legacy layout: <li> rows, Kindle button id tmm-ebooks
        old = ('<ul>'
               '<li><span class="a-button" id="tmm-ebooks"><span class="a-button-text">Kindle $1.99 Available instantly</span></span></li>'
               '<li><span class="a-button" id="tmm_pap_swatch_0"><span class="a-button-text">Paperback $8.94</span></span></li>'
               '</ul>')
        info = self._info(f'<div id="tmmSwatches">{old}</div>')
        assert info.get("is_ebook") is True


# ─── List-price / savings basis (spike t_e934a2a3 §6a) ─────────────

class TestListPriceBasis:
    """The Kindle ebook list price must be preferred over the PRINT list price.

    apex-basisprice-value on a Kindle deal page is the print edition's list
    price ("Print List Price: $12.99") — trusting it inflates savings %.
    parse_product_page must use the Kindle-specific list price when the page
    exposes one (struck-through price in the Kindle row, or a "Kindle Price"
    basis element labelled "List Price"), and only fall back to
    apex-basisprice-value when no Kindle-specific list is found. Savings must
    be recomputed from price/list_price, never read from
    apex-savings-percentage (which references the print list).
    """

    def _scraper(self):
        from sources.amazon import AmazonDealsScraper
        cfg = {"sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }}, "scraping": {"max_books_per_source": 50}}
        return AmazonDealsScraper(cfg)

    def _info(self, body: str) -> dict:
        from bs4 import BeautifulSoup
        return self._scraper().parse_product_page(
            BeautifulSoup(f"<html><body>{body}</body></html>", "lxml"))

    def test_kindle_row_struck_list_price_preferred_over_print(self):
        # Kindle row carries the ebook list price as a struck-through element;
        # apex-basisprice-value holds the PRINT list price (higher). The parser
        # must pick the KINDLE list, not the print basis.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle '
            '<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">$9.99</span>$9.99</span> '
            '$1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$12.99 $12.99</span>'
            '<span class="apex-savings-percentage">-85%</span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99
        assert info["list_price"] == 9.99          # ebook list, NOT 12.99
        assert info["savings_pct"] == 80           # recomputed, NOT 85
        assert info["available"] is True

    def test_kindle_price_basis_element_preferred(self):
        # A "Kindle Price" basis element labelled "List Price" (not "Print
        # List Price") supplies the ebook list price.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle $1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$12.99 $12.99</span>'
            '<span class="kindle-price">Kindle Price: $1.99 '
            '<span class="a-color-secondary">List Price:</span> '
            '<span class="a-text-price">$9.99</span></span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99
        assert info["list_price"] == 9.99
        assert info["savings_pct"] == 80

    def test_print_list_fallback_when_no_kindle_list(self):
        # No Kindle-specific list on the page → NO list price is claimed
        # (t_13047664). apex-basisprice-value is the PRINT list price; using
        # it as the savings basis inflated Shards of Earth to "90% off" when
        # the ebook's own Digital List Price is $5.00. Without an ebook list
        # price, savings_pct stays unset and the require_discount gate drops
        # the book — never a print-list-based savings claim.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle $1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$19.99 $19.99</span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99
        assert "list_price" not in info          # print list NOT used
        assert "savings_pct" not in info         # no unverifiable savings claim

    def test_list_price_from_kindle_row_still_works(self):
        # When the page DOES expose the ebook's own list (struck-through in
        # the Kindle row), savings is computed from it.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle '
            '<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">$5.00</span>$5.00</span> '
            '$1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$19.99 $19.99</span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99
        assert info["list_price"] == 5.00
        assert info["savings_pct"] == 60           # round((1-1.99/5.00)*100)

    def test_apex_savings_percentage_ignored(self):
        # apex-savings-percentage (85%, vs PRINT list) must be ignored even
        # when present; savings is recomputed from the ebook list price.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle '
            '<span class="a-text-price" data-a-strike="true">$9.99</span> '
            '$1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$12.99 $12.99</span>'
            '<span class="apex-savings-percentage">-85%</span>'
        )
        info = self._info(body)
        assert info["savings_pct"] == 80

    def test_live_dom_shape_ebook_price_value(self):
        # Real B0B2P2N58X DOM (verified 2026-08-14): the Kindle row uses
        # `.ebook-price-value` + aria-label for the deal and slot-extraMessage
        # for availability. The ebook list price lives in a struck
        # `.a-text-price` INSIDE the row — the parser must prefer it over the
        # print apex basis even though apex appears later in the DOM.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="slot-title">Kindle</span>'
            '<span class="slot-price">'
            '<span aria-label="$1.99" class="a-color-price ebook-price-value">$1.99</span>'
            '<span class="a-text-price" data-a-strike="true">$9.99</span>'
            '</span>'
            '<span class="slot-extraMessage"><span class="kindleExtraMessage">'
            '<span aria-label="Available instantly">Available instantly</span>'
            '</span></span>'
            '</div>'
            '<div class="swatchElement" id="tmm-grid-swatch-AUDIO_DOWNLOAD">Audiobook $0.00</div>'
            '<div class="swatchElement" id="tmm-grid-swatch-HARDCOVER">Hardcover $11.71</div>'
            '<div class="swatchElement" id="tmm-grid-swatch-PAPERBACK">Paperback $8.94</div>'
            '</div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$12.99 $12.99</span>'
            '<span class="apex-savings-percentage">-85%</span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99
        assert info["list_price"] == 9.99          # ebook list, NOT 12.99
        assert info["savings_pct"] == 80           # 80%, NOT 85%
        assert info.get("available") is True
        assert info.get("is_ebook") is True

    def test_membership_row_or_price_to_buy(self):
        # 'Kindle $0.00 or $1.99 to buy' — the "to buy" price is the deal;
        # the $0.00 membership token must not be reported as the price.
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="slot-title">Kindle</span>'
            '<span class="slot-price">'
            '<span aria-label="$0.00" class="a-color-price ebook-price-value">$0.00</span>'
            '</span>'
            '<span class="slot-extraMessage"><span class="kindleExtraMessage">'
            '<span>or $1.99 to buy</span>'
            '</span></span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
        )
        info = self._info(body)
        assert info["price"] == 1.99               # "to buy" price wins
        assert "list_price" not in info            # no list exposed → unset

    def test_clean_price_first_numeric_token(self):
        # "$12.99 $12.99" (hidden+visible spans) must not become "12.9912.99"
        from sources.amazon import AmazonDealsScraper
        assert AmazonDealsScraper._clean_price("$12.99 $12.99") == 12.99
        assert AmazonDealsScraper._clean_price("$ 1 . 99") == 1.99
        assert AmazonDealsScraper._clean_price("$0.00") == 0.0
        assert AmazonDealsScraper._clean_price("") is None

    def test_price_over_cap_dropped_by_require_discount(self, bf):
        # A cheap price with NO real discount must not pass the limited-time gate
        books = [sample_book(price=1.99, list_price=2.99, savings_pct=33)]
        assert bf.apply(books, require_discount=True) == []


# ─── Formatter: limited-time marker (spike t_e934a2a3 §6f) ─────────

class TestLimitedTimeMarker:
    def test_marker_present_at_50_percent(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=50)], 1, 0)
        assert "limited time" in r

    def test_marker_present_above_50(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=80)], 1, 0)
        assert "limited time" in r

    def test_marker_absent_below_50(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=30)], 1, 0)
        assert "limited time" not in r

    def test_marker_absent_without_savings(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=None)], 1, 0)
        assert "limited time" not in r


# ─── Storage: BookBub price gates (best-price 30d, anti-stale 14d) ──

from datetime import datetime, timedelta, timezone  # noqa: E402


def _date(days_ago: int) -> str:
    """ISO date `days_ago` before today (UTC) — for price_history keys."""
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def _seed_seen(storage, asin: str, hist: dict[int, float],
               lowest=None, dropped_on_days_ago=None):
    """Write a seen_books.json entry with price_history keyed by days-ago."""
    entry = {
        "title": "Test Book",
        "author": "",
        "url": "",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:00:00+00:00",
        "lowest_price": lowest if lowest is not None else min(hist.values()),
        "price_history": {_date(d): p for d, p in hist.items()},
    }
    if dropped_on_days_ago is not None:
        entry["price_dropped_on"] = _date(dropped_on_days_ago) + "T00:00:00+00:00"
    storage._write_seen({asin: entry})


class TestPriceHistory:
    def test_mark_seen_records_today(self, tmp_storage):
        tmp_storage.mark_seen("B0X", "T", 1.99)
        entry = tmp_storage._read_seen()["B0X"]
        assert entry["price_history"] == {_date(0): 1.99}

    def test_same_day_last_price_wins(self, tmp_storage):
        tmp_storage.mark_seen("B0X", "T", 1.99)
        tmp_storage.mark_seen("B0X", "T", 2.49)
        hist = tmp_storage._read_seen()["B0X"]["price_history"]
        assert hist == {_date(0): 2.49}          # one entry per day

    def test_older_days_kept(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {1: 1.99, 2: 2.49})
        tmp_storage.mark_seen("B0X", "T", 0.99)
        hist = tmp_storage._read_seen()["B0X"]["price_history"]
        assert hist[_date(1)] == 1.99
        assert hist[_date(2)] == 2.49
        assert hist[_date(0)] == 0.99

    def test_legacy_entry_backfilled(self, tmp_storage):
        # Old seen_books.json entries have no price_history → seeded on mark_seen
        tmp_storage._write_seen({"B0X": {"title": "T", "lowest_price": 1.99}})
        tmp_storage.mark_seen("B0X", "T", 2.49)
        entry = tmp_storage._read_seen()["B0X"]
        assert entry["price_history"] == {_date(0): 2.49}


class TestBestPrice30d:
    def test_worse_price_suppressed(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {3: 1.99})
        assert tmp_storage.best_price_30d("B0X", 2.99) is False

    def test_equal_price_allowed(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {3: 1.99})
        assert tmp_storage.best_price_30d("B0X", 1.99) is True

    def test_better_price_allowed(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {3: 1.99})
        assert tmp_storage.best_price_30d("B0X", 0.99) is True

    def test_unknown_asin_allowed(self, tmp_storage):
        assert tmp_storage.best_price_30d("B0NEW", 2.99) is True

    def test_no_history_allowed(self, tmp_storage):
        tmp_storage._write_seen({"B0X": {"title": "T", "lowest_price": 1.99}})
        assert tmp_storage.best_price_30d("B0X", 2.99) is True

    def test_older_than_30d_ignored(self, tmp_storage):
        # A 0.99 from 40 days ago is OUTSIDE the window — must not block 2.99
        _seed_seen(tmp_storage, "B0X", {40: 0.99})
        assert tmp_storage.best_price_30d("B0X", 2.99) is True
        # ...but an in-window price still gates: 2.99 > 2.49 (5d ago) → blocked
        _seed_seen(tmp_storage, "B0Y", {40: 0.99, 5: 2.49})
        assert tmp_storage.best_price_30d("B0Y", 2.49) is True
        assert tmp_storage.best_price_30d("B0Y", 2.99) is False

    def test_legacy_lowest_within_30d(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {}, lowest=1.99, dropped_on_days_ago=5)
        assert tmp_storage.best_price_30d("B0X", 2.99) is False


class TestAntiStale:
    def test_days_at_price_counts_within_window(self, tmp_storage):
        hist = {i: 1.99 for i in range(20)}
        hist.update({i: 2.99 for i in range(20, 30)})
        _seed_seen(tmp_storage, "B0X", hist)
        assert tmp_storage.days_at_price("B0X", 1.99) == 20

    def test_days_at_price_ignores_outside_window(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {i: 1.99 for i in range(35, 45)})
        assert tmp_storage.days_at_price("B0X", 1.99) == 0

    def test_stale_after_14_days(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {i: 1.99 for i in range(15)})
        assert tmp_storage.is_stale("B0X", 1.99) is True

    def test_not_stale_under_14_days(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {i: 1.99 for i in range(10)})
        assert tmp_storage.is_stale("B0X", 1.99) is False

    def test_custom_max_days(self, tmp_storage):
        _seed_seen(tmp_storage, "B0X", {i: 1.99 for i in range(12)})
        assert tmp_storage.is_stale("B0X", 1.99, max_days=10) is True
        assert tmp_storage.is_stale("B0X", 1.99, max_days=15) is False

    def test_unknown_asin_not_stale(self, tmp_storage):
        assert tmp_storage.is_stale("B0NEW", 1.99) is False

    def test_should_report_combines_gates(self, tmp_storage):
        # worse price → suppressed even though not stale
        _seed_seen(tmp_storage, "B0X", {3: 1.99})
        assert tmp_storage.should_report("B0X", 2.99) is False
        # best price but parked there for 20 days → stale → suppressed
        _seed_seen(tmp_storage, "B0Y", {i: 1.99 for i in range(20)})
        assert tmp_storage.should_report("B0Y", 1.99) is False
        # fresh + best price → reportable
        _seed_seen(tmp_storage, "B0Z", {i: 1.99 for i in range(5)})
        assert tmp_storage.should_report("B0Z", 1.99) is True


# ─── Filters: BookBub limited-time gate (>=50% off) ────────────────

class TestDiscountGate:
    def test_50_percent_passes(self, bf):
        assert bf.matches_discount(50) is True

    def test_above_50_passes(self, bf):
        assert bf.matches_discount(80) is True

    def test_below_50_fails(self, bf):
        assert bf.matches_discount(49) is False

    def test_no_savings_fails(self, bf):
        assert bf.matches_discount(None) is False

    def test_apply_requires_discount(self, bf):
        books = [
            sample_book(savings_pct=80),
            sample_book(asin="B0TEST00002", savings_pct=20),
            sample_book(asin="B0TEST00003", savings_pct=None),
        ]
        result = bf.apply(books, require_discount=True)
        assert len(result) == 1
        assert result[0]["savings_pct"] == 80

    def test_apply_default_keeps_missing_savings(self, bf):
        # Pre-enrichment pass must not require the discount yet
        books = [sample_book(savings_pct=None)]
        assert len(bf.apply(books)) == 1


# ─── Descriptive failure reasons (t_f893b2c1) ───────────────────────

class TestErrorReason:
    """last_failures/log reasons must say WHAT failed (captcha vs rate-limit),
    not a meaningless 'HTTP 200'."""

    def test_rate_limit_status(self):
        assert LightpandaFetcher._error_reason(503, "anything") == "HTTP 503"

    def test_captcha_validate(self):
        content = ("<h4>Click the button below to continue shopping</h4>"
                   "<form action='/errors_page/validateCaptcha'>")
        # The real interstitial contains BOTH signatures; the first listed
        # (validateCaptcha) wins.
        assert LightpandaFetcher._error_reason(200, content) == "captcha (validateCaptcha)"

    def test_cloudfront_sorry(self):
        assert LightpandaFetcher._error_reason(
            200, "<html>Sorry! Something went wrong.</html>") == "cloudfront 503"

    def test_unknown_body(self):
        assert LightpandaFetcher._error_reason(200, "<html>hello</html>") == "error page content"


# ─── Noise: one summarized WARN line, per-URL only in debug (t_f893b2c1) ─

class TestSummarizedWarning:
    def test_normal_run_summarizes_reasons_without_urls(self, tmp_path, monkeypatch, capsys):
        url = "https://www.amazon.com/dp/B0TEST00001"
        captcha = ("<html><body><h4>Click the button below to continue "
                   "shopping</h4></body></html>")
        state = _fake_subprocess_run(monkeypatch, [_lp_result(url, 200, captcha)])
        cfg = {"scraping": {
            "lightpanda_cookies": str(tmp_path / "cookies.json"),
            "lightpanda_retries": 3,
            "lightpanda_retry_backoff": [0, 0],
            "debug": False,
        }}
        f = LightpandaFetcher(cfg)
        f.fetch_all([url])
        out = capsys.readouterr().out
        assert "failed 1 URL(s) after 3 attempt(s): 1× captcha (continue shopping)" in out
        # Normal mode: no per-URL dump
        assert "https://www.amazon.com/dp/B0TEST00001" not in out

    def test_debug_mode_dumps_per_url(self, tmp_path, monkeypatch, capsys):
        url = "https://www.amazon.com/dp/B0TEST00001"
        captcha = ("<html><body><h4>Click the button below to continue "
                   "shopping</h4></body></html>")
        state = _fake_subprocess_run(monkeypatch, [_lp_result(url, 200, captcha)])
        cfg = {"scraping": {
            "lightpanda_cookies": str(tmp_path / "cookies.json"),
            "lightpanda_retries": 3,
            "lightpanda_retry_backoff": [0, 0],
            "debug": True,
        }}
        f = LightpandaFetcher(cfg)
        f.fetch_all([url])
        out = capsys.readouterr().out
        assert "failed 1 URL(s) after 3 attempt(s): 1× captcha (continue shopping)" in out
        assert url in out          # per-URL dump present in debug mode

    def test_mixed_reasons_aggregated(self, tmp_path, monkeypatch, capsys):
        u1 = "https://www.amazon.com/dp/B0TEST00001"
        u2 = "https://www.amazon.com/dp/B0TEST00002"
        u3 = "https://www.amazon.com/dp/B0TEST00003"
        one_response = json.dumps({"results": [
            {"url": u1, "http_status": 200,
             "content": "<h4>Click the button below to continue shopping</h4>"},
            {"url": u2, "http_status": 200,
             "content": "<h4>Click the button below to continue shopping</h4>"},
            {"url": u3, "http_status": 503,
             "content": "Sorry! Something went wrong."},
        ]})
        state = _fake_subprocess_run(monkeypatch, [one_response])
        cfg = {"scraping": {
            "lightpanda_cookies": str(tmp_path / "cookies.json"),
            "lightpanda_retries": 1,
        }}
        f = LightpandaFetcher(cfg)
        f.fetch_all([u1, u2, u3])
        out = capsys.readouterr().out
        assert "2× captcha (continue shopping)" in out
        assert "1× HTTP 503" in out
        assert u1 not in out         # no URLs in normal mode


# ─── HARD RULE: never report a book without a live product-page price ─

class TestEnrichHardRule:
    """enrich_books() must DROP books whose live price can't be confirmed
    from the product page (t_f893b2c1) — no stale deal-listing price may
    reach the report."""

    def _scraper(self):
        from sources.amazon import AmazonDealsScraper
        cfg = {"sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }}, "scraping": {"max_books_per_source": 50}}
        return AmazonDealsScraper(cfg)

    def _soup(self, body: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(f"<html><body>{body}</body></html>", "lxml")

    def test_no_soup_dropped(self, capsys):
        from scraper import enrich_books
        book = sample_book(url="https://www.amazon.com/dp/B0TEST00001")
        out = enrich_books([book], {}, self._scraper())
        assert out == []
        assert "DROP (no product page)" in capsys.readouterr().err

    def test_soup_without_price_dropped(self, capsys):
        from scraper import enrich_books
        book = sample_book(url="https://www.amazon.com/dp/B0TEST00001")
        soups = {"https://www.amazon.com/dp/B0TEST00001": self._soup("<div>no price here</div>")}
        out = enrich_books([book], soups, self._scraper())
        assert out == []
        assert "DROP (no live price)" in capsys.readouterr().err

    def test_captcha_page_dropped(self, capsys):
        # A captcha interstitial has no product price → dropped, never
        # reported with the deal-listing price.
        from scraper import enrich_books
        book = sample_book(url="https://www.amazon.com/dp/B0TEST00001", price=0.99)
        captcha = ("<div class='a-box'><h4>Click the button below to continue "
                   "shopping</h4></div>")
        soups = {"https://www.amazon.com/dp/B0TEST00001": self._soup(captcha)}
        out = enrich_books([book], soups, self._scraper())
        assert out == []

    def test_live_price_kept_and_overwrites(self, capsys):
        from scraper import enrich_books
        book = sample_book(url="https://www.amazon.com/dp/B0TEST00001", price=0.99)
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="slot-price"><span aria-label="$1.99" class="ebook-price-value">$1.99</span></span>'
            '<span class="slot-extraMessage">Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
        )
        soups = {"https://www.amazon.com/dp/B0TEST00001": self._soup(body)}
        out = enrich_books([book], soups, self._scraper())
        assert len(out) == 1
        assert out[0]["price"] == 1.99     # live product-page price wins
        assert out[0].get("price_source") == "kindle_row"

    def test_print_list_only_page_kept_price_but_no_savings(self, capsys):
        """Regression (t_13047664): a page exposing ONLY the PRINT list
        (apex-basisprice-value) must NOT claim savings from it. enrich keeps
        the verified price but leaves list_price/savings_pct unset so the
        require_discount gate drops the book instead of reporting an
        inflated print-list-based discount."""
        from scraper import enrich_books
        book = sample_book(url="https://www.amazon.com/dp/B0TEST00001", price=0.99)
        body = (
            '<div id="tmmSwatches">'
            '<div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle $1.99 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 1 . 99</span>'
            '<span class="apex-basisprice-value">$19.99 $19.99</span>'
        )
        soups = {"https://www.amazon.com/dp/B0TEST00001": self._soup(body)}
        out = enrich_books([book], soups, self._scraper())
        assert len(out) == 1
        assert out[0]["price"] == 1.99
        assert "list_price" not in out[0]
        assert "savings_pct" not in out[0]


# ─── Region detection (t_13047664) ─────────────────────────────────

class TestRegionDetection:
    def test_us_page_detected(self):
        from scraper import detect_region
        region, evidence = detect_region("<html>$1.99 Print List Price: $19.99</html>")
        assert region == "US"

    def test_eur_page_detected(self):
        from scraper import detect_region
        region, evidence = detect_region("<html>€3,66 Digital List Price: 5,00 €</html>")
        assert region == "non-US"
        assert "EUR" in evidence

    def test_amazon_de_page_detected(self):
        from scraper import detect_region
        # A page actually SERVED by the German marketplace carries EUR
        # currency JSON even when the footer text mentions amazon.de.
        region, _ = detect_region('<html>"currencyCode": "EUR" amazon.de</html>')
        assert region == "non-US"

    def test_footer_marketplace_list_not_false_positive(self):
        from scraper import detect_region
        # US pages list every marketplace in the footer; a bare amazon.de
        # mention must NOT flag the page as non-US (t_13047664).
        region, _ = detect_region(
            '<html>"currencyCode": "USD" $1.99 '
            '"amazon.ca","amazon.co.uk","amazon.de","amazon.fr"</html>')
        assert region == "US"

    def test_empty_page_unknown(self):
        from scraper import detect_region
        region, _ = detect_region("")
        assert region == "unknown"

    def test_robot_page_unknown(self):
        from scraper import detect_region
        region, _ = detect_region("<html>Amazon.com</html>")
        assert region == "unknown"

    def test_visible_ron_pricing_detected(self):
        # (t_ccbd16c0, spike RC-3) visible "RON" price text — the
        # currencyCode JSON isn't always present but the rendered price is.
        from scraper import detect_region
        region, evidence = detect_region(
            '<html>Kindle RON 0.00 or RON 9.02 to buy</html>')
        assert region == "non-US"
        assert "RON" in evidence

    def test_visible_lei_pricing_detected(self):
        from scraper import detect_region
        region, evidence = detect_region('<html>9,02 lei</html>')
        assert region == "non-US"
        assert "RON" in evidence


# ─── Buybox Digital List Price (t_ccbd16c0 Layer 1) ────────────────

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture_soup(name: str):
    from bs4 import BeautifulSoup
    html = (FIXTURES_DIR / f"{name}.html").read_text()
    return BeautifulSoup(html, "lxml")


class TestBuyboxDigitalListPrice:
    """Layer 1 (t_ccbd16c0, spike RC-1): the ebook list price now lives in
    the buybox apex-basisprice-value labelled 'Digital List Price' — NOT in
    the Kindle swatch (the struck price is gone from the no-JS HTML). The
    parser must read it and set list_source='apex_basisprice_digital'.
    'Print List Price' (apex-basisprice-value with a print label) must
    never be used as a savings basis."""

    def _scraper(self):
        from sources.amazon import AmazonDealsScraper
        cfg = {"sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }}, "scraping": {"max_books_per_source": 50}}
        return AmazonDealsScraper(cfg)

    def test_digital_list_fixture_extracts_list_and_savings(self):
        # Real B0FX7CJNYJ dump: Digital List Price: $4.99, Kindle $2.49 to buy
        info = self._scraper().parse_product_page(_fixture_soup("digital_list_buybox"))
        assert info["price"] == 2.49
        assert info["list_price"] == 4.99
        assert info["list_source"] == "apex_basisprice_digital"
        assert info["savings_pct"] == 50        # round((1-2.49/4.99)*100)
        assert info["available"] is True

    def test_print_list_fixture_never_used(self):
        # Real B00J1ISJFA dump: Print List Price: $20.00 → NOT the ebook
        # list; no savings may be claimed from it (t_13047664).
        info = self._scraper().parse_product_page(_fixture_soup("print_list_buybox"))
        assert info["price"] == 1.99
        assert "list_price" not in info
        assert "savings_pct" not in info

    def test_no_basisprice_fixture_no_list(self):
        # Real B08GC6FXVZ dump (Kindle Unlimited, no basisprice at all)
        info = self._scraper().parse_product_page(_fixture_soup("no_basisprice_buybox"))
        assert info["price"] == 1.99
        assert "list_price" not in info
        assert "savings_pct" not in info

    def test_helper_digital_vs_print(self):
        from sources.amazon import AmazonDealsScraper
        assert AmazonDealsScraper._buybox_digital_list_price(
            _fixture_soup("digital_list_buybox")) == 4.99
        assert AmazonDealsScraper._buybox_digital_list_price(
            _fixture_soup("print_list_buybox")) is None
        assert AmazonDealsScraper._buybox_digital_list_price(
            _fixture_soup("no_basisprice_buybox")) is None
        assert AmazonDealsScraper._buybox_digital_list_price(None) is None

    def test_helper_with_label_sibling_dom(self):
        # Compact synthetic DOM mirroring the real layout: offscreen label
        # ("Digital List Price: $4.99") + visible label + value as siblings
        # inside the same centralizedApexBasisPriceCSS div.
        body = (
            '<div class="centralizedApexBasisPriceCSS">'
            '<span class="apex-basisprice-feature"><span class="aok-relative">'
            '<span data-basisprice-label="{label} {price}" class="a-size-small aok-offscreen '
            'apex-basisprice-offscreen-label">Digital List Price: $4.99</span>'
            '<span aria-hidden="true" class="a-size-small a-color-secondary aok-align-center">'
            '<span class="apex-basisprice-label">Digital List Price:</span>'
            '<span class="a-price a-text-price apex-basisprice-value" data-a-size="s" '
            'data-a-strike="true" data-a-color="secondary">'
            '<span class="a-offscreen">$4.99</span><span aria-hidden="true">$4.99</span>'
            '</span></span></span></div>'
        )
        from bs4 import BeautifulSoup
        from sources.amazon import AmazonDealsScraper
        soup = BeautifulSoup(body, "lxml")
        assert AmazonDealsScraper._buybox_digital_list_price(soup) == 4.99
        # Same DOM but labelled Print → None
        soup_print = BeautifulSoup(body.replace("Digital List Price", "Print List Price"), "lxml")
        assert AmazonDealsScraper._buybox_digital_list_price(soup_print) is None

    def test_digital_list_wired_before_legacy_basis(self):
        # Both the new buybox digital list AND the legacy "Kindle Price"
        # basis present: the buybox digital list wins (it's the modern,
        # exact "Digital List Price" source).
        body = (
            '<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            '<span class="a-button-text">Kindle $2.49 Available instantly</span>'
            '</div></div>'
            '<span class="apex-pricetopay-value">$ 2 . 49</span>'
            '<div class="centralizedApexBasisPriceCSS">'
            '<span class="apex-basisprice-offscreen-label">Digital List Price: $4.99</span>'
            '<span class="apex-basisprice-value">$4.99 $4.99</span></div>'
            '<span class="kindle-price">Kindle Price: $2.49 '
            '<span class="a-color-secondary">List Price:</span> '
            '<span class="a-text-price">$9.99</span></span>'
        )
        from bs4 import BeautifulSoup
        info = self._scraper().parse_product_page(BeautifulSoup(body, "lxml"))
        assert info["list_price"] == 4.99
        assert info["list_source"] == "apex_basisprice_digital"
        assert info["savings_pct"] == 50


# ─── Currency-agnostic parsing (t_ccbd16c0 Layer 2) ────────────────

class TestCurrencyAgnosticParsing:
    """The parser must not anchor on '$': a RON-served page ("RON 9.02",
    "lei 9,02") must still extract prices instead of silently returning
    None everywhere (spike RC-3)."""

    def _scraper(self):
        from sources.amazon import AmazonDealsScraper
        cfg = {"sources": {"amazon": {
            "base_url": "https://www.amazon.com",
            "sff_todays_deals": "/x",
            "sff_monthly_deals": "/y",
        }}, "scraping": {"max_books_per_source": 50}}
        return AmazonDealsScraper(cfg)

    def _info(self, body: str) -> dict:
        from bs4 import BeautifulSoup
        return self._scraper().parse_product_page(
            BeautifulSoup(f"<html><body>{body}</body></html>", "lxml"))

    def test_clean_price_strips_leading_currency(self):
        from sources.amazon import AmazonDealsScraper
        assert AmazonDealsScraper._clean_price("RON 9.02") == 9.02
        assert AmazonDealsScraper._clean_price("USD 12.99") == 12.99
        assert AmazonDealsScraper._clean_price("lei 9.02") == 9.02
        assert AmazonDealsScraper._clean_price("€ 3.66") == 3.66
        assert AmazonDealsScraper._clean_price("£ 7.99") == 7.99
        assert AmazonDealsScraper._clean_price("$1.99") == 1.99          # unchanged
        assert AmazonDealsScraper._clean_price("$12.99 $12.99") == 12.99  # unchanged

    def test_ron_membership_row_parses(self):
        # "Kindle RON 0.00 or RON 9.02 to buy" — the "to buy" price wins,
        # the row counts as a priced Kindle ebook, and it's buyable.
        info = self._info(
            '<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            'Kindle RON 0.00 or RON 9.02 to buy</div></div>'
            '<span class="apex-pricetopay-value">RON 9 . 02</span>')
        assert info["price"] == 9.02
        assert info.get("is_ebook") is True
        assert info.get("available") is True

    def test_ron_digital_list_price(self):
        # Digital List Price rendered in RON — list extracted + savings
        # recomputed from the currency-agnostic numbers.
        info = self._info(
            '<div id="tmmSwatches"><div class="swatchElement" id="tmm-grid-swatch-KINDLE">'
            'Kindle RON 0.00 or RON 9.02 to buy</div></div>'
            '<span class="apex-pricetopay-value">RON 9 . 02</span>'
            '<div class="centralizedApexBasisPriceCSS">'
            '<span class="apex-basisprice-offscreen-label">Digital List Price: RON 19.99</span>'
            '<span class="apex-basisprice-value">RON 19.99 RON 19.99</span></div>')
        assert info["price"] == 9.02
        assert info["list_price"] == 19.99
        assert info["list_source"] == "apex_basisprice_digital"
        assert info["savings_pct"] == 55        # round((1-9.02/19.99)*100)

    def test_kindle_price_basis_currency_agnostic(self):
        from bs4 import BeautifulSoup
        from sources.amazon import AmazonDealsScraper
        soup = BeautifulSoup(
            '<span class="kindle-price">Kindle Price: RON 9.02 '
            '<span class="a-color-secondary">List Price:</span> '
            '<span class="a-text-price">RON 19.99</span></span>', "lxml")
        assert AmazonDealsScraper._kindle_price_basis(soup) == 19.99


# ─── History-based deal fallback (t_ccbd16c0 Layer 3) ──────────────

class TestHistoryDealFallback:
    """A book with NO digital list price on the page (savings_pct unset)
    is still a deal when the pipeline flagged list_source='history'
    (price under the cap + fresh at-or-below-best vs 30-day history). The
    strict >=50% gate stays in force whenever savings_pct IS available."""

    def test_history_marker_passes_require_discount(self, bf):
        book = sample_book(savings_pct=None, list_price=None, list_source="history")
        assert bf.apply([book], require_discount=True) == [book]

    def test_no_marker_still_dropped(self, bf):
        book = sample_book(savings_pct=None, list_price=None)
        assert bf.apply([book], require_discount=True) == []

    def test_verified_discount_still_passes_strict_gate(self, bf):
        book = sample_book(savings_pct=80)
        assert bf.apply([book], require_discount=True) == [book]

    def test_verified_small_discount_still_dropped(self, bf):
        # A real list price with savings < 50% is not a deal — history
        # fallback must NOT rescue it (only the absence of a list price).
        book = sample_book(savings_pct=30, list_price=2.99, list_source="kindle_row")
        assert bf.apply([book], require_discount=True) == []

    def test_history_marker_over_cap_dropped(self, bf):
        book = sample_book(price=7.99, savings_pct=None, list_price=None,
                           list_source="history")
        assert bf.apply([book], require_discount=True) == []

    def test_matches_discount_or_history(self, bf):
        assert bf.matches_discount_or_history(sample_book(savings_pct=80)) is True
        assert bf.matches_discount_or_history(
            sample_book(savings_pct=None, list_source="history")) is True
        assert bf.matches_discount_or_history(
            sample_book(savings_pct=None)) is False
        assert bf.matches_discount_or_history(
            sample_book(savings_pct=20)) is False


# ─── Availability: Kindle-Unlimited membership rows (t_ccbd16c0 L4) ─

class TestAvailabilityKURow:
    """'Kindle $0.00 or $1.99 to buy' is a buyable now (KU membership +
    purchase price) — available=True. Only 'Currently unavailable' /
    'will be released on' / 'Pre-order' mean unavailable."""

    def test_ku_row_or_price_to_buy_available(self):
        body = ('<div id="tmmSwatches"><div class="swatchElement selected" '
                'id="tmm-grid-swatch-KINDLE">'
                '<span class="slot-title">Kindle</span>'
                '<span class="slot-price"><span class="ebook-price-value">$0.00</span></span>'
                '<span class="slot-extraMessage"><span class="kindleExtraMessage">'
                '<span>or $1.99 to buy</span></span></span>'
                '</div></div>')
        info = _sff_scraper().parse_product_page(_pp_soup(body))
        assert info.get("available") is True
        assert info.get("preorder", False) is False

    def test_ku_row_ron_available(self):
        body = ('<div id="tmmSwatches"><div class="swatchElement" '
                'id="tmm-grid-swatch-KINDLE">'
                'Kindle RON 0.00 or RON 9.02 to buy</div></div>')
        info = _sff_scraper().parse_product_page(_pp_soup(body))
        assert info.get("available") is True

    def test_unavailable_still_unavailable_with_or_price(self):
        # Even a row that mentions a to-buy price but ALSO says currently
        # unavailable must stay unavailable (unavailable wins).
        body = ('<div id="tmmSwatches"><div class="swatchElement" '
                'id="tmm-grid-swatch-KINDLE">'
                'Kindle $0.00 or $1.99 to buy Currently unavailable</div></div>')
        info = _sff_scraper().parse_product_page(_pp_soup(body))
        assert info.get("available") is False


# ─── Formatter: history-deal marker (t_ccbd16c0 Layer 3) ───────────

class TestHistoryDealMarker:
    def test_history_deal_shows_price_drop_marker(self):
        r = format_report([sample_book(list_price=None, savings_pct=None,
                                       list_source="history")], 1, 0)
        assert "📉 price drop" in r
        assert "limited time" not in r

    def test_verified_savings_still_shows_limited_time(self):
        r = format_report([sample_book(list_price=9.99, savings_pct=80)], 1, 0)
        assert "limited time" in r
        assert "📉 price drop" not in r

    def test_no_marker_for_plain_book(self):
        r = format_report([sample_book(list_price=None, savings_pct=None)], 1, 0)
        assert "📉 price drop" not in r
        assert "limited time" not in r


# ─── Robot-check detection in curl_cffi (t_13047664) ───────────────

class TestRobotCheckDetection:
    def test_robot_check_signature_detected(self):
        from sources.base import BaseScraper
        assert BaseScraper._is_robot_check(
            "<html><body>Amazon.com Robot Check</body></html>") is True

    def test_captcha_signature_detected(self):
        from sources.base import BaseScraper
        assert BaseScraper._is_robot_check(
            "<form action='/errors_page/validateCaptcha'>") is True

    def test_large_real_page_not_robot_check(self):
        from sources.base import BaseScraper
        big = "<html>" + ("<div data-asin='X'>book $1.99</div>" * 5000) + "</html>"
        assert BaseScraper._is_robot_check(big) is False

    def test_robot_check_reason_named(self):
        from sources.base import BaseScraper
        assert "captcha" in BaseScraper._robot_check_reason(
            "Click the button below to continue shopping")
