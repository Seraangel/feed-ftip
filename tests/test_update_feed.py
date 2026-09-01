from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch
from xml.etree import ElementTree as ET

import requests

from scripts.update_feed import (
    ATOM_NAMESPACE,
    RSS_URL,
    build_rss,
    ensure_schema,
    extract_articles,
    fetch_daily_html,
    load_feed_items,
    upsert_articles,
    write_if_changed,
)


SAMPLE_HTML = """
<main>
  <article class="daily-card">
    <a href="/daily/foo/"><img alt="Image: Foo" /></a>
    <h3><a href="/daily/foo/">Foo kostet weniger</a></h3>
    <p>Eine kurze Zusammenfassung mit genug Zeichen.</p>
    <time datetime="2026-06-07">07.06.2026</time>
  </article>
  <article class="daily-card">
    <h3><a href="https://www.finanztip.de/daily/bar/">Bar wird besser</a></h3>
    <p>Die zweite Zusammenfassung fuer den Feed.</p>
    <span>06.06.2026</span>
  </article>
  <article class="daily-card">
    <h3><a href="/daily/umlaut/">So \u00fcberzeugst Du ETF-Skeptiker</a></h3>
    <p>F\u00fcr Unterst\u00fctzer und K\u00e4ufer sauber lesbar.</p>
    <span>05.06.2026</span>
  </article>
</main>
"""


class UpdateFeedTests(unittest.TestCase):
    @staticmethod
    def http_error(status_code: int) -> requests.HTTPError:
        response = Mock(status_code=status_code)
        return requests.HTTPError(response=response)

    def test_fetch_retries_503_every_ten_seconds(self) -> None:
        unavailable = Mock()
        unavailable.raise_for_status.side_effect = self.http_error(503)
        success = Mock(content=b"<main></main>")
        success.raise_for_status.return_value = None

        with patch("scripts.update_feed.requests.get", side_effect=[unavailable, unavailable, success]) as get:
            with patch("scripts.update_feed.time.sleep") as sleep:
                self.assertEqual(fetch_daily_html(30), "<main></main>")

        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(10), call(10)])

    def test_fetch_stops_429_retries_after_one_minute(self) -> None:
        limited = Mock()
        limited.raise_for_status.side_effect = self.http_error(429)

        with patch("scripts.update_feed.requests.get", return_value=limited) as get:
            with patch("scripts.update_feed.time.sleep") as sleep:
                with self.assertRaises(requests.HTTPError):
                    fetch_daily_html(30)

        self.assertEqual(get.call_count, 5)
        self.assertEqual(sleep.call_args_list, [call(15)] * 4)

    def test_extract_articles_skips_image_links(self) -> None:
        articles = extract_articles(SAMPLE_HTML)

        self.assertEqual([article.url for article in articles], [
            "https://www.finanztip.de/daily/foo/",
            "https://www.finanztip.de/daily/bar/",
            "https://www.finanztip.de/daily/umlaut/",
        ])
        self.assertEqual(articles[0].title, "Foo kostet weniger")
        self.assertEqual(articles[0].published_at, "2026-06-07T00:00:00+00:00")
        self.assertEqual(articles[2].title, "So \u00fcberzeugst Du ETF-Skeptiker")

    def test_upsert_is_stable_when_content_is_unchanged(self) -> None:
        articles = extract_articles(SAMPLE_HTML)
        with sqlite3.connect(":memory:") as connection:
            ensure_schema(connection)
            self.assertEqual(upsert_articles(connection, articles, "2026-06-08T10:00:00+00:00"), 3)
            self.assertEqual(upsert_articles(connection, articles, "2026-06-08T10:15:00+00:00"), 0)

    def test_build_rss_limits_to_loaded_items(self) -> None:
        articles = extract_articles(SAMPLE_HTML)
        with sqlite3.connect(":memory:") as connection:
            ensure_schema(connection)
            upsert_articles(connection, articles, "2026-06-08T10:00:00+00:00")
            items = load_feed_items(connection, 1)

        rss = ET.fromstring(build_rss(items))
        channel = rss.find("channel")
        self.assertIsNotNone(channel)
        self.assertEqual(len(channel.findall("item")), 1)
        self.assertEqual(channel.findtext("title"), "Finanztip Daily")
        self_link = channel.find(f"{{{ATOM_NAMESPACE}}}link")
        self.assertIsNotNone(self_link)
        self.assertEqual(self_link.attrib, {
            "href": RSS_URL,
            "rel": "self",
            "type": "application/rss+xml",
        })

    def test_write_paths_can_be_created_in_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "public" / "rss.xml"
            self.assertTrue(write_if_changed(out_path, b"feed"))
            self.assertFalse(write_if_changed(out_path, b"feed"))
            self.assertEqual(out_path.read_bytes(), b"feed")


if __name__ == "__main__":
    unittest.main()
