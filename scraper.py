"""DJ Mag Top 100 DJs poll archiver.

Scrapes the yearly Top 100 DJs poll results from djmag.com and stores them as
CSV files in djmag_rankings/ (one file per year plus a consolidated file
covering every archived year).

By default only incomplete years (no CSV yet, or fewer than 100 entries) and
the current poll year are fetched, so existing archives are never re-scraped.
Use --years to force specific years, --all to re-fetch everything, and --force
to overwrite existing files even when a fresh scrape finds less data.
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://djmag.com"
START_YEAR = 2004
POLL_SIZE = 100
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 2  # seconds between requests; don't hammer the site
MAX_PAGES = 5  # safety bound when following poll pagination
# djmag.com sits behind Cloudflare; a browser-like UA plus retries on
# transient errors keeps us from being dropped on the first hiccup.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Matches /top100djs/<year>/<rank>/<slug> on the path of both relative and
# absolute hrefs (the site mixes the two).
DJ_PATH_PATTERN = re.compile(r"^top100djs/(\d{4})/(\d{1,3})/(.+)$")


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    retry = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch(session, url):
    """GET a URL with a polite delay. Returns the Response, or None on failure."""
    time.sleep(REQUEST_DELAY)
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        print(f"  Request failed: {e}")
        return None


def clean_name(raw):
    """Collapse whitespace; empty string stays empty so callers can fall back."""
    return " ".join(str(raw).split()) if raw else ""


def name_from_slug(slug):
    """Rebuild a DJ name from a URL slug. Lossy for '&' and accents, so this
    is only a fallback when no real name is available from the page."""
    return clean_name(unquote(slug).replace("-", " ")).title()


def scrape_dj_mag_json(session, year_to_scrape):
    """Scrape the latest poll from the JSON-LD embedded in the main page."""
    print(f"Fetching {BASE_URL}/top100djs (JSON-LD method)")
    response = fetch(session, BASE_URL + "/top100djs")
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    script_tag = soup.find("script", {"type": "application/ld+json"})
    if not script_tag or not script_tag.string:
        print("  Could not find a JSON-LD script tag on the main page.")
        return []
    try:
        data = json.loads(script_tag.string)
    except json.JSONDecodeError as e:
        print(f"  Could not parse JSON-LD: {e}")
        return []
    return parse_json_ld(data, year_to_scrape)


def parse_json_ld(data, year_to_scrape):
    item_list = next(
        (item for item in data.get("@graph", []) if item.get("@type") == "ItemList"),
        None,
    )
    if not item_list:
        print("  Could not find ItemList in JSON-LD.")
        return []

    djs = []
    for item in item_list.get("itemListElement", []):
        try:
            path = urlparse(item["url"]).path.strip("/").split("/")
            year = int(path[-3])
            rank = int(item.get("position", path[-2]))
            # Prefer the real name; the slug is a lossy fallback.
            name = clean_name(item.get("name")) or name_from_slug(path[-1])
            if year == year_to_scrape:
                djs.append({"year": year, "rank": rank, "name": name})
        except (KeyError, IndexError, ValueError) as e:
            print(f"  Skipping a JSON-LD item due to a parsing error: {e} - {item}")
            continue
    return djs


def scrape_dj_mag_html(session, year_to_scrape):
    """Scrape a historical poll page, following its pagination.

    The listings are paginated (e.g. the 2004 poll shows ranks 1-64 on page 1
    and 65-100 on page 2), so keep fetching pages the pager links to, bounded
    by MAX_PAGES. Poll pages use the standard Drupal pager: /top100djs/<year>?page=N.
    """
    poll_url = f"{BASE_URL}/top100djs/{year_to_scrape}"
    entries = {}
    fetched = set()
    queue = [0]
    while queue and len(fetched) < MAX_PAGES:
        page_num = queue.pop(0)
        if page_num in fetched:
            continue
        fetched.add(page_num)
        url = poll_url if page_num == 0 else f"{poll_url}?page={page_num}"
        print(f"Fetching {url} (HTML link method, page {page_num + 1})")
        response = fetch(session, url)
        if response is None:
            continue
        page_entries, pager_pages = parse_poll_page(response.text, year_to_scrape)
        merge_entries(entries, page_entries)
        for p in sorted(pager_pages):
            if p not in fetched and p not in queue:
                queue.append(p)
        # If the first page is incomplete but no pager link was recognized,
        # probe the standard Drupal second page once as a fallback.
        if page_num == 0 and not queue and len(entries) < POLL_SIZE:
            queue.append(1)

    return entries_to_rows(entries, year_to_scrape)


def parse_poll_page(html, year_to_scrape):
    """Parse one page of a poll listing.

    Returns (entries, pager_pages): entries maps rank -> {"text": name from
    link text, "slug": name from URL slug}; pager_pages is the set of further
    ?page=N numbers the pager links to.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    pager_pages = set()
    poll_path = f"top100djs/{year_to_scrape}"
    for link in soup.find_all("a", href=True):
        url = urlparse(urljoin(poll_url_of(year_to_scrape), link["href"]))
        path = url.path.strip("/")
        if path == poll_path:
            query = parse_qs(url.query)
            pager_pages.update(int(p) for p in query.get("page", []) if p.isdigit())
            continue
        match = DJ_PATH_PATTERN.match(path)
        if not match or int(match.group(1)) != year_to_scrape:
            continue
        rank = int(match.group(2))
        entry = entries.setdefault(rank, {"text": "", "slug": ""})
        # The same DJ URL is often linked twice (image + caption); keep the
        # first non-empty version of each.
        entry["text"] = entry["text"] or clean_name(link.get_text())
        entry["slug"] = entry["slug"] or name_from_slug(match.group(3))
    return entries, pager_pages


def poll_url_of(year):
    return f"{BASE_URL}/top100djs/{year}"


def merge_entries(target, source):
    for rank, entry in source.items():
        existing = target.setdefault(rank, {"text": "", "slug": ""})
        existing["text"] = existing["text"] or entry["text"]
        existing["slug"] = existing["slug"] or entry["slug"]


def entries_to_rows(entries, year_to_scrape):
    if not entries:
        print("  Could not find any DJ links matching the required pattern.")
        return []
    missing = sorted(set(range(1, POLL_SIZE + 1)) - set(entries))
    if missing:
        print(f"  Warning: no link found for rank(s) {missing}.")
    return [
        {"year": year_to_scrape, "rank": rank, "name": entry["text"] or entry["slug"]}
        for rank, entry in sorted(entries.items())
    ]


def read_existing_rows(filename):
    """Read an archived yearly CSV back as row dicts; [] if missing/broken."""
    rows = []
    try:
        with open(filename, newline="", encoding="utf-8") as csvfile:
            for row in csv.DictReader(csvfile):
                rows.append({
                    "year": int(row["Year"]),
                    "rank": int(row["Rank"]),
                    "name": row["DJ Name"],
                })
    except (OSError, KeyError, ValueError):
        return []
    return rows


def write_csv(filename, data):
    """Write data to a CSV file with English headers."""
    fieldnames = ["Year", "Rank", "DJ Name"]
    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow({
                    "Year": row["year"],
                    "Rank": row["rank"],
                    "DJ Name": row["name"],
                })
        print(f"Successfully wrote {len(data)} records to {filename}")
    except OSError as e:
        print(f"Error writing to {filename}: {e}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--years",
        help="comma-separated years to (re)scrape, e.g. --years 2004,2007",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"re-scrape every year {START_YEAR}..current instead of only incomplete ones",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing yearly CSVs even when the fresh scrape found less data",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = "djmag_rankings"
    os.makedirs(output_dir, exist_ok=True)
    current_year = datetime.datetime.now().year

    if args.years:
        try:
            targets = [int(y.strip()) for y in args.years.split(",") if y.strip()]
        except ValueError:
            sys.exit("--years expects comma-separated numbers, e.g. --years 2004,2007")
        if not targets:
            sys.exit("--years expects at least one year, e.g. --years 2004")
    elif args.all:
        targets = list(range(START_YEAR, current_year + 1))
    else:
        # Incremental: only fetch years whose archive is missing or incomplete,
        # plus the current poll year so a freshly published poll is picked up.
        targets = [
            year
            for year in range(START_YEAR, current_year + 1)
            if len(read_existing_rows(os.path.join(output_dir, f"{year}.csv"))) != POLL_SIZE
        ]
        if not targets:
            print(f"All years are already archived with {POLL_SIZE} entries. Nothing to do.")
            return

    session = make_session()
    scraped = {}
    for year in targets:
        print(f"--- Poll year {year} ---")
        year_djs = []
        if year == current_year:
            year_djs = scrape_dj_mag_json(session, year)
        if len(year_djs) < POLL_SIZE:
            html_djs = scrape_dj_mag_html(session, year)
            if len(html_djs) > len(year_djs):
                year_djs = html_djs
        if not year_djs:
            print(f"!!! Failed to retrieve any data for {year}.")
            continue
        scraped[year] = sorted(year_djs, key=lambda d: d["rank"])[:POLL_SIZE]

    if targets and not scraped:
        print("Every scrape attempt failed; aborting without changing any files.")
        sys.exit(1)

    for year, rows in scraped.items():
        filename = os.path.join(output_dir, f"{year}.csv")
        existing = read_existing_rows(filename)
        if existing and len(existing) > len(rows) and not args.force:
            print(
                f"!!! Keeping {filename} ({len(existing)} rows): the fresh scrape "
                f"only found {len(rows)}. Re-run with --force to overwrite."
            )
            continue
        write_csv(filename, rows)

    # Rebuild the consolidated file from the yearly files so it always mirrors
    # whatever is currently archived, even when this run only touched a few years.
    all_djs = []
    for year in range(START_YEAR, current_year + 1):
        all_djs.extend(read_existing_rows(os.path.join(output_dir, f"{year}.csv")))
    if not all_djs:
        print("No data archived; nothing to consolidate.")
        return

    all_djs.sort(key=lambda d: (-d["year"], d["rank"]))
    min_year = all_djs[-1]["year"]
    max_year = all_djs[0]["year"]
    all_filename = os.path.join(output_dir, f"all_{min_year}-{max_year}.csv")

    for name in os.listdir(output_dir):
        if name.startswith("all") and name != all_filename:
            os.remove(os.path.join(output_dir, name))
            print(f"Removed stale consolidated file: {name}")

    write_csv(all_filename, all_djs)
    print(f"Archive now covers {min_year}-{max_year}, {len(all_djs)} entries total.")


if __name__ == "__main__":
    main()
