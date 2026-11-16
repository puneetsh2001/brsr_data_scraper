
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import Company, BRSRReport


router = APIRouter(
    prefix="/companies",
    tags=["Companies"]
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/report-status")
def get_company_report_status(db: Session = Depends(get_db)):

    companies = db.query(Company).all()

    response = []

    for company in companies:

        reports = (
            db.query(BRSRReport)
            .filter(BRSRReport.company_id == company.id)
            .all()
        )

        financial_years = [
            report.financial_year for report in reports
        ]

        response.append({
            "company_name": company.company_name,
            "cin": company.cin,
            "report_available": len(reports) > 0,
            "total_reports": len(reports),
            "available_financial_years": financial_years
        })

    return {
        "total_companies": len(companies),
        "companies_with_reports": sum(
            1 for company in response if company["report_available"]
        ),
        "companies_without_reports": sum(
            1 for company in response if not company["report_available"]
        ),
        "companies": response
    }
