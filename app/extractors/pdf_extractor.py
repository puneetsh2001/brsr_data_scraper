import fitz
from pathlib import Path


def extract_text_from_pdf(pdf_path):
    pdf_path = Path(pdf_path)

    print(f"\nProcessing: {pdf_path.name}")

    try:
        document = fitz.open(pdf_path)
        extracted_text = []

        for page_number, page in enumerate(document, start=1):
            page_text = page.get_text("text")

            extracted_text.append(
                f"\n--- PAGE {page_number} ---\n{page_text}"
            )

        document.close()

        return "\n".join(extracted_text)

    except Exception as error:
        print(f"Extraction failed: {error}")
        return ""


def extract_all_brsr_pdfs():
    input_folder = Path("data/raw/Reliance Industries Limited")
    output_folder = Path("data/processed/extracted_text")

    output_folder.mkdir(parents=True, exist_ok=True)

    pdf_files = list(input_folder.glob("*.pdf"))

    print("Total PDF files found:", len(pdf_files))

    for pdf_file in pdf_files:
        extracted_text = extract_text_from_pdf(pdf_file)

        if not extracted_text.strip():
            print("No text extracted. Skipping:", pdf_file.name)
            continue

        output_file = output_folder / f"{pdf_file.stem}.txt"
        output_file.write_text(extracted_text, encoding="utf-8")

        print("Saved:", output_file)
        print("Extracted characters:", len(extracted_text))

    print("\nPDF text extraction completed!")


if __name__ == "__main__":
    extract_all_brsr_pdfs()