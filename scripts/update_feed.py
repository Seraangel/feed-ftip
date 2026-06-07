#!/usr/bin/env python3
"""Fetch Finanztip Daily articles, persist them in SQLite, and emit RSS."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup, Tag


SOURCE_URL = "https://www.finanztip.de/daily/"
SOURCE_HOST = "www.finanztip.de"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"

GERMAN_MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "maerz": 3,
    "marz": 3,
    "m\u00e4rz": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    summary: str
    published_at: str | None


def normalize_url(href: str) -> str | None:
    url = urljoin(SOURCE_URL, href)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != SOURCE_HOST:
        return None

    path = parsed.path.rstrip("/") + "/"
    if path == "/daily/" or not path.startswith("/daily/"):
        return None

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_german_date(text: str) -> str | None:
    normalized = (
        text.lower()
        .replace("\xa0", " ")
        .replace(",", " ")
        .replace("den ", " ")
    )

    numeric = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", normalized)
    if numeric:
        day, month, year = (int(part) for part in numeric.groups())
        return datetime(year, month, day, tzinfo=timezone.utc).isoformat()

    named = re.search(
        r"\b(\d{1,2})\.\s*([a-z\u00e4\u00f6\u00fc]+)\s+(\d{4})\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if named:
        day = int(named.group(1))
        month_name = named.group(2).replace("\u00e4", "ae")
        month = GERMAN_MONTHS.get(named.group(2)) or GERMAN_MONTHS.get(month_name)
        if month:
            return datetime(int(named.group(3)), month, day, tzinfo=timezone.utc).isoformat()

    return None


def nearest_container(anchor: Tag) -> Tag:
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"article", "li"}:
            return parent
        classes = " ".join(parent.get("class", []))
        if re.search(r"(card|teaser|article|news|daily|item)", classes, re.IGNORECASE):
            return parent
    return anchor


def extract_summary(container: Tag, title: str) -> str:
    candidates: list[str] = []
    for selector in ("p", "[class*=summary]", "[class*=description]", "[class*=text]"):
        for node in container.select(selector):
            text = clean_text(node.get_text(" "))
            if text and text != title and len(text) >= 25:
                candidates.append(text)

    for candidate in candidates:
        if title not in candidate:
            return candidate
    return candidates[0] if candidates else ""


def extract_published_at(container: Tag) -> str | None:
    for node in container.find_all(["time", "span", "p", "div"], limit=20):
        if isinstance(node, Tag) and node.name == "time":
            raw_datetime = node.get("datetime")
            if raw_datetime:
                try:
                    parsed = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
                    return parsed.astimezone(timezone.utc).isoformat()
                except ValueError:
                    pass
        parsed = parse_german_date(clean_text(node.get_text(" ")))
        if parsed:
            return parsed
    return parse_german_date(clean_text(container.get_text(" ")))


def fetch_daily_html(timeout: int) -> str:
    response = requests.get(
        SOURCE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def extract_articles(document: str) -> list[Article]:
    soup = BeautifulSoup(document, "html.parser")
    articles: list[Article] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        url = normalize_url(str(anchor["href"]))
        if not url or url in seen:
            continue

        title = clean_text(anchor.get_text(" "))
        if len(title) < 8 or title.lower().startswith("image:"):
            continue

        container = nearest_container(anchor)
        articles.append(
            Article(
                url=url,
                title=title,
                summary=extract_summary(container, title),
                published_at=extract_published_at(container),
            )
        )
        seen.add(url)

    if not articles:
        raise RuntimeError("No Finanztip Daily article links found; page structure may have changed.")
    return articles


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_sort ON articles(published_at DESC, first_seen_at DESC)"
    )


def upsert_articles(connection: sqlite3.Connection, articles: Iterable[Article], now: str) -> int:
    changed = 0
    for article in articles:
        existing = connection.execute(
            "SELECT title, summary, published_at FROM articles WHERE url = ?",
            (article.url,),
        ).fetchone()

        if existing is None:
            connection.execute(
                """
                INSERT INTO articles (url, title, summary, published_at, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (article.url, article.title, article.summary, article.published_at, now, now),
            )
            changed += 1
            continue

        if tuple(existing) != (article.title, article.summary, article.published_at):
            connection.execute(
                """
                UPDATE articles
                SET title = ?, summary = ?, published_at = ?, updated_at = ?
                WHERE url = ?
                """,
                (article.title, article.summary, article.published_at, now, article.url),
            )
            changed += 1

    return changed


def load_feed_items(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(
        connection.execute(
            """
            SELECT url, title, summary, published_at, first_seen_at, updated_at
            FROM articles
            ORDER BY COALESCE(published_at, first_seen_at) DESC, first_seen_at DESC, url ASC
            LIMIT ?
            """,
            (limit,),
        )
    )


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def add_text(parent: ET.Element, name: str, text: str) -> ET.Element:
    element = ET.SubElement(parent, name)
    element.text = text
    return element


def build_rss(items: list[sqlite3.Row]) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    add_text(channel, "title", "Finanztip Daily")
    add_text(channel, "link", SOURCE_URL)
    add_text(channel, "description", "Aktuelle Daily-Artikel von Finanztip.")
    add_text(channel, "language", "de-DE")

    if items:
        latest_update = max(parse_iso_datetime(item["updated_at"]) for item in items)
        add_text(channel, "lastBuildDate", format_datetime(latest_update, usegmt=True))

    for row in items:
        item = ET.SubElement(channel, "item")
        add_text(item, "title", row["title"])
        add_text(item, "link", row["url"])
        guid = add_text(item, "guid", row["url"])
        guid.set("isPermaLink", "true")
        if row["summary"]:
            add_text(item, "description", row["summary"])
        item_date = parse_iso_datetime(row["published_at"] or row["first_seen_at"])
        add_text(item, "pubDate", format_datetime(item_date, usegmt=True))

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def update_feed(db_path: Path, out_path: Path, limit: int, timeout: int) -> tuple[int, int, bool]:
    document = fetch_daily_html(timeout)
    articles = extract_articles(document)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        ensure_schema(connection)
        changed_rows = upsert_articles(connection, articles, now)
        items = load_feed_items(connection, limit)
        rss_changed = write_if_changed(out_path, build_rss(items))

    return len(articles), changed_rows, rss_changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/articles.sqlite"))
    parser.add_argument("--out", type=Path, default=Path("public/rss.xml"))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        found, changed_rows, rss_changed = update_feed(args.db, args.out, args.limit, args.timeout)
    except Exception as exc:
        print(f"Failed to update feed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Found {found} articles; changed {changed_rows} database rows; "
        f"rss.xml {'updated' if rss_changed else 'unchanged'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
