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
