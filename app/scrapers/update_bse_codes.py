
import csv
from pathlib import Path

from sqlalchemy import text

from app.database.connection import SessionLocal
from app.database.models import Company


REPORT_FILE = Path(
    "data/processed/bse_company_lookup_results.csv"
)


def update_bse_codes():

    print("Loading verified BSE lookup report...")

    with open(REPORT_FILE, "r", encoding="utf-8-sig", newline="") as file:
        report = list(csv.DictReader(file))

    db = SessionLocal()

    try:

        updated = 0
        skipped = 0

        # All changes are committed together or rolled back on failure.
        with db.begin():

            for row in report:

                if row["match_status"] != "MATCHED":
                    raise ValueError(
                        f"Invalid match for company ID {row['company_id']}"
                    )

                company_id = int(row["company_id"])
                new_code = row["bse_scrip_code"].strip()

                if not new_code:
                    raise ValueError(
                        f"Missing BSE code for company ID {company_id}"
                    )

                company = db.query(Company).filter(
                    Company.id == company_id
                ).with_for_update().first()

                if not company:
                    raise ValueError(
                        f"Company ID {company_id} not found"
                    )

                existing_code = (company.bse_code or "").strip()

                if existing_code:

                    if existing_code != new_code:
                        raise ValueError(
                            f"BSE code conflict for {company.company_name}: "
                            f"existing={existing_code}, new={new_code}"
                        )

                    skipped += 1
                    continue

                # Populate only NULL/empty BSE codes.
                company.bse_code = new_code
                updated += 1

        print("\n========== UPDATE SUMMARY ==========")
        print(f"New BSE codes populated: {updated}")
        print(f"Existing correct codes preserved: {skipped}")
        print("Transaction committed successfully.")

        # Verify database after update
        total = db.query(Company).count()

        with_bse = db.query(Company).filter(
            Company.bse_code.isnot(None),
            Company.bse_code != ""
        ).count()

        without_bse = total - with_bse

        print("\n========== DATABASE VERIFICATION ==========")
        print(f"Total companies: {total}")
        print(f"Companies with BSE code: {with_bse}")
        print(f"Companies without BSE code: {without_bse}")

    except Exception as error:

        db.rollback()

        print("\nUPDATE FAILED!")
        print("Transaction rolled back.")
        print("Error:", error)

    finally:
        db.close()


if __name__ == "__main__":
    update_bse_codes()
