# DJ Mag Top 100 History

This project automatically scrapes and archives the annual Top 100 DJs poll results from the [DJ Mag website](https://djmag.com/top100djs).

## Data

The scraped data is stored in the `djmag_rankings/` directory:

- `<year>.csv` — one file per poll year, ranked 1–100
- `all_<min>-<max>.csv` — every archived year combined

Each CSV has three columns: `Year`, `Rank`, `DJ Name`.

## Workflow

A GitHub Actions workflow keeps the archive up to date. It runs monthly, daily during October (when DJ Mag announces the results), and can also be triggered manually from the Actions tab. When new data changes the CSVs, the workflow commits them back to the repository.

By default the scraper runs **incrementally**: it only fetches years that have no CSV yet or fewer than 100 entries, so archived years are never re-scraped and existing data is not replaced by a worse scrape.

## Manual Usage

To run the scraper manually:

1.  **Clone the repository.**

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the script:**
    ```bash
    python scraper.py                          # incremental update (recommended)
    python scraper.py --years 2004,2007        # re-scrape specific years
    python scraper.py --all                    # re-scrape every year
    python scraper.py --force                  # allow overwriting with fewer rows
    ```

The script exits with a non-zero code if none of the attempted scrapes succeeded, so scheduled/CI runs show up as failed when the site could not be reached.

## License

[MIT](LICENSE)
