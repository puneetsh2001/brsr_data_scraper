
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.models import Company


def get_company_website(company_name):

    with Session(engine) as session:

        company = (
            session.query(Company)
            .filter(Company.company_name == company_name)
            .first()
        )

        if not company:
            print(f"Company not found in database: {company_name}")
            return None

        return {
            "company_name": company.company_name,
            "website": company.website,
            "investor_url": company.investor_url
        }


if __name__ == "__main__":

    with Session(engine) as session:

        companies = session.query(Company).all()

        print(f"Total companies: {len(companies)}")

        for company in companies[:5]:

            print({
                "company_name": company.company_name,
                "website": company.website,
                "investor_url": company.investor_url
            })
