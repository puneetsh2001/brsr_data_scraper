from app.database.connection import engine
from app.database.models import Company
from sqlalchemy.orm import Session

# Create a database session
with Session(engine) as session:

    company = Company(
        company_name="Reliance Industries Limited",
        cin="L17110MH1973PLC019786",
        nse_symbol="RELIANCE",
        bse_code="500325",
        isin="INE002A01018"
    )

    session.add(company)
    session.commit()

    print("Company inserted successfully!")
    print("Company ID:", company.id)