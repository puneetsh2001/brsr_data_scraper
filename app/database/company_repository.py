
import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.database.connection import engine
from app.database.models import Company


# --------------------------------------------------
# FILE PATH
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]

JSON_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "company_details.json"
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
# SAVE COMPANY DETAILS
# --------------------------------------------------

def save_company_details():

    # Check JSON file
    if not JSON_FILE.exists():

        print("ERROR: Company JSON file not found!")
        print("Expected location:", JSON_FILE)
        return

    # Read JSON data
    with open(
        JSON_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        company_data = json.load(file)

    # Extract financial year separately
    financial_year = company_data.pop(
        "financial_year",
        None
    )

    # Convert incorporation year to integer
    year = company_data.get("incorporation_year")

    if year:
        company_data["incorporation_year"] = int(year)

    db = SessionLocal()

    try:

        cin = company_data.get("cin")

        # Check existing company using CIN
        company = None

        if cin:

            company = db.query(Company).filter(
                Company.cin == cin
            ).first()

        if company:

            print("Company already exists. Updating details...")

            for key, value in company_data.items():

                setattr(company, key, value)

        else:

            print("New company found. Inserting details...")

            company = Company(**company_data)

            db.add(company)

        # Save company information
        db.commit()

        # Get generated company ID
        db.refresh(company)

        print("\nCOMPANY SAVED SUCCESSFULLY!")
        print("=" * 50)

        print("Company ID:", company.id)
        print("Company Name:", company.company_name)
        print("CIN:", company.cin)
        print("Incorporation Year:", company.incorporation_year)
        print("Email:", company.email)
        print("Website:", company.website)

        print("\nBRSR REPORT INFORMATION")
        print("=" * 50)

        print("Financial Year:", financial_year)

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

    save_company_details()
