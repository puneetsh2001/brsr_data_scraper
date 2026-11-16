
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database.connection import engine
from app.database.models import Company


def insert_companies(company_records):

    inserted = 0
    skipped = 0
    updated = 0

    allowed_fields = {
        "company_name",
        "cin",
        "nse_symbol",
        "bse_code",
        "isin",
        "incorporation_year",
        "registered_office",
        "corporate_address",
        "email",
        "telephone",
        "website",
        "source_url",
        "source_name"
        "industry",
        "series",
    }

    with Session(engine) as session:

        try:
            for company_data in company_records:

                company_name = company_data.get("company_name")

                if not company_name or not company_name.strip():
                    print("Skipped: Missing company name")
                    skipped += 1
                    continue

                # Require at least one verified company identifier
                identifiers = {
                    key: company_data.get(key)
                    for key in ("cin", "nse_symbol", "bse_code", "isin")
                    if company_data.get(key)
                }

                if not identifiers:
                    print(f"Skipped: No identifier for {company_name}")
                    skipped += 1
                    continue

                # Require source information for traceability
                if not company_data.get("source_url") or not company_data.get("source_name"):
                    print(f"Skipped: Missing source information for {company_name}")
                    skipped += 1
                    continue

                # Find an existing company using its identifiers
                conditions = [
                    getattr(Company, key) == value
                    for key, value in identifiers.items()
                ]

                existing_company = (
                    session.query(Company)
                    .filter(or_(*conditions))
                    .first()
                )

                clean_data = {
                    key: value
                    for key, value in company_data.items()
                    if key in allowed_fields and value is not None
                }

                if existing_company:

                    # Fill missing fields without overwriting existing data
                    for key, value in clean_data.items():

                        if getattr(existing_company, key) is None:
                            setattr(existing_company, key, value)

                    updated += 1
                    print(f"Already exists: {company_name}")

                else:

                    company = Company(**clean_data)
                    session.add(company)
                    inserted += 1

            session.commit()

            print("\nCompany master processing completed!")
            print(f"New companies inserted: {inserted}")
            print(f"Existing companies checked: {updated}")
            print(f"Records skipped: {skipped}")

        except Exception:
            session.rollback()
            raise
