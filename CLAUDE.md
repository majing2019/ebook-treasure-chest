# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

电子书下载宝库 — an automated e-book aggregation system that scrapes book data from Chinese reading platforms (帆书, 微信读书, 京东读书, 喜马拉雅), stores it as categorized Markdown files, generates a searchable static site, and deploys via GitHub Pages. Currently tracks 24,000+ books across 1,000 categories.

Live site: https://jbiaojerry.github.io/ebook-treasure-chest/

## Architecture & Data Flow

```
Source Website → Sync Scripts → md/ (Markdown files) → JSON → HTML (docs/) → GitHub Pages
```

The pipeline has four stages:

1. **Sync Engine** (`scripts/sync/`): Async Python scrapers fetch book details by ID from the source site. Supports full sync, incremental sync, and batch processing with resume/crash-recovery via `processed_ids.json`.
2. **Markdown Storage** (`md/`): 1,000 category files, each containing a table of books (`书名 | 作者 | 格式 | 下载链接`). This is the canonical data store.
3. **Data Processing** (`scripts/parse_md_to_json.py`): Parses all MD files into `docs/all-books.json` (7.1 MB) and `docs/parse-stats.json`.
4. **Site Generation** (`scripts/generate_index.py`): Builds `docs/index.html` (Solarized Dark theme) with real-time client-side search (`docs/search.js` loads `all-books.json`).

## Key Commands

```bash
# Install dependencies
pip install requests beautifulsoup4 aiohttp

# Full sync (scrapes all books — requires BOOK_SITE_DOMAIN)
BOOK_SITE_DOMAIN='https://example.com' python3 scripts/sync/sync_all_books.py
BOOK_SITE_DOMAIN='https://example.com' python3 scripts/sync/sync_all_books.py --batch-size 20000  # batched mode

# Incremental sync (only new books since last run)
BOOK_SITE_DOMAIN='https://example.com' python3 scripts/sync/incremental_sync.py

# Regenerate JSON from markdown
python3 scripts/parse_md_to_json.py

# Regenerate the HTML site
python3 scripts/generate_index.py
```

## Environment Variables

- **`BOOK_SITE_DOMAIN`** — Base URL of the source book website (stored in GitHub Secrets). Required by all sync scripts. Locally, you can create `scripts/sync/config.py` with this value, or export the env var.
- **`OUTPUT_DIR`** — `md` for production, `md_test` for testing (default: `md_test`).

## GitHub Actions Workflows

| Workflow | Trigger | Timeout | What it does |
|---|---|---|---|
| `full-sync.yml` | Manual only | 10h | Full book sync with batch processing (20k books/batch), updates README + JSON, commits with conflict resolution |
| `incremental-sync.yml` | Daily 2AM UTC + manual | 1h | Finds new book IDs, syncs only new books, updates README + JSON |
| `generate-site.yml` | Push to main (scripts/** or md/**) | — | Parses MD → JSON → HTML, commits generated site files |

All workflows use `GITHUB_TOKEN` for commits. Sync workflows include retry logic for git push conflicts.

## Code Conventions

- **Language**: Python 3.10, no external build system (plain scripts, no pip package)
- **Chinese comments and UI**: All user-facing strings, commit messages, and log output are in Chinese
- **Async scraping**: Uses `aiohttp` with semaphore (20 concurrent) and 0.5s delay between requests
- **Markdown table format**: Each category file follows `| 书名 | 作者 | 格式 | 下载链接 |` — parsers depend on this exact structure
- **Error handling in sync**: Resume via `processed_ids.json`, backup before full sync (`backup_md.py`), retry on push failures

## Important Files

- `scripts/sync/test_batch_sync.py` — Core batch processing engine (despite the "test" name, it's the production batch processor)
- `scripts/sync/parse_book_detail_enhanced.py` — BeautifulSoup scraper for individual book pages
- `scripts/sync/find_max_book_id.py` — Discovers the maximum book ID (homepage scan → pagination → binary search fallback)
- `scripts/sync/update_readme_hot_categories.py` — Updates the top-20 categories section in README.md
- `docs/search.js` — Client-side search with multi-keyword support, debounced input, XSS-safe URL rendering
