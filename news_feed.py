# © 2026 CoAssisted Workspace. Licensed under MIT.
"""News feed adapter for the daily standup right column.

Returns up to N normalized news items shaped like:

    {
        "title":        str,
        "url":          str,           # canonical link to article
        "source":       str,           # publication / RSS title
        "snippet":      str,           # 1-2 sentence summary
        "thumb_url":    str | None,    # cover image URL
        "thumb_color":  str,           # fallback solid color when no thumb
        "published_at": str,           # ISO 8601
    }

Sources (in fallback order):
    1. config['news_rss_url'] — your preferred RSS feed
    2. BBC World News RSS (default)
    3. Built-in fixture for offline / test mode

Items cache for 30 minutes via external_feeds.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.request import Request, urlopen

import config
import external_feeds


# Default RSS source — BBC World feed (no auth, free, reliable).
DEFAULT_RSS_URL = "https://feeds.bbci.co.uk/news/world/rss.xml"

# Stable color palette to assign to thumb-less items (deterministic hashing
# means the same article always gets the same color across regenerations).
_THUMB_COLORS = [
    "#1a4f8c", "#2d6e3e", "#a23a3a", "#9a6b00",
    "#5a3a8c", "#0d6470", "#7a3a8c", "#4a5a6c",
]


# --------------------------------------------------------------------------- #
# Fetcher
# --------------------------------------------------------------------------- #


def _color_for(seed: str) -> str:
    h = hashlib.md5((seed or "").encode("utf-8")).hexdigest()
    idx = int(h[:2], 16) % len(_THUMB_COLORS)
    return _THUMB_COLORS[idx]


def _strip_html(text: str) -> str:
    """Drop HTML tags + collapse whitespace. Just enough for snippet rendering."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch_rss(url: str, timeout_seconds: int = 8) -> str:
    """Pull raw RSS XML. Raises on network failure."""
    req = Request(url, headers={"User-Agent": "CoAssistedWorkspace/0.7"})
    with urlopen(req, timeout=timeout_seconds) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_rss(xml_text: str, source_label: str = "") -> list[dict]:
    """Parse RSS 2.0 / Atom XML into normalized item dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Discover channel-level title for default source label
    channel_title = source_label
    chan = root.find("channel")
    if chan is not None:
        title_el = chan.find("title")
        if title_el is not None and title_el.text:
            channel_title = source_label or title_el.text.strip()

    items_root = chan.findall("item") if chan is not None else []
    if not items_root:
        # Atom feed
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items_root = root.findall("atom:entry", ns) or root.findall("entry")

    out: list[dict] = []
    for it in items_root:
        title = _grab(it, "title")
        url = _grab(it, "link") or _grab_link_href(it)
        description = _grab(it, "description") or _grab(it, "summary")
        pub_date = _grab(it, "pubDate") or _grab(it, "published") or _grab(it, "updated")
        thumb = _extract_thumb(it)
        out.append({
            "title": (title or "").strip(),
            "url": (url or "").strip(),
            "source": channel_title.strip(),
            "snippet": _truncate(_strip_html(description or ""), 160),
            "thumb_url": thumb,
            "thumb_color": _color_for(url or title or ""),
            "published_at": _normalize_date(pub_date),
        })
    return [r for r in out if r["title"]]


def _grab(elem, tag: str) -> str | None:
    el = elem.find(tag)
    if el is None:
        return None
    return (el.text or "").strip() or None


def _grab_link_href(elem) -> str | None:
    """Atom-style <link href='...'/>."""
    for link in elem.findall("link"):
        href = link.get("href")
        if href:
            return href
    return None


def _extract_thumb(elem) -> str | None:
    """Look for media:thumbnail / media:content / enclosure with image type."""
    # media:thumbnail
    for tag in ("{http://search.yahoo.com/mrss/}thumbnail",
                "{http://search.yahoo.com/mrss/}content"):
        el = elem.find(tag)
        if el is not None:
            url = el.get("url")
            if url:
                return url
    # enclosure with image
    for enc in elem.findall("enclosure"):
        url = enc.get("url")
        if url and (enc.get("type", "").startswith("image/") or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return url
    return None


def _normalize_date(s: str | None) -> str:
    if not s:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is None:
            return s
        return dt.isoformat()
    except Exception:
        return s


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def get_top_news(
    *,
    limit: int = 6,
    rss_url: Optional[str] = None,
    source_label: str = "",
    ttl_seconds: int = 1800,
) -> list[dict]:
    """Return up to `limit` news items. Cached for 30 minutes by default.

    Resolution order:
        1. `rss_url` argument
        2. config['news_rss_url']
        3. DEFAULT_RSS_URL (BBC World)
        4. Fixture if all network attempts fail
    """
    url = rss_url or config.get("news_rss_url") or DEFAULT_RSS_URL
    key = f"news:{url}"

    # Frozen path for tests
    if key in external_feeds._FROZEN_VALUES:
        items = external_feeds._FROZEN_VALUES[key]
        return list(items)[:limit]

    def fetch() -> list[dict]:
        try:
            xml_text = _fetch_rss(url)
            items = _parse_rss(xml_text, source_label=source_label)
            if items:
                return items
        except Exception:
            pass
        return _fixture_news()

    items = external_feeds._cached(key, ttl_seconds=ttl_seconds, fetcher=fetch)
    return list(items or [])[:limit]


def _fixture_news() -> list[dict]:
    """Reasonable offline fallback so the briefing always has a news column."""
    today = _dt.datetime.now().astimezone()
    base = today.replace(hour=5, minute=0, second=0, microsecond=0)
    items = [
        ("Markets open higher on tech earnings beat",
         "Reuters", "https://example.com/news/markets-open-higher",
         "Tech sector continues to lead broader market gains as Q1 earnings "
         "outpace consensus expectations.", 0,
         "https://picsum.photos/seed/markets-tech/200/200"),
        ("Fed minutes signal patience on rate path",
         "WSJ", "https://example.com/news/fed-minutes",
         "Officials suggested they remain on hold pending more data on "
         "inflation and the labor market.", 1,
         "https://picsum.photos/seed/fed-minutes/200/200"),
        ("Anthropic + Surefox announce expanded partnership",
         "TechCrunch", "https://example.com/news/anthropic-surefox",
         "The two companies extended their multi-year agreement covering "
         "agent infrastructure and safety research.", 2,
         "https://picsum.photos/seed/partnership/200/200"),
        ("Climate accord enters next ratification phase",
         "AP", "https://example.com/news/climate-accord",
         "Member nations met to finalize implementation rules for the "
         "framework's third tier.", 3,
         "https://picsum.photos/seed/climate-accord/200/200"),
        ("AI policy hearing scheduled for next week",
         "Politico", "https://example.com/news/ai-policy-hearing",
         "Senate committee chair set the date and witness list for the next "
         "round of regulatory testimony.", 4,
         "https://picsum.photos/seed/ai-policy/200/200"),
        ("Major airline returns to profitability",
         "Bloomberg", "https://example.com/news/airline-profit",
         "Q1 results showed cost discipline outpacing the demand softness "
         "analysts had projected.", 5,
         "https://picsum.photos/seed/airline-profit/200/200"),
    ]
    return [
        {
            "title": title,
            "url": url,
            "source": source,
            "snippet": snippet,
            "thumb_url": thumb,
            "thumb_color": _color_for(url),
            "published_at": (base - _dt.timedelta(hours=offset)).isoformat(),
        }
        for title, source, url, snippet, offset, thumb in items
    ]


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def freeze_for_tests(items: list[dict], rss_url: str = DEFAULT_RSS_URL) -> None:
    external_feeds._FROZEN_VALUES[f"news:{rss_url}"] = list(items)


def unfreeze_for_tests() -> None:
    keys = [k for k in list(external_feeds._FROZEN_VALUES) if k.startswith("news:")]
    for k in keys:
        external_feeds._FROZEN_VALUES.pop(k, None)
