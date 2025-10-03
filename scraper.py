import requests
import json
import csv
import os
import re
from bs4 import BeautifulSoup
import datetime
from urllib.parse import unquote

def scrape_dj_mag_json(year_to_scrape):
    """Scrapes DJ Mag Top 100 for a specific year using the JSON-LD data.
    This method is most reliable for the latest poll year."""
    print(f"Fetching from: https://djmag.com/top100djs (JSON method)")
    try:
        response = requests.get("https://djmag.com/top100djs", timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Could not fetch main page for JSON-LD: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    script_tag = soup.find('script', {'type': 'application/ld+json'})
    if not script_tag:
        print(f"Could not find JSON-LD script tag on main page.")
        return []

    data = json.loads(script_tag.string)
    djs = []
    item_list = next((item for item in data.get('@graph', []) if item.get('@type') == 'ItemList'), None)
    if not item_list:
        print(f"Could not find ItemList in JSON-LD on main page.")
        return []

    for item in item_list.get('itemListElement', []):
        try:
            position = item.get('position')
            dj_url = item.get('url')
            name_slug = dj_url.split('/')[-1]
            dj_name = unquote(name_slug).replace('-', ' ').title()
            year_from_url = int(dj_url.split('/')[-3])
            
            if year_from_url == year_to_scrape:
                djs.append({'year': year_from_url, 'rank': position, 'name': dj_name})
        except (AttributeError, IndexError, ValueError) as e:
            print(f"Skipping a JSON item due to parsing error: {e} - Item: {item}")
            continue
    return djs

def scrape_dj_mag_html(year_to_scrape):
    """Scrapes DJ Mag Top 100 for historical years by finding and parsing URLs."""
    url = f"https://djmag.com/top100djs/{year_to_scrape}"
    print(f"Fetching from: {url} (URL parsing method)")
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Could not fetch {url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    djs = []
    
    dj_url_pattern = re.compile(r'^/top100djs/(\d{4})/(\d{1,3})/.+$')
    
    found_djs = set()

    for link in soup.find_all('a', href=True):
        href = link['href']
        match = dj_url_pattern.match(href)
        
        if match:
            year = int(match.group(1))
            rank = int(match.group(2))
            name_slug = href.split('/')[-1]
            name = unquote(name_slug).replace('-', ' ').title()

            if year == year_to_scrape:
                found_djs.add((rank, name))

    if not found_djs:
        print(f"Could not find any DJ links matching the required pattern on {url}.")
        return []

    for rank, name in sorted(list(found_djs)):
        djs.append({'year': year_to_scrape, 'rank': rank, 'name': name})
        
    return djs

def write_csv(filename, data):
    """Helper function to write data to a CSV file with English headers."""
    fieldnames = ['Year', 'Rank', 'DJ Name']
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow({'Year': row['year'], 'Rank': row['rank'], 'DJ Name': row['name']})
        print(f"Successfully wrote {len(data)} records to {filename}")
    except IOError as e:
        print(f"Error writing to {filename}: {e}")

def main():
    """Main function to scrape data, and write yearly and consolidated CSVs."""
    all_djs = []
    start_year = 2004
    current_year = datetime.datetime.now().year
    output_dir = "djmag_rankings"
    os.makedirs(output_dir, exist_ok=True)

    for year in range(start_year, current_year + 1):
        print(f"--- Scraping data for poll year {year} ---")
        
        year_djs = []
        if year == current_year:
            year_djs = scrape_dj_mag_json(year)
        
        if not year_djs:
            year_djs = scrape_dj_mag_html(year)

        if year_djs:
            year_djs = sorted(year_djs, key=lambda x: x['rank'])[:100]
            yearly_filename = os.path.join(output_dir, f"{year}.csv")
            write_csv(yearly_filename, year_djs)
            all_djs.extend(year_djs)
        else:
            print(f"!!! Failed to retrieve any data for {year}.")

    if not all_djs:
        print("No data was scraped. Exiting.")
        return

    all_djs.sort(key=lambda x: (-x['year'], x['rank']))
    min_year = min(d['year'] for d in all_djs) if all_djs else start_year
    max_year = max(d['year'] for d in all_djs) if all_djs else current_year
    all_filename = os.path.join(output_dir, f"all ({min_year}-{max_year}).csv")
    write_csv(all_filename, all_djs)

if __name__ == "__main__":
    main()
