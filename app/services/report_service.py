
from app.database.connection import SessionLocal
from app.database.models import Company

from app.scrapers.report_discovery import discover_brsr_reports


def discover_company_reports(company_name=None):
    """
    Discover BRSR reports for one company or all registered companies.
    """

    db = SessionLocal()

    try:

        # If company name is provided, process only that company.
        if company_name:

            companies = (
                db.query(Company)
                .filter(
                    Company.company_name.ilike(
                        f"%{company_name.strip()}%"
                    )
                )
                .all()
            )

        # Otherwise, process all registered companies.
        else:
            companies = db.query(Company).all()

        if not companies:
            print("No matching companies found.")
            return []

        all_reports = []

        print(f"\nCompanies to process: {len(companies)}")

        for company in companies:

            print("\n" + "=" * 50)
            print(f"Processing: {company.company_name}")
            print("=" * 50)

            reports = discover_brsr_reports(
                company.company_name
            )

            if reports:
                all_reports.extend(reports)

                for report in reports:
                    print("----------------------------------")
                    print("Company:", report["company_name"])
                    print("Financial Year:", report["financial_year"])
                    print("Report Type:", report["report_type"])
                    print("Report URL:", report["report_url"])

            else:
                print("No BRSR reports found.")

        print("\n" + "=" * 50)
        print("REPORT DISCOVERY SUMMARY")
        print("=" * 50)
        print("Companies processed:", len(companies))
        print("Reports discovered:", len(all_reports))

        return all_reports

    finally:
        db.close()
