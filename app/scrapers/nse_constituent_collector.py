from app.company.company_service import insert_companies
import csv
import requests

from pathlib import Path
from datetime import datetime, timezone


NSE_CSV_URL = (
    "https://nsearchives.nseindia.com/content/indices/"
    "ind_nifty100list.csv"
)

NSE_PAGE_URL = (
    "https://www.nseindia.com/static/products-services/"
    "indices-nifty100-index"
)

OUTPUT_DIR = Path("data/raw")
OUTPUT_FILE = OUTPUT_DIR / "nse_nifty100_constituents.csv"


def download_nifty100():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*",
        "Referer": NSE_PAGE_URL
    }

    print("Downloading official NSE Nifty 100 list...")

    response = requests.get(
        NSE_CSV_URL,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    if not response.content:
        raise ValueError("NSE returned an empty CSV file.")

    OUTPUT_FILE.write_bytes(response.content)

    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)
        records = list(reader)

    if len(records) != 100:
        raise ValueError(
            f"Expected 100 companies, received {len(records)}. "
            "Check the downloaded file before proceeding."
        )

    if not reader.fieldnames:
        raise ValueError("CSV column headers are missing.")

    print("\nDownload successful!")
    print(f"Records found: {len(records)}")
    print(f"CSV columns: {reader.fieldnames}")
    print(f"Saved file: {OUTPUT_FILE}")
    print(
        "Retrieved at:",
        datetime.now(timezone.utc).isoformat()
    )

    print("\nFirst five records:")

    for record in records[:5]:
        print(record)


def load_nifty100_records():

    import csv

    records = []

    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            company = {
                "company_name": row["Company Name"].strip(),
                "industry": row["Industry"].strip() or None,
                "nse_symbol": row["Symbol"].strip(),
                "series": row["Series"].strip() or None,
                "isin": row["ISIN Code"].strip(),
                "source_url": NSE_PAGE_URL,
                "source_name": "NSE Nifty 100 Constituents"
            }

            records.append(company)

    print(f"Total company records loaded: {len(records)}")

    return records



if __name__ == "__main__":

    companies = load_nifty100_records()

    print(f"\nReady to process {len(companies)} companies.")

    insert_companies(companies)