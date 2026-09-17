"""İçerik türü sınıflandırması (YouTube'daki Shorts / uzun video ayrımının karşılığı)."""
from __future__ import annotations

CONTENT_TYPES = ["Reels", "Fotoğraf", "Carousel", "Video", "Diğer"]


def classify(media_type: str | None, media_product_type: str | None) -> str:
    if media_product_type == "REELS":
        return "Reels"
    if media_type == "CAROUSEL_ALBUM":
        return "Carousel"
    if media_type == "IMAGE":
        return "Fotoğraf"
    if media_type == "VIDEO":
        return "Video"
    return "Diğer"

# Kaba gruplama: Reels ile geri kalan her şey (fotoğraf, carousel, feed videosu) = Feed
GROUPS = ["Reels", "Feed"]


def group_of(content_type: str | None) -> str:
    return "Reels" if content_type == "Reels" else "Feed"
