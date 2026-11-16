
import csv
from pathlib import Path

from app.database.connection import SessionLocal
from app.database.models import Company


BSE_FILE = Path(
    "data/raw/reference/bse/extracted/SCRIP/BSE_EQ_SCRIP_09112026.csv"
)

OUTPUT_FILE = Path(
    "data/processed/bse_company_lookup_results.csv"
)


def lookup_bse_companies():

    print("Loading BSE Equity Master...")

    if not BSE_FILE.exists():
        print(f"File not found: {BSE_FILE}")
        return

    # Load BSE records grouped by ISIN
    bse_records = {}

    with open(BSE_FILE, "r", encoding="utf-8-sig", newline="") as file:

        reader = csv.DictReader(file)

        for row in reader:

            isin = (row.get("ISIN") or "").strip().upper()

            if isin:
                bse_records.setdefault(isin, []).append(row)

    total_records = sum(len(records) for records in bse_records.values())

    print(f"BSE records loaded: {total_records}")

    db = SessionLocal()

    try:

        companies = db.query(Company).order_by(Company.id).all()

        print(f"Database companies: {len(companies)}")

        results = []

        matched = 0
        not_found = 0
        ambiguous = 0

        for company in companies:

            isin = (company.isin or "").strip().upper()

            all_matches = bse_records.get(isin, [])

            # Select active standard equity records
            eligible_records = [
                record for record in all_matches
                if (record.get("TckrSymb") or "").strip()
                and not (record.get("TckrSymb") or "").strip().endswith("#")
                and (record.get("SctyTpFlg") or "").strip() == "EQ"
                and (record.get("Sts") or "").strip() == "A"
                and not (record.get("DelFlg") or "").strip()
            ]

            record = {}

            if len(eligible_records) == 1:

                record = eligible_records[0]
                match_status = "MATCHED"
                matched += 1

            elif len(eligible_records) > 1:

                match_status = "MULTIPLE_STANDARD_EQUITY_RECORDS"
                ambiguous += 1

            else:

                match_status = "NO_STANDARD_EQUITY_RECORD"
                not_found += 1

            results.append({

                "company_id": company.id,
                "database_company_name": company.company_name,
                "nse_symbol": company.nse_symbol,
                "isin": company.isin,

                "total_bse_records": len(all_matches),
                "eligible_equity_records": len(eligible_records),

                "bse_company_name": record.get("FinInstrmNm", ""),
                "bse_scrip_code": record.get("FinInstrmId", ""),
                "bse_trading_symbol": record.get("TckrSymb", ""),
                "bse_series": record.get("SctySrs", ""),

                "match_status": match_status
            })

        # Save report
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = list(results[0].keys()) if results else []

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        print("\n========== CORRECTED BSE LOOKUP SUMMARY ==========")
        print(f"Total database companies: {len(companies)}")
        print(f"Successfully matched: {matched}")
        print(f"No standard equity record: {not_found}")
        print(f"Ambiguous records: {ambiguous}")

        print("\nReport saved to:")
        print(OUTPUT_FILE)

        print("\nPostgreSQL database was NOT modified.")

    finally:
        db.close()


if __name__ == "__main__":
    lookup_bse_companies()
