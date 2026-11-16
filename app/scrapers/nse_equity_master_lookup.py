
import csv
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.models import Company


INPUT_FILE = Path("data/raw/reference/NSE_EQUITY_L.csv")

OUTPUT_FILE = Path(
    "data/processed/nse_company_lookup_results.csv"
)


def load_nse_equity_master():

    records = {}

    with open(INPUT_FILE, "r", encoding="utf-8-sig", newline="") as file:

        reader = csv.DictReader(file)

        for row in reader:

            # Remove whitespace from CSV column names
            row = {
                key.strip(): value.strip() if value else ""
                for key, value in row.items()
            }

            symbol = row.get("SYMBOL", "")

            if symbol:
                records[symbol] = row

    return records


def lookup_companies():

    print("Loading NSE Equity Master...")

    nse_records = load_nse_equity_master()

    print("NSE records loaded:", len(nse_records))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    exact_matches = 0
    isin_mismatches = 0
    symbols_not_found = 0

    results = []

    with Session(engine) as session:

        companies = session.query(Company).all()

        print("Database companies:", len(companies))

        for company in companies:

            symbol = (company.nse_symbol or "").strip()
            isin = (company.isin or "").strip()

            nse_record = nse_records.get(symbol)

            if not nse_record:

                status = "SYMBOL_NOT_FOUND"
                symbols_not_found += 1

            elif isin == nse_record.get("ISIN NUMBER", ""):

                status = "EXACT_MATCH"
                exact_matches += 1

            else:

                status = "ISIN_MISMATCH"
                isin_mismatches += 1

            results.append({
                "company_name": company.company_name,
                "database_symbol": symbol,
                "database_isin": isin,
                "nse_company_name": (
                    nse_record.get("NAME OF COMPANY", "")
                    if nse_record else ""
                ),
                "nse_isin": (
                    nse_record.get("ISIN NUMBER", "")
                    if nse_record else ""
                ),
                "series": (
                    nse_record.get("SERIES", "")
                    if nse_record else ""
                ),
                "date_of_listing": (
                    nse_record.get("DATE OF LISTING", "")
                    if nse_record else ""
                ),
                "paid_up_value": (
                    nse_record.get("PAID UP VALUE", "")
                    if nse_record else ""
                ),
                "face_value": (
                    nse_record.get("FACE VALUE", "")
                    if nse_record else ""
                ),
                "match_status": status
            })

    fieldnames = list(results[0].keys()) if results else []

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(results)

    print("\n========== LOOKUP SUMMARY ==========")

    print("Total database companies:", len(results))
    print("Exact matches:", exact_matches)
    print("ISIN mismatches:", isin_mismatches)
    print("Symbols not found:", symbols_not_found)

    print("\nLookup report saved to:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    lookup_companies()
