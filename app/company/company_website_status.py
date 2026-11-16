
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.models import Company


def check_company_websites():

    with Session(engine) as session:

        companies = session.query(Company).order_by(
            Company.company_name
        ).all()

        print("\nCOMPANY WEBSITE STATUS")
        print("=" * 70)

        for company in companies:

            print(
                f"{company.company_name} | "
                f"Website: {company.website or 'MISSING'} | "
                f"Investor URL: {company.investor_url or 'MISSING'}"
            )

        total = len(companies)
        websites = sum(bool(c.website) for c in companies)
        investor_urls = sum(bool(c.investor_url) for c in companies)

        print("\nSUMMARY")
        print("=" * 70)
        print("Total companies:", total)
        print("Companies with website:", websites)
        print("Companies without website:", total - websites)
        print("Companies with investor URL:", investor_urls)
        print("Companies without investor URL:", total - investor_urls)


if __name__ == "__main__":
    check_company_websites()
