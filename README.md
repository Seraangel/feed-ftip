# Finanztip Daily RSS

This project turns the Finanztip Daily overview page into a static RSS feed.

The GitHub Action fetches `https://www.finanztip.de/daily/` when triggered by
the Cloudflare scheduler,
stores discovered articles in SQLite, generates `public/rss.xml` from up to 100
newest articles, and publishes the result through GitHub Pages when the feed
data changed.

## Local usage

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
python scripts/update_feed.py --db data/articles.sqlite --out public/rss.xml --limit 100
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

The feed contains up to 100 of the newest stored articles. GitHub Pages is a
static host, so query parameters cannot change the generated XML: for example,
`rss.xml?size=500` returns the same file as `rss.xml`. A dynamic `size` parameter
(with a maximum of 1000) requires a server-side endpoint, such as a Cloudflare
Worker, Vercel Function, or GitHub Actions-generated static feed variants.

## Scheduling

The internal GitHub `schedule` trigger is not used because GitHub can delay or
drop scheduled runs during high load. A free Cloudflare Worker in
[`cloudflare-scheduler`](cloudflare-scheduler) instead triggers the workflow
with GitHub `repository_dispatch`.

Cloudflare invokes the Worker every five minutes (UTC). The Worker then applies
this daylight-saving-safe schedule in `Europe/Berlin`:

```text
06:00-22:55  every five minutes
23:00-05:59  once per hour, on the hour
```

One-time setup instructions are in
[`cloudflare-scheduler/README.md`](cloudflare-scheduler/README.md). The Python
updater, SQLite database, and GitHub Pages deployment stay unchanged.

## Monthly tags

On the first successful update of each month, the workflow creates an annotated
tag named `YYYY.MM`, for example `2026.08`. The tag points to the current
`main` state and uses the `Europe/Berlin` time zone.

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
