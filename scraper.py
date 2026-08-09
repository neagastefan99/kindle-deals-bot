#!/usr/bin/env python3
"""Kindle Deals Bot — scrapes Amazon SFF deals and prints a Markdown report."""

import sys
from pathlib import Path

import yaml

# Ensure project root is on Python path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from sources.amazon import AmazonDealsScraper
from filters import BookFilter
from formatter import format_report, format_empty_report
from storage import Storage


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    storage = Storage(PROJECT_ROOT / "data")
    book_filter = BookFilter(config)
    
    # --- Scrape ---
    print("🔍 Scraping Amazon Kindle SFF deals...", file=sys.stderr)
    scraper = AmazonDealsScraper(config)
    all_books = scraper.scrape_all()
    print(f"  Deal books scraped: {len(all_books)}", file=sys.stderr)
    
    # Also scrape Best Sellers for trending books
    print("🔍 Scraping Amazon SFF Best Sellers...", file=sys.stderr)
    bs_url = scraper.base_url + config["sources"]["amazon"]["sff_best_sellers"]
    try:
        bs_books = scraper.scrape_best_sellers(bs_url)
        print(f"  Best sellers scraped: {len(bs_books)} books", file=sys.stderr)
        # Deduplicate best sellers against existing books
        existing_asins = {b["asin"] for b in all_books if b.get("asin")}
        for book in bs_books:
            if book.get("asin") and book["asin"] not in existing_asins:
                existing_asins.add(book["asin"])
                all_books.append(book)
    except Exception as e:
        print(f"  [WARN] Best sellers scrape failed: {e}", file=sys.stderr)
    
    print(f"  Total combined: {len(all_books)} books", file=sys.stderr)
    
    if not all_books:
        print(format_empty_report())
        storage.log_run({"scraped": 0, "filtered": 0, "new": 0, "price_drops": 0, "error": "No books scraped"})
        return
    
    # --- Filter ---
    filtered = book_filter.apply(all_books)
    print(f"  After filtering: {len(filtered)} books", file=sys.stderr)
    
    # --- Enrich with accurate product-page prices ---
    # Deal page prices can differ from the actual product page (KU vs buy price,
    # region-specific deals, dynamic pricing). Visit each product page for the
    # real apex-pricetopay price.
    print("💰 Fetching accurate product-page prices...", file=sys.stderr)
    for book in filtered:
        if not book.get("url"):
            continue
        try:
            soup = scraper.fetch_html(book["url"])
            if soup:
                apex = soup.select_one('.apex-pricetopay-value .a-offscreen')
                if apex and apex.text.strip():
                    real_price = scraper._clean_price(apex.text.strip())
                    if real_price is not None and real_price > 0:
                        old = book.get("price")
                        book["price"] = real_price
                        if old != real_price:
                            print(f"  💵 {book['title'][:50]}... ${old} → ${real_price}", file=sys.stderr)
        except Exception:
            pass  # keep original price if product page fails
    
    # Re-filter with accurate prices (some may now exceed max_price)
    filtered = book_filter.apply(filtered)
    print(f"  After price enrichment: {len(filtered)} books", file=sys.stderr)
    
    # --- Deduplicate & track ---
    new_count = 0
    dropped_count = 0
    report_books = []
    
    for book in filtered:
        asin = book.get("asin", "")
        title = book.get("title", "")
        author = book.get("author", "")
        price = book.get("price")
        url = book.get("url", "")
        
        if not asin or not title:
            continue
        
        is_new = storage.is_new(asin)
        better_price = storage.is_better_price(asin, price or 999.99)
        
        if is_new or better_price:
            storage.mark_seen(asin, title, price or 0.0, author, url)
            report_books.append(book)
            if is_new:
                new_count += 1
                print(f"  🆕 NEW: {title} (${price})", file=sys.stderr)
            elif better_price:
                dropped_count += 1
                print(f"  📉 DROP: {title} (${price})", file=sys.stderr)
    
    # --- Format & output ---
    print(f"  Reporting: {new_count} new + {dropped_count} price drops", file=sys.stderr)
    report = format_report(report_books, new_count, dropped_count)
    print(report)
    
    # --- Log run ---
    storage.log_run({
        "scraped": len(all_books),
        "filtered": len(filtered),
        "new": new_count,
        "price_drops": dropped_count,
        "reported": len(report_books),
    })


if __name__ == "__main__":
    main()
