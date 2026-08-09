"""Book filters: price cap, genre keywords, tracked authors."""

import re
from typing import Any


class BookFilter:
    def __init__(self, config: dict[str, Any]):
        fc = config.get("filters", {})
        self.max_price = fc.get("max_price", 4.99)
        self.genres = [g.lower() for g in fc.get("genres", [])]
        self.tracked_authors = [a.lower() for a in fc.get("tracked_authors", [])]
    
    def matches_price(self, price: float | None) -> bool:
        """Book must have a price and be under the cap."""
        return price is not None and price <= self.max_price
    
    def matches_genre(self, title: str, author: str = "", 
                      description: str = "", from_sff_page: bool = False) -> bool:
        """Check if title/author/description contains genre keywords.
        If the book came from an SFF-specific Amazon page, genre check is lenient
        (Amazon already categorized it as SFF)."""
        # Books from SFF pages are already genre-verified by Amazon
        if from_sff_page:
            return True
        
        text = f"{title} {author} {description}".lower()
        for genre in self.genres:
            # Use word boundaries for short keywords to avoid false matches
            if len(genre.split()) == 1 and len(genre) <= 8:
                if re.search(rf"\b{re.escape(genre)}\b", text):
                    return True
            elif genre in text:
                return True
        return False
    
    def matches_author(self, author: str) -> bool:
        """Check if author matches any tracked authors. 
        Always returns True when no authors are tracked."""
        if not self.tracked_authors:
            return True  # no filter active
        author_lower = author.lower()
        return any(ta in author_lower for ta in self.tracked_authors)
    
    def apply(self, books: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of books. Returns only matches."""
        results = []
        for book in books:
            price = book.get("price")
            title = book.get("title", "")
            author = book.get("author", "")
            from_sff = book.get("from_sff_page", False)
            
            if not self.matches_price(price):
                continue
            if not self.matches_genre(title, author, from_sff_page=from_sff):
                continue
            if not self.matches_author(author):
                continue
            
            results.append(book)
        return results
