#!/usr/bin/env python3
"""
Fetch upcoming events in and around Blankenberge and generate an RSS 2.0 feed.

Sources (tried in order):
  1. Publiq / UiT Databank Search API  (set env PUBLIQ_API_KEY)
  2. Scrape uitinvlaanderen.be HTML
  3. Fallback: keep existing feed items from feed.xml
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── config ────────────────────────────────────────────────────────────────────
FEED_TITLE = "Evenementen in en rond Blankenberge"
FEED_LINK = "https://kristofpoppe.github.io/aitests/blankenberge-rss/feed.xml"
SITE_LINK = "https://www.visitblankenberge.be"
FEED_DESCRIPTION = (
    "Upcoming events in and around Blankenberge, Belgium – updated daily."
)
FEED_LANGUAGE = "nl-BE"
FEED_TTL = "60"

OUTPUT_FILE = Path(__file__).parent / "feed.xml"
PUBLIQ_KEY = os.getenv("PUBLIQ_API_KEY", "")

HEADERS = {
    "User-Agent": (
        "Blankenberge-Events-RSS/1.0 "
        "(https://github.com/kristofpoppe/aitests; tourist info bot)"
    ),
    "Accept": "application/json",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _get(url: str, extra_headers: dict | None = None) -> bytes:
    h = {**HEADERS, **(extra_headers or {})}
    req = Request(url, headers=h)
    with urlopen(req, timeout=20) as r:
        return r.read()


def _nl(obj: object, default: str = "") -> str:
    """Extract Dutch (or first available) text from a multilingual dict."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for lang in ("nl", "fr", "en"):
            if lang in obj and obj[lang]:
                return obj[lang]
        vals = [v for v in obj.values() if isinstance(v, str) and v]
        return vals[0] if vals else default
    return default


def _iso_to_rfc(iso: str) -> str:
    if not iso:
        return format_datetime(datetime.now(timezone.utc))
    iso = re.sub(r"\+\d{2}:\d{2}$", "+00:00", iso).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    except ValueError:
        return format_datetime(datetime.now(timezone.utc))


# ── source 1: Publiq SAPI ─────────────────────────────────────────────────────
def _fetch_publiq() -> list[dict]:
    if not PUBLIQ_KEY:
        print("[publiq] No API key – skipping.", file=sys.stderr)
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    params = {
        "addressLocality": "Blankenberge",
        "availableFrom": today,
        "limit": 50,
        "embed": "true",
        "workflowStatus": "READY_FOR_VALIDATION,APPROVED",
        "sort[startDate]": "asc",
        "apiKey": PUBLIQ_KEY,
    }
    url = "https://search.uitdatabank.be/offers/?" + urlencode(params)
    try:
        data = json.loads(_get(url))
        members = data.get("member", [])
        print(f"[publiq] {len(members)} events fetched.", file=sys.stderr)
        return members
    except URLError as e:
        print(f"[publiq] Request failed: {e}", file=sys.stderr)
        return []


def _publiq_to_item(event: dict) -> ET.Element | None:
    title = _nl(event.get("name", ""))
    if not title:
        return None

    url = event.get("url") or event.get("@id") or ""
    guid = event.get("@id") or url or title

    # dates
    start = (event.get("subEvent") or [{}])[0].get("startDate") or event.get(
        "startDate", ""
    )
    end = (event.get("subEvent") or [{}])[0].get("endDate") or event.get("endDate", "")

    parts: list[str] = []
    if start:
        date_str = start[:10]
        if end and end[:10] != start[:10]:
            date_str += f" – {end[:10]}"
        parts.append(f"<b>Datum:</b> {date_str}")

    loc = event.get("location", {})
    place = _nl(loc.get("name", ""))
    addr = loc.get("address", {})
    street = _nl(addr.get("streetAddress", ""))
    city = _nl(addr.get("addressLocality", ""))
    if place:
        parts.append(f"<b>Locatie:</b> {place}")
    if street or city:
        parts.append(", ".join(filter(None, [street, city])))

    desc = _nl(event.get("description", ""))
    if desc:
        short = desc[:500] + ("…" if len(desc) > 500 else "")
        parts.append(f"<p>{short}</p>")

    imgs = event.get("mediaObject") or []
    if imgs and isinstance(imgs, list):
        img_url = imgs[0].get("contentUrl", "")
        if img_url:
            parts.append(f'<img src="{img_url}" alt="{title}" style="max-width:600px"/>')

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = url
    ET.SubElement(item, "guid", isPermaLink="false").text = guid
    ET.SubElement(item, "description").text = "<br/>".join(parts)
    ET.SubElement(item, "pubDate").text = _iso_to_rfc(start)

    for term in (event.get("terms") or [])[:3]:
        label = _nl(term.get("label", ""))
        if label:
            ET.SubElement(item, "category").text = label

    return item


# ── source 2: scrape uitinvlaanderen.be ───────────────────────────────────────
def _fetch_scrape() -> list[ET.Element]:
    """
    Scrape uitinvlaanderen.be agenda page for Blankenberge events.
    Returns ready-made <item> elements (no intermediate dict needed).
    """
    try:
        from html.parser import HTMLParser
    except ImportError:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = (
        "https://www.uitinvlaanderen.be/agenda?"
        + urlencode({"q": "", "location": "Blankenberge", "date": today})
    )

    try:
        html = _get(url, {"Accept": "text/html"}).decode("utf-8", errors="replace")
    except URLError as e:
        print(f"[scrape] uitinvlaanderen failed: {e}", file=sys.stderr)
        return []

    class EventParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.events: list[dict] = []
            self._cur: dict | None = None
            self._field: str | None = None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            cls = d.get("class", "")
            if "c-event-teaser" in cls or "js-event" in cls:
                self._cur = {"url": d.get("href", ""), "title": "", "date": "", "place": ""}
            if self._cur is not None:
                if "c-event-teaser__title" in cls or "c-teaser__title" in cls:
                    self._field = "title"
                elif "c-date-display" in cls or "c-event-teaser__date" in cls:
                    self._field = "date"
                elif "c-event-teaser__location" in cls or "c-teaser__location" in cls:
                    self._field = "place"
                elif tag == "a" and "href" in d and self._cur.get("url") == "":
                    self._cur["url"] = d["href"]

        def handle_data(self, data):
            if self._cur and self._field:
                self._cur[self._field] = (
                    self._cur.get(self._field, "") + data
                ).strip()

        def handle_endtag(self, tag):
            self._field = None
            if self._cur and self._cur.get("title"):
                self.events.append(self._cur)
                self._cur = None

    parser = EventParser()
    parser.feed(html)
    print(f"[scrape] {len(parser.events)} events scraped.", file=sys.stderr)

    items = []
    for ev in parser.events:
        title = ev.get("title", "").strip()
        if not title:
            continue
        url = ev.get("url", "")
        if url and not url.startswith("http"):
            url = "https://www.uitinvlaanderen.be" + url
        item = ET.Element("item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", isPermaLink="false").text = url or title
        desc_parts = []
        if ev.get("date"):
            desc_parts.append(f"<b>Datum:</b> {ev['date']}")
        if ev.get("place"):
            desc_parts.append(f"<b>Locatie:</b> {ev['place']}")
        ET.SubElement(item, "description").text = "<br/>".join(desc_parts)
        ET.SubElement(item, "pubDate").text = format_datetime(
            datetime.now(timezone.utc)
        )
        items.append(item)
    return items


# ── source 3: preserve existing items ────────────────────────────────────────
def _load_existing_items() -> list[ET.Element]:
    if not OUTPUT_FILE.exists():
        return []
    try:
        tree = ET.parse(OUTPUT_FILE)
        return tree.getroot().findall(".//item")
    except ET.ParseError:
        return []


# ── build feed ────────────────────────────────────────────────────────────────
def build_feed(items: list[ET.Element]) -> ET.ElementTree:
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")

    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = FEED_TITLE
    ET.SubElement(ch, "link").text = SITE_LINK
    ET.SubElement(ch, "description").text = FEED_DESCRIPTION
    ET.SubElement(ch, "language").text = FEED_LANGUAGE
    ET.SubElement(ch, "ttl").text = FEED_TTL
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )
    atom_link = ET.SubElement(ch, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", FEED_LINK)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for item in items:
        ch.append(item)

    print(f"[feed] {len(items)} items in feed.", file=sys.stderr)
    return ET.ElementTree(rss)


def main() -> None:
    items: list[ET.Element] = []

    # 1. Publiq SAPI (requires API key)
    publiq_events = _fetch_publiq()
    for ev in publiq_events:
        item = _publiq_to_item(ev)
        if item is not None:
            items.append(item)

    # 2. HTML scrape fallback
    if not items:
        items = _fetch_scrape()

    # 3. Keep existing items if nothing new was fetched
    if not items:
        print("[feed] No new items – preserving existing feed.", file=sys.stderr)
        items = _load_existing_items()

    tree = build_feed(items)
    ET.indent(tree, space="  ")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)

    print(f"[feed] Written to {OUTPUT_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
