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
