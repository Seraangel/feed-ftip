# Finanztip Daily RSS

This project turns the Finanztip Daily overview page into a static RSS feed.

The GitHub Action fetches `https://www.finanztip.de/daily/` every 5 minutes,
stores discovered articles in SQLite, generates `public/rss.xml` from the 50
newest articles, and publishes the result through GitHub Pages.

## Local usage

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
python scripts/update_feed.py --db data/articles.sqlite --out public/rss.xml --limit 50
```

The scraper uses this User-Agent:

```text
Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0
```

## GitHub Pages

Enable GitHub Pages for this repository with **GitHub Actions** as the source.
After the workflow has run successfully, the feed is available at:

```text
https://<user-or-org>.github.io/<repo>/rss.xml
```

## Scheduling

The workflow uses this cron expression:

```text
*/5 * * * *
```

Scheduled GitHub Actions run in UTC and can be delayed during high load. The
short interval helps compensate for occasional scheduling or deployment delays.

## Stored data

Articles are stored in `data/articles.sqlite` with these fields:

- `url`
- `title`
- `summary`
- `published_at`
- `first_seen_at`
- `updated_at`

Only data visible on the overview page is stored; full article contents are not
copied.
