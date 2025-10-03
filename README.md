# DJ Mag Top 100 History

This project automatically scrapes and archives the annual Top 100 DJs poll results from the [DJ Mag website](https://djmag.com/top100djs).

## Data

The scraped data is stored in the `djmag_rankings/` directory.

## Automation

This repository uses GitHub Actions to automatically run the scraper script. This ensures the data stays up-to-date with the latest poll results. The workflow will automatically commit any updated CSV files back to the repository.

## Manual Usage

To run the scraper manually:

1.  **Clone the repository.**

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the script:**
    ```bash
    python scraper.py
    ```
