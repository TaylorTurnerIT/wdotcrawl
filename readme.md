A Python command line client for the wiki hosting platform [Wikidot](http://www.wikidot.com).

Original work by [wdotcrawl's author](https://github.com/wdotcrawl) — thank you for building this!

---

#### What it does

- List all pages on a Wikidot site
- View revision history and page source for any page
- Download the entire site as a Git repository, with accurate commit dates, authors, and messages pulled from Wikidot's revision history
- Incrementally update an existing dump — re-running the same command only fetches revisions newer than the last commit

---

#### Setup

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```
uv sync
```

---

#### Usage

**Full dump** (first run):

```
uv run python crawl.py http://example.wikidot.com --dump ./ExampleRepo
```

**Incremental update** (subsequent runs — same command):

```
uv run python crawl.py http://example.wikidot.com --dump ./ExampleRepo
```

The script detects the existing `.git` repository, reads the last commit timestamp, and only fetches revisions newer than that point.

**Other queries:**

```
# List all pages
uv run python crawl.py http://example.wikidot.com --list-pages

# View a page's revision log
uv run python crawl.py http://example.wikidot.com --log --page example-page

# Print a page's source
uv run python crawl.py http://example.wikidot.com --source --page example-page
```

**Options:**

| Flag | Description |
|------|-------------|
| `--dump DIR` | Download site history to DIR as a Git repo |
| `--page NAME` | Target a single page (used with --source, --log, --dump) |
| `--depth N` | Limit to last N revisions per page (default: 10000) |
| `--revids` | Store Wikidot revision IDs in `.revid` file with each commit |
| `--delay MS` | Delay between Wikidot requests in milliseconds (default: 200) |
| `--debug` | Print debug info |

---

#### Automation

To keep a dump up to date, run on a schedule. Example cron entry (daily at 3am):

```
0 3 * * * cd /path/to/wdotcrawl && uv run python crawl.py http://example.wikidot.com --dump ./ExampleRepo >> ./sync.log 2>&1
```

---

#### Changes from original

- Ported from Python 2 to Python 3
- Replaced Mercurial repository backend with Git
- Added incremental update support — re-running syncs only new revisions
- Cross-platform paths (was Windows-only)

---

#### Useful links

- [Wikidot ListPages module](http://www.wikidot.com/doc-modules:listpages-module)
- [Wikidot source (old)](https://github.com/gabrys/wikidot/blob/master/php/modules/history/PageRevisionListModule.php)
- [Similar project](https://github.com/kerel-fs/ogn-rdb/blob/master/wikidotcrawler.py)
