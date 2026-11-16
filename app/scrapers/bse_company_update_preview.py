
import csv
from pathlib import Path

from app.database.connection import SessionLocal
from app.database.models import Company


REPORT_FILE = Path(
    "data/processed/bse_company_lookup_results.csv"
)


def preview_bse_update():

    with open(REPORT_FILE, "r", encoding="utf-8-sig", newline="") as file:
        report = list(csv.DictReader(file))

    db = SessionLocal()

    try:
        companies = db.query(Company).all()

        report_by_id = {
            int(row["company_id"]): row
            for row in report
        }

        to_update = 0
        already_correct = 0
        conflicts = 0
        invalid = 0

        print("\n========== BSE UPDATE PREVIEW ==========")

        for company in companies:

            row = report_by_id.get(company.id)

            if not row or row["match_status"] != "MATCHED":
                invalid += 1
                print(f"INVALID MATCH: {company.company_name}")
                continue

            new_code = row["bse_scrip_code"].strip()
            old_code = (company.bse_code or "").strip()

            if not new_code:
                invalid += 1
                print(f"MISSING BSE CODE: {company.company_name}")
                continue

            if not old_code:
                to_update += 1

            elif old_code == new_code:
                already_correct += 1

            else:
                conflicts += 1
                print(
                    f"CONFLICT: {company.company_name} | "
                    f"Database: {old_code} | "
                    f"Lookup: {new_code}"
                )

        print("\n========== SUMMARY ==========")
        print(f"Companies in database: {len(companies)}")
        print(f"New codes to populate: {to_update}")
        print(f"Already correct: {already_correct}")
        print(f"Conflicting existing codes: {conflicts}")
        print(f"Invalid or missing lookup: {invalid}")

        print("\nDatabase was NOT modified.")

    finally:
        db.close()


if __name__ == "__main__":
    preview_bse_update()
