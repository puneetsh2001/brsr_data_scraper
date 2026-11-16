
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.models import BRSRReport, BRSRChunk


REPORT_FILES = [
    {
        "financial_year": "FY 2022-23",
        "input_file": Path(
            "data/processed/chunks_by_year/BRSR202223_chunks_with_embeddings.json"
        )
    },
    {
        "financial_year": "FY 2024-25",
        "input_file": Path(
            "data/processed/chunks_by_year/BRSR202425_chunks_with_embeddings.json"
        )
    },
    {
        "financial_year": "FY 2025-26",
        "input_file": Path(
            "data/processed/chunks_by_year/BRSR202526_chunks_with_embeddings.json"
        )
    }
]


def save_chunks():

    session = Session(bind=engine)

    total_inserted = 0

    try:

        for report_info in REPORT_FILES:

            financial_year = report_info["financial_year"]
            input_file = report_info["input_file"]

            print("\n" + "=" * 50)
            print("Processing:", financial_year)
            print("=" * 50)

            if not input_file.exists():
                print("Input file not found:", input_file)
                continue

            with open(input_file, "r", encoding="utf-8") as file:
                chunks = json.load(file)

            print("Total chunks loaded:", len(chunks))

            # Find report using financial year
            report = (
                session.query(BRSRReport)
                .filter(
                    BRSRReport.financial_year == financial_year,
                    BRSRReport.company_id == 6
                )
                .first()
            )

            if not report:
                print("Report not found in database!")
                continue

            print("Report ID:", report.id)

            # Prevent duplicate insertion
            existing_count = (
                session.query(BRSRChunk)
                .filter(
                    BRSRChunk.report_id == report.id
                )
                .count()
            )

            if existing_count > 0:

                print(
                    f"{existing_count} chunks already exist."
                )

                print("Skipping to avoid duplicates.")
                continue

            chunk_records = []

            for chunk in chunks:

                embedding = chunk.get("embedding")

                if not embedding or len(embedding) != 384:
                    raise ValueError(
                        f"Invalid embedding in {financial_year}, "
                        f"chunk {chunk.get('chunk_id')}"
                    )

                record = BRSRChunk(
                    report_id=report.id,
                    section="BRSR",
                    page_number=chunk["page_number"],
                    chunk_text=chunk["chunk_text"],
                    embedding=embedding
                )

                chunk_records.append(record)

            session.add_all(chunk_records)
            session.commit()

            print("Chunks inserted:", len(chunk_records))

            total_inserted += len(chunk_records)

        print("\n" + "=" * 50)
        print("DATABASE INSERTION COMPLETED!")
        print("Total new chunks inserted:", total_inserted)
        print("=" * 50)

    except Exception as error:

        session.rollback()

        print("Database insertion failed!")
        print("Error:", error)

    finally:

        session.close()


if __name__ == "__main__":
    save_chunks()
