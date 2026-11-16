
import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.database.connection import engine
from app.database.models import Company, BRSRReport


# --------------------------------------------------
# FILE PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]

JSON_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "company_details.json"
)

REPORT_FILE = (
    BASE_DIR
    / "data"
    / "raw"
    / "Reliance"
    / "Reliance_BRSR_2023-24.pdf"
)


# --------------------------------------------------
# DATABASE SESSION
# --------------------------------------------------

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# --------------------------------------------------
# SAVE BRSR REPORT
# --------------------------------------------------

def save_brsr_report():

    # Check JSON file
    if not JSON_FILE.exists():
        print("ERROR: Company details JSON not found!")
        return

    # Check PDF file
    if not REPORT_FILE.exists():
        print("ERROR: BRSR PDF file not found!")
        print("Expected location:", REPORT_FILE)
        return

    # Read company information
    with open(
        JSON_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        company_data = json.load(file)

    cin = company_data.get("cin")
    financial_year = company_data.get("financial_year")

    if not cin or not financial_year:
        print("ERROR: CIN or financial year missing!")
        return

    db = SessionLocal()

    try:

        # Find company using CIN
        company = db.query(Company).filter(
            Company.cin == cin
        ).first()

        if not company:
            print("ERROR: Company not found in database!")
            print("Please save company details first.")
            return

        # Check whether this report already exists
        existing_report = db.query(BRSRReport).filter(
            BRSRReport.company_id == company.id,
            BRSRReport.financial_year == financial_year,
            BRSRReport.report_type == "BRSR"
        ).first()

        if existing_report:

            print("BRSR report already exists. Updating details...")

            existing_report.file_path = str(
                REPORT_FILE.relative_to(BASE_DIR)
            )

        else:

            # Create new BRSR report record
            report = BRSRReport(
                company_id=company.id,
                financial_year=financial_year,
                report_type="BRSR",
                file_path=str(
                    REPORT_FILE.relative_to(BASE_DIR)
                )
            )

            db.add(report)

        db.commit()

        print("\nBRSR REPORT SAVED SUCCESSFULLY!")
        print("=" * 50)

        print("Company ID:", company.id)
        print("Company Name:", company.company_name)
        print("Financial Year:", financial_year)
        print("Report Type: BRSR")
        print("PDF Path:", REPORT_FILE.relative_to(BASE_DIR))

    except Exception as error:

        db.rollback()

        print("\nDATABASE INSERTION FAILED!")
        print("Error:", error)

    finally:

        db.close()


# --------------------------------------------------
# MAIN
# --------------------------------------------------

if __name__ == "__main__":
    save_brsr_report()
