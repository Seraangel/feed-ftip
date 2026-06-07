from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.update_feed import (
    build_rss,
    ensure_schema,
    extract_articles,
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
</main>
"""


class UpdateFeedTests(unittest.TestCase):
    def test_extract_articles_skips_image_links(self) -> None:
        articles = extract_articles(SAMPLE_HTML)

        self.assertEqual([article.url for article in articles], [
            "https://www.finanztip.de/daily/foo/",
            "https://www.finanztip.de/daily/bar/",
        ])
        self.assertEqual(articles[0].title, "Foo kostet weniger")
        self.assertEqual(articles[0].published_at, "2026-06-07T00:00:00+00:00")

    def test_upsert_is_stable_when_content_is_unchanged(self) -> None:
        articles = extract_articles(SAMPLE_HTML)
        with sqlite3.connect(":memory:") as connection:
            ensure_schema(connection)
            self.assertEqual(upsert_articles(connection, articles, "2026-06-08T10:00:00+00:00"), 2)
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

    def test_write_paths_can_be_created_in_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "public" / "rss.xml"
            self.assertTrue(write_if_changed(out_path, b"feed"))
            self.assertFalse(write_if_changed(out_path, b"feed"))
            self.assertEqual(out_path.read_bytes(), b"feed")


if __name__ == "__main__":
    unittest.main()
