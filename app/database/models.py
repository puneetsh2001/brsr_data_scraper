
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.database.connection import engine

Base = declarative_base()


# --------------------------------------------------
# COMPANY MODEL
# --------------------------------------------------


class Company(Base):

    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)

    # Company identification
    company_name = Column(String(255), nullable=False)
    cin = Column(String(30), unique=True, nullable=True)
    nse_symbol = Column(String(50), unique=True, nullable=True)
    bse_code = Column(String(20), unique=True, nullable=True)
    isin = Column(String(20), unique=True, nullable=True)

    # Company details
    incorporation_year = Column(Integer, nullable=True)
    registered_office = Column(String(500), nullable=True)
    corporate_address = Column(String(500), nullable=True)
    email = Column(String(255), nullable=True)
    telephone = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)
    investor_url = Column(String(1000), nullable=True)

    # Data traceability
    source_url = Column(String(1000), nullable=True)
    source_name = Column(String(255), nullable=True)
    industry = Column(String(255), nullable=True)
    series = Column(String(20), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    reports = relationship(
        "BRSRReport",
        back_populates="company"
    )



# --------------------------------------------------
# BRSR REPORT MODEL
# --------------------------------------------------

class BRSRReport(Base):

    __tablename__ = "brsr_reports"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False
    )

    financial_year = Column(String(20), nullable=False)
    report_type = Column(String(100))
    file_path = Column(String(500))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Each report belongs to one company
    company = relationship(
        "Company",
        back_populates="reports"
    )

    # One report can have multiple chunks
    chunks = relationship(
        "BRSRChunk",
        back_populates="report"
    )


# --------------------------------------------------
# BRSR CHUNK MODEL
# --------------------------------------------------

class BRSRChunk(Base):

    __tablename__ = "brsr_chunks"

    id = Column(Integer, primary_key=True, index=True)

    report_id = Column(
        Integer,
        ForeignKey("brsr_reports.id"),
        nullable=False
    )

    section = Column(String(100))
    page_number = Column(Integer)

    chunk_text = Column(String, nullable=False)

    embedding = Column(Vector(384))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Each chunk belongs to one report
    report = relationship(
        "BRSRReport",
        back_populates="chunks"
    )


# --------------------------------------------------
# CREATE TABLES
# --------------------------------------------------

def create_tables():

    Base.metadata.create_all(bind=engine)

    print("Database tables created successfully!")
