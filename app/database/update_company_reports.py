
import json
from pathlib import Path

from app.database.connection import SessionLocal
from app.database.models import Company, BRSRReport


JSON_FILE = Path("data/processed/company_details_by_year.json")

REPORT_FOLDER = Path("data/raw/Reliance Industries Limited")


def update_company_reports():

    if not JSON_FILE.exists():
        print("Company details JSON file not found!")
        return

    with open(JSON_FILE, "r", encoding="utf-8") as file:
        all_details = json.load(file)

    db = SessionLocal()

    try:

        # Find existing company using CIN
        company = db.query(Company).filter(
            Company.cin == "L17110MH1973PLC019786"
        ).first()

        if not company:
            print("Reliance company record not found!")
            return

        # Update company details
        first_details = next(iter(all_details.values()))

        company.company_name = first_details["company_name"]
        company.cin = first_details["cin"]
        company.incorporation_year = int(
            first_details["incorporation_year"]
        )
        company.registered_office = first_details["registered_office"]
        company.corporate_address = first_details["corporate_address"]
        company.email = first_details["email"]
        company.telephone = first_details["telephone"]
        company.website = first_details["website"]

        db.commit()

        print("Company details updated successfully!")

        # Process all four financial years
        for report_key, details in all_details.items():

            financial_year = details["financial_year"]

            # Check whether report already exists
            existing_report = db.query(BRSRReport).filter(
                BRSRReport.company_id == company.id,
                BRSRReport.financial_year == financial_year
            ).first()

            if existing_report:

                print(
                    f"Report already exists: {financial_year} "
                    f"(ID: {existing_report.id})"
                )

                continue

            # Convert FY 2022-23 into BRSR202223
            year_code = financial_year.replace("FY ", "").replace("-", "")

            pdf_path = (
                REPORT_FOLDER / f"BRSR{year_code}.pdf"
            )

            if not pdf_path.exists():

                print(
                    f"PDF not found for {financial_year}: {pdf_path}"
                )

                continue

            report = BRSRReport(
                company_id=company.id,
                financial_year=financial_year,
                report_type="BRSR",
                file_path=str(pdf_path)
            )

            db.add(report)
            db.commit()
            db.refresh(report)

            print(
                f"Report inserted: {financial_year} "
                f"(ID: {report.id})"
            )

        print("\nDatabase update completed!")

    except Exception as error:

        db.rollback()
        print("Database update failed:", error)

    finally:

        db.close()


if __name__ == "__main__":
    update_company_reports()
