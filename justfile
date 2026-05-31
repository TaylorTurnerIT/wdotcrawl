# wdotcrawl — Wikidot site crawler and wiki parser
#   just install     Install Python dependencies
#   just crawl       Dump the wiki into a git repo
#   just parse       Convert dumped .txt files to HTML
#   just all         Run the full pipeline: crawl + parse

site  := "http://spheresofpower.wikidot.com"
repo  := "./spheresofpower-repo"
html  := "./out_html"

# Install Python dependencies
install:
    uv sync

# Crawl the Wikidot site and build a git repo (incremental after first run)
crawl: install
    uv run python crawl.py {{site}} --dump {{repo}}

# Parse the dumped .txt files into .html
parse: install
    uv run python wiki_parser.py {{repo}} -o {{html}}

# List all pages on the site
list:
    uv run python crawl.py {{site}} --list-pages

# View a page's revision history (usage: just log PAGE=alteration)
log PAGE:
    uv run python crawl.py {{site}} --log --page {{PAGE}}

# View a page's source (usage: just source PAGE=alteration)
source PAGE:
    uv run python crawl.py {{site}} --source --page {{PAGE}}

# Run the full pipeline: crawl then parse
all: crawl parse
